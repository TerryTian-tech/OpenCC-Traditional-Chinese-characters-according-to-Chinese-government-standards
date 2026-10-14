import difflib
import functools
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

@functools.lru_cache(maxsize=128)
def _measure_font(font_path: str, px: float) -> Optional[ImageFont.FreeTypeFont]:
    """
    加载用于测量的 PIL 字体对象，按 (路径, 字号) 进程内有限缓存——
    长驻进程连续转换多份文件时不会无界累积字体对象。加载失败返回
    None（同样被缓存，不会反复重试失败的路径）。
    """
    try:
        return ImageFont.truetype(font_path, px or 1)
    except Exception:
        return None


def _measure(text: str, font_path: str, size: float) -> Optional[float]:
    """
    用 PIL 测量文本以指定字体渲染时的 advance 宽度（PDF pt，1px@72dpi = 1pt）。
    测量失败（字体不支持）返回 None，调用方回退到不缩放。
    """
    if not text:
        return 0.0
    font = _measure_font(font_path, round(size, 2))
    if font is None:
        return None
    try:
        return float(font.getlength(text))
    except Exception:
        return None


def _estimate_width(text: str, size: float) -> float:
    """无法用字体测量时的保守宽度估算：宽字符（CJK/全角等）按 1em、其余按 0.55em"""
    wide = 0
    for ch in text:
        cp = ord(ch)
        if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7A3
                or 0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE4F
                or 0xFF00 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6
                or 0x20000 <= cp <= 0x3FFFD):
            wide += 1
    return (wide + (len(text) - wide) * 0.55) * size


def _measure_or_estimate(text: str, font_path: str, size: float) -> float:
    """测量优先，测量失败回退估算——保证任何段都有有限宽度可推进绘制位置"""
    w = _measure(text, font_path, size)
    return w if w is not None else _estimate_width(text, size)


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


def _upright_page_is_vertical(chars: list) -> bool:
    """
    依据直立字符的坐标分布判断版面方向：y 方向聚类数远多于 x 方向聚类数
    （且行数足够）时为竖排版面。

    竖排的中日文正文（方正书版/InDesign/Word 等）字形直立、沿列自上而下
    排布，与“字形旋转 90° 的侧排文字”（_sideways 路径）是两种不同的实现；
    前者的字符旋转角为 0，只能靠坐标分布识别。用聚类计数而非去重计数，
    避免基线/列位的微小抖动把同一行/列拆成多个。字符太少的页面不做判定
    （保持横排处理；单列少量字符按行读取时 y 降序恰好也是正确阅读序）。
    仅凭聚类计数会把窄横向版面（每行一两个字的小表格、表头等）误判为
    竖排：真竖排的列内相邻字符步进约 1em（字身框），而窄横向版面按 x
    聚出的“列”内纵向步进是行距（通常 >= 1.5em），据此再过滤一道。
    """
    if len(chars) < 12:
        return False
    sizes = sorted((c.font_size or 10.0) for c in chars)
    tol = max(sizes[len(sizes) // 2] * 0.35, 1.5)

    def _cluster_count(values) -> int:
        count, anchor = 0, None
        for v in sorted(values):
            if anchor is None or v - anchor > tol:
                count += 1
                anchor = v
        return count

    n_rows = _cluster_count(c.origin_y for c in chars)
    n_cols = _cluster_count(c.origin_x for c in chars)
    if not (n_rows >= 4 and n_rows > n_cols * 2):
        return False

    # 列内步进校验：与调用方一致按 x 贪心聚列，取列内相邻字符纵向步进
    # （以字号归一）的中位数，须落在约 1em 附近才算真竖排。行距极紧
    # （约 1.4em 以内）的窄表格仍可能通过，属可接受的残余误判。
    cols: List[List] = []  # [锚点列 x, 字符列表]
    for c in sorted(chars, key=lambda c: c.origin_x):
        if cols and abs(c.origin_x - cols[-1][0]) <= tol:
            cols[-1][1].append(c)
        else:
            cols.append([c.origin_x, [c]])
    gaps: List[float] = []
    for _ax, col in cols:
        col.sort(key=lambda c: -c.origin_y)
        for a, b in zip(col, col[1:]):
            dy = a.origin_y - b.origin_y
            if dy > 0:
                gaps.append(dy / max(a.font_size or 10.0, b.font_size or 10.0))
    if not gaps:
        return False
    gaps.sort()
    return 0.75 <= gaps[len(gaps) // 2] <= 1.4


def _char_runs_from_page(doc: PdfDocument, page_index: int,
                         log: Callable[[str], None]) -> Optional[List[dict]]:
    """
    用字符级数据（extract_chars）聚合出绘制单元，替代 extract_spans 的文本。

    extract_spans 生成的文本会在字符间隙较大时插入“推断空格”（原文中并不
    存在空格字符），直接重绘会把单词拆开（如 Work -> W ork）。改为从字符的
    精确坐标出发：同风格且间隙小于阈值的字符合并为一个单元，间隙大的各自
    按原坐标定位——既不引入多余空格，也保留原文版式（含两端对齐的拉伸）。

    排序先做行/列聚类再在行内按 x（列内按 y）排序。不能直接用
    round(y, 1) 分桶排序：0.1 的取整边界会把基线有微小抖动的同行字符拆进
    相邻两个“桶”，与行内合并阈值（约 0.35em）不一致，造成行内乱序、单元
    被无谓拆散。旋转字符（竖排文字、侧排标注）不再丢弃：沿其阅读方向聚合成
    单元后按原位横排绘制（内容不丢失，字形方向与原文可能不同）。

    版面方向检测为竖排时（直立字形沿列排布，见 _upright_page_is_vertical），
    直立字符改按“列从右到左、列内自上而下”聚合：竖排按行横读会把各列
    同一高度的字交织成乱序文本，一对多转换的词上下文（如“於是→于是”、
    “彷彿→仿佛”）完全失效；按列读取后上下文与阅读序一致。竖排列获得
    自右而左递增的列号（写入 line 字段，rot 保持 0、vert 置 True），
    供跨单元连续性判断使用。

    返回 None 表示字符提取失败或无可用字符（调用方回退到 span 模式），
    否则返回单元列表：
    {text, x, y, w, font_name, size, color, bold, rot, line, lx0, xs, ys,
    vert}，坐标为 PDF 用户空间，xs/ys 为逐字坐标（与 text 等长），
    line 为所在水平行的行号（竖排列为列号，旋转列为 None），lx0 为所在
    行的行首 x，vert 表示是否为竖排列单元。
    """
    try:
        chars = doc.extract_chars(page_index)
    except Exception as e:
        log(f"  ⚠ 第{page_index + 1}页字符提取失败: {e}，改用片段模式重排")
        return None
    if not chars:
        log(f"  ⚠ 第{page_index + 1}页字符提取结果为空，改用片段模式重排")
        return None

    def _line_tol(size: float) -> float:
        return max(size * 0.35, 1.5)

    def _char_size(ch) -> float:
        return ch.font_size if ch.font_size and ch.font_size > 0 else 10.0

    def _rot_deg(ch) -> float:
        # 归一到 [0, 360)，±45° 以内视为水平（消除 359.9° 之类的噪声）
        r = (getattr(ch, 'rotation_degrees', 0.0) or 0.0) % 360.0
        return 0.0 if r >= 315.0 or r < 45.0 else r

    def _sideways(rot: float) -> bool:
        # 90°/270°：文本沿垂直方向排布（竖排、侧排），按列聚合
        return 45.0 <= rot < 135.0 or 225.0 <= rot < 315.0

    usable = [ch for ch in chars if ch.char]
    upright = [ch for ch in usable if not _sideways(_rot_deg(ch))]
    sideways = [ch for ch in usable if _sideways(_rot_deg(ch))]

    # 水平字符按基线聚成行（自上而下，行内按 x）；侧排字符按列聚合（自左而右，列内按 y 自上而下）
    horiz = sorted(upright, key=lambda c: (-c.origin_y, c.origin_x))
    vert = sorted(sideways, key=lambda c: (c.origin_x, -c.origin_y))

    h_lines: List[List] = []  # [锚点基线, 字符列表]
    for ch in horiz:
        if h_lines and abs(h_lines[-1][0] - ch.origin_y) <= _line_tol(_char_size(ch)):
            h_lines[-1][1].append(ch)
        else:
            h_lines.append([ch.origin_y, [ch]])

    v_lines: List[List] = []  # [锚点列 x, 字符列表]
    for ch in vert:
        if v_lines and abs(v_lines[-1][0] - ch.origin_x) <= _line_tol(_char_size(ch)):
            v_lines[-1][1].append(ch)
        else:
            v_lines.append([ch.origin_x, [ch]])

    # 行/列按（最靠上的字符 y, 最靠左的字符 x）排序，保持整体自上而下、自左而右；
    # 水平行获得自上而下递增的行号（旋转列为 None），供跨行连续性判断使用。
    # 竖排版面改为：直立字符按 x 聚成列、按“列从右到左”编号（列内自上而下），
    # 列号写入 line 字段（rot 保持 0、vert 置 True），侧排列仍附在最后。
    vertical_page = _upright_page_is_vertical(upright)
    lines = []  # (字符列表, 是否旋转列, 竖排列号或 None)
    if vertical_page:
        cols: List[List] = []  # [锚点列 x, 字符列表]，x 升序贪心聚类
        for ch in sorted(upright, key=lambda c: c.origin_x):
            if cols and abs(ch.origin_x - cols[-1][0]) <= _line_tol(_char_size(ch)):
                cols[-1][1].append(ch)
            else:
                cols.append([ch.origin_x, [ch]])
        cols.sort(key=lambda col: -col[0])  # 阅读序：列从右到左
        for idx, (_ax, col_chars) in enumerate(cols):
            col_chars.sort(key=lambda c: -c.origin_y)
            lines.append((col_chars, False, idx))
    else:
        for _anchor, chs in h_lines:
            chs.sort(key=lambda c: c.origin_x)
            lines.append((chs, False, None))
    for _anchor, chs in v_lines:
        chs.sort(key=lambda c: -c.origin_y)
        lines.append((chs, True, None))
    if not vertical_page:
        lines.sort(key=lambda entry: (-max(c.origin_y for c in entry[0]),
                                      min(c.origin_x for c in entry[0])))

    runs: List[dict] = []
    cur = None
    tail = None  # 当前单元的最后一个字符

    line_no = 0
    for chs, sideways_line, vcol in lines:
        # 行号只编给水平行（连续且不被旋转列的插入打断）；竖排列写列号
        # （自右而左递增），旋转列为 None
        line_id = None
        if vcol is not None:
            line_id = vcol
        elif not sideways_line:
            line_id = line_no
            line_no += 1
        line_x0 = min(c.origin_x for c in chs)
        for ch in chs:
            char = ch.char
            size = _char_size(ch)
            bold = _char_is_bold(ch)
            rot = _rot_deg(ch)
            # 合并判定用归一化字体名（剥离子集前缀、忽略大小写）与量化颜色：
            # 同一文字流的子集前缀不同（ABCDEF+SimSun / GHIJKL+SimSun）或
            # 颜色仅差转换噪声时不应拆开，否则会削弱转换的词上下文。
            # 旋转角也需量化——extract_chars 的 rotation_degrees 不吸附象限
            # （逐字矩阵的角度可有微小抖动），不取整会拆散同一侧排列
            base_name = ch.font_name or ''
            if '+' in base_name:
                base_name = base_name.split('+', 1)[1]
            color = ch.color or (0.0, 0.0, 0.0)
            color_key = tuple(round(min(max(c, 0.0), 1.0) * 255) for c in color)
            style = (base_name.casefold(), round(size, 1), color_key, bold, round(rot, 1))

            merged = False
            if cur is not None and cur['style'] == style:
                if vcol is not None:
                    # 竖排列内：同列（x 相近）且纵向步进约 1em 即合并
                    same_col = abs(ch.origin_x - tail.origin_x) <= _line_tol(size)
                    dy = tail.origin_y - ch.origin_y
                    if same_col and 0.0 < dy <= size * 1.6:
                        merged = True
                elif _sideways(rot):
                    # 侧排字符沿 y 方向阅读：同列（x 相近）且垂直间距正常即合并
                    same_col = abs(ch.origin_x - tail.origin_x) <= _line_tol(size)
                    dy = abs(ch.origin_y - tail.origin_y)
                    if same_col and 0.0 < dy <= size * 1.6:
                        merged = True
                else:
                    same_line = abs(ch.origin_y - tail.origin_y) <= _line_tol(size)
                    gap = ch.origin_x - (tail.origin_x + (tail.advance_width or 0.0))
                    # 西文相邻用较紧的阈值（真实空格约 0.25em），中西文及中文之间放宽，
                    # 尽量让词语留在同一单元以保证词汇级转换的上下文
                    latin_pair = (_LATIN_RUN_RE.fullmatch(tail.char or '') is not None
                                  and _LATIN_RUN_RE.fullmatch(char) is not None)
                    threshold = size * (0.20 if latin_pair else 0.30)
                    if same_line and gap <= threshold:
                        merged = True
            if merged:
                cur['text'] += char
                cur['xs'].append(ch.origin_x)
                cur['ys'].append(ch.origin_y)
                cur['w'] = max(cur['w'],
                               (ch.origin_x + (ch.advance_width or 0.0)) - cur['x'])
                tail = ch
            else:
                cur = {
                    'text': char,
                    'x': ch.origin_x,
                    'y': ch.origin_y,
                    'w': ch.advance_width or 0.0,
                    'font_name': ch.font_name,
                    'size': size,
                    'color': ch.color,
                    'bold': bold,
                    'rot': rot,
                    'style': style,
                    'line': line_id,
                    'lx0': line_x0,
                    'xs': [ch.origin_x],
                    'ys': [ch.origin_y],
                    'vert': vcol is not None,
                }
                runs.append(cur)
                tail = ch

    if not runs:
        return None

    rotated_runs = 0
    for r in runs:
        if r['rot']:
            rotated_runs += 1
            # 逆时针侧排（90°）与倒排（180°）文本的阅读方向与扫描序相反，
            # 反转后 text 与 xs/ys 仍逐字配对，转换也能拿到正序上下文
            if r['rot'] in (90.0, 180.0):
                r['text'] = r['text'][::-1]
                r['xs'].reverse()
                r['ys'].reverse()
        r.pop('style', None)

    if rotated_runs:
        log(f"  ℹ 第{page_index + 1}页有 {rotated_runs} 段旋转文字，已按原位横排绘制"
            f"（字形方向可能与原文不同）")
    return runs


# ---------------------------------------------------------------------------
# OpenCC 批量转换（按版面连续性拼接整体转换，按位置索引切回）
# ---------------------------------------------------------------------------


def _runs_contiguity(runs: List[dict]) -> List[bool]:
    """
    判断每个绘制单元与其前一个在版面上是否连续（供 _convert_page_texts
    决定直接拼接还是插入换行分隔）：

    - 竖排列单元（vert）之间：同列且与前一单元底部的纵向间距不超过约
      1.6em：连续——列内风格切换拆开的单元之间可能正是一个词；跨列
      （列边界、页眉列/正文列/脚注列）不连续，用换行分隔；
    - 同一行且水平间距不超过约 1em（含轻微重叠）：连续——字体切换、两端
      对齐拉伸拆开的单元之间可能正是一个词（“电”“复”分属宋体与 Times）；
    - 相邻两行（行距 0.75em~2em，即正常行距）且两行行首 x 对齐（相差
      不超过约 2.5em，同一段落的行首对齐或首行缩进）：连续——行尾行首
      可能拆开一个词（“重”“复”）；分栏/居中标题等行首错开的则分隔；
    - 其余（页眉/正文/脚注、分栏、表格单元间的大间距，以及旋转单元）：
      不连续，转换时用换行分隔，避免边界处拼出原文不存在的“词”触发
      错误短语转换。
    """
    flags: List[bool] = []
    prev = None
    for run in runs:
        flag = False
        if (prev is not None and run.get('vert') and prev.get('vert')
                and run.get('line') is not None and run['line'] == prev.get('line')):
            # 同一竖排列：与前一单元最后一个字的纵向间距正常即连续
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            prev_bottom = (prev.get('ys') or [prev['y']])[-1]
            dy = prev_bottom - run['y']
            flag = 0.0 < dy <= size * 1.6
        elif (prev is not None and not run.get('vert') and not prev.get('vert')
                and not run.get('rot') and not prev.get('rot')
                and run.get('line') is not None and run['line'] == prev.get('line')):
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            gap = run['x'] - (prev['x'] + prev.get('w', 0.0))
            flag = -0.3 * size <= gap <= size
        elif (prev is not None and not run.get('vert') and not prev.get('vert')
                and not run.get('rot') and not prev.get('rot')
                and run.get('line') is not None and prev.get('line') is not None
                and run['line'] == prev['line'] + 1):
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            dy = abs(run['y'] - prev['y'])
            dx_first = abs(run.get('lx0', run['x']) - prev.get('lx0', prev['x']))
            flag = 0.75 * size <= dy <= 2.0 * size and dx_first <= 2.5 * size
        flags.append(flag)
        prev = run
    return flags


def _spans_contiguity(spans: list) -> List[bool]:
    """span 回退路径的相邻连续性判断（规则同 _runs_contiguity，基于 span.bbox）"""
    flags: List[bool] = []
    prev = None
    for span in spans:
        flag = False
        if prev is not None:
            x, y = span.bbox[0], span.bbox[1]
            px, py, pw = prev.bbox[0], prev.bbox[1], prev.bbox[2]
            size = max(span.font_size or 10.0, prev.font_size or 10.0)
            if abs(y - py) <= max(size * 0.35, 1.5):
                flag = -0.3 * size <= x - (px + pw) <= size
            elif 0.75 * size <= abs(y - py) <= 2.0 * size:
                flag = abs(x - px) <= 2.5 * size  # 近似行首对齐（span 无行结构）
        flags.append(flag)
        prev = span
    return flags


def _convert_page_texts(cc, texts: List[str],
                        page_no: int, log: Callable[[str], None],
                        contiguous: Optional[List[bool]] = None) -> List[str]:
    """
    将一页的绘制单元文本按版面连续性拼成若干段整体转换，再按位置索引切回
    各单元（移植自 doc_converter 的段落级上下文方案）。

    PDF 字符级聚合出的单元常在词中间被切断（字体切换、换行、大间距，如
    分属行尾行首的“重复”、分属宋体与 Times 的“电复”），逐单元转换会让
    歧义字失去词上下文（“复”→“復”而非“複”）。连续单元直接拼接后整段
    一次转换，跨单元的词也能正确消歧；不连续的单元之间插入换行分隔
    （OpenCC 不转换换行，也不跨它分词），避免页眉/脚注/分栏等无关区域
    的边界拼出原文不存在的词。拆回不依赖分隔符内容——单元边界就是拼接
    时的字符偏移：

    - 转换前后长度一致（繁简映射绝大多数 1:1）：按偏移切割，各单元原文
      与译文等长，可走逐字定位绘制路径，版式与原文一致；
    - 长度不一致（罕见，如 s2twp 的“内存”→“記憶體”）：用前缀转换定位
      各单元边界在译文中的落点后切割，长度变化的单元自动落入整段重排
      路径，其余单元仍走逐字定位。
    """
    if not texts:
        return []
    parts: List[str] = []
    positions = []
    offset = 0
    for i, t in enumerate(texts):
        if i and not (contiguous and contiguous[i]):
            parts.append('\n')
            offset += 1
        positions.append((offset, offset + len(t)))
        offset += len(t)
        parts.append(t)
    full = ''.join(parts)
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

    开销为 O(单元数 × 全文长)，但只有转换改变字符数的页面才会走到这里
    （繁简映射绝大多数 1:1，常用配置整本书都不会触发），实际页面在
    几十毫秒量级，无需进一步优化（分治/整页对齐方案实测都无收益）。
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
# 修改字高亮（可选）：转换中被修改的汉字以黄色底纹标出
# ---------------------------------------------------------------------------

# 高亮底纹颜色（黄色）
_HIGHLIGHT_RGB = (1.0, 1.0, 0.0)

# 汉字（表意文字）码位区间，用于判断“修改的字”：标点、西文、数字等
# 非汉字的变化不在其列；高亮只比较文字码位，与重建 PDF 时的输出字体
# 替换（原文的宋体/黑体/方正白体等换成系统字体）无关
_CJK_IDEOGRAPH_RANGES = (
    (0x3007, 0x3007),    # 〇
    (0x3400, 0x4DBF),    # 扩展A
    (0x4E00, 0x9FFF),    # 基本区
    (0xF900, 0xFAFF),    # 兼容表意文字
    (0x20000, 0x2A6DF),  # 扩展B
    (0x2A700, 0x2EBEF),  # 扩展C~F
    (0x2F800, 0x2FA1F),  # 兼容表意文字增补
    (0x30000, 0x323AF),  # 扩展G~H
)


def _is_cjk_ideograph(ch: str) -> bool:
    """是否为汉字（表意文字，含扩展区、兼容区与〇）"""
    cp = ord(ch)
    if 0x3400 <= cp <= 0x9FFF:  # 扩展A + 基本区，绝大多数命中在此
        return True
    return any(lo <= cp <= hi for lo, hi in _CJK_IDEOGRAPH_RANGES)


def _char_is_modified_hanzi(orig: str, conv: str) -> bool:
    """
    单个码位的替换是否属于应高亮的“修改的字”：码位发生变化，且原文
    或译文至少一方是汉字。兼容区与基本区之间的同形换码（如 U+F900
    豈 → U+8C48 豈）码位确实变了，按修改高亮；标点符号（含全半角
    互换）与西文、数字的变化不高亮。
    """
    if orig == conv:
        return False
    return _is_cjk_ideograph(orig) or _is_cjk_ideograph(conv)


def _highlight_mask(orig: str, conv: str) -> List[bool]:
    """
    逐字比较原文与译文，得到与译文等长的高亮掩码（True=该字被修改）。

    长度一致（繁简映射绝大多数 1:1）时按位比较；长度变化（罕见的多字
    映射，如 s2twp 的“内存”→“記憶體”）用最长公共子序列对齐，只在
    替换/插入区块内高亮汉字——equal 区块是未变化的原文，无需比较。
    """
    n = len(conv)
    if n == 0:
        return []
    if len(orig) == n:
        return [_char_is_modified_hanzi(o, c) for o, c in zip(orig, conv)]
    mask = [False] * n
    matcher = difflib.SequenceMatcher(a=orig, b=conv, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        for j in range(j1, j2):
            mask[j] = _is_cjk_ideograph(conv[j])
    return mask


def _highlight_rect_metrics(size: float) -> Tuple[float, float]:
    """高亮底纹相对基线的几何：下探 0.80em、总高 1.05em（约覆盖汉字字身框）"""
    return -0.80 * size, 1.05 * size


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
_OPEN_PUNCT_RE = re.compile(r'[（《〈“‘【〔「『]')
_OPEN_PUNCT_INK_RIGHT = 0.9  # 开标点墨迹右缘约占字身框比例（宋体系实测 0.85~0.91）


def _draw_text(page_builder, text: str, x: float, y: float, run_w: float,
               font_name: Optional[str], size: float, color, is_bold: bool,
               fonts: dict, char_xs: Optional[List[float]] = None,
               char_ys: Optional[List[float]] = None,
               highlight_mask: Optional[List[bool]] = None) -> bool:
    """
    按脚本分组绘制一段已转换的文本到指定位置。

    文本由 _convert_page_texts 按页批量转换后传入，本函数只负责排版绘制：

    - 按原文风格类别（黑体/宋体/楷体/仿宋）匹配输出字体，保留字体区分度
    - 西文/数字按原文风格选择衬线/非衬线拉丁字体；中文语境下的半角标点
      跟随中文字体（源文档里它们由中文字库渲染，落到西文字体会明显变小）
    - 优先逐字按原始坐标绘制（char_xs，可选 char_ys）：完整保留两端对齐
      的字距拉伸与方正书版“标点半角”版式——整段按字体自然宽度推进会让
      段内文字逐渐左漂，在段尾与后一单元之间留下空隙。char_xs/char_ys
      对应原文文本，长度与转换后文本一致时可用；竖排等旋转单元靠
      char_ys 逐字落到各自的基线上。OpenCC 短语转换可能改变字符数
      （如 s2twp 的“内存”→“記憶體”），此时回退到整段重排
    - highlight_mask 与 text 等长，为 True 的字先铺黄色底纹再绘制文字
      （内容流按调用顺序叠放，先绘制的底纹位于文字下层）
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

    size = size if size and size > 0 else 10.0
    # 单元宽度缺失（如旋转字符 advance 为 0）时按字数估算，避免重排路径
    # 因 span_w 为 0 而把字号缩到下限
    span_w = run_w if run_w and run_w > 0 else _estimate_width(text, size)
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
    # char_xs/char_ys 与原文等长；转换后长度一致（OpenCC 多数映射为 1:1）时可用
    if char_xs and len(char_xs) == len(text):
        n = len(char_xs)
        has_ys = bool(char_ys) and len(char_ys) == n
        # 书写轴判定：常规水平文本的 char_xs 单调递增；竖排文字与旋转页上的
        # 文本沿竖直方向书写（char_xs 近似恒定、逐字位置由 char_ys 区分），
        # 180° 旋转页则是镜像递减。这些布局下“到下一字的水平距离”与按
        # span_w 推算的末字字格都不成立（会得到 0 或整行宽的错误值），
        # 一律返回 0 走测量兜底——兜底宽度≈字宽，恰好覆盖方块字
        stacked = has_ys and (max(char_xs) - min(char_xs)) < size * 0.5
        mirrored = char_xs[-1] < char_xs[0] - size * 0.5

        def _cell(i: int) -> float:
            if stacked:
                return 0.0
            if i + 1 < n:
                return char_xs[i + 1] - char_xs[i]
            if mirrored:
                return 0.0
            return span_w - (char_xs[i] - char_xs[0])

        # 修改字高亮：先铺黄色底纹，后绘制的文字位于其上层
        if highlight_mask is not None:
            dy, dh = _highlight_rect_metrics(size)
            for i, marked in enumerate(highlight_mask[:n]):
                if not marked:
                    continue
                cx = char_xs[i]
                cy = char_ys[i] if has_ys else y
                cell = _cell(i)
                if cell <= 0:
                    cell = _measure(text[i], cjk_entry['path'], size) or size
                page_builder.filled_rect(cx, cy + dy, cell, dh,
                                         _HIGHLIGHT_RGB[0], _HIGHLIGHT_RGB[1],
                                         _HIGHLIGHT_RGB[2])
        for i, (ch, cx) in enumerate(zip(text, char_xs)):
            draw_x = cx
            cy = char_ys[i] if has_ys else y
            if _OPEN_PUNCT_RE.fullmatch(ch):
                # 开标点墨迹靠字身框右侧：源单元（到下一字的距离）比全宽
                # 窄时，左移使墨迹右缘对齐单元右界，贴住后面的字
                cell = _cell(i)
                natural = _measure(ch, cjk_entry['path'], size)
                if cell > 0 and natural and cell < natural:
                    draw_x = cx + cell - _OPEN_PUNCT_INK_RIGHT * size
            if (_LATIN_RUN_RE.fullmatch(ch) and _LATIN_ALNUM_RE.search(ch)):
                entry = latin_entry
            else:
                entry = cjk_entry
            page_builder.font(entry['name'], size).at(draw_x, cy).inline_color(
                color[0], color[1], color[2], ch)
        return True

    # --- 整段重排路径 ---
    segments = []  # [text, 注册名, 字体路径, 是否可压缩标点]
    for part, is_latin in _split_by_script(text):
        entry = latin_entry if is_latin else cjk_entry
        if is_latin:
            segments.append([part, entry['name'], entry['path'], False])
            continue
        for sub in _COMPRESSIBLE_PUNCT_RE.split(part):
            if not sub:
                continue
            punct = _COMPRESSIBLE_PUNCT_RE.fullmatch(sub) is not None
            segments.append([sub, entry['name'], entry['path'], punct])

    # 宽度自适应：真实测量值用于开标点定位（naturals），测量失败的段用
    # 估算宽度推进绘制位置——否则 x 不前进，后续段会画到同一位置重叠
    measured = [_measure(seg[0], seg[2], size) for seg in segments]
    naturals = list(measured)
    widths = [w if w is not None else _estimate_width(seg[0], size)
              for w, seg in zip(measured, segments)]
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
            widths = [_measure_or_estimate(seg[0], seg[2], size) for seg in segments]
            widths = [w * 0.5 if seg[3] else w
                      for w, seg in zip(widths, segments)]

    # 修改字高亮：整段重排路径中按段内自然字宽定位各字铺底纹
    # （段宽已压缩/缩放时按比例归一，保证底纹与实际绘制位置一致）
    if highlight_mask is not None:
        dy, dh = _highlight_rect_metrics(size)
        hx = x
        base = 0  # 当前段首字在掩码中的索引
        for seg, width in zip(segments, widths):
            char_ws = [_measure(ch, seg[2], size) for ch in seg[0]]
            total = sum(w for w in char_ws if w)
            scale = (width / total) if (width and total > 0) else 1.0
            cx = hx
            for k, w in enumerate(char_ws):
                w = (w if w is not None else size) * scale
                if base + k < len(highlight_mask) and highlight_mask[base + k] and w > 0:
                    page_builder.filled_rect(cx, y + dy, w, dh,
                                             _HIGHLIGHT_RGB[0], _HIGHLIGHT_RGB[1],
                                             _HIGHLIGHT_RGB[2])
                cx += w
            base += len(seg[0])
            if width:
                hx += width

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
                    transform: Callable[[float, float], Tuple[float, float]],
                    rotation: int = 0,
                    highlight_mask: Optional[List[bool]] = None) -> bool:
    """按 span 绘制已转换的文本（字符级聚合失败时的回退路径）"""
    if not text or not text.strip():
        return False
    size = span.font_size if span.font_size and span.font_size > 0 else 10.0
    x, y = transform(span.bbox[0], span.bbox[1])
    # 旋转 90/270 后原用户空间的宽在输出中变为“高”，整段重排的宽度
    # 自适应应以另一维（bbox[3]）为准
    run_w = span.bbox[3] if rotation in (90, 270) else span.bbox[2]
    return _draw_text(page_builder, text, x, y, run_w,
                      span.font_name, size, span.color, span.is_bold, fonts,
                      highlight_mask=highlight_mask)


# ---------------------------------------------------------------------------
# 页面元素重建辅助
# ---------------------------------------------------------------------------

def _page_space_transform(rotation: int, ox: float, oy: float,
                          width: float, height: float
                          ) -> Tuple[float, float, Callable[[float, float], Tuple[float, float]]]:
    """
    构造用户空间 -> 输出页面空间的坐标变换（裁剪框平移 + 页面旋转）。

    pdf_oxide 的提取接口（extract_chars/extract_spans/extract_images 等）
    返回的是未旋转的用户空间坐标，而 render_pixmap 按旋转后的视觉方向
    渲染；输出端 FluentPageBuilder 不支持页面 /Rotate，因此旋转页需对
    所有绘制坐标自行做旋转变换（页面宽高随之对调）。返回
    (输出页宽, 输出页高, 点变换)；rotation 为 90 的倍数，非法值按 0 处理。
    """
    if rotation == 90:
        return height, width, lambda x, y: (y - oy, width - (x - ox))
    if rotation == 180:
        return width, height, lambda x, y: (width - (x - ox), height - (y - oy))
    if rotation == 270:
        return height, width, lambda x, y: (height - (y - oy), x - ox)
    return width, height, lambda x, y: (x - ox, y - oy)


def _draw_lines(page_builder, lines: list,
                transform: Callable[[float, float], Tuple[float, float]]) -> None:
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
                x1, y1 = transform(cur[0], cur[1])
                x2, y2 = transform(end[0], end[1])
                page_builder.stroke_line(x1, y1, x2, y2, width, color)
                cur = end


def _draw_rects(page_builder, rects: list,
                transform: Callable[[float, float], Tuple[float, float]]) -> None:
    """重画矩形（底色块、边框等）；有填充色优先按填充绘制"""
    for rect in rects:
        x, y, w, h = rect.get('bbox', (0.0, 0.0, 0.0, 0.0))
        fill = rect.get('fill_color')
        # 变换对角点后取包围盒：旋转 90/270 时宽高自然互换
        x0, y0 = transform(x, y)
        x1, y1 = transform(x + w, y + h)
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if fill is not None:
            page_builder.filled_rect(rx, ry, rw, rh, fill[0], fill[1], fill[2])
        else:
            stroke = rect.get('stroke_color') or (0.0, 0.0, 0.0)
            width = rect.get('stroke_width') or 1.0
            page_builder.stroke_rect(rx, ry, rw, rh, width, stroke)


def _match_images_to_blobs(metas: list, blobs: list, page_index: int,
                           log: Callable[[str], None]) -> list:
    """
    将图片位置元数据与图像数据配对。两者没有共同的对象标识，正常情况下
    依赖提取顺序一致；数量不一致时退化为按（像素宽, 像素高）逐个配对，
    配不上的跳过——宁可少画，也不把图像画到别的位置上去。
    """
    if len(metas) == len(blobs):
        return list(zip(metas, blobs))
    if metas or blobs:
        log(f"  ⚠ 第{page_index + 1}页图片位置与图像数据数量不一致"
            f"（{len(metas)}/{len(blobs)}），尝试按尺寸配对")

    def _dims(item):
        if isinstance(item, dict):
            return (item.get('width'), item.get('height'))
        return None

    pairs = []
    used = [False] * len(blobs)
    for meta in metas:
        key = _dims(meta)
        hit = None
        if key:
            for j, blob in enumerate(blobs):
                if not used[j] and _dims(blob) == key:
                    hit = j
                    break
        if hit is None:
            if isinstance(meta, dict) and meta.get('bbox'):
                log(f"  ⚠ 第{page_index + 1}页一张图片未能配对到图像数据，已跳过")
            continue
        used[hit] = True
        pairs.append((meta, blobs[hit]))
    return pairs


def _rotate_image_bytes(data: bytes, rotation: int) -> bytes:
    """
    按页面旋转方向旋转图像内容（输出页无法表达 /Rotate，图像内容需与
    坐标一起旋转才能与原文视觉一致）。旋转失败时原样返回。
    JPEG 重编码用高质量参数（PIL 默认 quality=75 会让旋转页的图片明显
    画质劣化）；PNG 本身无损，保持默认。
    """
    try:
        im = Image.open(io.BytesIO(data))
        angle = {90: -90, 180: 180, 270: 90}[rotation]
        buf = io.BytesIO()
        rotated = im.rotate(angle, expand=True)
        if (im.format or "").upper() == "JPEG":
            rotated.save(buf, format="JPEG", quality=95, subsampling=0)
        else:
            rotated.save(buf, format=im.format or "PNG")
        return buf.getvalue()
    except Exception:
        return data


def _draw_images(page_builder, doc: PdfDocument, page_index: int,
                 transform: Callable[[float, float], Tuple[float, float]],
                 rotation: int, log: Callable[[str], None]) -> int:
    """
    将页面上的图片按原位置嵌入新文档。
    extract_images 提供位置（bbox 为 x,y,w,h），extract_image_bytes 提供图像
    字节，两者按内容流顺序一一对应；数量不一致时按像素尺寸配对兜底。
    """
    metas = doc.extract_images(page_index) or []
    blobs = doc.extract_image_bytes(page_index) or []

    drawn = 0
    for meta, blob in _match_images_to_blobs(metas, blobs, page_index, log):
        bbox = meta.get('bbox')
        data = blob.get('data') if isinstance(blob, dict) else blob
        if not bbox or not data:
            continue
        x, y, w, h = bbox
        # 变换对角点后取包围盒：旋转 90/270 时宽高自然互换
        x0, y0 = transform(x, y)
        x1, y1 = transform(x + w, y + h)
        if rotation in (90, 180, 270):
            data = _rotate_image_bytes(data, rotation)
        page_builder.image_with_alt(data, min(x0, x1), min(y0, y1),
                                    abs(x1 - x0), abs(y1 - y0), "")
        drawn += 1
    return drawn


def _rasterize_page_png(doc: PdfDocument, page_index: int, dpi: int = 150) -> Optional[bytes]:
    """
    将整页栅格化为 PNG 字节（用于没有文本层的扫描页，保留原页面外观）。
    栅格化失败返回 None，调用方按空白页处理。
    """
    try:
        pm = doc.render_pixmap(page_index, dpi=dpi)
        # render_pixmap 返回预乘 RGBA8888（PDF 透明度模型）：需按 PIL 的
        # "RGBa" 模式读入再转直通 alpha，直接当 "RGBA" 用会让半透明内容
        # 的颜色偏暗
        im = Image.frombytes("RGBa", (pm.width, pm.height), pm.data).convert("RGBA")
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
    is_cancelled_callback: Optional[Callable[[], bool]] = None,
    highlight_changes: bool = False
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
    highlight_changes : bool
        是否为转换中修改的字添加黄色高亮。只高亮汉字的实质变化；
        标点符号变化，以及重建时整体替换输出字体（宋体/黑体等）带来的
        观感差异不属于“修改”，不高亮

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

    if highlight_changes:
        log("修改字高亮已启用：被转换修改的汉字将以黄色底纹标出（标点符号变化与输出字体替换不标）")

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

        # 实例化逻辑字体表；同一字体文件只注册一次，多个逻辑名共享注册名。
        # 注册失败的路径也记入 registered_by_path（值为 None），共用该路径的
        # 其他逻辑名不会反复重试、重复告警；基础黑体注册失败则无法兜底，致命
        fonts = {}
        registered_by_path = {}  # 字体文件路径 -> 注册名（注册失败为 None）
        for key, default_name in _DEFAULT_FONT_NAMES.items():
            path = font_paths[key]
            if path not in registered_by_path:
                try:
                    builder = builder.register_embedded_font(default_name, EmbeddedFont.from_file(path))
                    registered_by_path[path] = default_name
                except Exception as e:
                    registered_by_path[path] = None
                    log(f"警告：字体 {os.path.basename(path)} 注册失败（{e}），相关文字将回退到黑体渲染")
            if registered_by_path[path] is not None:
                fonts[key] = {'name': registered_by_path[path], 'path': path}
            elif key == 'sans':
                log("错误：基础中文字体（黑体）注册失败，无法生成 PDF")
                return False
            else:
                fonts[key] = dict(fonts['sans'])

        converted_spans = 0
        highlighted_chars = 0
        vertical_pages = 0

        for page_index in range(total_pages):
            if is_cancelled_callback and is_cancelled_callback():
                log("转换已被取消")
                return False

            log(f"  [{page_index + 1}/{total_pages}] 处理第 {page_index + 1} 页")

            # 裁剪框决定可见区域，输出页面以它为基准
            try:
                box = doc.page_crop_box(page_index)
            except Exception:
                box = None
            if not box:
                try:
                    box = doc.page_media_box(page_index)
                except Exception:
                    box = (0.0, 0.0, 612.0, 792.0)
            cx0, cy0, cx1, cy1 = box
            page_w, page_h = cx1 - cx0, cy1 - cy0
            if page_w <= 0 or page_h <= 0:
                try:
                    mb = doc.page_media_box(page_index)
                except Exception:
                    mb = (0.0, 0.0, 612.0, 792.0)
                cx0, cy0 = mb[0], mb[1]
                page_w, page_h = mb[2] - mb[0], mb[3] - mb[1]

            # 页面旋转：提取坐标未旋转、栅格化已旋转，输出端无 /Rotate，
            # 用坐标变换把旋转应用到文本/矢量/图片上（扫描页栅格图本身就是
            # 旋转后的视觉，直接铺满即可）
            try:
                rotation = int(doc.page_rotation(page_index) or 0) % 360
            except Exception:
                rotation = 0
            if rotation % 90:
                log(f"  ⚠ 第{page_index + 1}页旋转角度非 90 的倍数（{rotation}°），按未旋转处理")
                rotation = 0
            out_w, out_h, transform = _page_space_transform(
                rotation, cx0, cy0, page_w, page_h)

            page_builder = builder.page(out_w, out_h)

            # --- 无文本层的扫描页：整页栅格化保留 ---
            if not doc.has_text_layer(page_index):
                png = _rasterize_page_png(doc, page_index)
                if png:
                    page_builder.image_with_alt(png, 0.0, 0.0, out_w, out_h, "")
                else:
                    log(f"  ⚠ 第{page_index + 1}页栅格化失败，输出为空白页")
                builder = page_builder.done()
                continue

            # --- 图片（先画，位于文字下层） ---
            try:
                _draw_images(page_builder, doc, page_index, transform, rotation, log)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页图片保留失败: {e}")

            # --- 矢量线条与矩形 ---
            try:
                _draw_lines(page_builder, doc.extract_lines(page_index) or [], transform)
                _draw_rects(page_builder, doc.extract_rects(page_index) or [], transform)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页矢量图形保留失败: {e}")

            # --- 文本：字符级聚合成绘制单元，按版面连续性拼接转换后按位置切回重排
            #     （连续拼接保留跨单元的词上下文；字符聚合避免推断空格拆开单词） ---
            char_runs = _char_runs_from_page(doc, page_index, log)
            if char_runs is not None:
                if any(r.get('vert') for r in char_runs):
                    vertical_pages += 1
                contiguous = _runs_contiguity(char_runs)
                converted_texts = _convert_page_texts(
                    cc, [run['text'] for run in char_runs], page_index + 1, log,
                    contiguous)
                for run, converted in zip(char_runs, converted_texts):
                    mask = (_highlight_mask(run['text'], converted)
                            if highlight_changes else None)
                    tpoints = [transform(a, b) for a, b in
                               zip(run.get('xs', ()), run.get('ys', ()))]
                    try:
                        drawn = _draw_text(page_builder, converted,
                                           *transform(run['x'], run['y']), run['w'],
                                           run['font_name'], run['size'], run['color'],
                                           run['bold'], fonts,
                                           char_xs=[p[0] for p in tpoints] or None,
                                           char_ys=[p[1] for p in tpoints] or None,
                                           highlight_mask=mask)
                    except Exception as e:
                        # 单个单元绘制失败不应中断整份文件（如个别字符触发了
                        # 字体/测量的边界错误），跳过该单元继续
                        log(f"  ⚠ 第{page_index + 1}页一个文本单元绘制失败: {e}")
                        continue
                    if drawn:
                        converted_spans += 1
                        if mask:
                            highlighted_chars += sum(mask)
            else:
                # 回退：按 span 重排
                try:
                    spans = doc.extract_spans(page_index)
                except Exception as e:
                    log(f"  ⚠ 第{page_index + 1}页文本提取失败: {e}")
                    spans = []

                contiguous = _spans_contiguity(spans)
                converted_texts = _convert_page_texts(
                    cc, [span.text or '' for span in spans], page_index + 1, log,
                    contiguous)
                for span, converted in zip(spans, converted_texts):
                    mask = (_highlight_mask(span.text or '', converted)
                            if highlight_changes else None)
                    try:
                        drawn = _draw_span_text(page_builder, converted, span, fonts,
                                                transform, rotation, mask)
                    except Exception as e:
                        log(f"  ⚠ 第{page_index + 1}页一个文本片段绘制失败: {e}")
                        continue
                    if drawn:
                        converted_spans += 1
                        if mask:
                            highlighted_chars += sum(mask)

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
        summary = f"PDF 转换完成：共 {total_pages} 页，转换 {converted_spans} 个文本片段"
        if vertical_pages:
            summary += f"（其中 {vertical_pages} 页为竖排版面，已按“列从右到左、列内自上而下”顺序转换）"
        if highlight_changes:
            summary += f"，高亮 {highlighted_chars} 个修改字"
        log(summary)
        return output_path
    finally:
        try:
            doc.close() if hasattr(doc, 'close') else None
        except Exception:
            pass
