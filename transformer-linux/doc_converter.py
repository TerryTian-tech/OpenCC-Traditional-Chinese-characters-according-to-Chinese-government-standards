import os
import re
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET

from docx import Document
from opencc import OpenCC
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from text_converter import detect_encoding, safe_read_file

class DocxTraditionalSimplifiedConverter:
    def __init__(self, log_callback, is_cancelled_callback, config='t2gov', preserve_format=True, convert_footnotes=True):
        """
        初始化转换器
        - 't2gov': 繁体转规范繁体
        - 't2new': 繁体旧字形转新字形，但保留异体字不转换
        - 't2gov_keep_simp': 繁体转规范繁体，但保留文档内原有简体字
        - 't2new_keep_simp': 繁体旧字形转新字形，但保留文档内原有简体字和异体字
        - 't2s': 繁体转简体
        - 's2t': 简体转规范繁体
        """
        self.log_callback = log_callback
        self.is_cancelled_callback = is_cancelled_callback
        self.cc = OpenCC(config)
        self.config = config
        self.preserve_format = preserve_format
        self.convert_footnotes = convert_footnotes

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def convert_text(self, text):
        """转换文本内容"""
        if text and isinstance(text, str):
            return self.cc.convert(text)
        return text

    def _convert_footnotes_using_zip_manipulation(self, input_path, output_path):
        """
        通过直接操作docx文件（zip格式）来转换脚注和尾注
        这是最可靠的方法，因为它直接修改XML文件
        """
        try:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return False

            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return False

                # 解压docx文件
                with zipfile.ZipFile(input_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return False

                # 转换脚注XML文件
                footnotes_path = os.path.join(temp_dir, 'word', 'footnotes.xml')
                if os.path.exists(footnotes_path):
                    self._convert_xml_file(footnotes_path)
                    self.log("已转换脚注内容")
                else:
                    self.log("文档中没有脚注")

                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return False

                # 转换尾注XML文件（如果有）
                endnotes_path = os.path.join(temp_dir, 'word', 'endnotes.xml')
                if os.path.exists(endnotes_path):
                    self._convert_xml_file(endnotes_path)
                    self.log("已转换尾注内容")

                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return False

                # 重新压缩为docx文件
                with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # 计算在zip中的相对路径
                            arcname = os.path.relpath(file_path, temp_dir)
                            zipf.write(file_path, arcname)

                return True

        except Exception as e:
            self.log(f"通过zip操作转换脚注时出错: {e}")
            return False

    def _convert_xml_file(self, xml_path):
        """转换XML文件中的文本内容"""
        try:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return

            # 读取XML文件
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # 定义XML命名空间
            namespaces = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
            }

            # 注册命名空间以便XPath查询
            for prefix, uri in namespaces.items():
                ET.register_namespace(prefix, uri)

            # 查找所有文本节点
            text_elements = root.findall('.//w:t', namespaces)
            for elem in text_elements:
                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return

                if elem.text:
                    elem.text = self.convert_text(elem.text)

            # 保存修改后的XML
            tree.write(xml_path, encoding='utf-8', xml_declaration=True)

        except Exception as e:
            self.log(f"转换XML文件 {xml_path} 时出错: {e}")
            # 如果XML解析失败，尝试使用正则表达式方法
            self._convert_xml_file_with_regex(xml_path)

    def _convert_xml_file_with_regex(self, xml_path):
        """使用正则表达式转换XML文件中的文本内容（备用方法）"""
        try:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return

            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return

            # 使用正则表达式找到XML标签外的文本内容并转换
            def convert_text_in_xml(match):
                # 匹配文本内容但不匹配标签属性
                text = match.group(1)
                # 只在文本包含中文字符时转换
                if any('\u4e00' <= char <= '\u9fff' for char in text):
                    return '>' + self.convert_text(text) + '<'
                else:
                    return match.group(0)

            # 匹配标签之间的文本内容
            pattern = r'>([^<]+?)<'
            converted_content = re.sub(pattern, convert_text_in_xml, content)

            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return

            with open(xml_path, 'w', encoding='utf-8') as f:
                f.write(converted_content)

        except Exception as e:
            self.log(f"使用正则表达式转换XML文件 {xml_path} 时出错: {e}")

    def convert_document(self, input_path, output_path=None):
        """
        转换整个Word文档，根据设置决定是否保留格式和转换脚注
        """
        # 检查是否已取消
        if self.is_cancelled_callback and self.is_cancelled_callback():
            return None

        if output_path is None:
            filename, ext = os.path.splitext(input_path)
            output_path = f"convert_{filename}{ext}"

        self.log(f"开始转换文档: {input_path}")

        # 首先处理脚注和尾注（通过zip操作）- 根据设置决定是否执行
        if self.convert_footnotes:
            temp_output = output_path + ".temp.docx"
            footnote_success = self._convert_footnotes_using_zip_manipulation(input_path, temp_output)

            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return None

            if footnote_success:
                # 如果脚注转换成功，使用temp文件继续处理其他内容
                processing_file = temp_output
            else:
                # 如果脚注转换失败，使用原始文件
                self.log("脚注转换失败，将只转换正文内容")
                processing_file = input_path
                temp_output = None
        else:
            self.log("跳过脚注和尾注转换")
            processing_file = input_path
            temp_output = None

        try:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                if temp_output and os.path.exists(temp_output):
                    os.remove(temp_output)
                return None

            # 读取文档并转换其他内容
            doc = Document(processing_file)

            # 转换正文段落
            self._convert_paragraphs(doc.paragraphs)

            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                if temp_output and os.path.exists(temp_output):
                    os.remove(temp_output)
                return None

            # 转换表格内容
            self._convert_tables(doc.tables)

            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                if temp_output and os.path.exists(temp_output):
                    os.remove(temp_output)
                return None

            # 转换页眉
            for section in doc.sections:
                self._convert_paragraphs(section.header.paragraphs)
                # 转换页眉中的表格
                if hasattr(section.header, 'tables') and section.header.tables:
                    self._convert_tables(section.header.tables)

                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    if temp_output and os.path.exists(temp_output):
                        os.remove(temp_output)
                    return None

            # 转换页脚
            for section in doc.sections:
                self._convert_paragraphs(section.footer.paragraphs)
                # 转换页脚中的表格
                if hasattr(section.footer, 'tables') and section.footer.tables:
                    self._convert_tables(section.footer.tables)

                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    if temp_output and os.path.exists(temp_output):
                        os.remove(temp_output)
                    return None

            # 保存最终文档
            doc.save(output_path)
            self.log(f"转换完成，保存至: {output_path}")

            # 清理临时文件
            if temp_output and os.path.exists(temp_output):
                os.remove(temp_output)

            return output_path

        except Exception as e:
            self.log(f"处理文档时出错: {e}")
            # 如果出错，尝试直接复制文件
            if temp_output and os.path.exists(temp_output):
                shutil.copy2(temp_output, output_path)
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                self.log(f"已保存基本转换的文档: {output_path}")
                return output_path
            else:
                # 最后的手段：直接复制原始文件
                shutil.copy2(input_path, output_path)
                self.log(f"转换失败，已复制原始文件到: {output_path}")
                return output_path

    def _convert_paragraphs(self, paragraphs):
        """转换段落集合"""
        for paragraph in paragraphs:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return
            self._convert_paragraph(paragraph)

    def _convert_paragraph(self, paragraph):
        """转换单个段落，根据设置决定是否保留格式"""
        if not paragraph.text.strip():
            return

        # 如果设置了保留格式，逐个处理run
        if self.preserve_format:
            for run in paragraph.runs:
                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return

                if run.text.strip():
                    original_text = run.text
                    converted_text = self.convert_text(original_text)

                    # 保留原有格式的情况下更新文本
                    self._preserve_run_format(run, converted_text)
        else:
            # 如果不保留格式，直接转换整个段落的文本
            if paragraph.text.strip():
                original_text = paragraph.text
                converted_text = self.convert_text(original_text)
                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return

                paragraph.text = converted_text

    def _preserve_run_format(self, run, new_text):
        """
        保留run的所有原始格式，只更新文本内容
        包括字体、大小、颜色、粗体、斜体、下划线等
        """
        # 保存当前格式
        original_bold = run.bold
        original_italic = run.italic
        original_underline = run.underline
        original_color = run.font.color.rgb if run.font.color and run.font.color.rgb else None

        # 安全地获取高亮颜色
        original_highlight = None
        try:
            original_highlight = run.font.highlight_color
        except:
            pass

        # 保存字体信息
        original_font_name = run.font.name
        original_size = run.font.size

        # 更新文本内容
        run.text = new_text

        # 恢复格式
        run.bold = original_bold
        run.italic = original_italic
        run.underline = original_underline

        if original_color:
            run.font.color.rgb = original_color

        if original_highlight:
            try:
                run.font.highlight_color = original_highlight
            except:
                pass

        if original_font_name:
            run.font.name = original_font_name
            # 设置中文字体
            try:
                if hasattr(run, '_element') and hasattr(run._element, 'rPr'):
                    rpr = run._element.rPr
                    if rpr is not None:
                        # 创建或获取字体设置
                        fonts = rpr.find(qn('w:rFonts'))
                        if fonts is None:
                            fonts = OxmlElement('w:rFonts')
                            rpr.append(fonts)
                        fonts.set(qn('w:eastAsia'), original_font_name)
            except Exception as e:
                self.log(f"设置中文字体时出错: {e}")

        if original_size:
            run.font.size = original_size

    def _convert_tables(self, tables):
        """转换表格内容"""
        for table in tables:
            # 检查是否已取消
            if self.is_cancelled_callback and self.is_cancelled_callback():
                return

            for row in table.rows:
                # 检查是否已取消
                if self.is_cancelled_callback and self.is_cancelled_callback():
                    return

                for cell in row.cells:
                    # 转换单元格中的段落
                    self._convert_paragraphs(cell.paragraphs)

                    # 递归处理嵌套表格
                    for nested_table in cell.tables:
                        self._convert_tables([nested_table])


def convert_docx_file(input_path, output_folder, conversion_type, preserve_format=True, convert_footnotes=True,
                      log_callback=None, is_cancelled_callback=None):
    """
    将Word文档转换为简体/繁体
    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :param conversion_type: 转换类型
    :param preserve_format: 是否保留格式
    :param convert_footnotes: 是否转换脚注
    :param log_callback: 日志回调函数
    :param is_cancelled_callback: 取消检查回调函数
    :return: 转换后的文件路径
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    try:
        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            log(f"创建输出目录: {output_folder}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        if os.path.isfile(input_path) and input_path.lower().endswith('.docx'):
            log(f"正在处理: {os.path.basename(input_path)}")

            # 检查是否已取消
            if is_cancelled_callback and is_cancelled_callback():
                return False

            # 使用新的转换器类
            converter = DocxTraditionalSimplifiedConverter(log_callback, is_cancelled_callback, conversion_type,
                                                            preserve_format, convert_footnotes)
            output_path = os.path.join(output_folder, f"convert_{os.path.basename(input_path)}")
            result = converter.convert_document(input_path, output_path)

            if result:
                log(f"已保存: {result}")
                return result
            else:
                return False

        else:
            log("错误：输入的路径不是有效的.docx文件")
            return False

    except Exception as e:
        log(f"处理 {input_path} 时出错: {str(e)}")
        return False
