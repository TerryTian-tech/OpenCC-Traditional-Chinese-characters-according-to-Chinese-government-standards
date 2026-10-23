import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from typing import Callable, Dict, Optional, Set, Tuple, Union
from opencc import OpenCC
from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.formatter import XMLFormatter
import chardet

# ---------------------------------------------------------------------------
# EPUB 结构常量
# ---------------------------------------------------------------------------

#: EPUB 规范要求 mimetype 必须是包内第一个条目且不压缩
EPUB_MIMETYPE_NAME = 'mimetype'
CONTAINER_XML_PATH = 'META-INF/container.xml'

#: 视为“正文文档”的 OPF manifest media-type（对应 ebooklib.ITEM_DOCUMENT）
DOCUMENT_MEDIA_TYPES = frozenset({'application/xhtml+xml', 'text/html'})

#: EPUB2 目录文件的 media-type
NCX_MEDIA_TYPE = 'application/x-dtbncx+xml'

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

#: 递归跳过的标签：其自身及全部子孙内容都不转换
_SKIP_TAGS = {'script', 'style', 'svg', 'math'}


def _convert_dom_text(soup: BeautifulSoup, cc: OpenCC) -> None:
    """
    批量转换 DOM 文本节点：收集所有文本 → 直接拼接整体转换 → 按边界切回。

    - 递归跳过 <script> / <style> / <svg> / <math> 子树（包括其子孙元素）
    - 只处理精确 NavigableString 类型，排除 Comment 等子类
      （否则注释中的中文会被转换并变成可见文本节点）
    - 直接拼接（不插分隔符）使跨节点边界（行内标签、span 等）的词不被
      拆开，对 OpenCC 的词组转换保持完整上下文——分隔符会被 OpenCC
      分词视为词界，起不到保上下文的作用；空白节点一并拼入（转换不改
      空白），避免跳过空白反而拼出不存在的词
    - 长度一致时按原字符位置切回；长度变化时用前缀转换定位各节点边界
      （与 doc_converter/pdf_converter 的 _split_converted_by_prefix 同方案），
      词内的字不会被错配到相邻节点
    """
    nodes = []          # NavigableString 对象
    texts = []          # 对应文本内容

    def walk(parent) -> None:
        for child in parent.children:
            if isinstance(child, Tag):
                if child.name in _SKIP_TAGS:
                    continue
                walk(child)
            elif type(child) is NavigableString:
                nodes.append(child)
                texts.append(str(child))

    walk(soup)

    if not texts:
        return

    full = ''.join(texts)
    converted = cc.convert(full)

    if len(converted) == len(full):
        # 长度一致：按原字符位置切回（绝大多数映射 1:1）
        pos = 0
        for node, t in zip(nodes, texts):
            n = len(t)
            node.replace_with(NavigableString(converted[pos:pos + n]))
            pos += n
        return

    # 长度变化：对每个节点结束位置 p，len(convert(full[:p])) 即该边界在
    # 译文中的落点（由 OpenCC 自身分词决定）；钳制保证边界单调，不丢字、
    # 不重复
    bounds = [0]
    p = 0
    for t in texts:
        p += len(t)
        q = len(cc.convert(full[:p]))
        bounds.append(min(max(q, bounds[-1]), len(converted)))
    for node, (s, e) in zip(nodes, zip(bounds, bounds[1:])):
        node.replace_with(NavigableString(converted[s:e]))


def _detect_encoding_from_bytes(raw_data: bytes, log_callback=None):
    """从原始字节中检测编码，特别处理中文ANSI编码

    :param raw_data: 原始字节数据
    :param log_callback: 日志回调函数
    :return: 检测到的编码名称
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 首先尝试chardet检测
    result = chardet.detect(raw_data)
    encoding = result['encoding']
    confidence = result['confidence']

    log(f"chardet检测结果: {encoding} (置信度: {confidence})")

    # 特别处理GB18030编码
    # 如果检测到GB2312，优先尝试GB18030以确保兼容性
    if encoding == 'GB2312' and confidence < 0.95:
        try:
            raw_data.decode('gb18030', errors='strict')
            log("使用GB18030编码以确保兼容性")
            return 'gb18030'
        except UnicodeDecodeError:
            # 如果GB18030解码失败，回退到检测到的编码
            pass

    # 如果置信度低或者是常见误判情况，尝试中文编码
    if confidence < 0.7 or encoding in ['ISO-8859-1', 'Windows-1252', 'ASCII']:
        # 尝试常见中文编码，优先尝试GB18030
        chinese_encodings = ['GB18030', 'GBK', 'GB2312']
        for enc in chinese_encodings:
            try:
                # 修复：改为全文检测，而不是只检测前1000字节
                decoded = raw_data.decode(enc, errors='strict')
                # 如果包含中文字符，认为可能是正确的编码
                has_chinese = any(
                    '\u4e00' <= char <= '\u9fff'      # CJK 基本区汉字
                    or '\u3400' <= char <= '\u4dbf'    # CJK 扩展A区
                    or '\u3000' <= char <= '\u303f'    # CJK 标点符号
                    or '\uff00' <= char <= '\uffef'    # 全角字符
                    for char in decoded
                )
                if has_chinese:
                    log(f"检测到中文字符，使用编码: {enc}")
                    return enc
            except UnicodeDecodeError:
                continue

        # 如果严格解码没有匹配到中文字符，使用宽松模式再试一次
        # 某些文件可能混有少量非标准字节（如BOM头、控制字符），
        # strict 模式下会抛异常导致整个编码被跳过
        for enc in ['GB18030', 'GBK']:
            try:
                decoded = raw_data.decode(enc, errors='replace')
                has_chinese = any(
                    '\u4e00' <= char <= '\u9fff'
                    or '\u3400' <= char <= '\u4dbf'
                    or '\u3000' <= char <= '\u303f'
                    or '\uff00' <= char <= '\uffef'
                    for char in decoded
                )
                if has_chinese:
                    # 验证：检查是否有被替换的无效字符
                    # 如果文件真的是 GB18030，replace 模式应该很少产生替换
                    replaced_count = decoded.count('\ufffd')
                    if replaced_count == 0:
                        log(f"宽松模式下检测到中文且无替换字符，使用编码: {enc}")
                        return enc
                    else:
                        # 替换字符占比很低时（<0.5%），仍然可能是正确的编码
                        ratio = replaced_count / len(decoded) if decoded else 1
                        if ratio < 0.005:
                            log(f"宽松模式下检测到中文（替换率{ratio:.4%}极低），使用编码: {enc}")
                            return enc
            except Exception:
                continue

    # 如果检测到utf-8但置信度不高，尝试GB18030
    if encoding == 'utf-8' and confidence < 0.8:
        try:
            # 尝试用GB18030解码
            decoded = raw_data.decode('GB18030', errors='strict')
            # 检查是否包含中文字符
            if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                log("检测到GB18030编码的中文字符，使用GB18030编码")
                return 'gb18030'
        except UnicodeDecodeError:
            pass

    # 默认使用检测到的编码，如果是None则使用utf-8
    if not encoding:
        encoding = 'utf-8'

    # 如果是GB2312，优先使用GB18030以确保兼容性
    if encoding.lower() in ['GB2312', 'GBK']:
        log(f"将{encoding}升级为GB18030以确保更好的兼容性")
        return 'gb18030'

    # 如果是big5，优先使用cp950以确保兼容性
    if encoding == 'Big5':
        log(f"将{encoding}升级为cp950以确保更好的兼容性")
        return 'cp950'
    
    # 最终回退：如果chardet检测到的是非中文编码且置信度不高，
    # 强制使用gb18030作为最终回退（中文文件最常见的ANSI编码）
    if encoding.lower() not in ['utf-8', 'UTF-8-SIG', 'GB18030', 'GBK', 'GB2312', 'Big5']:
        if confidence < 0.5:
            log(f"chardet检测到非中文编码'{encoding}'（置信度{confidence:.4%}），回退到GB18030")
            return 'gb18030'

    return encoding


def _decode_bytes(raw: bytes, log_callback: Optional[Callable] = None) -> str:
    """智能编码检测并解码；检测失败时回退 utf-8 宽松模式。"""
    detected_enc = _detect_encoding_from_bytes(raw, log_callback=log_callback)
    try:
        return raw.decode(detected_enc)
    except (UnicodeDecodeError, LookupError):
        return raw.decode('utf-8', errors='replace')


def _restore_bom(raw: bytes, converted: bytes) -> bytes:
    """原内容带 BOM 而转换结果没有时补回 BOM。"""
    if raw[:3] == b'\xef\xbb\xbf' and converted[:3] != b'\xef\xbb\xbf':
        return b'\xef\xbb\xbf' + converted
    return converted


def _has_cjk(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in text)


def _convert_xhtml_bytes(raw: bytes, cc: OpenCC, log_callback: Optional[Callable]) -> bytes:
    """
    转换单个 XHTML 文档的原始字节。
    编码检测 → BeautifulSoup 解析 → 文本节点批量转换 → 序列化回字节。
    """
    content = _decode_bytes(raw, log_callback=log_callback)

    try:
        soup = BeautifulSoup(content, 'xml')  # EPUB XHTML 是 XML 序列化
    except Exception:
        soup = BeautifulSoup(content, 'html.parser')  # 容错：非良构 XML 时回退到 HTML 解析器
    _convert_dom_text(soup, cc)

    # bs4 序列化为字节，保留 XML 声明
    converted_bytes = soup.encode('utf-8', formatter=XMLFormatter())
    return _restore_bom(raw, converted_bytes)


# 只转换目录条目标签（navLabel/text）；docTitle 等 NCX 元数据不转换
_NCXML_LABEL_RE = re.compile(
    r'(<navLabel>\s*<text(?:\s[^>]*)?>)(.*?)(</text>)', re.DOTALL
)


def _convert_ncx_bytes(raw: bytes, cc: OpenCC,
                       log_callback: Optional[Callable] = None) -> bytes:
    """转换单个 NCX（toc.ncx）中的目录条目文字；元数据保持原样。"""
    content = _decode_bytes(raw, log_callback=log_callback)

    def repl(match) -> str:
        label = match.group(2)
        if _has_cjk(label):
            return match.group(1) + cc.convert(label) + match.group(3)
        return match.group(0)

    converted = _NCXML_LABEL_RE.sub(repl, content)
    return _restore_bom(raw, converted.encode('utf-8'))


# ---------------------------------------------------------------------------
# EPUB（ZIP/OPF）读写辅助 —— 标准库实现，替代 ebooklib
# ---------------------------------------------------------------------------

def _local_name(tag: str) -> str:
    """去掉 XML 命名空间，返回本地标签名，如 '{ns}item' -> 'item'"""
    return tag.rsplit('}', 1)[-1]


def _get_document_paths(
    zf: zipfile.ZipFile, log: Callable[[str], None]
) -> Tuple[Set[str], Set[str], bool]:
    """
    解析 container.xml → OPF manifest，找出需要转换的 XHTML 文档
    与 NCX 目录文件在 ZIP 内的完整路径集合。

    返回 (文档路径集合, NCX 路径集合, 是否依据 OPF 解析成功)。
    当 container.xml / OPF 缺失或解析失败时，回退为按扩展名识别。
    """
    names = set(zf.namelist())
    doc_paths: Set[str] = set()
    ncx_paths: Set[str] = set()

    # --- container.xml 定位 OPF ---
    opf_path = None
    try:
        container_root = ET.fromstring(zf.read(CONTAINER_XML_PATH))
        for elem in container_root.iter():
            if _local_name(elem.tag) == 'rootfile' and elem.get('full-path'):
                opf_path = elem.get('full-path')
                break
    except KeyError:
        log(f"警告：包内缺少 {CONTAINER_XML_PATH}")
    except ET.ParseError as e:
        log(f"警告：container.xml 解析失败 - {e}")

    # --- 解析 OPF manifest ---
    if opf_path and opf_path in names:
        try:
            opf_root = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as e:
            log(f"警告：OPF 文件解析失败 - {e}")
            opf_root = None

        if opf_root is not None:
            opf_dir = posixpath.dirname(opf_path)
            lower_map = {n.lower(): n for n in names}  # 条目名大小写兜底
            for elem in opf_root.iter():
                if _local_name(elem.tag) != 'item':
                    continue
                media_type = (elem.get('media-type') or '').strip().lower()
                href = elem.get('href')
                is_doc = media_type in DOCUMENT_MEDIA_TYPES
                is_ncx = media_type == NCX_MEDIA_TYPE or (
                    href or '').lower().split('#')[0].endswith('.ncx')
                if not href or not (is_doc or is_ncx):
                    continue
                # manifest href 是相对 OPF 的（可能 URL 编码的）路径
                rel = unquote(href.split('#', 1)[0])
                full = posixpath.normpath(posixpath.join(opf_dir, rel)) if opf_dir else posixpath.normpath(rel)
                target = doc_paths if is_doc else ncx_paths
                if full in names:
                    target.add(full)
                elif full.lower() in lower_map:
                    target.add(lower_map[full.lower()])
    elif opf_path:
        log(f"警告：OPF 文件不存在于包内 - {opf_path}")

    if doc_paths:
        return doc_paths, ncx_paths, True

    # 兜底：无法从 OPF 解析时，按扩展名识别 XHTML 文档与 NCX 目录
    log("警告：OPF 清单中未找到 XHTML 文档项，将按扩展名识别")
    doc_paths = {n for n in names if n.lower().endswith(('.xhtml', '.html', '.htm'))}
    ncx_paths = {n for n in names if n.lower().endswith('.ncx')}
    return doc_paths, ncx_paths, False


def _write_epub(zin: zipfile.ZipFile, output_path: str, converted: Dict[str, bytes]) -> None:
    """
    将原 EPUB 逐条目复制到新文件，仅替换 converted 中给出的条目内容。

    遵循 EPUB 规范：
    - mimetype 必须是第一个条目
    - mimetype 必须以 Stored（不压缩）方式写入，且不携带 extra 字段
    其余条目保持原有顺序，统一使用 Deflate 压缩。
    """
    # 稳定排序：mimetype 提到最前，其余保持原顺序
    infos = sorted(zin.infolist(), key=lambda i: i.filename != EPUB_MIMETYPE_NAME)

    with zipfile.ZipFile(output_path, 'w') as zout:
        for info in infos:
            data = converted.get(info.filename)
            if data is None:
                data = zin.read(info.filename)

            # 复制原条目的元信息（日期、属性等）
            new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new_info.comment = info.comment
            new_info.create_system = info.create_system
            new_info.external_attr = info.external_attr
            new_info.internal_attr = info.internal_attr
            new_info.extra = info.extra

            if info.filename == EPUB_MIMETYPE_NAME:
                new_info.compress_type = zipfile.ZIP_STORED
                new_info.extra = b''  # mimetype 条目不携带 extra 字段
            else:
                new_info.compress_type = zipfile.ZIP_DEFLATED

            zout.writestr(new_info, data)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def convert_epub_file(
    input_path: str,
    output_folder: str,
    conversion_type: str,
    log_callback: Optional[Callable[[str], None]] = None,
    is_cancelled_callback: Optional[Callable[[], bool]] = None
) -> Union[str, bool]:
    """
    将 EPUB 文件中的文字内容进行繁简转换，输出新的 EPUB 文件。

    参数
    ----------
    input_path : str
        源 EPUB 文件路径
    output_folder : str
        输出文件夹路径
    conversion_type : str
        OpenCC 转换类型配置名称，如 't2gov', 't2s', 's2t' 等
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

    if input_path.lower().endswith('.epub'):
        log(f"正在处理 EPUB 文件: {os.path.basename(input_path)}")
    else:
        log(f"警告：文件后缀不是 .epub，将尝试以 EPUB 格式打开: {os.path.basename(input_path)}")

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

    # --- 读取 EPUB（EPUB 本质是 ZIP 包，标准库 zipfile 即可处理） ---
    if not zipfile.is_zipfile(input_path):
        log("错误：无法读取 EPUB 文件 - 不是有效的 ZIP/EPUB 容器")
        log("提示：该文件可能受 DRM 保护或不是有效的 EPUB 格式")
        return False

    try:
        zin = zipfile.ZipFile(input_path, 'r')
    except (zipfile.BadZipFile, OSError) as e:
        log(f"错误：无法读取 EPUB 文件 - {e}")
        log("提示：该文件可能受 DRM 保护或不是有效的 EPUB 格式")
        return False
    log("EPUB 文件打开成功")

    try:
        # --- 取消检查 ---
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # --- 解析 OPF，定位 XHTML 文档项与 NCX 目录 ---
        doc_paths, ncx_paths, from_opf = _get_document_paths(zin, log)
        all_targets = doc_paths | ncx_paths
        total_items = len(all_targets)
        if from_opf:
            log(f"检测到 {len(doc_paths)} 个文档项"
                + (f"、{len(ncx_paths)} 个目录文件" if ncx_paths else ""))
        else:
            log(f"按扩展名识别到 {len(doc_paths)} 个文档项"
                + (f"、{len(ncx_paths)} 个目录文件" if ncx_paths else ""))

        # --- 遍历并转换内容项 ---
        doc_count = 0
        converted: Dict[str, bytes] = {}

        for info in zin.infolist():
            if info.filename not in all_targets:
                continue

            if is_cancelled_callback and is_cancelled_callback():
                return False

            doc_count += 1
            file_name = info.filename
            log(f"  [{doc_count}/{total_items}] 转换文档: {file_name}")
            try:
                raw = zin.read(info.filename)
                if info.filename in ncx_paths:
                    converted[file_name] = _convert_ncx_bytes(raw, cc, log_callback)
                else:
                    converted[file_name] = _convert_xhtml_bytes(raw, cc, log_callback)
            except Exception as e:
                log(f"  ⚠ 处理 {file_name} 时出错: {e}，已跳过该文件")
                continue

        if doc_count == 0:
            log("警告：在 EPUB 中未找到任何 XHTML 文档项，生成的输出可能为空")

        # --- 取消检查 ---
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # --- 写出新 EPUB ---
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)

        try:
            # 确保扩展名为 .epub
            if not output_path.lower().endswith('.epub'):
                output_path += '.epub'

            _write_epub(zin, output_path, converted)
            log(f"已保存: {output_path}")
        except Exception as e:
            log(f"错误：写出 EPUB 文件失败 - {e}")
            return False
    finally:
        zin.close()

    log(f"EPUB 转换完成，共处理 {doc_count} 个文档项")
    return output_path
