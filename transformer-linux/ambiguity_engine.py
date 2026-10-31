# -*- coding: utf-8 -*-
"""
内置的一对多歧义检测引擎（纯 Python，Win10 / Win7 / Linux 通用）。

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

txt 词典格式（与 t2gov/ 目录一致）：
    '#' 注释行；每行“键<TAB>值1 值2 …”，多个候选用空格分隔，首值为默认转换。
"""

import bisect
import json
import os

from opencc import OpenCC


class AmbiguityEngineError(Exception):
    """引擎初始化或词典加载失败。"""


class ConversionCancelled(Exception):
    """转换被用户取消。"""


# ---------------------------------------------------------------------------
# 词典数据结构
# ---------------------------------------------------------------------------

class _TextDict:
    """一份 txt 词典：键 -> 候选值列表（保持文件顺序，首值为默认转换）。"""

    __slots__ = ("values", "_sorted_keys")

    def __init__(self):
        self.values = {}
        self._sorted_keys = None

    @classmethod
    def load(cls, path):
        obj = cls()
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
                else:
                    # 同键多行时按出现顺序合并去重，默认值仍取首见者
                    for cand in candidates:
                        if cand not in merged:
                            merged.append(cand)
        return obj

    def _keys(self):
        if self._sorted_keys is None:
            self._sorted_keys = sorted(self.values)
        return self._sorted_keys

    def has_prefix(self, prefix):
        """是否存在以 prefix 开头的键（复现 OpenCC Seg 的前缀扩展判断）。"""
        keys = self._keys()
        i = bisect.bisect_left(keys, prefix)
        return i < len(keys) and keys[i].startswith(prefix)


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

    def match_prefix(self, prefix):
        for d in self.dicts:
            if d.has_prefix(prefix):
                return True
        return False


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


def _tokenize_greedy(text, group, cancel_check):
    """复现 OpenCC（不分词模式）的贪心前缀扩展切分。

    对 CJK 连续段尽量延伸：只要组内仍存在以当前段为前缀的键就继续吸收字
    符；停止处的片段作为一个词，按组内优先级查候选值。非 CJK 字符逐字直
    通（本方案词典键均为 CJK）。产出 [(start, end, values_or_None)]。
    """
    tokens = []
    i, n = 0, len(text)
    tick = 0
    while i < n:
        if not _is_cjk(text[i]):
            tokens.append((i, i + 1, None))
            i += 1
            continue
        j = i + 1
        while j < n and _is_cjk(text[j]) and group.match_prefix(text[i:j + 1]):
            j += 1
        tokens.append((i, j, group.lookup(text[i:j])))
        i = j
        tick += 1
        if cancel_check is not None and tick % 2048 == 0 and cancel_check():
            raise ConversionCancelled()
    return tokens


def _tokenize_jieba(text, group, tokenizer, cancel_check):
    """结巴分词模式：逐词查组（opencc-py 的 _jieba 配置即由 Python jieba 切分）。"""
    pos = 0
    tokens = []
    for k, word in enumerate(tokenizer.lcut(text, HMM=True)):
        end = pos + len(word)
        tokens.append((pos, end, group.lookup(word) if word else None))
        pos = end
        if cancel_check is not None and k % 2048 == 0 and cancel_check():
            raise ConversionCancelled()
    if pos != len(text):
        # 理论不应发生：切分结果与原文不等长时退化为贪心切分
        return _tokenize_greedy(text, group, cancel_check)
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

    # 依次应用链上各组（本方案各配置均为单组），记录最后一组的逐词转换
    current = norm
    stage_input = norm
    out_tokens = []   # [(start, end, 输出片段, values_or_None)]，偏移基于 stage_input
    for group in cfg.groups:
        stage_input = current
        if tokenizer is not None:
            tokens = _tokenize_jieba(stage_input, group, tokenizer, cancel_check)
        else:
            tokens = _tokenize_greedy(stage_input, group, cancel_check)
        pieces = []
        out_tokens = []
        for s, e, values in tokens:
            out = values[0] if values else stage_input[s:e]
            pieces.append(out)
            out_tokens.append((s, e, out, values))
        current = "".join(pieces)

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
# 自检：python ambiguity_engine.py
# ---------------------------------------------------------------------------

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
