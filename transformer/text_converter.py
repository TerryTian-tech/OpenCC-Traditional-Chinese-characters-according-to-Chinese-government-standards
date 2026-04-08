import os
import re
import chardet

from opencc import OpenCC
import jieba


def detect_encoding(file_path, log_callback=None, force_encoding=None):
    """检测文件编码，特别处理中文ANSI编码

    :param file_path: 文件路径
    :param log_callback: 日志回调函数
    :param force_encoding: 强制指定的编码（如 'big5'）。如果为 None 则自动检测。
    :return: 检测到的编码名称
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 如果用户强制指定了编码，直接返回
    if force_encoding:
        log(f"用户强制指定编码: {force_encoding}")
        return force_encoding

    log(f"检测文件编码: {file_path}")
    with open(file_path, 'rb') as f:
        raw_data = f.read()

    # 首先尝试chardet检测
    result = chardet.detect(raw_data)
    encoding = result['encoding']
    confidence = result['confidence']

    log(f"chardet检测结果: {encoding} (置信度: {confidence})")

    # 特别处理GB18030编码
    # 如果检测到GB2312，但文件可能包含GB18030特有字符，优先尝试GB18030
    if encoding == 'GB2312' and confidence < 0.95:
        try:
            # 尝试用GB18030解码整个文件
            decoded = raw_data.decode('gb18030', errors='strict')
            if any(ord(char) > 0x9FFF for char in decoded):  # 检查是否包含扩展汉字
                log("检测到GB18030扩展字符，使用GB18030编码")
                return 'gb18030'
            else:
                # 虽然没有扩展字符，但为了兼容性，仍使用GB18030
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
    if encoding == 'utf-8' and confidence < 0.9:
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


def safe_read_file(file_path, encoding, log_callback=None):
    """安全读取文件，处理编码问题"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 优先尝试GB18030，因为它兼容GB2312和GBK
    if encoding.lower() in ['gb2312', 'gbk']:
        try:
            with open(file_path, 'r', encoding='gb18030', errors='strict') as f:
                return f.read()
        except UnicodeDecodeError as e:
            log(f"GB18030严格模式读取失败: {e}，尝试原编码")

    try:
        with open(file_path, 'r', encoding=encoding, errors='strict') as f:
            return f.read()
    except UnicodeDecodeError:
        # 如果严格模式失败，尝试使用errors='ignore'
        log(f"使用严格模式读取失败，尝试忽略错误字符")
        try:
            with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                content = f.read()
                # 检查读取的内容是否包含有效的中文字符
                if any('\u4e00' <= char <= '\u9fff' for char in content):
                    return content
                else:
                    # 如果没有中文字符，可能是编码错误，尝试GB18030
                    log("读取内容不包含中文字符，尝试GB18030编码")
                    with open(file_path, 'r', encoding='gb18030', errors='ignore') as f2:
                        return f2.read()
        except Exception as e:
            log(f"读取文件时发生错误: {e}")
            # 最后尝试使用GB18030
            try:
                with open(file_path, 'r', encoding='gb18030', errors='ignore') as f:
                    return f.read()
            except Exception as e2:
                log(f"最终读取失败: {e2}")
                return ""


def convert_srt_file(input_path, output_folder, conversion_type, log_callback=None, is_cancelled_callback=None,
                      force_encoding=None):
    """
    将SRT字幕文件转换为繁体/简体
    SRT格式示例：
    1
    00:00:01,000 --> 00:00:04,000
    字幕文本内容

    2
    00:00:05,000 --> 00:00:08,000
    第二句字幕文本
    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :param conversion_type: 转换类型
    :param log_callback: 日志回调函数
    :param is_cancelled_callback: 取消检查回调函数
    :return: 转换后的文件路径或False
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 检查是否已取消
    if is_cancelled_callback and is_cancelled_callback():
        return False

    cc = OpenCC(conversion_type)

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            log(f"创建输出目录: {output_folder}")

        log(f"正在处理SRT字幕文件: {os.path.basename(input_path)}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 检测文件编码
        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 读取文件内容
        content = safe_read_file(input_path, encoding, log_callback)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 解析SRT文件并转换字幕文本
        # SRT格式：序号行、时间码行、字幕文本行（可能多行）、空行
        lines = content.split('\n')
        converted_lines = []
        i = 0

        while i < len(lines):
            # 检查是否已取消
            if is_cancelled_callback and is_cancelled_callback():
                return False

            line = lines[i]

            # 检查是否是序号行（纯数字）
            if line.strip().isdigit() and i+1 < len(lines) and '-->' in lines[i+1]:
                converted_lines.append(line)  # 序号行不转换
                i += 1

                if i < len(lines):
                    # 下一行应该是时间码行
                    time_line = lines[i]
                    if '-->' in time_line:
                        converted_lines.append(time_line)  # 时间码行不转换
                        i += 1

                        # 读取字幕文本行（直到遇到空行或下一个序号）
                        while i < len(lines):
                            text_line = lines[i]
                            # 空行表示字幕块结束
                            if text_line.strip() == '':
                                converted_lines.append(text_line)
                                i += 1
                                break
                            # 如果遇到数字行（下一个字幕块的序号），停止
                            if text_line.strip().isdigit():
                                break
                            # 转换字幕文本，保留ASS/SSA样式标签
                            converted_text = _convert_srt_text_with_tags(cc, text_line)
                            converted_lines.append(converted_text)
                            i += 1
                    else:
                        # 不是标准SRT格式，直接保留
                        converted_lines.append(line)
                        i += 1
                else:
                    i += 1
            else:
                # 不是序号行，可能是空行或其他内容
                converted_lines.append(line)
                i += 1

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 合并转换后的内容
        converted_content = '\n'.join(converted_lines)

        # 保存文件
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)

        log(f"已保存: {output_path}")
        return output_path

    except Exception as e:
        log(f"处理SRT字幕文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_srt_text_with_tags(cc, text):
    """
    转换SRT字幕文本，但保留ASS/SSA样式标签内的内容不变
    ASS/SSA样式标签格式: {\tag1 value1\tag2 value2...}
    例如: {\fn微软雅黑\fs13\fscx130\fscy130\3c&HFF8000&}字幕文本{\r}

    :param cc: OpenCC转换器实例
    :param text: 原始字幕文本
    :return: 转换后的字幕文本
    """
    # 查找所有样式标签 {...}
    result = []
    last_end = 0

    # 使用正则表达式匹配 {...} 样式标签
    pattern = re.compile(r'\{[^}]*\}')

    for match in pattern.finditer(text):
        # 转换标签之前的普通文本
        plain_text = text[last_end:match.start()]
        if plain_text:
            result.append(cc.convert(plain_text))

        # 保留样式标签内容不变
        result.append(match.group())
        last_end = match.end()

    # 转换最后一个标签之后的普通文本
    remaining_text = text[last_end:]
    if remaining_text:
        result.append(cc.convert(remaining_text))

    return ''.join(result)


def convert_ass_file(input_path, output_folder, conversion_type, log_callback=None, is_cancelled_callback=None,
                      force_encoding=None):
    """
    将ASS/SSA字幕文件转换为繁体/简体
    ASS/SSA格式包含多个部分：
    [Script Info] - 脚本信息
    [V4+ Styles] / [V4 Styles] - 样式定义
    [Events] - 字幕事件
    [Fonts] / [Graphics] - 字体和图片（可选）

    只转换 [Events] 部分中的字幕文本，保留样式标签 {...}
    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :param conversion_type: 转换类型
    :param log_callback: 日志回调函数
    :param is_cancelled_callback: 取消检查回调函数
    :return: 转换后的文件路径或False
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 检查是否已取消
    if is_cancelled_callback and is_cancelled_callback():
        return False

    cc = OpenCC(conversion_type)

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            log(f"创建输出目录: {output_folder}")

        log(f"正在处理ASS/SSA字幕文件: {os.path.basename(input_path)}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 检测文件编码
        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 读取文件内容
        content = safe_read_file(input_path, encoding, log_callback)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        converted_lines = []
        in_events_section = False

        for line in lines:
            # 检查是否已取消
            if is_cancelled_callback and is_cancelled_callback():
                return False

            stripped_line = line.strip()

            # 检测是否进入 [Events] 部分
            if stripped_line.lower() == '[events]':
                in_events_section = True
                converted_lines.append(line)
                continue

            # 检测是否离开 [Events] 部分（进入其他部分）
            if stripped_line.startswith('[') and stripped_line.endswith(']'):
                in_events_section = False
                converted_lines.append(line)
                continue

            # 在 [Events] 部分处理字幕行
            if in_events_section and (stripped_line.lower().startswith('dialogue:') or
                                      stripped_line.lower().startswith('comment:')):
                converted_line = _convert_ass_dialogue_line(cc, line)
                converted_lines.append(converted_line)
            else:
                # 其他部分保持不变（样式定义、脚本信息等）
                converted_lines.append(line)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 合并转换后的内容
        converted_content = '\n'.join(converted_lines)

        # 保存文件
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)

        log(f"已保存: {output_path}")
        return output_path

    except Exception as e:
        log(f"处理ASS/SSA字幕文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_ass_dialogue_line(cc, line):
    """
    转换ASS/SSA字幕对话行
    格式: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    或: Dialogue: Marked,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text (SSA格式)

    只转换 Text 字段，保留 ASS 样式标签 {...}

    :param cc: OpenCC转换器实例
    :param line: 原始对话行
    :return: 转换后的对话行
    """
    # 查找 "Dialogue:" 或 "Comment:" 的位置
    line_lower = line.lower()
    if line_lower.startswith('dialogue:'):
        prefix = line[:9]  # "Dialogue:"
        rest = line[9:]
    elif line_lower.startswith('comment:'):
        prefix = line[:8]  # "Comment:"
        rest = line[8:]
    else:
        return line

    # ASS/SSA 字段用逗号分隔，但文本字段可能包含逗号
    # 标准格式有 10 个字段，文本是最后一个字段
    parts = rest.split(',', 9)  # 最多分割9次，得到10个部分

    if len(parts) < 10:
        # 格式不完整，直接返回原行
        return line

    # 前9个字段保持不变（Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect）
    # 第10个字段是文本，需要转换但保留样式标签
    text_field = parts[9]

    # 使用与 SRT 相同的方法处理文本（保留 {...} 样式标签）
    converted_text = _convert_srt_text_with_tags(cc, text_field)

    # 重新组合
    parts[9] = converted_text
    return prefix + ','.join(parts)


def convert_lrc_file(input_path, output_folder, conversion_type, log_callback=None, is_cancelled_callback=None,
                      force_encoding=None):
    """
    将LRC歌词文件转换为繁体/简体
    LRC格式示例：
    [ti:歌名]
    [ar:歌手]
    [al:专辑]
    [00:01.23]歌词文本
    [00:02.34]第二句歌词
    [00:03.45]<01>增强<02>型<03>歌词

    ID标签（ti, ar, al, by, offset等）中的内容也需要转换
    时间标签 [mm:ss.xx] 保持不变
    增强型标签 <xx> 保持不变
    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :param conversion_type: 转换类型
    :param log_callback: 日志回调函数
    :param is_cancelled_callback: 取消检查回调函数
    :return: 转换后的文件路径或False
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 检查是否已取消
    if is_cancelled_callback and is_cancelled_callback():
        return False

    cc = OpenCC(conversion_type)

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            log(f"创建输出目录: {output_folder}")

        log(f"正在处理LRC歌词文件: {os.path.basename(input_path)}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 检测文件编码
        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 读取文件内容
        content = safe_read_file(input_path, encoding, log_callback)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        converted_lines = []

        # ID标签列表，这些标签中的内容也需要转换
        id_tags = ['ti', 'ar', 'al', 'by', 're', 've', 'offset']

        for line in lines:
            # 检查是否已取消
            if is_cancelled_callback and is_cancelled_callback():
                return False

            stripped_line = line.strip()

            if not stripped_line:
                converted_lines.append(line)
                continue

            # 处理 ID 标签 [tag:content]
            id_tag_match = re.match(r'^\[([a-zA-Z]+):(.+)\]$', stripped_line)
            if id_tag_match:
                tag_name = id_tag_match.group(1).lower()
                tag_content = id_tag_match.group(2)

                if tag_name in id_tags:
                    # 转换 ID 标签内容（如歌名、歌手名等）
                    converted_content = cc.convert(tag_content)
                    converted_line = f'[{id_tag_match.group(1)}:{converted_content}]'
                    # 保留原始行的缩进
                    indent = line[:line.index('[')]
                    converted_lines.append(indent + converted_line)
                else:
                    converted_lines.append(line)
                continue

            # 处理歌词行 [mm:ss.xx]歌词文本 或 [mm:ss.xxx]歌词文本
            # 时间标签格式: [mm:ss.xx] 或 [mm:ss.xxx] 或 [mm:ss.xx][mm:ss.xx]（双时间标签）
            time_tag_pattern = r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$'
            time_match = re.match(time_tag_pattern, stripped_line)

            if time_match:
                time_tags = time_match.group(1)  # 时间标签部分，可能有多个
                lyric_text = time_match.group(2)  # 歌词文本部分

                # 转换歌词文本，保留增强型标签 <xx>
                converted_lyric = _convert_lrc_lyric_text(cc, lyric_text)

                # 保留原始行的缩进
                indent = line[:line.index('[')] if '[' in line else ''
                converted_lines.append(indent + time_tags + converted_lyric)
            else:
                # 不匹配任何格式，保持原样
                converted_lines.append(line)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 合并转换后的内容
        converted_content = '\n'.join(converted_lines)

        # 保存文件
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)

        log(f"已保存: {output_path}")
        return output_path

    except Exception as e:
        log(f"处理LRC歌词文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_lrc_lyric_text(cc, text):
    """
    转换LRC歌词文本，保留增强型时间标签 <xx>
    增强型LRC格式: <01>歌<02>词<03>文<04>字
    每个字前面可能有时间标签，用于精确到字的时间同步

    :param cc: OpenCC转换器实例
    :param text: 原始歌词文本
    :return: 转换后的歌词文本
    """
    if not text:
        return text

    result = []
    last_end = 0

    # 匹配增强型时间标签 <数字>
    pattern = re.compile(r'<\d+>')

    for match in pattern.finditer(text):
        # 转换标签之前的歌词文本
        plain_text = text[last_end:match.start()]
        if plain_text:
            result.append(cc.convert(plain_text))

        # 保留增强型时间标签
        result.append(match.group())
        last_end = match.end()

    # 转换最后一个标签之后的歌词文本
    remaining_text = text[last_end:]
    if remaining_text:
        result.append(cc.convert(remaining_text))

    return ''.join(result)


# 全局变量存储三个独立的分词器实例
_jieba_modern = None
_jieba_ancient_simplified = None  # 古汉语-简体主词典分词器（用于 s2t）
_jieba_ancient_traditional = None  # 古汉语-繁体主词典分词器（用于 t2gov, t2gov_keep_simp, t2s）
_jieba_ancient_simplified_userdict_loaded = None  # 记录古汉语简体分词器当前加载的用户词典
_jieba_ancient_traditional_userdict_loaded = None  # 记录古汉语繁体分词器当前加载的用户词典


def _get_jieba_modern(dict_path=None, log_callback=None):
    """
    获取现代汉语分词器实例（单例模式）
    使用 jieba 默认实例，不加载用户自定义词典

    :param dict_path: 主词典路径
    :param log_callback: 日志回调函数
    :return: 现代汉语分词器实例
    """
    global _jieba_modern

    if _jieba_modern is None:
        _jieba_modern = jieba.Tokenizer()
        _jieba_modern.cache_file = "jieba.modern.cache"

        # 设置主词典
        if dict_path and os.path.exists(dict_path):
            _jieba_modern.set_dictionary(dict_path)
            if log_callback:
                log_callback(f"现代汉语分词器已设置主词典: {dict_path}")

        # 初始化
        _jieba_modern.initialize()
        if log_callback:
            log_callback("现代汉语分词器初始化完成")

    return _jieba_modern


def _get_jieba_ancient_simplified(dict_path=None, userdict_path=None, log_callback=None):
    """
    获取古汉语分词器实例（简体主词典，单例模式）
    用于 s2t（简体转规范繁体）转换

    :param dict_path: 主词典路径
    :param userdict_path: 用户自定义词典路径
    :param log_callback: 日志回调函数
    :return: 古汉语简体主词典分词器实例
    """
    global _jieba_ancient_simplified, _jieba_ancient_simplified_userdict_loaded

    if _jieba_ancient_simplified is None:
        _jieba_ancient_simplified = jieba.Tokenizer()
        _jieba_ancient_simplified.cache_file = "jieba.ancient.simplified.cache"

        # 设置主词典
        if dict_path and os.path.exists(dict_path):
            _jieba_ancient_simplified.set_dictionary(dict_path)
            if log_callback:
                log_callback(f"古汉语简体分词器已设置主词典: {dict_path}")

        # 初始化
        _jieba_ancient_simplified.initialize()
        if log_callback:
            log_callback("古汉语简体分词器初始化完成")

    # 加载用户自定义词典（如果指定且未加载）
    if userdict_path and _jieba_ancient_simplified_userdict_loaded != userdict_path:
        if os.path.exists(userdict_path):
            _jieba_ancient_simplified.load_userdict(userdict_path)
            _jieba_ancient_simplified_userdict_loaded = userdict_path
            if log_callback:
                log_callback(f"古汉语简体分词器已加载用户词典: {userdict_path}")
        else:
            if log_callback:
                log_callback(f"用户词典不存在: {userdict_path}")

    return _jieba_ancient_simplified


def _get_jieba_ancient_traditional(dict_path=None, userdict_path=None, log_callback=None):
    """
    获取古汉语分词器实例（繁体主词典，单例模式）
    用于 t2gov、t2gov_keep_simp、t2s 转换

    :param dict_path: 主词典路径
    :param userdict_path: 用户自定义词典路径
    :param log_callback: 日志回调函数
    :return: 古汉语繁体主词典分词器实例
    """
    global _jieba_ancient_traditional, _jieba_ancient_traditional_userdict_loaded

    if _jieba_ancient_traditional is None:
        _jieba_ancient_traditional = jieba.Tokenizer()
        _jieba_ancient_traditional.cache_file = "jieba.ancient.traditional.cache"

        # 设置主词典
        if dict_path and os.path.exists(dict_path):
            _jieba_ancient_traditional.set_dictionary(dict_path)
            if log_callback:
                log_callback(f"古汉语繁体分词器已设置主词典: {dict_path}")

        # 初始化
        _jieba_ancient_traditional.initialize()
        if log_callback:
            log_callback("古汉语繁体分词器初始化完成")

    # 加载用户自定义词典（如果指定且未加载）
    if userdict_path and _jieba_ancient_traditional_userdict_loaded != userdict_path:
        if os.path.exists(userdict_path):
            _jieba_ancient_traditional.load_userdict(userdict_path)
            _jieba_ancient_traditional_userdict_loaded = userdict_path
            if log_callback:
                log_callback(f"古汉语繁体分词器已加载用户词典: {userdict_path}")
        else:
            if log_callback:
                log_callback(f"用户词典不存在: {userdict_path}")

    return _jieba_ancient_traditional


def _segment_with_jieba_modern(text, dict_path=None, log_callback=None):
    """
    使用现代汉语分词器对文本进行分词

    :param text: 待分词的文本
    :param dict_path: 主词典路径
    :param log_callback: 日志回调函数
    :return: 分词后的文本（词之间用 '\x1e' 分隔）
    """
    try:
        tokenizer = _get_jieba_modern(dict_path, log_callback)
        tokens = tokenizer.cut(text, cut_all=False)
        return '\x1e'.join(tokens)
    except Exception as e:
        if log_callback:
            log_callback(f"现代汉语分词失败: {e}，使用原文")
        return text


def _segment_with_jieba_ancient(text, dict_path=None, userdict_path=None, log_callback=None, conversion_type=None):
    """
    使用古汉语分词器对文本进行分词
    根据 conversion_type 选择简体或繁体主词典分词器

    :param text: 待分词的文本
    :param dict_path: 主词典路径
    :param userdict_path: 用户自定义词典路径
    :param log_callback: 日志回调函数
    :param conversion_type: 转换类型，用于选择简体或繁体分词器
    :return: 分词后的文本（词之间用 '\x1e' 分隔）
    """
    try:
        # s2t 使用简体主词典分词器，其他（t2gov, t2gov_keep_simp, t2s）使用繁体主词典分词器
        if conversion_type == 's2t':
            tokenizer = _get_jieba_ancient_simplified(dict_path, userdict_path, log_callback)
        else:
            tokenizer = _get_jieba_ancient_traditional(dict_path, userdict_path, log_callback)
        tokens = tokenizer.cut(text, cut_all=False)
        return '\x1e'.join(tokens)
    except Exception as e:
        if log_callback:
            log_callback(f"古汉语分词失败: {e}，使用原文")
        return text


def get_jieba_dict_path(segment_mode=None, conversion_type=None):
    """
    获取结巴分词主词典文件路径

    :param segment_mode: 分词模式，'jieba_modern' 或 'jieba_ancient'
    :param conversion_type: 转换类型，如 's2t', 't2s', 't2gov' 等
    :return: 主词典文件的完整路径
    """
    jieba_dir = os.path.dirname(jieba.__file__)

    # 现代汉语模式使用默认词典 dict.txt
    if segment_mode == 'jieba_modern':
        return os.path.join(jieba_dir, 'dict.txt')

    # 古汉语模式根据转换类型选择不同的主词典
    if segment_mode == 'jieba_ancient':
        # s2t（简体转规范繁体）使用 dict_ancient_chinese.txt（简体主词典）
        if conversion_type == 's2t':
            return os.path.join(jieba_dir, 'dict_ancient_chinese.txt')
        # t2gov、t2gov_keep_simp（繁体转规范繁体）和 t2s（繁体转简体）使用 dict_ancient_chinese_traditional.txt（繁体主词典）
        elif conversion_type in ('t2gov', 't2gov_keep_simp', 't2s'):
            return os.path.join(jieba_dir, 'dict_ancient_chinese_traditional.txt')
        else:
            # 默认使用简体主词典
            return os.path.join(jieba_dir, 'dict_ancient_chinese.txt')

    # 默认返回默认词典
    return os.path.join(jieba_dir, 'dict.txt')


def get_jieba_userdict_path(conversion_type):
    """
    获取结巴分词用户自定义词典路径

    :param conversion_type: 转换类型
    :return: 用户自定义词典的完整路径
    """
    jieba_dir = os.path.dirname(jieba.__file__)

    # s2t（简体转规范繁体）使用 userdict.txt（简体用户词典）
    # t2gov、t2gov_keep_simp（繁体转规范繁体）和 t2s（繁体转简体）使用 userdict_traditional.txt（繁体用户词典）
    if conversion_type == 's2t':
        return os.path.join(jieba_dir, 'userdict.txt')
    else:
        return os.path.join(jieba_dir, 'userdict_traditional.txt')


def _remove_segment_marks(text):
    """
    移除分词标记，恢复原始文本格式

    :param text: 包含分词标记的文本
    :return: 移除分词标记后的文本
    """
    # 移除 '\x1e' (RS, Record Separator) 分词标记
    return text.replace('\x1e', '')


def convert_txt_file(input_path, output_folder, conversion_type, log_callback=None, is_cancelled_callback=None,
                      force_encoding=None, segment_mode=None):
    """
    将txt文件转换为繁体/简体

    :param input_path: 输入文件路径
    :param output_folder: 输出文件夹路径
    :param conversion_type: 转换类型
    :param log_callback: 日志回调函数
    :param is_cancelled_callback: 取消检查回调函数
    :param force_encoding: 强制指定的编码
    :param segment_mode: 分词模式，可选 'jieba_modern'（结巴-现代汉语）、'jieba_ancient'（结巴-古汉语）或 None（不分词）
    :return: 转换后的文件路径或False
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 检查是否已取消
    if is_cancelled_callback and is_cancelled_callback():
        return False

    cc = OpenCC(conversion_type)

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            log(f"创建输出目录: {output_folder}")

        log(f"正在处理txt文件: {os.path.basename(input_path)}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 检测文件编码
        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 读取文件内容
        content = safe_read_file(input_path, encoding, log_callback)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 分词处理（如果需要）
        # 注意：t2new 和 t2new_keep_simp 模式（只转换繁体旧字形到新字形）不启用分词功能
        if conversion_type in ('t2new', 't2new_keep_simp'):
            if segment_mode in ('jieba_modern', 'jieba_ancient'):
                log(f"转换类型 {conversion_type} 不启用分词功能")
            segment_mode = None

        segmented_content = content
        if segment_mode == 'jieba_modern':
            log("使用结巴分词器（现代汉语）进行分词...")
            dict_path = get_jieba_dict_path(segment_mode, conversion_type)
            segmented_content = _segment_with_jieba_modern(content, dict_path, log_callback)
        elif segment_mode == 'jieba_ancient':
            # 根据转换类型选择不同的分词器和词典
            if conversion_type == 's2t':
                log("使用结巴分词器（古汉语-简体主词典）进行分词...")
            else:
                log("使用结巴分词器（古汉语-繁体主词典）进行分词...")
            # 获取主词典路径（根据转换类型选择不同的主词典）
            dict_path = get_jieba_dict_path(segment_mode, conversion_type)
            # 获取用户自定义词典路径（根据转换类型选择不同的词典）
            userdict_path = get_jieba_userdict_path(conversion_type)
            segmented_content = _segment_with_jieba_ancient(content, dict_path, userdict_path, log_callback, conversion_type)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 繁简转换
        converted_content = cc.convert(segmented_content)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 移除分词标记，恢复原始格式
        if segment_mode in ('jieba_modern', 'jieba_ancient'):
            log("移除分词标记，恢复原始格式...")
            converted_content = _remove_segment_marks(converted_content)

        # 检查是否已取消
        if is_cancelled_callback and is_cancelled_callback():
            return False

        # 保存文件
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)

        log(f"已保存: {output_path}")
        return output_path

    except Exception as e:
        log(f"处理txt文件 {input_path} 时出错: {str(e)}")
        return False
