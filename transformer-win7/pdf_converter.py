import difflib
import functools
import io
import os
import re
from typing import Callable, List, Optional, Tuple, Union

from opencc import OpenCC
from PIL import Image, ImageFont

# ---------------------------------------------------------------------------
# PDF 引擎（纯 Python，Win7 可用）
#
# 读取侧 pdfminer.six：字符级坐标 / 字体名 / 字号 / 颜色 / 矢量路径 / 图片；
# 写出侧 reportlab：文字排版、TTF/TTC 子集内嵌、线条 / 矩形 / 图片绘制。
# pypdf 可选，作为复杂编码图片（CCITT 传真等）的解码兜底。
# 替代原 pdf-oxide 方案（Rust 二进制轮子，Win7 旧 Python 上不可用）。
# ---------------------------------------------------------------------------

try:
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.image import (LITERALS_DCT_DECODE, LITERALS_JPX_DECODE,
                                LITERAL_DEVICE_CMYK, LITERAL_DEVICE_GRAY,
                                LITERAL_DEVICE_RGB,
                                LITERAL_INLINE_DEVICE_GRAY,
                                LITERAL_INLINE_DEVICE_RGB)
    from pdfminer.layout import LTChar, LTContainer, LTCurve, LTImage, LTRect
    from pdfminer.pdfdocument import PDFDocument as _MinerDocument
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.utils import apply_matrix_pt, mult_matrix
    _PDFMINER_IMPORT_ERROR = ""
except ImportError as _e:
    _PDFMINER_IMPORT_ERROR = str(_e)

try:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    _REPORTLAB_IMPORT_ERROR = ""
except ImportError as _e:
    _REPORTLAB_IMPORT_ERROR = str(_e)

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

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
    # JP 变体，而 reportlab/PIL 都只会取第 0 个 face，会把部分汉字渲染成日式写法
    "/usr/share/fonts/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei.ttc",
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


# reportlab 只能内嵌 TrueType 轮廓的字体：sfnt 版本 0x00010000 / 'true'（ttf）
# 与 'ttcf'（合集）；CFF 轮廓的 .otf（版本 'OTTO'）与 woff 无法处理。
# 用文件头魔数探测，避免为每个候选做完整解析。
_FONT_MAGIC_OK = (b"\x00\x01\x00\x00", b"true", b"ttcf")


def _probe_font_file(path: str) -> bool:
    """判断字体文件是否为 reportlab 可内嵌的 TrueType/TrueType 集合"""
    try:
        with open(path, "rb") as f:
            return f.read(4) in _FONT_MAGIC_OK
    except OSError:
        return False


def _find_first_loadable_font(candidates: List[str], log: Callable[[str], None]) -> Optional[str]:
    """
    在候选列表中找到第一个存在且可被内嵌的字体文件。
    CFF .otf 候选（Linux 上 noto 单 face 版）会被静默跳过，
    由列表中排在其后的 .ttc 候选补位。
    """
    for path in candidates:
        if not os.path.isfile(path):
            continue
        if _probe_font_file(path):
            return path
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
    按文件名模式在字体目录中查找可内嵌的字体。
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
            if _probe_font_file(path):
                return path
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
# reportlab 字体注册（进程级名字缓存）
# ---------------------------------------------------------------------------

# reportlab 的 pdfmetrics.registerFont 对同名动态字体会静默沿用旧对象
# （fontName 已存在时整个存储分支被跳过），且 TTFont 还会按字面名
# （face name）复用对象。若每次转换都用 Ft0/Ft1… 这类每轮重置的名字
# 注册，同进程转换第二份文件时会解析到上一份文件的旧 TTFont 对象；
# 两次转换各字体的惰性加载顺序不同（如 Ft1 一份是 Times、另一份是
# 楷体）时，文字会落到不含这些字形的错误字体上，splitString 把缺字
# 映射为 0 号空白字形，输出整段文字丢失。因此注册名按字体文件路径
# 进程级固定——同名必同字体；TTFont 本身支持跨文档复用（子集状态
# state[doc] 按文档隔离），顺带免去重复解析同一字体文件的开销。
_font_names_by_path: dict = {}
_font_failed_paths: set = set()


def _register_font_once(path: str, log: Callable[[str], None]) -> Optional[str]:
    """把字体文件注册进 reportlab 全局表，名字按路径进程级固定（失败缓存同样进程级）"""
    name = _font_names_by_path.get(path)
    if name:
        return name
    if path in _font_failed_paths:
        return None
    name = 'Ft%d' % len(_font_names_by_path)
    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
    except Exception as e:
        log(f"警告：字体 {os.path.basename(path)} 注册失败（{e}），相关文字将使用黑体渲染")
        _font_failed_paths.add(path)
        return None
    _font_names_by_path[path] = name
    return name


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
# 读取引擎（pdfminer.six）：解析页面内容为字符 / 矢量 / 图片记录
# ---------------------------------------------------------------------------

def _color_to_rgb(color) -> Tuple[float, float, float]:
    """
    把 pdfminer 的颜色对象（灰度标量 / RGB / CMYK 元组，或 None）转成 RGB 三元组。
    无法理解的颜色空间按黑色处理。
    """
    if color is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color, (int, float)):
        v = min(max(float(color), 0.0), 1.0)
        return (v, v, v)
    if isinstance(color, (tuple, list)):
        try:
            comps = [min(max(float(v), 0.0), 1.0) for v in color]
        except (TypeError, ValueError):
            return (0.0, 0.0, 0.0)
        if len(comps) == 1:
            return (comps[0], comps[0], comps[0])
        if len(comps) == 3:
            return (comps[0], comps[1], comps[2])
        if len(comps) == 4:  # CMYK
            c, m, y, k = comps
            return (1.0 - min(1.0, c + k), 1.0 - min(1.0, m + k),
                    1.0 - min(1.0, y + k))
    return (0.0, 0.0, 0.0)


def _char_is_bold(font_name: Optional[str]) -> bool:
    """根据字符的字体名判断是否加粗（名称含 Bold/Black/Heavy）"""
    name = (font_name or '').lower()
    return ('bold' in name) or ('black' in name) or ('heavy' in name)


def _inverse_matrix(m) -> Optional[Tuple[float, float, float, float, float, float]]:
    """仿射矩阵求逆；退化（行列式为 0）返回 None"""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia = d / det
    ib = -b / det
    ic = -c / det
    id_ = a / det
    return (ia, ib, ic, id_,
            -(ia * e + ic * f), -(ib * e + id_ * f))


def _norm_bbox(points) -> Tuple[float, float, float, float]:
    """把若干坐标点归一化为 (x0, y0, x1, y1) 包围盒"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _walk_layout(item, content: dict, transform) -> None:
    """
    深度优先遍历 pdfminer 的布局树，收集四类记录：
    字符 {text,x,y,adv,font,size,color,bold,rot}、线段、矩形、图片。

    pdfminer 的坐标在“设备空间”（页面旋转 /Rotate 与媒体框平移已应用）。
    transform 为 None 时原样记录；为逆矩阵时把坐标变换回用户空间——
    旋转页的文字经逆变换后恢复水平方向，与未旋转页同一路径处理。
    旋转（非直立）字符与无法映射到 Unicode 的字符 (cid:xxx) 计数后跳过。
    """
    import math
    if isinstance(item, LTChar):
        content['char_total'] += 1
        text = item.get_text() or ''
        if not text or text.startswith('(cid:'):
            content['skipped_cid'] += 1
            return
        if any(ord(c) < 32 for c in text):
            content['skipped_cid'] += 1
            return
        m = item.matrix
        if transform is not None:
            m = mult_matrix(m, transform)
            # 用容差而非精确相等：个别生成器会写出 1e-10 量级的非零分量，
            # 精确比较会把整页字符误判为“旋转”而丢弃
            upright = (abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9 and m[0] * m[3] > 0)
        else:
            upright = item.upright
        if not upright:
            content['skipped_rotated'] += 1
            return
        # 书写方向角（atan2 归一到 [0,360)，±5° 视为水平）：pdfminer 的
        # upright 判定对 89.9° 这类“接近垂直”的文字仍返回 True，需要
        # 靠角度把它们分流到侧排聚合路径，否则会按水平散字混排且
        # 上下文顺序颠倒
        rot = math.degrees(math.atan2(m[1], m[0])) % 360.0
        if rot >= 355.0 or rot < 5.0:
            rot = 0.0
        content['chars'].append({
            'text': text,
            'x': float(m[4]),
            'y': float(m[5]),
            'adv': float(item.adv or 0.0),
            'font': item.fontname or '',
            'size': float(item.size or 0.0),
            'color': _color_to_rgb(item.graphicstate.ncolor
                                   if item.graphicstate is not None else None),
            'bold': _char_is_bold(item.fontname),
            'rot': rot,
        })
        return
    if isinstance(item, LTImage):
        corners = [(item.bbox[0], item.bbox[1]), (item.bbox[2], item.bbox[3])]
        if transform is not None:
            corners = [apply_matrix_pt(transform, p) for p in corners]
        bbox = _norm_bbox(corners)
        if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            content['images'].append({'name': item.name, 'bbox': bbox, 'item': item})
        return
    if isinstance(item, LTRect):
        corners = [(item.bbox[0], item.bbox[1]), (item.bbox[2], item.bbox[3])]
        if transform is not None:
            corners = [apply_matrix_pt(transform, p) for p in corners]
        content['rects'].append({
            'bbox': _norm_bbox(corners),
            'stroke': bool(item.stroke),
            'fill': bool(item.fill),
            'width': float(item.linewidth or 0.0),
            'scolor': _color_to_rgb(item.stroking_color),
            'ncolor': _color_to_rgb(item.non_stroking_color),
        })
        return
    if isinstance(item, LTCurve):  # 含 LTLine；仅重画“直线段”路径
        if not item.stroke or len(item.pts) < 2:
            return
        ops = [seg[0] for seg in (item.original_path or [])]
        if not ops or any(op not in ('m', 'l', 'h') for op in ops):
            return  # 贝塞尔曲线无法按线段重画，跳过
        color = _color_to_rgb(item.stroking_color)
        width = float(item.linewidth or 0.0)
        pts = item.pts
        if transform is not None:
            pts = [apply_matrix_pt(transform, p) for p in pts]
        for p, q in zip(pts, pts[1:]):
            content['lines'].append((float(p[0]), float(p[1]),
                                     float(q[0]), float(q[1]), width, color))
        return
    if isinstance(item, LTContainer):
        for child in item:
            _walk_layout(child, content, transform)


def _empty_page_content() -> dict:
    return {'chars': [], 'lines': [], 'rects': [], 'images': [],
            'char_total': 0, 'skipped_cid': 0, 'skipped_rotated': 0}


def _collect_page_content(ltpage, transform, log: Optional[Callable[[str], None]] = None) -> dict:
    content = _empty_page_content()
    if ltpage is not None:
        try:
            _walk_layout(ltpage, content, transform)
        except Exception as e:
            # 不能静默吞掉：布局遍历中的代码错误若被掩盖，整页按空白输出
            # 且无从定位原因
            if log:
                log(f"  ⚠ 页面布局遍历失败（{type(e).__name__}: {e}），该页仅保留已收集的部分内容")
    return content


# ---------------------------------------------------------------------------
# 字符级文本聚合（避免提取器在字距较大时插入的“推断空格”破坏单词）
# ---------------------------------------------------------------------------

def _page_is_vertical(chars: List[dict]) -> bool:
    """
    依据字符坐标分布判断版面方向：y 方向聚类数远多于 x 方向聚类数（且
    行数足够）时为竖排版面。

    竖排的中日文正文（方正书版/InDesign/Word 等）字形直立、沿列自上而下
    排布，与“字形旋转 90° 的侧排文字”是两种不同的实现；前者的字符旋转角
    为 0，只能靠坐标分布识别。用聚类计数而非去重计数，避免基线/列位的
    微小抖动把同一行/列拆成多个。字符太少的页面不做判定（保持横排处理；
    单列少量字符按行读取时 y 降序恰好也是正确阅读序）。

    仅凭聚类计数会把窄横向版面（每行一两个字的小表格、表头等）误判为
    竖排：真竖排的列内相邻字符步进约 1em（字身框），而窄横向版面按 x
    聚出的“列”内纵向步进是行距（通常 >= 1.5em），据此再过滤一道。
    """
    if len(chars) < 12:
        return False
    sizes = sorted(c['size'] if c['size'] and c['size'] > 0 else 10.0
                   for c in chars)
    tol = max(sizes[len(sizes) // 2] * 0.35, 1.5)

    def _cluster_count(values) -> int:
        count, anchor = 0, None
        for v in sorted(values):
            if anchor is None or v - anchor > tol:
                count += 1
                anchor = v
        return count

    def _ch_size(c: dict) -> float:
        return c['size'] if c['size'] and c['size'] > 0 else 10.0

    n_rows = _cluster_count(c['y'] for c in chars)
    n_cols = _cluster_count(c['x'] for c in chars)
    if not (n_rows >= 4 and n_rows > n_cols * 2):
        return False

    # 列内步进校验：按 x 贪心聚列，取列内相邻字符纵向步进（以字号归一）
    # 的中位数，须落在约 1em 附近才算真竖排。行距极紧（约 1.4em 以内）
    # 的窄表格仍可能通过，属可接受的残余误判。
    cols: List[list] = []  # [锚点列 x, 字符列表]
    for c in sorted(chars, key=lambda c: c['x']):
        if cols and abs(c['x'] - cols[-1][0]) <= tol:
            cols[-1][1].append(c)
        else:
            cols.append([c['x'], [c]])
    gaps: List[float] = []
    for _ax, col in cols:
        col.sort(key=lambda c: -c['y'])
        for a, b in zip(col, col[1:]):
            dy = a['y'] - b['y']
            if dy > 0:
                gaps.append(dy / max(_ch_size(a), _ch_size(b)))
    if not gaps:
        return False
    gaps.sort()
    return 0.75 <= gaps[len(gaps) // 2] <= 1.4


def _char_runs_from_page(content: dict, page_index: int,
                         log: Callable[[str], None]) -> List[dict]:
    """
    用字符级数据聚合出绘制单元。

    行级提取器（pdfminer 的 LTTextLine / pdf-oxide 的 extract_spans）生成的文本
    会在字符间隙较大时插入“推断空格”（原文中并不存在空格字符），直接重绘会把
    单词拆开（如 Work -> W ork）。改为从字符的精确坐标出发：同风格且间隙小于
    阈值的字符合并为一个单元，间隙大的各自按原坐标定位——既不引入多余空格，
    也保留原文版式（含两端对齐的拉伸）。

    版面方向检测为竖排时（直立字形沿列排布，见 _page_is_vertical），改按
    “列从右到左、列内自上而下”聚合：竖排按行横读会把各列同一高度的字交织成
    乱序文本，一对多转换的词上下文（如“於是→于是”、“彷彿→仿佛”）完全失效；
    按列读取后上下文与阅读序一致。竖排单元带 col（列号，自右而左递增）与
    ys（逐字 y 坐标）字段，供连续性判断与逐字定位绘制使用。

    返回单元列表：{text, x, y, w, font, size, color, bold, xs, col, ys,
    line, lx0, rot}，坐标为 PDF 用户空间；竖排单元的 y 为首字（最上字）
    基线，ys 与 text 等长；水平单元带 line（行号）与 lx0（行首 x）供连续
    性判断；rot 为书写方向角（侧排单元非 0，文本已反转为阅读序）。
    """
    chars = content['chars']
    if content['skipped_rotated']:
        log(f"  ⚠ 第{page_index + 1}页有 {content['skipped_rotated']} 个旋转字符无法按水平文本重排，已跳过")
    if content['skipped_cid']:
        log(f"  ⚠ 第{page_index + 1}页有 {content['skipped_cid']} 个字符无法映射到 Unicode，已跳过")
    if not chars:
        return []

    def _sideways(c: dict) -> bool:
        r = c.get('rot') or 0.0
        return 45.0 <= r < 135.0 or 225.0 <= r < 315.0

    sideways = [c for c in chars if _sideways(c)]
    chars = [c for c in chars if not _sideways(c)]
    if not chars and not sideways:
        return []

    vertical = _page_is_vertical(chars)
    # ordered 的元素为 (字符, 竖排列号或 None, 水平行号或 None, 行首 x 或 None)
    ordered: List[Tuple[dict, Optional[int], Optional[int], Optional[float]]] = []
    if vertical:
        # 竖排版面：直立字符按 x 聚成列，阅读序为“列从右到左、列内自上而下”
        cols: List[list] = []  # [锚点列 x, 字符列表]，x 升序贪心聚类
        for ch in sorted(chars, key=lambda c: c['x']):
            size0 = ch['size'] if ch['size'] and ch['size'] > 0 else 10.0
            if cols and abs(ch['x'] - cols[-1][0]) <= max(size0 * 0.35, 1.5):
                cols[-1][1].append(ch)
            else:
                cols.append([ch['x'], [ch]])
        cols.sort(key=lambda col: -col[0])
        for idx, (_ax, col_chars) in enumerate(cols):
            col_chars.sort(key=lambda c: -c['y'])
            ordered.extend((ch, idx, None, None) for ch in col_chars)
    else:
        # 横排版面：先按基线锚点容差聚成行，行内按 x、行间自上而下。
        # 不能用 round(y,1) 分桶排序：0.1 的取整边界会把基线有微小抖动
        # 的同行字符拆进相邻两个“桶”，与行内合并阈值（约 0.35em）不一致，
        # 造成行内乱序、发给转换器的词上下文被打乱
        horiz = sorted(chars, key=lambda c: (-c['y'], c['x']))
        h_lines: List[list] = []  # [锚点基线, 字符列表]
        for ch in horiz:
            size0 = ch['size'] if ch['size'] and ch['size'] > 0 else 10.0
            if h_lines and abs(h_lines[-1][0] - ch['y']) <= max(size0 * 0.35, 1.5):
                h_lines[-1][1].append(ch)
            else:
                h_lines.append([ch['y'], [ch]])
        h_lines.sort(key=lambda entry: -max(c['y'] for c in entry[1]))
        for line_id, (_anchor, line_chars) in enumerate(h_lines):
            line_chars.sort(key=lambda c: c['x'])
            lx0 = line_chars[0]['x']
            ordered.extend((ch, None, line_id, lx0) for ch in line_chars)

    # 侧排字符附在最后：按 x 聚列、列内自上而下。列号加 9000 偏移避免
    # 与竖排列号冲突；列通道的合并逻辑（同列且纵向步进正常）同样适用。
    # pdfminer 对 89.9° 这类近垂直文字的 upright 判定为 True，不分流的
    # 话它们会按水平散字混排，且发给转换器的上下文顺序颠倒
    if sideways:
        s_cols: List[list] = []  # [锚点列 x, 字符列表]，x 升序贪心聚类
        for ch in sorted(sideways, key=lambda c: c['x']):
            size0 = ch['size'] if ch['size'] and ch['size'] > 0 else 10.0
            if s_cols and abs(ch['x'] - s_cols[-1][0]) <= max(size0 * 0.35, 1.5):
                s_cols[-1][1].append(ch)
            else:
                s_cols.append([ch['x'], [ch]])
        s_cols.sort(key=lambda col: -col[0])
        for idx, (_ax, col_chars) in enumerate(s_cols):
            col_chars.sort(key=lambda c: -c['y'])
            ordered.extend((ch, 9000 + idx, None, None) for ch in col_chars)

    runs: List[dict] = []
    cur = None
    tail = None  # 当前单元的最后一个字符

    for ch, col_idx, line_id, lx0 in ordered:
        char = ch['text']
        size = ch['size'] if ch['size'] and ch['size'] > 0 else 10.0
        bold = ch['bold']
        style = (ch['font'], round(size, 1), tuple(ch['color']), bold)

        merged = False
        if cur is not None and cur['style'] == style:
            if col_idx is not None:
                # 竖排列内：同列（x 相近）且纵向步进约 1em 即合并
                same_col = abs(ch['x'] - tail['x']) <= max(size * 0.35, 1.5)
                dy = tail['y'] - ch['y']
                if same_col and 0.0 < dy <= size * 1.6:
                    merged = True
            else:
                same_line = abs(ch['y'] - tail['y']) <= max(size * 0.35, 1.5)
                gap = ch['x'] - (tail['x'] + (tail['adv'] or 0.0))
                # 西文相邻用较紧的阈值（真实空格约 0.25em），中西文及中文之间放宽，
                # 尽量让词语留在同一单元以保证词汇级转换的上下文
                latin_pair = (_LATIN_RUN_RE.fullmatch(tail['text'] or '') is not None
                              and _LATIN_RUN_RE.fullmatch(char) is not None)
                threshold = size * (0.20 if latin_pair else 0.30)
                if same_line and gap <= threshold:
                    merged = True

        if merged:
            cur['text'] += char
            cur['xs'].append(ch['x'])
            if col_idx is not None:
                cur['ys'].append(ch['y'])
            # 取 max 防止重叠字符（gap 为负）的合并使宽度收缩，
            # 宽度失真会破坏连续性判定与重排
            cur['w'] = max(cur['w'],
                           (ch['x'] + (ch['adv'] or 0.0)) - cur['x'])
            tail = ch
        else:
            cur = {
                'text': char,
                'x': ch['x'],
                'y': ch['y'],
                'w': ch['adv'] or 0.0,
                'font': ch['font'],
                'size': size,
                'color': ch['color'],
                'bold': bold,
                'style': style,
                'xs': [ch['x']],
                'col': col_idx,
                'line': line_id,   # 水平行号（竖排为 None），供连续性判断
                'lx0': lx0,        # 所在行行首 x（竖排为 None）
                'rot': ch.get('rot') or 0.0,  # 书写方向角（侧排单元非 0）
            }
            if col_idx is not None:
                cur['ys'] = [ch['y']]
            runs.append(cur)
            tail = ch

    rotated_runs = 0
    for r in runs:
        rot = r.get('rot') or 0.0
        if rot:
            rotated_runs += 1
            # 逆时针侧排（约90°，阅读方向自下而上）与倒排（约180°）的
            # 阅读序与扫描序相反：区间判断（角度有微小抖动，如 89.9°），
            # 反转后 text 与 xs/ys 仍逐字配对，转换拿到正序上下文
            if 85.0 <= rot <= 95.0 or 175.0 <= rot <= 185.0:
                r['text'] = r['text'][::-1]
                r['xs'].reverse()
                if r.get('ys'):
                    r['ys'].reverse()
        r.pop('style', None)
    if rotated_runs:
        log(f"  ℹ 第{page_index + 1}页有 {rotated_runs} 段侧排文字，已按阅读序转换并按原位横排绘制（字形方向可能与原文不同）")
    return runs


# ---------------------------------------------------------------------------
# OpenCC 批量转换（按页直拼整体转换，按位置索引切回）
# ---------------------------------------------------------------------------


def _runs_contiguity_vertical(runs: List[dict]) -> List[bool]:
    """
    竖排页的相邻单元连续性判断（供 _convert_page_texts 决定直接拼接还是插入
    换行分隔）：同一列且与前一单元最后一个字的纵向间距不超过约 1.6em 为连续
    （列内风格切换拆开的单元之间可能正是一个词）；跨列或间距异常则插入换行，
    避免列边界拼出原文不存在的“词”触发错误短语转换。
    """
    flags: List[bool] = []
    prev = None
    for run in runs:
        flag = False
        if (prev is not None and run.get('col') is not None
                and run['col'] == prev.get('col')
                and not run.get('rot') and not prev.get('rot')):
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            prev_bottom = (prev.get('ys') or [prev['y']])[-1]
            dy = prev_bottom - run['y']
            flag = 0.0 < dy <= size * 1.6
        flags.append(flag)
        prev = run
    return flags


def _runs_contiguity_horizontal(runs: List[dict]) -> List[bool]:
    """
    横排页的相邻单元连续性判断：

    - 同一行且水平间距不超过约 1em（含轻微重叠）：连续——字体切换、两端
      对齐拉伸拆开的单元之间可能正是一个词（“电”“复”分属宋体与 Times）；
    - 相邻两行（行距 0.75em~2em，即正常行距）且两行行首 x 对齐（相差
      不超过约 2.5em，同一段落的行首对齐或首行缩进）：连续——行尾行首
      可能拆开一个词（“重”“复”）；
    - 其余（页眉/正文/脚注、分栏、居中标题、表格单元间的大间距，以及
      侧排单元）：不连续，转换时用换行分隔，避免边界处拼出原文不存在
      的“词”触发错误短语转换。
    """
    flags: List[bool] = []
    prev = None
    for run in runs:
        flag = False
        if (prev is not None and run.get('col') is None and prev.get('col') is None
                and not run.get('rot') and not prev.get('rot')
                and run.get('line') is not None and run['line'] == prev.get('line')):
            # 同一行：水平间距正常即连续
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            gap = run['x'] - (prev['x'] + prev.get('w', 0.0))
            flag = -0.3 * size <= gap <= size
        elif (prev is not None and run.get('col') is None and prev.get('col') is None
                and not run.get('rot') and not prev.get('rot')
                and run.get('line') is not None and prev.get('line') is not None
                and run['line'] == prev['line'] + 1):
            # 相邻两行：行距正常且行首对齐才连续
            size = max(run.get('size', 10.0), prev.get('size', 10.0))
            dy = abs(run['y'] - prev['y'])
            dx_first = abs(run.get('lx0', run['x']) - prev.get('lx0', prev['x']))
            flag = 0.75 * size <= dy <= 2.0 * size and dx_first <= 2.5 * size
        flags.append(flag)
        prev = run
    return flags


def _convert_page_texts(cc, texts: List[str],
                        page_no: int, log: Callable[[str], None],
                        contiguous: Optional[List[bool]] = None) -> List[str]:
    """
    将一页的所有绘制单元文本按版面连续性拼成若干段整体转换，再按位置索引
    切回各单元（移植自 doc_converter 的段落级上下文方案）。

    PDF 字符级聚合出的单元常在词中间被切断（字体切换、换行、大间距，如
    分属行尾行首的“重复”、分属宋体与 Times 的“电复”），逐单元转换会让
    歧义字失去词上下文（“复”→“復”而非“複”）。连续单元直接拼接后整段
    一次转换，跨单元的词也能正确消歧；不连续的单元之间插入换行分隔
    （OpenCC 不转换换行，也不跨它分词），避免页眉/脚注/竖排列边界等无关
    区域拼出原文不存在的词。拆回不依赖分隔符内容——单元边界就是拼接时的
    字符偏移：

    - 转换前后长度一致（繁简映射绝大多数 1:1）：按偏移切割，各单元原文
      与译文等长，可走逐字定位绘制路径，版式与原文一致；
    - 长度不一致（罕见，如 s2twp 的“内存”→“記憶體”）：用前缀转换定位
      各单元边界在译文中的落点后切割，长度变化的单元自动落入整段重排
      路径，其余单元仍走逐字定位。

    contiguous 由调用方按版面方向传入（竖排 _runs_contiguity_vertical /
    横排 _runs_contiguity_horizontal）；为 None 时全部直接拼接（仅测试用）。
    """
    if not texts:
        return []
    parts: List[str] = []
    positions = []
    offset = 0
    for i, t in enumerate(texts):
        if contiguous is not None and i and not contiguous[i]:
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
# 文本绘制（reportlab：批处理文本对象 + 中英分字体 + 宽度自适应）
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


class _TextBatch(object):
    """
    把一串“单字/片段级”的绘制请求合并进一个 PDF 文本对象（BT..ET），
    仅在字体、字号或颜色变化时发出状态指令，减少输出内容流的体积。
    """
    __slots__ = ('canvas', 't', 'font', 'size', 'color')

    def __init__(self, canvas):
        self.canvas = canvas
        self.t = None
        self.font = None
        self.size = 0.0
        self.color = None

    def put(self, font_name: str, size: float, color, x: float, y: float, s: str) -> None:
        if not s:
            return
        if self.t is None:
            self.t = self.canvas.beginText()
            self.font = None
        if font_name != self.font or size != self.size:
            self.t.setFont(font_name, size)
            self.font = font_name
            self.size = size
        if color != self.color:
            self.t.setFillColorRGB(color[0], color[1], color[2])
            self.color = color
        self.t.setTextOrigin(x, y)
        self.t.textOut(s)

    def flush(self) -> None:
        if self.t is not None:
            self.canvas.drawText(self.t)
            self.t = None


def _draw_text(canvas, text: str, x: float, y: float, run_w: float,
               font_name: Optional[str], size: float, color, is_bold: bool,
               resolve_font: Callable[[str], Optional[dict]],
               char_xs: Optional[List[float]] = None,
               char_ys: Optional[List[float]] = None,
               vert: bool = False,
               highlight_mask: Optional[List[bool]] = None) -> bool:
    """
    按脚本分组绘制一段已转换的文本到指定位置。

    文本由 _convert_page_texts 按页批量转换后传入，本函数只负责排版绘制：

    - 按原文风格类别（黑体/宋体/楷体/仿宋）匹配输出字体，保留字体区分度
    - 西文/数字按原文风格选择衬线/非衬线拉丁字体；中文语境下的半角标点
      跟随中文字体（源文档里它们由中文字库渲染，落到西文字体会明显变小）
    - 优先逐字按原始坐标绘制（char_xs，可选 char_ys）：完整保留两端对齐的
      字距拉伸与方正书版“标点半角”版式——整段按字体自然宽度推进会让段内
      文字逐渐左漂，在段尾与后一单元之间留下空隙。char_xs 对应原文文本，
      长度与转换后文本一致时可用；OpenCC 短语转换可能改变字符数（如 s2twp
      的“内存”→“記憶體”），此时回退到整段重排
    - 竖排单元（char_ys 逐字给出各字基线，char_xs 近似恒定）同样走逐字
      定位：各字按原 (x, y) 落位，保持竖排版式；“到下一字的水平距离”在
      竖排下不成立（高亮底纹与开标点定位改用字宽测量兜底）
    - highlight_mask 与 text 等长，为 True 的字先铺黄色底纹再绘制文字。
      reportlab 中矩形按调用顺序立即写入内容流，批处理文本对象在
      flush 时才写出，底纹自然位于文字下层
    - 整段重排路径（转换改变字符数）中，中文标点独立成段：
      输出中文字体的标点字身框是全宽的，而源文档标点推进宽度常只有一半，
      按自然宽度推进会触发缩字号、把标点明显变小。标点单独定位后只压缩
      其推进宽度（字形保持原字号），开标点再左移使墨迹贴住后字；标点压
      到半宽仍放不下时才回退为按比例缩小字号

    resolve_font 负责把逻辑字体键（sans/serif/kai/fangsong/latin_*）解析为
    惰性注册后的 {'name': 注册名, 'path': 字体文件路径}（失败时回退黑体）。
    返回是否实际绘制了内容。
    """
    if not text:
        return False

    size = size if size and size > 0 else 10.0
    # 单元宽度缺失（如 adv 为 0 的字符、负 gap 合并的收缩宽度）时按字数
    # 估算，避免重排路径因 span_w 为 0 而把字号缩到下限
    span_w = run_w if run_w and run_w > 0 else _estimate_width(text, size)
    color = color or (0.0, 0.0, 0.0)

    category = _classify_font_category(font_name)
    # 粗体仅在有同族粗体文件时生效，避免为保字重损失字体风格
    if is_bold and category in ('sans', 'serif'):
        key = category + '_bold'
    else:
        key = category
    cjk_entry = resolve_font(key) or resolve_font('sans')
    # 楷体/仿宋的西文部分按衬线处理（与宋体一致）
    if category in ('serif', 'kai', 'fangsong'):
        lkey = 'latin_serif_bold' if is_bold else 'latin_serif'
    else:
        lkey = 'latin_sans_bold' if is_bold else 'latin_sans'
    latin_entry = resolve_font(lkey) or resolve_font('sans')
    if cjk_entry is None or latin_entry is None:
        return False  # 连回退字体都无法注册，放弃该单元

    # --- 逐字定位路径：每个字落在原文坐标上 ---
    # char_xs/char_ys 与原文等长；转换后长度一致（OpenCC 多数映射为 1:1）时可用
    if char_xs and len(char_xs) == len(text):
        n = len(char_xs)
        has_ys = bool(char_ys) and len(char_ys) == n
        # 书写轴判定：常规水平文本的 char_xs 单调递增；竖排单元的 char_xs
        # 近似恒定、逐字位置由 char_ys 区分——“到下一字的水平距离”与按
        # span_w 推算的末字字格在竖排下都不成立（会得到 0 附近的错误值），
        # 高亮底纹与开标点定位改走测量兜底（兜底宽度≈字宽，恰好覆盖方块字）。
        # 判定须要求至少两个字符且 y 确有变化：半宽标点常独成单字单元
        # （专用的标点字体与正文字体不同），单字符的 x 跨度恒为 0，
        # 据旧判定会被误认为竖排而把可用 cell 归零，开标点左移修正失效
        stacked = (has_ys and n > 1
                   and (max(char_xs) - min(char_xs)) < size * 0.5
                   and (max(char_ys) - min(char_ys)) > size * 0.5)

        def _cell(i: int) -> float:
            if stacked:
                return 0.0
            if i + 1 < n:
                return char_xs[i + 1] - char_xs[i]
            return span_w - (char_xs[i] - char_xs[0])

        batch = _TextBatch(canvas)
        # 修改字高亮：矩形立即写入内容流，批处理文本对象在 flush 时才
        # 写出，文字位于底纹上层
        if highlight_mask is not None:
            canvas.setFillColorRGB(_HIGHLIGHT_RGB[0], _HIGHLIGHT_RGB[1],
                                   _HIGHLIGHT_RGB[2])
            dy, dh = _highlight_rect_metrics(size)
            for i, marked in enumerate(highlight_mask[:n]):
                if not marked:
                    continue
                cx = char_xs[i]
                cy = char_ys[i] if has_ys else y
                cell = _cell(i)
                if cell <= 0:
                    cell = _measure(text[i], cjk_entry['path'], size) or size
                canvas.rect(cx, cy + dy, cell, dh, stroke=0, fill=1)
        for i, (ch, cx) in enumerate(zip(text, char_xs)):
            draw_x = cx
            cy = char_ys[i] if has_ys else y
            if not vert and not stacked and _OPEN_PUNCT_RE.fullmatch(ch):
                # 开标点墨迹靠字身框右侧：源单元（到下一字的距离）明显比
                # 全宽窄（0.9 余量排除测量浮点噪声，源本就是全宽标点的
                # 不动）时，左移使墨迹右缘对齐单元右界，贴住后面的字
                # （竖排下该横向几何不成立，保持原位绘制）
                cell = _cell(i)
                natural = _measure(ch, cjk_entry['path'], size)
                if cell > 0 and natural and cell < natural * 0.9:
                    draw_x = cx + cell - _OPEN_PUNCT_INK_RIGHT * size
            if (_LATIN_RUN_RE.fullmatch(ch) and _LATIN_ALNUM_RE.search(ch)):
                entry = latin_entry
            else:
                entry = cjk_entry
            batch.put(entry['name'], size, color, draw_x, cy, ch)
        batch.flush()
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
            # 缩放后重测也可能失败（None），统一走估算兜底，避免 None*0.5 崩溃
            widths = [_measure_or_estimate(seg[0], seg[2], size) for seg in segments]
            widths = [w * 0.5 if seg[3] else w
                      for w, seg in zip(widths, segments)]

    # 修改字高亮：整段重排路径中按段内自然字宽定位各字铺底纹
    # （段宽已压缩/缩放时按比例归一，保证底纹与实际绘制位置一致）
    if highlight_mask is not None:
        canvas.setFillColorRGB(_HIGHLIGHT_RGB[0], _HIGHLIGHT_RGB[1],
                               _HIGHLIGHT_RGB[2])
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
                    canvas.rect(cx, y + dy, w, dh, stroke=0, fill=1)
                cx += w
            base += len(seg[0])
            if width:
                hx += width

    batch = _TextBatch(canvas)
    for seg, width, natural in zip(segments, widths, naturals):
        draw_x = x
        if (seg[3] and width and natural and width < natural
                and _OPEN_PUNCT_RE.fullmatch(seg[0])):
            # 开标点：墨迹右缘对齐压缩后单元的右界（贴住后面的字）
            draw_x = x + width - _OPEN_PUNCT_INK_RIGHT * size
        batch.put(seg[1], size, color, draw_x, y, seg[0])
        if width:
            x += width
    batch.flush()
    return True


# ---------------------------------------------------------------------------
# 页面元素重建辅助（图片 / 矢量线条与矩形）
# ---------------------------------------------------------------------------

def _draw_lines(canvas, lines: list, ox: float, oy: float) -> None:
    """按矢量线条记录重画线段（表格边框、下划线等）"""
    for x1, y1, x2, y2, width, color in lines:
        canvas.setStrokeColorRGB(color[0], color[1], color[2])
        canvas.setLineWidth(width or 1.0)
        canvas.line(x1 - ox, y1 - oy, x2 - ox, y2 - oy)


def _draw_rects(canvas, rects: list, ox: float, oy: float) -> None:
    """重画矩形（底色块、边框等）；填充与描边分别按原状态绘制"""
    for rect in rects:
        x0, y0, x1, y1 = rect['bbox']
        x, y = x0 - ox, y0 - oy
        w, h = x1 - x0, y1 - y0
        if rect['fill']:
            fill = rect['ncolor']
            canvas.setFillColorRGB(fill[0], fill[1], fill[2])
            canvas.rect(x, y, w, h, stroke=0, fill=1)
        if rect['stroke']:
            stroke = rect['scolor']
            canvas.setStrokeColorRGB(stroke[0], stroke[1], stroke[2])
            canvas.setLineWidth(rect['width'] or 1.0)
            canvas.rect(x, y, w, h, stroke=1, fill=0)


# pypdf 的图片名带扩展名（如 Im0.png），pdfminer 的资源名不带（Im0）；
# 但 reportlab 生成的资源名本身含点（FormXob.<hash>），只能按已知图片
# 扩展名剥离，避免把名称里的哈希段误当作扩展名
_IMAGE_NAME_EXTS = ('.png', '.jpg', '.jpeg', '.jp2', '.jpx', '.j2k', '.jb2',
                    '.jbig2', '.bmp', '.gif', '.tif', '.tiff')


def _pypdf_images_for_page(reader, page_index: int) -> dict:
    """
    用 pypdf 取出该页全部图片 {资源名: PIL.Image}。
    失败（加密页、非常规编码）时返回空表，调用方走自行解码路径。
    """
    if reader is None:
        return {}
    try:
        page = reader.pages[page_index]
        result = {}
        for img in page.images:
            name = img.name or ''
            lower = name.lower()
            for ext in _IMAGE_NAME_EXTS:
                if lower.endswith(ext):
                    name = name[:-len(ext)]
                    break
            try:
                result[name] = img.image
            except Exception:
                continue
        return result
    except Exception:
        return {}


def _decode_image(rec: dict, pypdf_images_getter: Callable[[], dict]) -> Optional[object]:
    """
    把一条图片记录解码为 reportlab 的 ImageReader：
    1) JPEG（DCT）直通：原压缩字节原样内嵌，避免有损转码与体积膨胀；
    2) pypdf 按资源名匹配（覆盖 CCITT 传真、LZW、Indexed 调色板等复杂编码）；
    3) 自行解码 JPEG2000 与未压缩位图（经 PIL）。
    解码失败返回 None（调用方记日志跳过该图）。
    """
    item = rec['item']
    try:
        filters = [f[0] for f in item.stream.get_filters()]
        data = item.stream.get_data()
        src_w, src_h = int(item.srcsize[0]), int(item.srcsize[1])
    except Exception:
        return None
    if src_w <= 0 or src_h <= 0 or not data:
        return None
    try:
        if any(f in LITERALS_DCT_DECODE for f in filters):
            return ImageReader(io.BytesIO(data))
        pypdf_images = pypdf_images_getter()
        if pypdf_images:
            pil = pypdf_images.get(rec['name'])
            if pil is not None:
                return ImageReader(pil)
        if filters and filters[-1] in LITERALS_JPX_DECODE:
            im = Image.open(io.BytesIO(data))
            if im.mode not in ('RGB', 'RGBA'):
                im = im.convert('RGB')
            return ImageReader(im)
        cs = item.colorspace or []
        bpc = int(item.bits or 8)
        if bpc == 8 and (LITERAL_DEVICE_RGB in cs or LITERAL_INLINE_DEVICE_RGB in cs):
            im = Image.frombytes('RGB', (src_w, src_h), data)
        elif bpc == 8 and (LITERAL_DEVICE_GRAY in cs or LITERAL_INLINE_DEVICE_GRAY in cs):
            im = Image.frombytes('L', (src_w, src_h), data)
        elif bpc == 1:
            im = Image.frombytes('1', (src_w, src_h), data)
        else:
            return None
        return ImageReader(im)
    except Exception:
        return None


def _draw_images(canvas, content: dict, pypdf_images_getter: Callable[[], dict],
                 ox: float, oy: float, page_no: int,
                 log: Callable[[str], None]) -> int:
    """将页面上的图片按原位置嵌入新文档，返回成功绘制的数量"""
    drawn = 0
    for rec in content['images']:
        reader = _decode_image(rec, pypdf_images_getter)
        if reader is None:
            log(f"  ⚠ 第{page_no}页图片 {rec['name']} 解码失败，未保留")
            continue
        x0, y0, x1, y1 = rec['bbox']
        try:
            canvas.drawImage(reader, x0 - ox, y0 - oy, x1 - x0, y1 - y0,
                             mask='auto')
            drawn += 1
        except Exception as e:
            log(f"  ⚠ 第{page_no}页图片 {rec['name']} 绘制失败: {e}")
    return drawn


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
    - 无文本层的扫描页按原页面图片保留（纯 Python 引擎无法整页栅格化，
      改为把页面上的原图重新嵌入，扫描页通常就是整页一张图）

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

    # --- 引擎依赖检查 ---
    if _PDFMINER_IMPORT_ERROR or _REPORTLAB_IMPORT_ERROR:
        missing = []
        if _PDFMINER_IMPORT_ERROR:
            missing.append(f"pdfminer.six ({_PDFMINER_IMPORT_ERROR})")
        if _REPORTLAB_IMPORT_ERROR:
            missing.append(f"reportlab ({_REPORTLAB_IMPORT_ERROR})")
        log("错误：缺少 PDF 转换依赖 - " + "；".join(missing))
        return False

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

    # --- 打开 PDF（pdfminer 为主，pypdf 仅作图片解码兜底） ---
    fp = None
    try:
        fp = open(input_path, 'rb')
        parser = PDFParser(fp)
        miner_doc = _MinerDocument(parser)
        pages = list(PDFPage.create_pages(miner_doc))
    except Exception as e:
        # 注意：不要在 except 子句里引用按名字导入的异常类（如 pdfminer 的
        # PDFPasswordIncorrect）——except 子句的名字是异常发生时才求值的，
        # 打包器（Nuitka 等）处理条件导入时可能丢失该名字绑定，导致 NameError
        # 反而把真实异常顶掉。改为按异常类名与消息文本分类。
        msg = str(e)
        ename = type(e).__name__
        if 'Password' in ename or 'password' in msg.lower() or 'encrypt' in msg.lower():
            log("错误：无法读取 PDF 文件 - 该文件已加密，本工具暂不支持带密码的 PDF")
        else:
            log(f"错误：无法读取 PDF 文件 - {ename}: {msg}")
            if 'head' in msg.lower() or 'EOF' in msg or 'format' in msg.lower() \
                    or 'syntax' in msg.lower() or 'xref' in msg.lower():
                log("提示：该文件可能不是有效的 PDF 文件")
        if fp is not None:
            fp.close()
        return False

    pypdf_reader = None
    if PdfReader is not None:
        try:
            pypdf_reader = PdfReader(input_path)
        except Exception:
            pypdf_reader = None

    try:
        total_pages = len(pages)
        if total_pages <= 0:
            log("错误：PDF 中没有任何页面")
            return False

        # --- 预检：全部页面都无字体资源时必为纯扫描件，早退避免逐页解析 ---
        # （字体资源存在但无法提取文本的情况仍由循环末尾的 text_pages 兜底）
        def _page_may_have_text(pg) -> bool:
            try:
                res = pg.resources or {}
                return any(k in res for k in ('Font', '/Font', b'Font', b'/Font'))
            except Exception:
                return True  # 无法判断时不预检，走主路径兜底

        if not any(_page_may_have_text(pg) for pg in pages):
            log("错误：该 PDF 没有可提取的文本层（可能是扫描或纯图片 PDF），无法进行文字转换")
            log("提示：如需转换扫描件，请先使用 OCR 工具识别文字后再尝试")
            return False

        # --- 初始化输出文档 ---
        # 同一字体文件只注册一次（惰性，首次绘制时触发），多个逻辑名共享注册名；
        # 注册名按路径进程级固定（见 _register_font_once 的说明），避免同进程
        # 连续转换多份文件时名字与字体对象错位
        registered_by_path = {}
        failed_paths = set()

        def _load_font(path: str) -> Optional[str]:
            if path in registered_by_path:
                return registered_by_path[path]
            if path in failed_paths:
                return None
            name = _register_font_once(path, log)
            if name is None:
                failed_paths.add(path)
                return None
            registered_by_path[path] = name
            return name

        # sans 必须可用（它是所有类别的最终回退）
        if _load_font(font_paths['sans']) is None:
            log("错误：中文字体注册失败，无法生成中文 PDF")
            return False

        fonts = {key: {'name': None, 'path': path}
                 for key, path in font_paths.items()}
        fonts['sans']['name'] = registered_by_path[font_paths['sans']]

        def resolve_font(key: str) -> Optional[dict]:
            entry = fonts.get(key) or fonts['sans']
            if entry['name'] is None:
                name = _load_font(entry['path'])
                if name is None and entry is not fonts['sans']:
                    entry = fonts['sans']
                    name = entry['name']
                if name is None:
                    return None
                entry['name'] = name
            return entry

        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        if not output_path.lower().endswith('.pdf'):
            output_path += '.pdf'

        canvas = Canvas(output_path, pageCompression=1)
        canvas.setTitle(f"convert_{os.path.splitext(os.path.basename(input_path))[0]}")

        rsrcmgr = PDFResourceManager()
        device = PDFPageAggregator(rsrcmgr, laparams=None)
        interpreter = PDFPageInterpreter(rsrcmgr, device)

        converted_spans = 0
        highlighted_chars = 0
        text_pages = 0
        vertical_pages = 0
        warned_rotation = False

        for page_index, page in enumerate(pages):
            if is_cancelled_callback and is_cancelled_callback():
                log("转换已被取消")
                return False

            log(f"  [{page_index + 1}/{total_pages}] 处理第 {page_index + 1} 页")

            # 裁剪框决定可见区域，输出页面以它为基准
            try:
                cx0, cy0, cx1, cy1 = [float(v) for v in page.cropbox]
            except Exception:
                cx0 = cy0 = cx1 = cy1 = 0.0
            page_w, page_h = cx1 - cx0, cy1 - cy0
            if page_w <= 0 or page_h <= 0:
                try:
                    mb = [float(v) for v in page.mediabox]
                except Exception:
                    mb = [0.0, 0.0, 612.0, 792.0]
                cx0, cy0 = mb[0], mb[1]
                page_w, page_h = mb[2] - mb[0], mb[3] - mb[1]

            rotation = int(page.rotate or 0) % 360
            if rotation and not warned_rotation:
                log(f"  ⚠ 第{page_index + 1}页设置了页面旋转（{rotation}°）：本引擎无法旋转整页输出，"
                    "该页文字将还原为水平方向且页面宽高不互换，输出方向与原文视觉方向不同")
                warned_rotation = True

            # pdfminer 的布局坐标在“设备空间”（旋转与媒体框平移已应用）。
            # 旋转页用 ctm 的逆矩阵把坐标还原到用户空间，让水平文字得以保留；
            # 非旋转页 pdfminer 仅做了媒体框平移，记录设备坐标即可，但绘制
            # 基准需相应改为“设备空间中的裁剪框原点”，避免平移量被重复扣除。
            try:
                mx0, my0, mx1, my1 = [float(v) for v in page.mediabox]
            except Exception:
                mx0, my0, mx1, my1 = 0.0, 0.0, 0.0, 0.0
            if rotation == 90:
                ctm = (0.0, -1.0, 1.0, 0.0, -my0, mx1)
            elif rotation == 180:
                ctm = (-1.0, 0.0, 0.0, -1.0, mx1, my1)
            elif rotation == 270:
                ctm = (0.0, 1.0, -1.0, 0.0, my1, -mx0)
            else:
                ctm = None
            if ctm is None:
                transform = None
                ox, oy = cx0 - mx0, cy0 - my0
            else:
                transform = _inverse_matrix(ctm)
                if transform is None:
                    transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                ox, oy = cx0, cy0

            canvas.setPageSize((page_w, page_h))

            # --- 解析页面内容 ---
            ltpage = None
            try:
                interpreter.process_page(page)
                ltpage = device.get_result()
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页内容解析失败: {e}")
            content = _collect_page_content(ltpage, transform, log)
            has_text = content['char_total'] > 0

            # --- 图片（先画，位于文字下层）；扫描页以此保留原貌 ---
            _pypdf_page_images = []

            def _pypdf_images_lazy() -> dict:
                # 只缓存当前页：pypdf 解出的 PIL Image 体积大（整页扫描图可
                # 达数 MB），跨页常驻缓存会在几百页的文件上把内存吃满
                if not _pypdf_page_images:
                    _pypdf_page_images.append(
                        _pypdf_images_for_page(pypdf_reader, page_index))
                return _pypdf_page_images[0]

            drawn_images = 0
            try:
                drawn_images = _draw_images(canvas, content, _pypdf_images_lazy,
                                            ox, oy, page_index + 1, log)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页图片保留失败: {e}")

            if not has_text:
                # 无文本层的扫描页：图片即整页原貌；图片都解码失败时只能留白
                if drawn_images == 0 and content['images']:
                    log(f"  ⚠ 第{page_index + 1}页扫描图片解码失败，输出为空白页")
                elif not content['images']:
                    log(f"  ⚠ 第{page_index + 1}页没有文本层也没有图片，输出为空白页")
                canvas.showPage()
                continue

            text_pages += 1

            # --- 矢量线条与矩形 ---
            try:
                _draw_lines(canvas, content['lines'], ox, oy)
                _draw_rects(canvas, content['rects'], ox, oy)
            except Exception as e:
                log(f"  ⚠ 第{page_index + 1}页矢量图形保留失败: {e}")

            # --- 文本：字符级聚合成绘制单元，按版面连续性拼接转换后按位置切回重排
            #     （连续拼接保留跨单元的词上下文；字符聚合避免推断空格拆开单词；
            #     竖排页按“列从右到左、列内自上而下”聚合，上下文与阅读序一致） ---
            char_runs = _char_runs_from_page(content, page_index, log)
            vertical = any(r.get('col') is not None for r in char_runs)
            if vertical:
                vertical_pages += 1
            contiguous = (_runs_contiguity_vertical(char_runs) if vertical
                          else _runs_contiguity_horizontal(char_runs))
            converted_texts = _convert_page_texts(
                cc, [run['text'] for run in char_runs], page_index + 1, log, contiguous)
            for run, converted in zip(char_runs, converted_texts):
                mask = (_highlight_mask(run['text'], converted)
                        if highlight_changes else None)
                try:
                    drawn = _draw_text(canvas, converted,
                                       run['x'] - ox, run['y'] - oy, run['w'],
                                       run['font'], run['size'], run['color'],
                                       run['bold'], resolve_font,
                                       char_xs=[v - ox for v in run.get('xs', ())] or None,
                                       char_ys=[v - oy for v in run.get('ys', ())] or None,
                                       vert=run.get('col') is not None,
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

            canvas.showPage()

        # --- 取消检查 ---
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # --- 全部为扫描页：无法转换 ---
        if text_pages == 0:
            log("错误：该 PDF 没有可提取的文本层（可能是扫描或纯图片 PDF），无法进行文字转换")
            log("提示：如需转换扫描件，请先使用 OCR 工具识别文字后再尝试")
            return False
        if text_pages < total_pages:
            log(f"警告：{total_pages - text_pages}/{total_pages} 页没有文本层（扫描页），这些页面已按原图保留")

        # --- 写出文件 ---
        try:
            canvas.save()
        except Exception as e:
            log(f"错误：写出 PDF 文件失败 - {e}")
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
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
        # 转换中的意外异常不再整体捕获压成 False：向上传播由调用方
        # （GUI）弹出错误对话框，避免掩盖真实的故障原因（取消类异常
        # 同样需要传播以中断流程）
        try:
            fp.close()
        except Exception:
            pass
