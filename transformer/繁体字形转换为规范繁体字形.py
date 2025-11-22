import os
from docx import Document
from opencc import OpenCC
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import re
import msvcrt  # 用于检测键盘输入
import codecs
import chardet
import zipfile
import tempfile
import shutil
import xml.etree.ElementTree as ET
import win32com.client as win32 # 添加win32com导入，用于DOC转DOCX

def wait_for_esc():
    """等待用户按下ESC键"""
    print("\n按ESC键退出...")
    while True:
        if msvcrt.kbhit():  # 检测键盘输入
            key = msvcrt.getch()
            if key == b'\x1b':  # ESC键的编码
                break

def detect_encoding(file_path):
    """检测文件编码，特别处理中文ANSI编码"""
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        
    # 首先尝试chardet检测
    result = chardet.detect(raw_data)
    encoding = result['encoding']
    confidence = result['confidence']
    
    print(f"chardet检测结果: {encoding} (置信度: {confidence})")
    
    # 特别处理GB18030编码
    # 如果检测到GB2312，但文件可能包含GB18030特有字符，优先尝试GB18030
    if encoding == 'GB2312' and confidence < 0.95:
        try:
            # 尝试用GB18030解码整个文件
            decoded = raw_data.decode('gb18030', errors='strict')
            # 检查是否包含GB18030特有的字符范围
            # GB18030扩展了GB2312，支持更多汉字和符号
            if any(ord(char) > 0x9FFF for char in decoded):  # 检查是否包含扩展汉字
                print("检测到GB18030扩展字符，使用GB18030编码")
                return 'gb18030'
            else:
                # 虽然没有扩展字符，但为了兼容性，仍使用GB18030
                print("使用GB18030编码以确保兼容性")
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
                # 尝试解码前1000个字节
                test_data = raw_data[:1000]
                decoded = test_data.decode(enc, errors='strict')
                # 如果包含中文字符，认为可能是正确的编码
                if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                    print(f"检测到中文字符，使用编码: {enc}")
                    return enc
            except UnicodeDecodeError:
                continue
    
    # 如果检测到UTF-8但置信度不高，尝试GB18030
    if encoding == 'utf-8' and confidence < 0.9:
        try:
            # 尝试用GB18030解码
            decoded = raw_data.decode('gb18030', errors='strict')
            # 检查是否包含中文字符
            if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                print("检测到GB18030编码的中文字符，使用GB18030编码")
                return 'gb18030'
        except UnicodeDecodeError:
            pass
    
    # 默认使用检测到的编码，如果是None则使用utf-8
    if not encoding:
        encoding = 'utf-8'
    
    # 如果是GB2312，优先使用GB18030以确保兼容性
    if encoding.lower() in ['gb2312', 'gbk']:
        print(f"将{encoding}升级为GB18030以确保更好的兼容性")
        return 'gb18030'
    
    return encoding

def safe_read_file(file_path, encoding):
    """安全读取文件，处理编码问题"""
    # 优先尝试GB18030，因为它兼容GB2312和GBK
    if encoding.lower() in ['gb2312', 'gbk']:
        try:
            with codecs.open(file_path, 'r', encoding='gb18030', errors='strict') as f:
                return f.read()
        except UnicodeDecodeError as e:
            print(f"GB18030严格模式读取失败: {e}，尝试原编码")
    
    try:
        with codecs.open(file_path, 'r', encoding=encoding, errors='strict') as f:
            return f.read()
    except UnicodeDecodeError:
        # 如果严格模式失败，尝试使用errors='ignore'
        print(f"使用严格模式读取失败，尝试忽略错误字符")
        try:
            with codecs.open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                content = f.read()
                # 检查读取的内容是否包含有效的中文字符
                if any('\u4e00' <= char <= '\u9fff' for char in content):
                    return content
                else:
                    # 如果没有中文字符，可能是编码错误，尝试GB18030
                    print("读取内容不包含中文字符，尝试GB18030编码")
                    with codecs.open(file_path, 'r', encoding='gb18030', errors='ignore') as f2:
                        return f2.read()
        except Exception as e:
            print(f"读取文件时发生错误: {e}")
            # 最后尝试使用GB18030
            try:
                with codecs.open(file_path, 'r', encoding='gb18030', errors='ignore') as f:
                    return f.read()
            except Exception as e2:
                print(f"最终读取失败: {e2}")
                return ""

def convert_doc_to_docx(input_path, output_folder):
    """
    将DOC文件转换为DOCX文件
    :param input_path: 输入的DOC文件路径
    :param output_folder: 输出文件夹路径
    :return: 转换后的DOCX文件路径或False
    """

    try:
        if not os.path.exists(input_path):
            print(f"错误：文件不存在 - {input_path}")
            return False
            
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            print(f"创建输出目录: {output_folder}")
        
        print(f"正在转换DOC文件: {os.path.basename(input_path)}")
        
        # 创建Word应用程序实例
        word = win32.gencache.EnsureDispatch('Word.Application')
        word.Visible = False  # 不显示Word界面
        
        try:
            # 打开DOC文件
            doc = word.Documents.Open(input_path)
            
            # 生成输出文件名
            filename = os.path.basename(input_path)
            docx_filename = os.path.splitext(filename)[0] + ".docx"
            output_path = os.path.join(output_folder, docx_filename)
            
            # 保存为DOCX格式
            doc.SaveAs(output_path, FileFormat=16)  # 16代表docx格式
            doc.Close()
            
            print(f"已转换为DOCX文件: {output_path}")
            return output_path
            
        except Exception as e:
            print(f"转换DOC文件时出错: {str(e)}")
            return False
            
        finally:
            # 确保Word应用程序被关闭
            word.Quit()
            
    except Exception as e:
        print(f"处理DOC文件 {input_path} 时出错: {str(e)}")
        return False

def convert_txt_t2gov(input_path, output_folder):
    """
    将txt文件转换为简体
    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :return: 转换后的文件路径或False
    """
    cc = OpenCC('t2gov')
    
    try:
        if not os.path.exists(input_path):
            print(f"错误：文件不存在 - {input_path}")
            return False
            
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            print(f"创建输出目录: {output_folder}")
        
        print(f"正在处理txt文件: {os.path.basename(input_path)}")
        
        # 检测文件编码
        encoding = detect_encoding(input_path)
        print(f"最终使用的编码: {encoding}")
        
        # 读取文件内容
        content = safe_read_file(input_path, encoding)
        
        # 繁简转换
        converted_content = cc.convert(content)
        
        # 保存文件
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        
        with codecs.open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        
        print(f"已保存: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"处理txt文件 {input_path} 时出错: {str(e)}")
        return False

class DocxTraditionalSimplifiedConverter:
    def __init__(self, config='t2gov'):
        """
        初始化转换器
        config: 转换配置
        - 't2gov': 繁体转简体
        """
        self.cc = OpenCC(config)
        self.config = config
    
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
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                # 解压docx文件
                with zipfile.ZipFile(input_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                
                # 转换脚注XML文件
                footnotes_path = os.path.join(temp_dir, 'word', 'footnotes.xml')
                if os.path.exists(footnotes_path):
                    self._convert_xml_file(footnotes_path)
                    print("已转换脚注内容")
                else:
                    print("文档中没有脚注")
                
                # 转换尾注XML文件（如果有）
                endnotes_path = os.path.join(temp_dir, 'word', 'endnotes.xml')
                if os.path.exists(endnotes_path):
                    self._convert_xml_file(endnotes_path)
                    print("已转换尾注内容")
                
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
            print(f"通过zip操作转换脚注时出错: {e}")
            return False
    
    def _convert_xml_file(self, xml_path):
        """转换XML文件中的文本内容"""
        try:
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
                if elem.text:
                    elem.text = self.convert_text(elem.text)
            
            # 保存修改后的XML
            tree.write(xml_path, encoding='utf-8', xml_declaration=True)
            
        except Exception as e:
            print(f"转换XML文件 {xml_path} 时出错: {e}")
            # 如果XML解析失败，尝试使用正则表达式方法
            self._convert_xml_file_with_regex(xml_path)
    
    def _convert_xml_file_with_regex(self, xml_path):
        """使用正则表达式转换XML文件中的文本内容（备用方法）"""
        try:
            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
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
            
            with open(xml_path, 'w', encoding='utf-8') as f:
                f.write(converted_content)
                
        except Exception as e:
            print(f"使用正则表达式转换XML文件 {xml_path} 时出错: {e}")
    
    def convert_document(self, input_path, output_path=None):
        """
        转换整个Word文档，保留所有格式（包含脚注和尾注转换）
        """
        if output_path is None:
            filename, ext = os.path.splitext(input_path)
            output_path = f"convert_{filename}{ext}"
        
        print(f"开始转换文档: {input_path}")
        
        # 首先处理脚注和尾注（通过zip操作）
        temp_output = output_path + ".temp.docx"
        footnote_success = self._convert_footnotes_using_zip_manipulation(input_path, temp_output)
        
        if footnote_success:
            # 如果脚注转换成功，使用temp文件继续处理其他内容
            processing_file = temp_output
        else:
            # 如果脚注转换失败，使用原始文件
            print("脚注转换失败，将只转换正文内容")
            processing_file = input_path
            temp_output = None
        
        try:
            # 读取文档并转换其他内容
            doc = Document(processing_file)
            
            # 转换正文段落
            self._convert_paragraphs(doc.paragraphs)
            
            # 转换表格内容
            self._convert_tables(doc.tables)
            
            # 转换页眉
            for section in doc.sections:
                self._convert_paragraphs(section.header.paragraphs)
                # 转换页眉中的表格
                if hasattr(section.header, 'tables') and section.header.tables:
                    self._convert_tables(section.header.tables)
            
            # 转换页脚
            for section in doc.sections:
                self._convert_paragraphs(section.footer.paragraphs)
                # 转换页脚中的表格
                if hasattr(section.footer, 'tables') and section.footer.tables:
                    self._convert_tables(section.footer.tables)
            
            # 保存最终文档
            doc.save(output_path)
            print(f"转换完成，保存至: {output_path}")
            
            # 清理临时文件
            if temp_output and os.path.exists(temp_output):
                os.remove(temp_output)
            
            return output_path
            
        except Exception as e:
            print(f"处理文档时出错: {e}")
            # 如果出错，尝试直接复制文件
            if temp_output and os.path.exists(temp_output):
                shutil.copy2(temp_output, output_path)
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                print(f"已保存基本转换的文档: {output_path}")
                return output_path
            else:
                # 最后的手段：直接复制原始文件
                shutil.copy2(input_path, output_path)
                print(f"转换失败，已复制原始文件到: {output_path}")
                return output_path
    
    def _convert_paragraphs(self, paragraphs):
        """转换段落集合，保留所有格式"""
        for paragraph in paragraphs:
            self._convert_paragraph(paragraph)
    
    def _convert_paragraph(self, paragraph):
        """转换单个段落，保留所有run的格式"""
        if not paragraph.text.strip():
            return
        
        # 逐个处理run，保留每个run的独立格式
        for run in paragraph.runs:
            if run.text.strip():
                original_text = run.text
                converted_text = self.convert_text(original_text)
                
                # 保留原有格式的情况下更新文本
                self._preserve_run_format(run, converted_text)
    
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
            if run.font.color is None:
                run.font.color.rgb = original_color
            else:
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
                print(f"设置中文字体时出错: {e}")
        
        if original_size:
            run.font.size = original_size
    
    def _convert_tables(self, tables):
        """转换表格内容，保留表格格式"""
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    # 转换单元格中的段落
                    self._convert_paragraphs(cell.paragraphs)
                    
                    # 递归处理嵌套表格
                    for nested_table in cell.tables:
                        self._convert_tables([nested_table])

def convert_docx_t2gov(input_path, output_folder):
    """
    将Word文档转换为简体/繁体
    :param input_path: 输入文件或文件夹路径
    :param output_folder: 输出文件夹路径
    """
    try:
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            print(f"创建输出目录: {output_folder}")
        
        if os.path.isfile(input_path) and input_path.lower().endswith('.docx'):
            files = [input_path]
        elif os.path.isdir(input_path):
            files = [os.path.join(input_path, f) for f in os.listdir(input_path) 
                    if f.lower().endswith('.docx')]
        else:
            print("错误：输入的路径既不是有效的.docx文件也不是文件夹")
            wait_for_esc()
            return
        
        print(f"找到 {len(files)} 个Word文档待处理")
        
        for file_path in files:
            try:
                print(f"正在处理: {os.path.basename(file_path)}")
                
                # 使用新的转换器类
                converter = DocxTraditionalSimplifiedConverter('t2gov')
                output_path = os.path.join(output_folder, f"convert_{os.path.basename(file_path)}")
                converter.convert_document(file_path, output_path)
                
                print(f"已保存: {output_path}")
                
            except Exception as e:
                print(f"处理 {file_path} 时出错: {str(e)}")
        
        # 转换完成后等待用户按下ESC
        wait_for_esc()
    
    except Exception as e:
        print(f"发生错误: {str(e)}")
        wait_for_esc()

def convert_t2gov(input_path, output_folder):
    """
    统一处理docx、doc和txt文件的繁简转换
    :param input_path: 输入文件或文件夹路径
    :param output_folder: 输出文件夹路径
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"创建输出目录: {output_folder}")
    
    # 处理单个文件
    if os.path.isfile(input_path):
        file_ext = os.path.splitext(input_path)[1].lower()
        
        if file_ext == '.docx':
            convert_docx_t2gov(input_path, output_folder)
        elif file_ext == '.doc':
            # 先转换为DOCX，然后再进行繁简转换   
            print("检测到DOC文件，先转换为DOCX格式...")
            
            # 创建临时目录用于存放临时转换的DOCX文件
            with tempfile.TemporaryDirectory() as temp_dir:
                docx_path = convert_doc_to_docx(input_path, temp_dir)
                if docx_path:
                    print("DOC文件转换成功，开始繁简转换...")
                    convert_docx_t2gov(docx_path, output_folder)
                else:
                    print("DOC文件转换失败")
                    wait_for_esc()
        elif file_ext == '.txt':
            result = convert_txt_t2gov(input_path, output_folder)
            if result:
                print("txt文件转换完成！")
                wait_for_esc()
            else:
                print("txt文件转换失败")
                wait_for_esc()
        else:
            print("错误：不支持的文件格式，仅支持docx、doc和txt文件")
            wait_for_esc()
            return
    
    # 处理文件夹
    elif os.path.isdir(input_path):
        # 获取所有支持的文件
        supported_files = []
        for f in os.listdir(input_path):
            file_ext = os.path.splitext(f)[1].lower()
            if file_ext in ['.docx', '.doc', '.txt']:
                supported_files.append(f)
        
        if not supported_files:
            print("在指定文件夹中未找到支持的.docx、.doc或.txt文件")
            wait_for_esc()
            return
            
        print(f"找到 {len(supported_files)} 个文件待处理")
        
        success_count = 0
        
        for i, filename in enumerate(supported_files, 1):
            print(f"\n处理文件 {i}/{len(supported_files)}: {filename}")
            file_path = os.path.join(input_path, filename)
            file_ext = os.path.splitext(filename)[1].lower()
            
            if file_ext == '.docx':
                # 对于docx文件，使用转换器类
                try:
                    converter = DocxTraditionalSimplifiedConverter('t2gov')
                    output_path = os.path.join(output_folder, f"convert_{filename}")
                    converter.convert_document(file_path, output_path)
                    success_count += 1
                    print(f"已保存: {output_path}")
                    
                except Exception as e:
                    print(f"处理 {filename} 时出错: {str(e)}")
            
            elif file_ext == '.doc':
                # 对于doc文件，先转换为docx，再转换                 
                print("检测到DOC文件，先转换为DOCX格式...")
                
                # 创建临时目录用于存放临时转换的DOCX文件
                with tempfile.TemporaryDirectory() as temp_dir:
                    docx_path = convert_doc_to_docx(file_path, temp_dir)
                    if docx_path:
                        print("DOC文件转换成功，开始繁简转换...")
                        try:
                            converter = DocxTraditionalSimplifiedConverter('t2gov')
                            final_output_path = os.path.join(output_folder, f"convert_{os.path.splitext(filename)[0]}.docx")
                            converter.convert_document(docx_path, final_output_path)
                            success_count += 1
                            print(f"已保存: {final_output_path}")
                        except Exception as e:
                            print(f"繁简转换 {filename} 时出错: {str(e)}")
                    else:
                        print(f"DOC文件 {filename} 转换失败")
            
            elif file_ext == '.txt':
                if convert_txt_t2gov(file_path, output_folder):
                    success_count += 1
        
        print(f"\n处理完成！成功转换 {success_count}/{len(supported_files)} 个文件")
        wait_for_esc()
    
    else:
        print("错误：输入的路径既不是有效的文件也不是文件夹")
        wait_for_esc()
        return

if __name__ == "__main__":
    print("将繁体Word文档或txt文件转换成2013年版《通用规范汉字表》规范繁体字形")
    print("=" * 60)
    
    input_path = input("请输入Word文档或txt文件路径: ").strip('"\'')
    output_folder = input("请输入输出文件夹路径: ").strip('"\'')
    convert_t2gov(input_path, output_folder)
