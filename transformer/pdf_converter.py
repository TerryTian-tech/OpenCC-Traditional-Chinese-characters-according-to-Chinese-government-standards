import io
import os
import re
from typing import Callable, List, Optional, Tuple, Union

from opencc import OpenCC
from pdf_oxide import DocumentBuilder, EmbeddedFont, PdfDocument
from PIL import Image, ImageFont

# ---------------------------------------------------------------------------
# 输出 PDF 使用的内嵌字体查找（按风格类别组织，尽量保留原文的字体区分度）
# ---------------------------------------------------------------------------

# 黑体 / 无衬线
_CJK_SANS_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    # Linux：单 face 的 SC 版 otf 优先——CJK 合集 .ttc 的第 0 个 face 是
    # JP 变体，而 EmbeddedFont/PIL 都无法指定 face，会把部分汉字渲染成日式写法
    "/usr/share/fonts/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

_CJK_SANS_BOLD_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\Dengb.ttf",
    # Linux：SC 版 otf 优先（原因同上）
    "/usr/share/fonts/noto/NotoSansSC-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Bold.otf",
    "/usr/share/fonts/noto-cjk/NotoSansSC-Bold.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansSC-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
]

# 宋体 / 衬线
_CJK_SERIF_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\simsun.ttc",
    # Linux：SC 版 otf 优先（原因同黑体）
    "/usr/share/fonts/noto/NotoSerifSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifSC-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSerifSC-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSerifSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSerifCJK-Regular.ttc",
    # macOS
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]

_CJK_SERIF_BOLD_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\simsunb.ttf",
    # Linux：SC 版 otf 优先（原因同上）
    "/usr/share/fonts/noto/NotoSerifSC-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifSC-Bold.otf",
    "/usr/share/fonts/noto-cjk/NotoSerifSC-Bold.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSerifSC-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSerifCJK-Bold.ttc",
]

# 楷体
_CJK_KAI_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\STKAITI.TTF",
    # Linux
    "/usr/share/fonts/opentype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/arphic/ukai.ttc",
    # macOS
    "/System/Library/Fonts/Supplemental/STKaiti.ttf",
    "/System/Library/Fonts/Supplemental/Kaiti.ttc",
]

# 仿宋
_CJK_FANGSONG_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\simfang.ttf",
    r"C:\Windows\Fonts\STFANGSO.TTF",
    # macOS
    "/System/Library/Fonts/Supplemental/STFangsong.ttf",
]

# 拉丁字体（用于英文、数字等西文内容，避免中文字体的方块半角拉丁字形）
_LATIN_SANS_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_LATIN_SANS_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

_LATIN_SERIF_CANDIDATES = [
    r"C:\Windows\Fonts\times.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
]

_LATIN_SERIF_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\timesbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
]

# 方正字库的 PostScript 缩写名（FZ + 字形缩写 + 末位 K=GBK / J=简体）。
# 这类名称不含 song/kai 等完整词（如 FZSSK=书宋、FZKTK=楷体、FZFSK=仿宋），
# 下面的通用关键词匹配不到；方正书版导出的 PDF 普遍使用这批字体，
# 漏判会把全书（含宋体正文）落到兜底的黑体。
_FOUNDER_FONT_MAP = [
    ('fzkt', 'kai'),        # 方正楷体
    ('fzxk', 'kai'),        # 方正行楷
    ('fzfs', 'fangsong'),   # 方正仿宋
    ('fzss', 'serif'),      # 方正书宋
    ('fzxb', 'serif'),      # 方正小标宋
    ('fzdb', 'serif'),      # 方正大标宋
    ('fzht', 'sans'),       # 方正黑体
    # 方正书版内部字体“白正”（BZ=白正，白体正字，宋体系；书版常用它
    # 单独渲染标点符号，映射到宋体即可与原书标点风格一致）
    ('e-bz', 'serif'),
]

# 原文字体名 -> 风格类别的关键词（按序匹配，先匹配到者生效；
# 楷体/仿宋的关键词更特殊，需在宋体/黑体之前判断）
_FONT_CATEGORY_KEYWORDS = [
    ('kai', ('kai', '楷')),
    ('fangsong', ('fangsong', 'fang', '仿宋')),
    ('serif', ('song', 'sun', 'ming', 'mincho', 'serif', 'times', 'roman',
               'georgia', 'garamond', 'book', '宋')),
    ('sans', ('hei', 'yahei', 'deng', 'pingfang', 'hiragino', 'gothic',
              'sans', 'noto', 'sourcehan', 'wqy', 'zenhei', 'microhei', '黑', '雅黑', '等线')),
]


def _classify_font_category(font_name: Optional[str]) -> str:
    """
    根据原文 span 的字体名判断其风格类别（sans/serif/kai/fangsong）。
    PDF 内嵌字体名常带子集前缀（如 ABCDEF+SimSun），需先剥离。
    无法识别时返回 'sans'（与整体回退字体一致）。
    """
    name = (font_name or '').lower()
    if '+' in name:
        name = name.split('+', 1)[1]
    # 方正缩写名优先（FZSSK 等不含完整关键词，通用匹配会漏判成黑体）
    for prefix, category in _FOUNDER_FONT_MAP:
        if prefix in name:
            return category
    for category, keywords in _FONT_CATEGORY_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return category
    return 'sans'


def _find_first_loadable_font(candidates: List[str], log: Callable[[str], None]) -> Optional[str]:
    """在候选列表中找到第一个存在且可被 EmbeddedFont 加载的字体文件"""
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            EmbeddedFont.from_file(path)
            return path
        except Exception:
            log(f"警告：字体文件无法加载，已跳过 - {path}")
    return None


# ---------------------------------------------------------------------------
# 字体目录扫描（固定候选路径未命中时的冗余发现，覆盖各 Linux 发行版差异）
# ---------------------------------------------------------------------------

# 非常规字重/变形的文件名特征（匹配常规体时排除，避免把 Bold/Light 当常规体）
_DECORATION_EXCLUDES = ("bold", "black", "heavy", "medium", "light", "thin",
                        "italic", "oblique", "condensed", "narrow", "mono",
                        "semibold", "extrabold", "variable", "-vf", "-var")

# 匹配粗体时的排除特征（不能排除 "bold" 本身，只排除更重/更轻及变形字重）
_DECORATION_EXCLUDES_BOLD = ("black", "heavy", "semibold", "extrabold", "medium",
                             "light", "thin", "italic", "oblique", "condensed",
                             "narrow", "mono", "variable", "-vf", "-var")

# 匹配西文字体时排除其他文字体系的字体文件（如 Noto Sans CJK / Noto Sans Arabic）
_NONLATIN_EXCLUDES = ("cjk", "arabic", "hebrew", "thai", "devanagari", "hangul",
                      "sc-", "tc-", "jp-", "kr-", "hk-", "simsunb")

# 各逻辑字体的扫描文件名模式（小写 fnmatch，按优先级排列）
_FONT_SCAN_PATTERNS = {
    'sans': [
        "notosanscjk-sc-regular*", "notosanssc-regular*", "notosanssc-*",
        "notosanscjk-regular*", "sourcehansans-sc-regular*", "sourcehansanssc*regular*",
        "sourcehansans-regular*", "*wqy*microhei*", "*wqy*zenhei*", "droidsansfallback*",
        "*simhei*",
    ],
    'sans_bold': [
        "notosanscjk-sc-bold*", "notosanssc-bold*", "notosanscjk-bold*",
        "sourcehansans*bold*", "*wqy*microhei*",
    ],
    'serif': [
        "notoserifcjk-sc-regular*", "notoserifsc-regular*", "notoserifsc-*",
        "notoserifcjk-regular*", "sourcehanserif-sc-regular*", "sourcehanserif*regular*",
        "sourcehanserif-regular*", "uming*", "*simsun.ttc", "*simsun.ttf",
    ],
    'serif_bold': [
        "notoserifcjk-sc-bold*", "notoserifsc-bold*", "notoserifcjk-bold*",
        "sourcehanserif*bold*",
    ],
    'kai': [
        "ukai*", "*kaiti*", "stkaiti*", "dfkai*", "*simkai*",
    ],
    'fangsong': [
        "*fangsong*", "stfangsong*", "simpfang*", "*simfang*",
    ],
    'latin_sans': [
        "liberationsans-regular*", "liberationsans-*", "dejavusans.ttf", "dejavusans-*",
        "carlito-regular*", "carlito-*", "notosans-regular*", "notosans-*",
        "freesans-*", "arial*.ttf",
    ],
    'latin_sans_bold': [
        "liberationsans-bold*", "dejavusans-bold*", "carlito-bold*",
        "notosans-bold*", "arialbd*",
    ],
    'latin_serif': [
        "liberationserif-regular*", "liberationserif-*", "dejavuserif.ttf", "dejavuserif-*",
        "tinos-regular*", "notoserif-regular*", "notoserif-*", "freeserif-*",
        "times*.ttf",
    ],
    'latin_serif_bold': [
        "liberationserif-bold*", "dejavuserif-bold*", "tinos-bold*",
        "notoserif-bold*", "timesbd*",
    ],
}

_font_scan_cache: Optional[List[str]] = None


def _font_scan_roots() -> List[str]:
    """
    汇总各平台的标准字体目录（去重、仅保留存在的目录）：
    Linux 发行版包路径差异大，连同 XDG 用户目录一起列入；macOS 与
    Windows 的系统/用户字体目录也包含，作为固定候选之外的兜底。
    """
    roots = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        "/opt/homebrew/share/fonts",
        "/System/Library/Fonts",
        "/Library/Fonts",
    ]
    xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    roots.append(os.path.join(xdg_data_home, "fonts"))
    roots.append(os.path.expanduser("~/.fonts"))
    roots.append(os.path.expanduser("~/Library/Fonts"))
    for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(os.pathsep):
        if d:
            roots.append(os.path.join(d, "fonts"))

    win_root = os.environ.get("WINDIR") or r"C:\Windows"
    roots.append(os.path.join(win_root, "Fonts"))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        roots.append(os.path.join(local_appdata, "Microsoft", "Windows", "Fonts"))

    unique: List[str] = []
    seen = set()
    for root in roots:
        root = os.path.normpath(root)
        if root in seen:
            continue
        seen.add(root)
        if os.path.isdir(root):
            unique.append(root)
    return unique


def _iter_font_files() -> List[str]:
    """遍历字体目录中的 ttf/ttc/otf 文件（按路径排序，进程内缓存）"""
    global _font_scan_cache
    if _font_scan_cache is None:
        files: List[str] = []
        for root in _font_scan_roots():
            try:
                for dirpath, _dirnames, filenames in os.walk(root):
                    for filename in filenames:
                        if filename.lower().endswith((".ttf", ".ttc", ".otf")):
                            files.append(os.path.join(dirpath, filename))
            except OSError:
                continue  # 个别目录不可读时跳过
        files.sort()
        _font_scan_cache = files
    return _font_scan_cache


def _find_font_by_scan(key: str, log: Callable[[str], None]) -> Optional[str]:
    """
    按文件名模式在字体目录中查找可加载的字体。
    模式按优先级依次尝试；常规体模式会排除 Bold/Light 等变体，
    西文字体额外排除其他文字体系的字体文件。
    """
    import fnmatch

    patterns = _FONT_SCAN_PATTERNS[key]
    latin = key.startswith('latin')
    decorations = (_DECORATION_EXCLUDES_BOLD if key.endswith('_bold')
                   else _DECORATION_EXCLUDES)
    excludes = decorations + (_NONLATIN_EXCLUDES if latin else ())
    files = _iter_font_files()

    for pattern in patterns:
        for path in files:
            name = os.path.basename(path).lower()
            if any(token in name for token in excludes):
                continue
            if not fnmatch.fnmatch(name, pattern):
                continue
            try:
                EmbeddedFont.from_file(path)
                return path
            except Exception:
                continue
    return None


def _find_font(exact_candidates: List[str], scan_key: str,
               log: Callable[[str], None]) -> Optional[str]:
    """
    字体发现入口：先试固定候选路径（快、可预测），
    未命中再按文件名模式扫描标准字体目录（覆盖发行版差异）。
    """
    path = _find_first_loadable_font(exact_candidates, log)
    if path:
        return path
    return _find_font_by_scan(scan_key, log)


# ---------------------------------------------------------------------------
# 文本宽度测量（PIL，用于精确推进绘制位置和自适应缩放）
# ---------------------------------------------------------------------------

_font_measure_cache = {}


def _measure(text: str, font_path: str, size: float) -> Optional[float]:
    """
    用 PIL 测量文本以指定字体渲染时的 advance 宽度（PDF pt，1px@72dpi = 1pt）。
    测量失败（字体不支持）返回 None，调用方回退到不缩放。
    """
    if not text:
        return 0.0
    key = (font_path, round(size, 2))
    font = _font_measure_cache.get(key)
    if font is None:
        try:
            font = ImageFont.truetype(font_path, round(size, 2) or 1)
        except Exception:
            _font_measure_cache[key] = False  # 标记该字体不可测量
            return None
        _font_measure_cache[key] = font
    if font is False:
        return None
    try:
        return float(font.getlength(text))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 文本绘制（中英分字体 + 按脚本切分 + 宽度自适应）
# ---------------------------------------------------------------------------

# 输出文档中各逻辑字体的默认注册名（convert_pdf_file 中按可用字体实例化）
_DEFAULT_FONT_NAMES = {
    'sans': "CJK-Hei",
    'sans_bold': "CJK-Hei-Bold",
    'serif': "CJK-Song",
    'serif_bold': "CJK-Song-Bold",
    'kai': "CJK-Kai",
    'fangsong': "CJK-FangSong",
    'latin_sans': "Latin-Sans",
    'latin_sans_bold': "Latin-Sans-Bold",
    'latin_serif': "Latin-Serif",
    'latin_serif_bold': "Latin-Serif-Bold",
}


# 拉丁字母及其扩展区（含带变音符号的法/德/越文字符），Arial/Times 均可覆盖
# 注意：捕获组使 re.split 把拉丁片段保留在结果里，而非作为分隔符丢弃
_LATIN_RUN_RE = re.compile(r'([\x20-\x7e\u00a0-\u024f\u1e00-\u1eff]+)')

# 拉丁片段中真正的字母/数字：纯标点片段（中文句子里的半角逗号、括号等）
# 不算西文——源文档里它们是用中文字库渲染的（方正字库 ASCII 标点区即
# 中文样式字形），落到西文字体会明显变小，需跟随所在片段的中文字体。
_LATIN_ALNUM_RE = re.compile(r'[0-9A-Za-z\u00c0-\u024f\u1e00-\u1eff]')


def _split_by_script(text: str) -> List[Tuple[str, bool]]:
    """
    将文本切分为 (片段, 是否拉丁文) 序列，保持原有顺序。
    英文/数字及带变音符号的拉丁字符使用拉丁字体，其余（中文、全角标点、
    中文语境下的半角标点）使用中文字体。
    """
    runs = []
    for part in _LATIN_RUN_RE.split(text):
        if not part:
            continue
        is_latin = _LATIN_RUN_RE.fullmatch(part) is not None
        if is_latin and not _LATIN_ALNUM_RE.search(part):
            is_latin = False
        runs.append((part, is_latin))
    return runs


# ---------------------------------------------------------------------------
# 字符级文本聚合（避免提取器在字距较大时插入的“推断空格”破坏单词）
# ---------------------------------------------------------------------------

def _char_is_bold(ch) -> bool:
    """根据字符的字体粗细描述判断是否加粗"""
    weight = str(getattr(ch, 'font_weight', '') or '').lower()
    return ('bold' in weight) or ('heavy' in weight) or ('black' in weight)


def _char_runs_from_page(doc: PdfDocument, page_index: int,
                         log: Callable[[str], None]) -> Optional[List[dict]]:
    """
    用字符级数据（extract_chars）聚合出绘制单元，替代 extract_spans 的文本。

    extract_spans 生成的文本会在字符间隙较大时插入“推断空格”（原文中并不
    存在空格字符），直接重绘会把单词拆开（如 Work -> W ork）。改为从字符的
    精确坐标出发：同风格且间隙小于阈值的字符合并为一个单元，间隙大的各自
    按原坐标定位——既不引入多余空格，也保留原文版式（含两端对齐的拉伸）。

    返回 None 表示字符提取失败（调用方回退到 span 模式），否则返回单元列表：
    {text, x, y, w, font_name, size, color, bold}，坐标为 PDF 用户空间。
    """
    try:
        chars = doc.extract_chars(page_index)
    except Exception as e:
        log(f"  ⚠ 第{page_index + 1}页字符提取失败: {e}，改用片段模式重排")
        return None
    if not chars:
        return []

    # 自上而下、自左而右扫描
    chars = sorted(chars, key=lambda c: (-round(c.origin_y, 1), c.origin_x))

    runs: List[dict] = []
    rotated_skipped = 0
    cur = None
    tail = None  # 当前单元的最后一个字符

    for ch in chars:
        char = ch.char
        if not char:
            continue
        if getattr(ch, 'rotation_degrees', 0.0):
            rotated_skipped += 1
            continue

        size = ch.font_size if ch.font_size and ch.font_size > 0 else 10.0
        bold = _char_is_bold(ch)
        style = (ch.font_name, round(size, 1), tuple(ch.color or (0.0, 0.0, 0.0)), bold)

        merged = False
        if cur is not None:
            same_line = abs(ch.origin_y - tail.origin_y) <= max(size * 0.35, 1.5)
            gap = ch.origin_x - (tail.origin_x + (tail.advance_width or 0.0))
            # 西文相邻用较紧的阈值（真实空格约 0.25em），中西文及中文之间放宽，
            # 尽量让词语留在同一单元以保证词汇级转换的上下文
            latin_pair = (_LATIN_RUN_RE.fullmatch(tail.char or '') is not None
                          and _LATIN_RUN_RE.fullmatch(char) is not None)
            threshold = size * (0.20 if latin_pair else 0.30)
            if same_line and gap <= threshold and cur['style'] == style:
                cur['text'] += char
                cur['xs'].append(ch.origin_x)
                cur['w'] = (ch.origin_x + (ch.advance_width or 0.0)) - cur['x']
                tail = ch
                merged = True

        if not merged:
            cur = {
                'text': char,
                'x': ch.origin_x,
                'y': ch.origin_y,
                'w': ch.advance_width or 0.0,
                'font_name': ch.font_name,
                'size': size,
                'color': ch.color,
                'bold': bold,
                'style': style,
                'xs': [ch.origin_x],
            }
            runs.append(cur)
            tail = ch

    if rotated_skipped:
        log(f"  ⚠ 第{page_index + 1}页有 {rotated_skipped} 个旋转字符无法按水平文本重排，已跳过")

    for r in runs:
        r.pop('style', None)
    return runs


# ---------------------------------------------------------------------------
# OpenCC 批量转换（按页直拼整体转换，按位置索引切回）
# ---------------------------------------------------------------------------


def _convert_page_texts(cc, texts: List[str],
                        page_no: int, log: Callable[[str], None]) -> List[str]:
    """
    将一页的所有绘制单元文本直接拼成一段整体转换，再按位置索引切回各单元
    （移植自 doc_converter 的段落级上下文方案）。

    PDF 字符级聚合出的单元常在词中间被切断（字体切换、换行、大间距，如
    分属行尾行首的“重复”、分属宋体与 Times 的“电复”），逐单元转换会让
    歧义字失去词上下文（“复”→“復”而非“複”）。单元直拼后整页一次转换，
    跨单元的词也能正确消歧；拆回不依赖分隔符——单元边界就是拼接时的字符
    偏移，与转换内容无关：

    - 转换前后长度一致（繁简映射绝大多数 1:1）：按偏移切割，各单元原文
      与译文等长，可走逐字定位绘制路径，版式与原文一致；
    - 长度不一致（罕见，如 s2twp 的“内存”→“記憶體”）：用前缀转换定位
      各单元边界在译文中的落点后切割，长度变化的单元自动落入整段重排
      路径，其余单元仍走逐字定位。
    """
    if not texts:
        return []
    positions = []
    offset = 0
    for t in texts:
        positions.append((offset, offset + len(t)))
        offset += len(t)
    full = ''.join(texts)
    converted = cc.convert(full)
    if len(converted) == len(full):
        return [converted[s:e] for (s, e) in positions]
    log(f"  ⚠ 第{page_no}页转换后字符数变化（{len(full)}→{len(converted)}），按前缀转换定位单元边界")
    return _split_converted_by_prefix(cc, full, positions, converted)


def _split_converted_by_prefix(cc, full: str, positions: List[Tuple[int, int]],
                               converted: str) -> List[str]:
    """
    转换改变字符数时，用“前缀转换”确定各单元边界在译文中的落点：
    对每个单元结束位置 p，len(convert(full[:p])) 即该边界的译文位置——
    由 OpenCC 自身的分词决定，词内的字不会被错配到相邻单元（draw 时
    错配意味着字符画到别的单元位置上）。

    前缀长度理论上可能非单调（边界切在词中间、词典长短映射），用钳制
    保证边界单调递增，最坏退化为按邻近边界对齐——不丢字、不重复。
    （与 doc_converter._convert_paragraph_fallback 同一方案。）
    """
    bounds = [0]
    for _s, e in positions:
        q = len(cc.convert(full[:e]))
        bounds.append(min(max(q, bounds[-1]), len(converted)))
    return [converted[bounds[i]:bounds[i + 1]] for i in range(len(positions))]


# ---------------------------------------------------------------------------
# 文本绘制（中英分字体 + 按脚本切分 + 宽度自适应）
# ---------------------------------------------------------------------------

# 可独立压缩推进宽度的标点（含中文语境的半角标点与全角标点、全角空格）：
# 闭标点墨迹靠字身框左侧、开标点靠右侧，推进宽度压到半宽左右不会与相邻
# 文字重叠。破折号/省略号墨迹贯穿整格、源文档也按全角排版，不参与压缩。
_COMPRESSIBLE_PUNCT_RE = re.compile(
    r'([，。、；：？！“”‘’《》〈〉（）【】〔〕「」『』·,.;:!?()\u3000]+)')

# 全角开标点（墨迹靠字身框右侧）：推进宽度被压缩后若仍按原位绘制，墨迹
# 会压到后面的字，需左移使其墨迹右缘对齐单元右界——源文档“标点半角”
# 正是开标点紧贴后字的效果。
_OPEN_PUNCT_RE = re.compile(r'[（（《〈“‘【〔「『]')
_OPEN_PUNCT_INK_RIGHT = 0.9  # 开标点墨迹右缘约占字身框比例（宋体系实测 0.85~0.91）


def _draw_text(page_builder, text: str, x: float, y: float, run_w: float,
               font_name: Optional[str], size: float, color, is_bold: bool,
               fonts: dict, char_xs: Optional[List[float]] = None) -> bool:
    """
    按脚本分组绘制一段已转换的文本到指定位置。

    文本由 _convert_page_texts 按页批量转换后传入，本函数只负责排版绘制：

    - 按原文风格类别（黑体/宋体/楷体/仿宋）匹配输出字体，保留字体区分度
    - 西文/数字按原文风格选择衬线/非衬线拉丁字体；中文语境下的半角标点
      跟随中文字体（源文档里它们由中文字库渲染，落到西文字体会明显变小）
    - 优先逐字按原始坐标绘制（char_xs）：完整保留两端对齐的字距拉伸与
      方正书版“标点半角”版式——整段按字体自然宽度推进会让段内文字逐渐
      左漂，在段尾与后一单元之间留下空隙。char_xs 对应原文文本，长度与
      转换后文本一致时可用；OpenCC 短语转换可能改变字符数（如 s2twp 的
      “内存”→“記憶體”），此时回退到整段重排
    - 整段重排路径（span 回退、转换改变字符数）中，中文标点独立成段：
      输出中文字体的标点字身框是全宽的，而源文档标点推进宽度常只有一半，
      按自然宽度推进会触发缩字号、把标点明显变小。标点单独定位后只压缩
      其推进宽度（字形保持原字号），开标点再左移使墨迹贴住后字；标点压
      到半宽仍放不下时才回退为按比例缩小字号

    fonts 为 逻辑字体键 -> {'name': 注册名, 'path': 字体文件路径}。
    返回是否实际绘制了内容。
    """
    if not text:
        return False

    span_w = run_w
    size = size if size and size > 0 else 10.0
    color = color or (0.0, 0.0, 0.0)

    category = _classify_font_category(font_name)
    # 粗体仅在有同族粗体文件时生效，避免为保字重损失字体风格
    if is_bold and category in ('sans', 'serif'):
        key = category + '_bold'
    else:
        key = category
    cjk_entry = fonts.get(key) or fonts['sans']
    # 楷体/仿宋的西文部分按衬线处理（与宋体一致）
    if category in ('serif', 'kai', 'fangsong'):
        lkey = 'latin_serif_bold' if is_bold else 'latin_serif'
    else:
        lkey = 'latin_sans_bold' if is_bold else 'latin_sans'
    latin_entry = fonts.get(lkey) or fonts['sans']

    # --- 逐字定位路径：每个字落在原文坐标上 ---
    # char_xs 与原文等长；转换后长度一致（OpenCC 多数映射为 1:1）时可用
    if char_xs and len(char_xs) == len(text):
        n = len(char_xs)
        for i, (ch, cx) in enumerate(zip(text, char_xs)):
            draw_x = cx
            if _OPEN_PUNCT_RE.fullmatch(ch):
                # 开标点墨迹靠字身框右侧：源单元（到下一字的距离）比全宽
                # 窄时，左移使墨迹右缘对齐单元右界，贴住后面的字
                cell = (char_xs[i + 1] - cx if i + 1 < n
                        else span_w - (cx - char_xs[0]))
                natural = _measure(ch, cjk_entry['path'], size)
                if cell > 0 and natural and cell < natural:
                    draw_x = cx + cell - _OPEN_PUNCT_INK_RIGHT * size
            if (_LATIN_RUN_RE.fullmatch(ch) and _LATIN_ALNUM_RE.search(ch)):
                entry = latin_entry
            else:
                entry = cjk_entry
            page_builder.font(entry['name'], size).at(draw_x, y).inline_color(
                color[0], color[1], color[2], ch)
        return True

    # --- 整段重排路径 ---
    segments = []  # [text, 注册名, 字体路径, 是否可压缩标点]
    for part, is_latin in _split_by_script(converted):
        entry = latin_entry if is_latin else cjk_entry
        if is_latin:
            segments.append([part, entry['name'], entry['path'], False])
            continue
        for sub in _COMPRESSIBLE_PUNCT_RE.split(part):
            if not sub:
                continue
            punct = _COMPRESSIBLE_PUNCT_RE.fullmatch(sub) is not None
            segments.append([sub, entry['name'], entry['path'], punct])

    # 宽度自适应：测量失败则不做缩放
    widths = [_measure(seg[0], seg[2], size) for seg in segments]
    naturals = [None] * len(segments)
    if all(w is not None for w in widths):
        naturals = list(widths)
        total = sum(widths)
        if total > span_w + 0.5 and total > 0:
            punct_total = sum(w for w, seg in zip(widths, segments) if seg[3])
            deficit = total - span_w
            if punct_total > 0 and deficit <= punct_total * 0.55:
                # 只压缩标点推进宽度（不低于半宽），字形保持原字号
                ratio = max(1.0 - deficit / punct_total, 0.45)
                widths = [w * ratio if seg[3] else w
                          for w, seg in zip(widths, segments)]
            else:
                # 标点压到半宽仍放不下：先压标点，剩余缺口再缩字号
                scale_denom = total - punct_total * 0.5
                scale = max(span_w / scale_denom, 0.5) if scale_denom > 0 else 0.5
                size *= scale
                widths = [_measure(seg[0], seg[2], size) for seg in segments]
                widths = [w * 0.5 if seg[3] else w
                          for w, seg in zip(widths, segments)]

    for seg, width, natural in zip(segments, widths, naturals):
        draw_x = x
        if (seg[3] and width and natural and width < natural
                and _OPEN_PUNCT_RE.fullmatch(seg[0])):
            # 开标点：墨迹右缘对齐压缩后单元的右界（贴住后面的字）
            draw_x = x + width - _OPEN_PUNCT_INK_RIGHT * size
        page_builder.font(seg[1], size).at(draw_x, y).inline_color(
            color[0], color[1], color[2], seg[0])
        if width:
            x += width
    return True


def _draw_span_text(page_builder, text: str, span, fonts: dict,
                    ox: float, oy: float) -> bool:
    """按 span 绘制已转换的文本（字符级聚合失败时的回退路径）"""
    if not text or not text.strip():
        return False
    size = span.font_size if span.font_size and span.font_size > 0 else 10.0
    return _draw_text(page_builder, text,
                      span.bbox[0] - ox, span.bbox[1] - oy, span.bbox[2],
                      span.font_name, size, span.color, span.is_bold, fonts)


# ---------------------------------------------------------------------------
# 页面元素重建辅助
# ---------------------------------------------------------------------------

def _offset_point(x: float, y: float, ox: float, oy: float) -> Tuple[float, float]:
    """将用户空间坐标平移到以裁剪框左下角为原点的页面坐标系"""
    return x - ox, y - oy


def _draw_lines(page_builder, lines: list, ox: float, oy: float) -> None:
    """按矢量线条的操作序列重画线段（表格边框、下划线等）"""
    for line in lines:
        color = line.get('stroke_color') or (0.0, 0.0, 0.0)
        width = line.get('stroke_width') or 1.0
        cur = None
        for op in line.get('operations', []):
            name = op.get('op')
            if name == 'move_to':
                cur = (op.get('x', 0.0), op.get('y', 0.0))
            elif name == 'line_to' and cur is not None:
                end = (op.get('x', 0.0), op.get('y', 0.0))
                x1, y1 = _offset_point(cur[0], cur[1], ox, oy)
                x2, y2 = _offset_point(end[0], end[1], ox, oy)
                page_builder.stroke_line(x1, y1, x2, y2, width, color)
                cur = end


def _draw_rects(page_builder, rects: list, ox: float, oy: float) -> None:
    """重画矩形（底色块、边框等）；有填充色优先按填充绘制"""
    for rect in rects:
        x, y, w, h = rect.get('bbox', (0.0, 0.0, 0.0, 0.0))
        x, y = _offset_point(x, y, ox, oy)
        fill = rect.get('fill_color')
        if fill is not None:
            page_builder.filled_rect(x, y, w, h, fill[0], fill[1], fill[2])
        else:
            stroke = rect.get('stroke_color') or (0.0, 0.0, 0.0)
            width = rect.get('stroke_width') or 1.0
            page_builder.stroke_rect(x, y, w, h, width, stroke)


def _draw_images(page_builder, doc: PdfDocument, page_index: int,
                 ox: float, oy: float, log: Callable[[str], None]) -> int:
    """
    将页面上的图片按原位置嵌入新文档。
    extract_images 提供位置（bbox），extract_image_bytes 提供图像字节，
    两者按内容流顺序一一对应。
    """
    metas = doc.extract_images(page_index) or []
    blobs = doc.extract_image_bytes(page_index) or []
    if len(metas) != len(blobs):
        log(f"  ⚠ 第{page_index + 1}页图片位置与图像数据数量不一致"
            f"（{len(metas)}/{len(blobs)}），仅按序号对齐绘制")

    drawn = 0
    for meta, blob in zip(metas, blobs):
        bbox = meta.get('bbox')
        data = blob.get('data') if isinstance(blob, dict) else blob
        if not bbox or not data:
            continue
        x, y = _offset_point(bbox[0], bbox[1], ox, oy)
        page_builder.image_with_alt(data, x, y, bbox[2], bbox[3], "")
        drawn += 1
    return drawn


def _rasterize_page_png(doc: PdfDocument, page_index: int, dpi: int = 150) -> Optional[bytes]:
    """
    将整页栅格化为 PNG 字节（用于没有文本层的扫描页，保留原页面外观）。
    栅格化失败返回 None，调用方按空白页处理。
    """
    try:
        pm = doc.render_pixmap(page_index, dpi=dpi)
        im = Image.frombytes("RGBA", (pm.width, pm.height), pm.data)
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def convert_pdf_file(
    input_path: str,
    output_folder: str,
    conversion_type: str,
    log_callback: Optional[Callable[[str], None]] = None,
    is_cancelled_callback: Optional[Callable[[], bool]] = None
) -> Union[str, bool]:
    """
    转换 PDF 文件中的文本内容并输出新的 PDF 文件。

    由于 PDF 的文本与字体深度绑定（嵌入式子集字体无法容纳转换后的新字符），
    本模块采用“提取 + 按原版式重建”策略：
    - 按原文位置、字号、颜色重排转换后的文字
    - 保留页面尺寸、图片、矢量线条与矩形
    - 无文本层的扫描页按原图栅格化保留

    参数
    ----------
    input_path : str
        源 PDF 文件路径
    output_folder : str
        输出文件夹路径
    conversion_type : str
        OpenCC 转换类型配置名称，如 's2t', 't2s' 等
    log_callback : callable or None
        日志回调函数，接收字符串参数
    is_cancelled_callback : callable or None
        取消检查回调，返回 True 表示用户请求取消

    返回
    -------
    str or bool
        成功时返回输出文件路径，失败时返回 False
    """
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    # --- 参数校验 ---
    if not os.path.isfile(input_path):
        log(f"错误：文件不存在 - {input_path}")
        return False

    if input_path.lower().endswith('.pdf'):
        log(f"正在处理 PDF 文件: {os.path.basename(input_path)}")
    else:
        log(f"警告：文件后缀不是 .pdf，将尝试以 PDF 格式打开: {os.path.basename(input_path)}")

    # --- 创建输出目录 ---
    try:
        os.makedirs(output_folder, exist_ok=True)
    except OSError as e:
        log(f"错误：无法创建输出目录 - {e}")
        return False

    # --- 取消检查 ---
    if is_cancelled_callback and is_cancelled_callback():
        return False

    # --- 初始化 OpenCC ---
    try:
        cc = OpenCC(conversion_type)
    except Exception as e:
        log(f"错误：OpenCC 初始化失败 ({conversion_type}) - {e}")
        return False

    # --- 查找输出用字体（固定候选路径 -> 目录扫描，按风格类别，带回退链） ---
    sans = _find_font(_CJK_SANS_CANDIDATES, 'sans', log)
    if not sans:
        log("错误：系统中未找到可用的中文字体（SimHei/微软雅黑/Noto CJK/文泉驿等），无法生成中文 PDF")
        return False
    serif = _find_font(_CJK_SERIF_CANDIDATES, 'serif', log)
    kai = _find_font(_CJK_KAI_CANDIDATES, 'kai', log)
    fangsong = _find_font(_CJK_FANGSONG_CANDIDATES, 'fangsong', log)
    sans_bold = _find_font(_CJK_SANS_BOLD_CANDIDATES, 'sans_bold', log)
    serif_bold = _find_font(_CJK_SERIF_BOLD_CANDIDATES, 'serif_bold', log)
    latin_sans = _find_font(_LATIN_SANS_CANDIDATES, 'latin_sans', log)
    latin_sans_bold = _find_font(_LATIN_SANS_BOLD_CANDIDATES, 'latin_sans_bold', log)
    latin_serif = _find_font(_LATIN_SERIF_CANDIDATES, 'latin_serif', log)
    latin_serif_bold = _find_font(_LATIN_SERIF_BOLD_CANDIDATES, 'latin_serif_bold', log)

    # 类别缺失时回退：楷体/仿宋 -> 宋体 -> 黑体；粗体缺失用同族常规体
    serif = serif or sans
    font_paths = {
        'sans': sans,
        'sans_bold': sans_bold or sans,
        'serif': serif,
        'serif_bold': serif_bold or serif,
        'kai': kai or serif,
        'fangsong': fangsong or serif,
        'latin_sans': latin_sans or sans,
        'latin_sans_bold': latin_sans_bold or latin_sans or sans,
        'latin_serif': latin_serif or latin_sans or serif,
        'latin_serif_bold': latin_serif_bold or latin_serif or latin_sans_bold
                            or latin_sans or serif,
    }

    # 日志：展示各类别实际使用的字体，方便用户核对字体区分度
    _CATEGORY_LABELS = [('sans', '黑体'), ('serif', '宋体'), ('kai', '楷体'), ('fangsong', '仿宋')]
    parts = [f"{label}={os.path.basename(font_paths[key])}" for key, label in _CATEGORY_LABELS]
    log("输出中文字体（按原文风格匹配）: " + "，".join(parts)
        + "；未找到同类字体时按 楷体/仿宋→宋体→黑体 回退")
    if latin_sans:
        latin_desc = os.path.basename(latin_serif) if latin_serif else os.path.basename(latin_sans)
        log(f"输出西文字体: {latin_desc}")
    else:
        log("警告：系统中未找到拉丁字体（Arial/Times 等），西文将使用中文字体渲染，可能与原文观感有差异")

    # --- 打开 PDF ---
    try:
        doc = PdfDocument(input_path)
    except Exception as e:
        msg = str(e)
        log(f"错误：无法读取 PDF 文件 - {msg}")
        if 'password' in msg.lower() or 'encrypt' in msg.lower():
            log("提示：该文件已加密，本工具暂不支持带密码的 PDF")
        elif 'head' in msg.lower() or 'EOF' in msg or 'format' in msg.lower():
            log("提示：该文件可能不是有效的 PDF 文件")
        return False

    try:
        total_pages = int(doc.page_count)
        if total_pages <= 0:
            log("错误：PDF 中没有任何页面")
            return False

        # --- 预检文本层：全部为扫描页时无法转换 ---
        text_pages = sum(1 for i in range(total_pages) if doc.has_text_layer(i))
        if text_pages == 0:
            log("错误：该 PDF 没有可提取的文本层（可能是扫描或纯图片 PDF），无法进行文字转换")
            log("提示：如需转换扫描件，请先使用 OCR 工具识别文字后再尝试")
            return False
        if text_pages < total_pages:
            log(f"警告：{total_pages - text_pages}/{total_pages} 页没有文本层（扫描页），这些页面将按原样栅格化保留")

        # --- 初始化输出文档 ---
        builder = DocumentBuilder().title(f"convert_{os.path.splitext(os.path.basename(input_path))[0]}")

        # 实例化逻辑字体表；同一字体文件只注册一次，多个逻辑名共享注册名
        fonts = {}
        registered_by_path = {}
        for key, default_name in _DEFAULT_FONT_NAMES.items():
            path = font_paths[key]
            if path not in registered_by_path:
                try:
                    builder = builder.register_embedded_font(default_name, EmbeddedFont.from_file(path))
                    registered_by_path[path] = default_name
                except Exception as e:
                    log(f"警告：字体 {os.path.basename(path)} 注册失败（{e}），相关文字将使用黑体渲染")
                    fonts[key] = {'name': _DEFAULT_FONT_NAMES['sans'], 'path': font_paths['sans']}
                    continue
            fonts[key] = {'name': registered_by_path[path], 'path': path}

        converted_spans = 0
        warned_rotation = False

        for page_index in range(total_pages):
            if is_cancelled_callback and is_cancelled_callback():
                log("转换已被取消")
                return False

            log(f"  [{page_index + 1}/{total_pages}] 处理第 {page_index + 1} 页")

            # 裁剪框决定可见区域，输出页面以它为基准
            try:
                cx0, cy0, cx1, cy1 = doc.page_crop_box(page_index)
            except Exception:
                cx0, cy0, cx1, cy1 = doc.page_media_box(page_index)
            page_w, page_h = cx1 - cx0, cy1 - cy0
            if page_w <= 0 or page_h <= 0:
                mb = doc.page_media_box(page_index)
                cx0, cy0 = mb[0], mb[1]
                page_w, page_h = mb[2] - mb[0], mb[3] - mb[1]

            rotation = doc.page_rotation(page_index)
            if rotation and not warned_rotation:
                log(f"  ⚠ 第{page_index + 1}页设置了页面旋转（{rotation}°），该页输出方向可能与原文不同")
                warned_rotation = True

            page_builder = builder.page(page_w, page_h)

            # --- 无文本层的扫描页：整页栅格化保留 ---
            if not doc.has_text_layer(page_index):
                png = _rasterize_page_png(doc, page_index)
                if png:
                    page_builder.image_with_alt(png, 0.0, 0.0, page_w, page_h, "")
                else:
                    log(f"  ⚠ 第{page_index + 1}页栅格化失败，输出为空白页")
                builder = page_builder.done()
                continue

            # --- 图片（先画，位于文字下层） ---
            try:
                _draw_images(page_builder, doc, page_index, cx0, cy0, log)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页图片保留失败: {e}")

            # --- 矢量线条与矩形 ---
            try:
                _draw_lines(page_builder, doc.extract_lines(page_index) or [], cx0, cy0)
                _draw_rects(page_builder, doc.extract_rects(page_index) or [], cx0, cy0)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页矢量图形保留失败: {e}")

            # --- 文本：字符级聚合成绘制单元，按页直拼转换后按位置切回重排
            #     （直拼转换保留跨单元的词上下文；字符聚合避免推断空格拆开单词） ---
            char_runs = _char_runs_from_page(doc, page_index, log)
            if char_runs is not None:
                converted_texts = _convert_page_texts(
                    cc, [run['text'] for run in char_runs], page_index + 1, log)
                for run, converted in zip(char_runs, converted_texts):
                    if _draw_text(page_builder, converted,
                                  run['x'] - cx0, run['y'] - cy0, run['w'],
                                  run['font_name'], run['size'], run['color'],
                                  run['bold'], fonts,
                                  char_xs=[v - cx0 for v in run.get('xs', ())] or None):
                        converted_spans += 1
            else:
                # 回退：按 span 重排
                try:
                    spans = doc.extract_spans(page_index)
                except Exception as e:
                    log(f"  ⚠ 第{page_index + 1}页文本提取失败: {e}")
                    spans = []

                converted_texts = _convert_page_texts(
                    cc, [span.text or '' for span in spans], page_index + 1, log)
                for span, converted in zip(spans, converted_texts):
                    if _draw_span_text(page_builder, converted, span, fonts, cx0, cy0):
                        converted_spans += 1

            builder = page_builder.done()

        # --- 取消检查 ---
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # --- 写出文件 ---
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        if not output_path.lower().endswith('.pdf'):
            output_path += '.pdf'

        try:
            builder.save(output_path)
        except Exception as e:
            log(f"错误：写出 PDF 文件失败 - {e}")
            return False

        log(f"已保存: {output_path}")
        log(f"PDF 转换完成：共 {total_pages} 页，转换 {converted_spans} 个文本片段")
        return output_path
    finally:
        try:
            doc.close() if hasattr(doc, 'close') else None
        except Exception:
            pass
