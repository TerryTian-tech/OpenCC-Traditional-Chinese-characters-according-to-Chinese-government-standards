import os
import re
import ebooklib
from ebooklib import epub
from typing import Callable, Optional, Union
from opencc_python import OpenCC
from bs4 import BeautifulSoup, NavigableString
from bs4.formatter import XMLFormatter
import chardet
import logging
logging.getLogger('ebooklib').setLevel(logging.WARNING) # 防止在无 GUI 环境中因 ebooklib 日志刷屏

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _convert_dom_text(soup: BeautifulSoup, cc: OpenCC) -> None:
    """
    递归遍历 BeautifulSoup DOM 树，替换所有纯文本节点。

    规则：
    - 跳过 <script> / <style> 内部的文本
    - 不处理纯空白节点（保留原空白）
    - 只处理非空文本节点
    """
    # 黑名单标签 —— 其内部文本不参与转换
    SKIP_TAGS = {'script', 'style', 'svg', 'math'}

    for element in soup.find_all(True):
        if element.name in SKIP_TAGS:
            continue
        for child in list(element.children):
            if isinstance(child, NavigableString) and child.strip():
                # 注意：不能直接 child.replace_with(cc.convert(child))，
                # 因为 child 的类型可能是 NavigableString 的子类 CData 等，
                # 统一转为普通字符串处理
                converted = cc.convert(str(child))
                child.replace_with(NavigableString(converted))


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
    if confidence < 0.7 or encoding in ['ISO-8859-1', 'Windows-1252', 'ascii']:
        # 尝试常见中文编码，优先尝试GB18030
        chinese_encodings = ['gb18030', 'gbk', 'gb2312', 'big5']
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
        for enc in ['gb18030', 'gbk']:
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

    # 如果检测到UTF-8但置信度不高，尝试GB18030
    if encoding == 'utf-8' and confidence < 0.8:
        try:
            # 尝试用GB18030解码
            decoded = raw_data.decode('gb18030', errors='strict')
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
    if encoding.lower() in ['gb2312', 'gbk']:
        log(f"将{encoding}升级为GB18030以确保更好的兼容性")
        return 'gb18030'

    # 最终回退：如果chardet检测到的是非中文编码且置信度不高，
    # 强制使用gb18030作为最终回退（中文文件最常见的ANSI编码）
    if encoding.lower() not in ['utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312', 'big5']:
        if confidence < 0.5:
            log(f"chardet检测到非中文编码'{encoding}'（置信度{confidence:.4%}），回退到GB18030")
            return 'gb18030'

    return encoding


def _convert_xhtml_item(item, cc: OpenCC, log_callback: Optional[Callable]) -> None:
    """
    转换单个 XHTML 内容项。
    使用 BeautifulSoup 解析 → 文本节点转换 → 序列化回 item。
    """
    raw = item.get_content()
    # 使用智能编码检测（与 detect_encoding 共用核心逻辑）
    detected_enc = _detect_encoding_from_bytes(raw, log_callback=log_callback)
    try:
        content = raw.decode(detected_enc)
    except (UnicodeDecodeError, LookupError):
        # 检测到的编码解码失败，回退到 utf-8 宽松模式
        content = raw.decode('utf-8', errors='replace')

    try:
        soup = BeautifulSoup(content, 'xml')  # EPUB XHTML 是 XML 序列化
    except Exception:
        soup = BeautifulSoup(content, 'html.parser')  # 容错：非良构 XML 时回退到 HTML 解析器
    _convert_dom_text(soup, cc)

    # bs4 序列化为字节，保留 XML 声明
    converted_bytes = soup.encode('utf-8', formatter=XMLFormatter())

    # 如果原始内容有 BOM 且转换后没了，补上
    if raw[:3] == b'\xef\xbb\xbf' and converted_bytes[:3] != b'\xef\xbb\xbf':
        converted_bytes = b'\xef\xbb\xbf' + converted_bytes

    item.set_content(converted_bytes)


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

    # --- 读取 EPUB ---
    book = None
    try:
        book = epub.read_epub(input_path)
        log("EPUB 文件打开成功")
    except Exception as e:
        log(f"错误：无法读取 EPUB 文件 - {e}")
        log("提示：该文件可能受 DRM 保护或不是有效的 EPUB 格式")
        return False

    # --- 取消检查 ---
    if is_cancelled_callback and is_cancelled_callback():
        return False

    # --- 遍历并转换内容项 ---
    doc_count = 0
    total_items = len(list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)))
    log(f"检测到 {total_items} 个文档项（正文 / 导航）")

    for item in book.get_items():
        if is_cancelled_callback and is_cancelled_callback():
            return False

        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            doc_count += 1
            file_name = item.get_name()
            log(f"  [{doc_count}/{total_items}] 转换文档: {file_name}")
            try:
                _convert_xhtml_item(item, cc, log_callback)
            except Exception as e:
                log(f"  ⚠ 处理 {file_name} 时出错: {e}，已跳过该文件")
                continue
        elif item.get_type() == ebooklib.ITEM_STYLE:
            # v1: CSS 透传不处理
            pass
        else:
            # 图片 / 字体 / 音频 / 视频等资源 —— 透传
            pass

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

        epub.write_epub(output_path, book)
        log(f"已保存: {output_path}")
    except Exception as e:
        log(f"错误：写出 EPUB 文件失败 - {e}")
        return False

    log(f"EPUB 转换完成，共处理 {doc_count} 个文档项")
    return output_path
