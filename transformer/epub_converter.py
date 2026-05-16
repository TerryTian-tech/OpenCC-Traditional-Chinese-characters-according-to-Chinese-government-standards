"""
EPUB 电子书繁简转换模块

支持 EPUB 2 和 EPUB 3 格式：
1. 使用 ebooklib 读取 EPUB 容器
2. 遍历所有 XHTML 内容文件
3. 使用 BeautifulSoup 解析 DOM，仅对纯文本节点做 OpenCC 转换
4. 保留全部标签、属性、资源文件（CSS / 图片 / 字体等）
5. 写回新的 EPUB 文件
"""

import os
import re
import ebooklib
from ebooklib import epub
from typing import Callable, Optional, Union
from opencc import OpenCC
from bs4 import BeautifulSoup, NavigableString
from bs4.formatter import XMLFormatter
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
        if element.name == 'pre':
            # <pre> 内的文本也有实际语义，应当转换
            pass
        for child in list(element.children):
            if isinstance(child, NavigableString) and child.strip():
                # 注意：不能直接 child.replace_with(cc.convert(child))，
                # 因为 child 的类型可能是 NavigableString 的子类 CData 等，
                # 统一转为普通字符串处理
                converted = cc.convert(str(child))
                child.replace_with(NavigableString(converted))


def _convert_xhtml_item(item, cc: OpenCC, log_callback: Optional[Callable]) -> None:
    """
    转换单个 XHTML 内容项。
    使用 BeautifulSoup 解析 → 文本节点转换 → 序列化回 item。
    """
    raw = item.get_content()
    # 尝试多种编码解码
    content = None
    for enc in ('utf-8', 'utf-8-sig', 'gb18030', 'gbk'):
        try:
            content = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if content is None:
        # 最后手段：用 chardet 或 errors=replace
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
    is_cancelled_callback: Optional[Callable[[], bool]] = None,
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
