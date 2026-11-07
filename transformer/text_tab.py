# -*- coding: utf-8 -*-
"""
“文字转换”选项卡：一对多歧义检测引擎与界面组件。

本模块分两部分：
- 一对多歧义检测引擎（纯 Python，不依赖 Qt，Win10 / Win7 / Linux 通用）；
- 界面组件：一对多歧义标注输出框 AmbiguityOutputEdit 与后台转换线程
  TextConversionWorker，供 main.py 的“文字转换”页使用。

“文字转换”页的转换结果仍由 OpenCC 绑定（opencc / opencc-py）产生，与“文件
转换”逐字一致；本模块自行解析随发行版安装的 txt 词典，复现 OpenCC 的词典
匹配过程，找出转换结果中来源于一对多映射的跨度（span）及其全部候选值，供
界面用红色波浪线标注并允许手动修正。因此不依赖原生 OpenCC CLI 的
--ambiguities 能力，也不依赖 OpenCC 1.4.3 才引入的 query_candidates 绑定。

对齐策略（宁可漏标、绝不标错）：
1. 快速路径——若按本引擎切分拼装的输出与 cc.convert() 完全一致，说明匹配
   过程与 OpenCC 实际转换一致，跨度直接精确可用；
2. 退化路径——不一致时（如 cppjieba 与 Python jieba 的个别切分差异），对每
   个歧义词用“前缀转换定位法”（与 pdf_converter._split_converted_by_prefix
   同一思路）逐个校验，只保留与实际输出对齐的跨度，其余安全跳过并计数。

匹配算法已按 OpenCC 源码逐条核对（E:\\Github\\OpenCC）：
- src/Conversion.cpp Conversion::AppendConverted——逐位置匹配：取当前起点
  上“组内按词典优先级短路后的最长键”，命中即替换为该键默认值（词条首值），
  未命中则单字原样输出；
- src/PrefixMatch.cpp GroupMatcher::MatchPrefixShortCircuit——组内短路：按
  词典声明顺序，第一个存在前缀匹配的词典胜出，用该词典自身的最长键；
- src/Config.cpp + ConfigBasedConverter——normalization 链先于主链对整段文
  本应用，之后（若有）结巴分词切段，每个词再走主链逐位置匹配；
- src/ConversionCandidates.cpp GetAllConversions——候选值 = 组内第一个含该
  键的词典的全部值。

txt 词典格式（与 t2gov/ 目录一致）：
    '#' 注释行；每行“键<TAB>值1 值2 …”，多个候选用空格分隔，首值为默认转换。
"""

import bisect
import json
import os

from opencc import OpenCC
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (QColor, QFontMetricsF, QPainter, QPainterPath, QPen,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import QTextEdit


class AmbiguityEngineError(Exception):
    """引擎初始化或词典加载失败。"""


class ConversionCancelled(Exception):
    """转换被用户取消。"""


# ---------------------------------------------------------------------------
# 词典数据结构
# ---------------------------------------------------------------------------

class _TextDict:
    """一份 txt 词典：键 -> 候选值列表（保持文件顺序，首值为默认转换）。"""

    __slots__ = ("values", "max_key_len", "first_chars")

    def __init__(self):
        self.values = {}
        self.max_key_len = 0
        self.first_chars = frozenset()

    @classmethod
    def load(cls, path):
        obj = cls()
        first_chars = set()
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line or line.startswith("#"):
                    continue
                key, sep, rest = line.partition("\t")
                if not sep:
                    continue
                candidates = rest.split()
                if not key or not candidates:
                    continue
                merged = obj.values.get(key)
                if merged is None:
                    obj.values[key] = candidates
                    if len(key) > obj.max_key_len:
                        obj.max_key_len = len(key)
                    first_chars.add(key[0])
                else:
                    # 同键多行时按出现顺序合并去重，默认值仍取首见者
                    for cand in candidates:
                        if cand not in merged:
                            merged.append(cand)
        obj.first_chars = frozenset(first_chars)
        return obj

    def longest_prefix(self, text, start):
        """返回 text[start:] 开头的最长键；无则 None（键长降序试探）。"""
        for length in range(min(self.max_key_len, len(text) - start), 0, -1):
            cand = text[start:start + length]
            if cand in self.values:
                return cand
        return None


class _DictGroup:
    """conversion_chain 中的一个词典组：按 json 声明顺序（优先级）排列。"""

    def __init__(self, dicts):
        self.dicts = dicts

    def lookup(self, key):
        """返回第一个含该键的词典的候选值列表；无命中返回 None。"""
        for d in self.dicts:
            values = d.values.get(key)
            if values is not None:
                return values
        return None

    def match_at(self, text, start):
        """复现 OpenCC 组内短路匹配（PrefixMatch::MatchPrefixShortCircuit）。

        按词典优先级取第一个存在前缀匹配的词典，用该词典自身的最长键。
        返回 (键, 候选值列表) 或 (None, None)。
        """
        ch = text[start]
        for d in self.dicts:
            if ch in d.first_chars:
                key = d.longest_prefix(text, start)
                if key is not None:
                    return key, d.values[key]
        return None, None


class _ConversionConfig:
    def __init__(self, name):
        self.name = name
        self.normalization = []   # [_TextDict]，依次应用（单值归一）
        self.groups = []          # [_DictGroup]，依次应用
        self.segmentation = None  # {"dict": 路径, "user_dict": 路径或 None}
        self.one_to_one = True    # 归一是否逐字符 1:1（偏移可与原文互通）


# ---------------------------------------------------------------------------
# 数据目录定位与配置加载
# ---------------------------------------------------------------------------

def _program_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _candidate_data_dirs():
    """按优先级返回候选数据目录（配置 json 与 txt 词典所在处）。

    发行版安装布局（README）：Windows 把 t2gov/* 复制到 opencc 包的
    clib/share/opencc/；Linux 把 txt 复制到 opencc_data 包的 data/、json 复制
    到 data/config/。源码运行时直接使用仓库的 t2gov/ 目录。
    """
    dirs = []
    try:
        import opencc as _opencc_mod
        dirs.append(os.path.join(os.path.dirname(os.path.abspath(_opencc_mod.__file__)),
                                 "clib", "share", "opencc"))
    except Exception:
        pass
    try:
        import opencc_data as _opencc_data
        base = os.path.dirname(os.path.abspath(_opencc_data.__file__))
        dirs.append(os.path.join(base, "data"))
        dirs.append(os.path.join(base, "data", "config"))
    except Exception:
        pass
    here = _program_dir()
    dirs.append(os.path.join(here, "t2gov"))
    dirs.append(os.path.normpath(os.path.join(here, os.pardir, "t2gov")))
    return [d for d in dirs if os.path.isdir(d)]


def _resolve_ref(name, bases):
    """在候选基准目录中解析 json 引用的相对文件路径。"""
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    for base in bases:
        cand = os.path.join(base, *name.split("/"))
        if os.path.exists(cand):
            return cand
    return None


def _resolve_jieba_ref(name, json_dir):
    """解析 jieba 分词词典路径（安装布局或源码仓库布局）。"""
    if not name:
        return None
    here = _program_dir()
    path = _resolve_ref(name, [json_dir, os.path.dirname(json_dir), here])
    if path is None:
        # 源码仓库布局：分词词典直接位于仓库根的 jieba/ 目录
        cand = os.path.normpath(
            os.path.join(here, os.pardir, "jieba", os.path.basename(name)))
        if os.path.exists(cand):
            return cand
    return path


def _load_dict_from_spec(spec, bases):
    """按 json 词典描述加载，返回 _TextDict 或其列表（group 展开）。

    本引擎只解析 txt 词典（发行版随附 txt 版词典与配置）。若引用的是 ocd2
    二进制词典（如 OpenCC 包自带的配置），先尝试改用同名词的 txt 词典；
    找不到则报错，由上层退化为“只转换、不标注歧义”。
    """
    kind = spec.get("type")
    file_name = spec.get("file", "")
    if kind in ("text", "ocd", "ocd2"):
        path = None
        if kind == "text":
            path = _resolve_ref(file_name, bases)
        elif file_name:
            stem = file_name.rsplit(".", 1)[0]
            for ext in (".txt",):
                path = _resolve_ref(stem + ext, bases)
                if path is not None:
                    break
        if path is None:
            raise AmbiguityEngineError(
                "词典 %s 为 %s 格式，本功能的歧义标注需要 txt 词典"
                "（请按 README 安装 t2gov 的 txt 词典）" % (file_name or "?", kind))
        return _TextDict.load(path)
    if kind == "group":
        dicts = []
        for sub in spec.get("dicts", []):
            loaded = _load_dict_from_spec(sub, bases)
            if isinstance(loaded, list):
                dicts.extend(loaded)
            else:
                dicts.append(loaded)
        return dicts
    raise AmbiguityEngineError("不支持的词典类型：%s" % kind)


def _as_list(x):
    return x if isinstance(x, list) else [x]


_CONFIG_CACHE = {}


def _load_config(name):
    cfg = _CONFIG_CACHE.get(name)
    if cfg is not None:
        return cfg

    json_path = None
    for d in _candidate_data_dirs():
        cand = os.path.join(d, name + ".json")
        if os.path.exists(cand):
            json_path = cand
            break
    if json_path is None:
        raise AmbiguityEngineError(
            "找不到转换配置 %s.json，请先按 README 把 t2gov 词典安装到 OpenCC 数据目录" % name)

    json_dir = os.path.dirname(json_path)
    # Linux opencc-py 布局：json 位于 data/config/ 而 txt 位于 data/，
    # 故 json 所在目录及其上级目录都作为文件引用的查找基准
    bases = [json_dir, os.path.dirname(json_dir)]
    with open(json_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    cfg = _ConversionConfig(name)
    for item in spec.get("normalization", []):
        cfg.normalization.extend(_as_list(_load_dict_from_spec(item.get("dict", {}), bases)))

    seg = spec.get("segmentation")
    if seg and seg.get("type") == "jieba":
        res = seg.get("resources", {})
        dict_path = _resolve_jieba_ref(res.get("dict_path", ""), json_dir)
        if dict_path is not None:
            cfg.segmentation = {
                "dict": dict_path,
                "user_dict": _resolve_jieba_ref(res.get("user_dict_path", ""), json_dir),
            }

    for entry in spec.get("conversion_chain", []):
        cfg.groups.append(_DictGroup(_as_list(_load_dict_from_spec(entry.get("dict", {}), bases))))
    if not cfg.groups:
        raise AmbiguityEngineError("配置 %s.json 中没有 conversion_chain" % name)

    for td in cfg.normalization:
        for key, values in td.values.items():
            if len(key) != 1 or len(values[0]) != 1:
                cfg.one_to_one = False

    _CONFIG_CACHE[name] = cfg
    return cfg


# ---------------------------------------------------------------------------
# 切分与转换
# ---------------------------------------------------------------------------

def _is_cjk(ch):
    cp = ord(ch)
    return (0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF
            or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x3FFFF)


def _apply_normalization(cfg, text):
    """应用链前置的单值归一（兼容表意字 -> 统一表意字）。"""
    for td in cfg.normalization:
        table = {k: v[0] for k, v in td.values.items() if len(k) == 1 and len(v[0]) == 1}
        if table:
            text = "".join(table.get(ch, ch) for ch in text)
    return text


_JIEBA_TOKENIZERS = {}


def _get_jieba_tokenizer(seg):
    """按配置构造独立的 jieba 分词器（不污染全局 jieba 状态）。"""
    if not seg or not seg.get("dict"):
        return None
    try:
        import jieba
    except ImportError:
        return None
    key = (seg["dict"], seg.get("user_dict"))
    tok = _JIEBA_TOKENIZERS.get(key)
    if tok is None:
        tok = jieba.Tokenizer()
        tok.initialize(dictionary=seg["dict"])
        if seg.get("user_dict"):
            try:
                tok.load_userdict(seg["user_dict"])
            except Exception:
                pass
        _JIEBA_TOKENIZERS[key] = tok
    return tok


def _convert_run(text, group, cancel_check=None):
    """复现 OpenCC Conversion::AppendConverted 的逐位置最长键匹配。

    每个位置取“组内按词典优先级短路后的最长键”：命中则输出该键默认值（条
    目首值）并跳过整个键，未命中则单字原样输出。产出
    [(start, end, 输出片段, values_or_None)]，偏移为 code point。
    """
    tokens = []
    i, n = 0, len(text)
    tick = 0
    while i < n:
        key, values = group.match_at(text, i)
        if key is not None:
            tokens.append((i, i + len(key), values[0], values))
            i += len(key)
        else:
            tokens.append((i, i + 1, text[i], None))
            i += 1
        tick += 1
        if cancel_check is not None and tick % 2048 == 0 and cancel_check():
            raise ConversionCancelled()
    return tokens


def _convert_jieba(text, group, tokenizer, cancel_check):
    """结巴分词模式：先按 jieba 切词，词内仍走逐位置最长键匹配。

    OpenCC 的 jieba 配置（SingleStageConverter）即如此：分词得到词段后，
    每个词段经主链 Conversion::AppendConverted 转换——词内无整词键时仍会
    匹配词内的短键/单字，而不是整词直通。
    """
    tokens = []
    pos = 0
    for k, word in enumerate(tokenizer.lcut(text, HMM=True)):
        for s, e, out, values in _convert_run(word, group):
            tokens.append((pos + s, pos + e, out, values))
        pos += len(word)
        if cancel_check is not None and k % 512 == 0 and cancel_check():
            raise ConversionCancelled()
    if pos != len(text):
        # 理论不应发生：jieba 切分结果与原文不等长时退回整段匹配
        tokens = _convert_run(text, group, cancel_check)
    return tokens


_OPENCC_CACHE = {}


def _get_opencc(name):
    cc = _OPENCC_CACHE.get(name)
    if cc is None:
        cc = OpenCC(name)
        _OPENCC_CACHE[name] = cc
    return cc


def convert_with_ambiguities(text, config_name, cancel_check=None):
    """转换 text 并检测其中的一对多歧义。

    返回 dict：
      output   —— OpenCC 实际转换结果（与“文件转换”逐字一致）
      spans    —— 歧义跨度列表（基于 output 的 code point 偏移）：
                  {"start", "length", "source"(原词), "candidates"(全部候选值)}
      skipped  —— 退化路径中因与实际转换无法对齐而被跳过的歧义词数

    词典数据不可用（AmbiguityEngineError）时由上层改调 plain_convert，
    仍可正常转换，只是不标注歧义。
    """
    cfg = _load_config(config_name)
    cc = _get_opencc(config_name)
    output = cc.convert(text)

    norm = _apply_normalization(cfg, text)
    tokenizer = _get_jieba_tokenizer(cfg.segmentation)

    # normalization 先行（ConfigBasedConverter：先归一、后主链），链上各组依
    # 次应用（ConversionChain）；jieba 模式先分词、词内逐位匹配。本方案各配
    # 置主链均为单组；多组链在 jieba 模式下 OpenCC 按词段逐级级联（词界两侧
    # 不跨词匹配），与此处整段级联在多组+jieba 同时出现时才可能不等价。
    current = norm
    stage_input = norm
    out_tokens = []   # [(start, end, 输出片段, values_or_None)]，偏移基于 stage_input
    for group in cfg.groups:
        stage_input = current
        if tokenizer is not None:
            out_tokens = _convert_jieba(stage_input, group, tokenizer, cancel_check)
        else:
            out_tokens = _convert_run(stage_input, group, cancel_check)
        current = "".join(out for _, _, out, _ in out_tokens)

    spans = []
    skipped = 0

    def _unique(values):
        # 去掉字典中同值重复的候选（如“於邑 於邑”这类钉住不改的条目）
        return list(dict.fromkeys(values))

    if current == output:
        # 快速路径：自拼输出与 OpenCC 实际输出完全一致，跨度天然精确
        pos = 0
        for s, e, out, values in out_tokens:
            if values:
                uniq = _unique(values)
                if len(uniq) > 1:
                    spans.append({"start": pos, "length": len(out),
                                  "source": stage_input[s:e],
                                  "candidates": uniq})
            pos += len(out)
    elif cfg.one_to_one:
        # 退化路径：逐歧义词做前缀转换校验，只保留与实际输出对齐的跨度
        prefix_cache = {}

        def _prefix_conv(up_to):
            got = prefix_cache.get(up_to)
            if got is None:
                got = cc.convert(text[:up_to])
                prefix_cache[up_to] = got
            return got

        for s, e, out, values in out_tokens:
            if not values or len(_unique(values)) <= 1:
                continue
            before = _prefix_conv(s)
            after = _prefix_conv(e)
            if after == before + out:
                spans.append({"start": len(before), "length": len(out),
                              "source": stage_input[s:e],
                              "candidates": _unique(values)})
            else:
                skipped += 1

    return {"output": output, "spans": spans, "skipped": skipped}


def plain_convert(text, config_name):
    """不标注歧义的纯转换（词典数据不可用时的降级路径）。"""
    return _get_opencc(config_name).convert(text)


# ---------------------------------------------------------------------------
# 界面组件：一对多歧义标注输出框与后台转换线程
# ---------------------------------------------------------------------------

def utf16_positions(text):
    """返回数组 p，p[i] 为 text 前 i 个字符（code point 计）的 UTF-16 码元长度。

    QTextDocument 的光标位置按 UTF-16 码元计，而转换结果的跨度索引按 code
    point 计（扩展平面字符每字 2 个码元），两套索引必须换算。
    """
    positions = [0] * (len(text) + 1)
    u16 = 0
    for i, ch in enumerate(text):
        positions[i] = u16
        u16 += 2 if ord(ch) > 0xFFFF else 1
    positions[len(text)] = u16
    return positions


class TextConversionWorker(QThread):
    """
    文字转换工作线程（带一对多歧义标注），避免转换时阻塞 UI
    """
    conversion_done = Signal(dict)  # 完成信号（转换结果：output/spans/skipped/notice）
    conversion_failed = Signal(str)  # 失败信号（错误信息）

    def __init__(self, text, config):
        super().__init__()
        self.text = text
        self.config = config
        self._cancelled = False

    def cancel(self):
        """取消转换"""
        self._cancelled = True

    def run(self):
        try:
            result = convert_with_ambiguities(
                self.text, self.config,
                cancel_check=lambda: self._cancelled)
            self.conversion_done.emit(result)
        except ConversionCancelled:
            self.conversion_failed.emit("转换已取消")
        except AmbiguityEngineError as e:
            # 词典数据不可用（如仅安装了 ocd2 词典）：退化为只转换、不标注歧义
            try:
                self.conversion_done.emit({
                    "output": plain_convert(self.text, self.config),
                    "spans": [],
                    "skipped": 0,
                    "notice": "已转换，但未能标注一对多歧义：%s" % e,
                })
            except Exception as e2:
                self.conversion_failed.emit("转换失败：%s" % e2)
        except Exception as e:
            if "jieba" in str(e).lower():
                self.conversion_failed.emit(
                    "转换失败：OpenCC 结巴分词插件未安装（Windows 需按 README 安装 opencc-jieba 插件）")
            else:
                self.conversion_failed.emit("转换失败：%s" % e)


class AmbiguityOutputEdit(QTextEdit):
    """
    转换结果输出框：一对多歧义跨度自绘红色波浪线（比内置下划线更粗），
    点击波浪线弹出候选字菜单
    """
    ambiguity_clicked = Signal(int, object)  # (歧义跨度序号, 弹出菜单的全局坐标 QPoint)

    WAVE_COLOR = QColor("#e11d48")
    WAVE_PEN_WIDTH = 2.0  # 波浪线粗细（内置下划线无法调粗，故自绘）
    WAVE_AMPLITUDE = 2.0  # 波幅（像素）
    WAVE_HALF_WAVE = 4.0  # 半个波浪的长度（像素）

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self._spans = []  # [{"start","length","source","candidates","resolved"}]（code point 索引）
        self._mark_ambiguities = False
        self._u16_map = [0]  # code point 位置 -> UTF-16 码元位置

    def display_result(self, text, spans, mark_ambiguities):
        """显示转换结果；mark_ambiguities 为真时给歧义跨度加红色波浪线"""
        self._spans = [dict(span, resolved=False) for span in spans]
        self._mark_ambiguities = mark_ambiguities
        self.setPlainText(text)
        self._rebuild_u16_map()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        self.viewport().update()

    def clear_result(self):
        """清空结果与标注"""
        self._spans = []
        self._mark_ambiguities = False
        self.clear()
        self._u16_map = [0]

    def set_mark_ambiguities(self, mark):
        """仅切换是否绘制波浪线（如“显示候选字”开关变化时）"""
        self._mark_ambiguities = mark
        self.viewport().update()

    def _rebuild_u16_map(self):
        """文本变化后重建 code point -> UTF-16 位置映射"""
        self._u16_map = utf16_positions(self.toPlainText())

    def _u16(self, cp_pos):
        """code point 位置 -> QTextCursor 位置（UTF-16 码元）"""
        return self._u16_map[cp_pos]

    def _cp(self, u16_pos):
        """QTextCursor 位置（UTF-16 码元）-> code point 位置"""
        return bisect.bisect_right(self._u16_map, u16_pos) - 1

    def spans(self):
        return self._spans

    def span_text(self, span_idx):
        span = self._spans[span_idx]
        return self.toPlainText()[span["start"]:span["start"] + span["length"]]

    def _span_at(self, pos):
        """返回坐标 pos 处的歧义跨度序号；不在任何跨度内返回 None"""
        position = self._cp(self.cursorForPosition(pos).position())
        for i, span in enumerate(self._spans):
            if span["start"] <= position < span["start"] + span["length"]:
                return i
        # 点击落在跨度最后一个字符的右边界时 position 恰为 end，回退一位再试
        if position > 0:
            position -= 1
            for i, span in enumerate(self._spans):
                if span["start"] <= position < span["start"] + span["length"]:
                    return i
        return None

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and event.modifiers() == Qt.NoModifier
                and not self.textCursor().hasSelection()):
            span_idx = self._span_at(event.position().toPoint())
            if span_idx is not None and not self._spans[span_idx]["resolved"]:
                self.ambiguity_clicked.emit(
                    span_idx, event.globalPosition().toPoint())
                return
        super().mouseReleaseEvent(event)

    def apply_candidate(self, span_idx, candidate):
        """用选中的候选值替换对应歧义跨度的文本，并调整后续跨度偏移"""
        span = self._spans[span_idx]
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._u16(span["start"]))
        cursor.setPosition(self._u16(span["start"] + span["length"]), QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(candidate, QTextCharFormat())
        delta = len(candidate) - span["length"]
        span["length"] = len(candidate)
        span["resolved"] = True
        for other in self._spans[span_idx + 1:]:
            other["start"] += delta
        self._rebuild_u16_map()
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._mark_ambiguities and self._spans):
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.WAVE_COLOR, self.WAVE_PEN_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        text = self.toPlainText()
        for span in self._spans:
            if span["resolved"]:
                continue
            self._draw_span_wave(painter, span, text)
        painter.end()

    def _draw_span_wave(self, painter, span, text):
        """在跨度下方绘制波浪线；逐字符取位置，跨行（自动换行）时分行绘制"""
        viewport = self.viewport()
        visible_top, visible_bottom = -40, viewport.height() + 40
        metrics = QFontMetricsF(self.font())
        run_x1 = run_x2 = run_y = None
        for pos in range(span["start"], span["start"] + span["length"]):
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._u16(pos))
            rect = self.cursorRect(cursor)
            if run_y is not None and (rect.bottom() < visible_top or rect.top() > visible_bottom):
                break
            char_width = metrics.horizontalAdvance(text[pos]) if pos < len(text) else rect.height()
            if run_y is not None and abs(rect.bottom() - run_y) <= 1:
                # 同一行，延伸当前波浪段
                run_x2 = rect.x() + char_width
            else:
                # 新的一行（或首个字符）：先画完上一段
                if run_y is not None:
                    self._paint_wave_segment(painter, run_x1, run_x2, run_y)
                run_x1, run_x2 = rect.x(), rect.x() + char_width
                run_y = rect.bottom() - 2
        if run_y is not None:
            self._paint_wave_segment(painter, run_x1, run_x2, run_y)

    def _paint_wave_segment(self, painter, x1, x2, y):
        """绘制 [x1, x2] 区间、基线为 y 的一段波浪线"""
        if x2 <= x1:
            return
        path = QPainterPath()
        path.moveTo(x1, y)
        x, up = x1, True
        while x < x2:
            next_x = min(x + self.WAVE_HALF_WAVE, x2)
            ctrl_y = y - self.WAVE_AMPLITUDE if up else y + self.WAVE_AMPLITUDE
            path.quadTo((x + next_x) / 2, ctrl_y, next_x, y)
            x, up = next_x, not up
        painter.drawPath(path)


# ---------------------------------------------------------------------------
# 自检：python text_tab.py
# ---------------------------------------------------------------------------

def _find_prefix_trap(cfg):
    """找一个“长键的前两字不是键、但逐字可转换”的词形，作回归用例。

    正确算法（逐位置最长键匹配）会把这两个字按单字键转换；错误的“前缀延
    伸后整段查键”会把它们整体直通——两者输出不同，可检验实现与 OpenCC
    （src/Conversion.cpp）的一致性。
    """
    group = cfg.groups[-1]
    all_keys = set()
    for d in group.dicts:
        all_keys.update(d.values)
    for key in all_keys:
        if len(key) >= 3:
            p = key[:2]
            if p in all_keys or not all(_is_cjk(c) for c in p):
                continue
            converted = ""
            for i in range(2):
                k, values = group.match_at(p, i)
                converted += values[0] if values else p[i]
            if converted != p:
                return p
    return None


def _self_test():
    samples = [
        ("s2t", "头发干了。干活。干干净净。万一半夜发行新版，复制于后台。"),
        ("s2t_jieba", "头发干了。干活。干干净净。万一半夜发行新版，复制于后台。"),
        ("t2gov", None),
        ("t2s", "頭髮乾了。乾乾淨淨。萬一夜裏發行新版。"),
    ]
    ok = True
    for name, fixed in samples:
        try:
            _get_opencc(name)
        except (RuntimeError, Exception) as e:
            if "jieba" in str(e).lower():
                # Windows 的 opencc wheel 需另行安装 opencc-jieba 插件（见 README），
                # 缺插件时 C 库无法构造 _jieba 配置，跳过该项自检
                print("[%s] 跳过：OpenCC 结巴分词插件未安装（%s）" % (name, e))
                continue
            raise
        cfg = _load_config(name)
        text = fixed
        if text is None:
            # 从词典中取几个真实的一对多键拼样例，确保样例一定含歧义
            amb_keys = []
            for d in cfg.groups[-1].dicts:
                for key, values in d.values.items():
                    if len(values) > 1 and 1 <= len(key) <= 3 and all(_is_cjk(c) for c in key):
                        amb_keys.append(key)
                        if len(amb_keys) >= 3:
                            break
                if len(amb_keys) >= 3:
                    break
            text = "今日，" + "，".join(amb_keys) + "，完畢。"
        trap = _find_prefix_trap(cfg)
        if trap:
            text = text + trap + "。"
            print("[%s] 前缀陷阱用例: %s" % (name, trap))
        result = convert_with_ambiguities(text, name)
        direct = _get_opencc(name).convert(text)
        matched = result["output"] == direct
        ok = ok and matched
        print("[%s] 输出与 OpenCC 一致: %s" % (name, "是" if matched else "否（走退化路径）"))
        print("    输入: %s" % text)
        print("    输出: %s" % result["output"])
        for sp in result["spans"]:
            cur = result["output"][sp["start"]:sp["start"] + sp["length"]]
            print("    歧义: %s -> %s（当前 %s）@[%d:+%d]" % (
                sp["source"], "/".join(sp["candidates"]), cur, sp["start"], sp["length"]))
            if cur not in sp["candidates"]:
                ok = False
                print("    !! 跨度当前值不在候选列表中")
        if result["skipped"]:
            print("    （%d 处歧义因与实际转换无法对齐已跳过）" % result["skipped"])
        if not result["spans"]:
            ok = False
            print("    !! 未检出任何歧义")
    print("自检结果: %s" % ("通过" if ok else "存在问题"))


if __name__ == "__main__":
    _self_test()
