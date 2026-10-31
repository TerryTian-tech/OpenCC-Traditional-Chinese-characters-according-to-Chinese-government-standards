import os
import sys
import tempfile
import subprocess

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QTextEdit, QFileDialog, QLabel, QProgressBar,
                             QMessageBox, QGroupBox, QComboBox, QCheckBox, QLineEdit,
                             QStyleFactory, QTabWidget, QMenu)
from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QIcon, QColor, QPalette, QKeySequence, QAction, QShortcut

from constants import VERSION
from updater import UpdateChecker
from text_converter import convert_txt_file, convert_srt_file, convert_ass_file, convert_lrc_file
from doc_converter import convert_docx_file
from epub_converter import convert_epub_file
from pdf_converter import convert_pdf_file
from text_tab import AmbiguityOutputEdit, TextConversionWorker
import jieba

# 转换类型显示名 -> OpenCC 配置名（“文件转换”与“文字转换”共用）
CONVERSION_TYPES = {
    "繁体转规范繁体": "t2gov",
    "繁体旧字形转新字形，但保留异体字不转换": "t2new",
    "繁体转规范繁体，但保留文档内原有简体字": "t2gov_keep_simp",
    "繁体旧字形转新字形，但保留文档内原有简体字和异体字": "t2new_keep_simp",
    "繁体转简体": "t2s",
    "只将异体字转为简体字": "v2s",
    "简体转规范繁体": "s2t"
}

class ConversionWorker(QThread):
    """
    转换工作线程，避免UI阻塞
    """
    progress_updated = Signal(int, str)  # 进度信号
    conversion_finished = Signal(bool, str, int, int)  # 完成信号 (success, message, success_count, total_files)
    log_message = Signal(str)  # 日志消息信号

    def __init__(self, input_path, output_folder, conversion_type='t2gov', preserve_format=True,
                 convert_footnotes=True, force_encoding=None, segment_mode=None, input_paths=None,
                 highlight_pdf=False):
        super().__init__()
        self.input_path = input_path
        self.output_folder = output_folder
        self.conversion_type = conversion_type
        self.preserve_format = preserve_format
        self.convert_footnotes = convert_footnotes
        self.force_encoding = force_encoding
        self.segment_mode = segment_mode
        self.input_paths = input_paths
        self.highlight_pdf = highlight_pdf
        self._is_cancelled = False

        # 根据分词模式自动调整OpenCC配置名称
        if self.segment_mode == 'jieba_modern':
            self.conversion_type += '_jieba'
        elif self.segment_mode == 'jieba_ancient':
            self.conversion_type += '_jieba_traditional'

    def run(self):
        try:
            self._success_count = 0
            self._total_files = 0
            success = self.process_files()

            # 检查是否已取消
            if self._is_cancelled:
                self.conversion_finished.emit(False, "转换已被用户取消", 0, 0)
                return

            if self._total_files > 0:
                # 多文件模式
                if self._success_count == self._total_files:
                    self.conversion_finished.emit(True, "转换成功完成", self._success_count, self._total_files)
                elif self._success_count > 0:
                    self.conversion_finished.emit(False, "部分文件转换失败", self._success_count, self._total_files)
                else:
                    self.conversion_finished.emit(False, "转换过程中出现错误", 0, self._total_files)
            else:
                # 单文件模式
                if success:
                    self.conversion_finished.emit(True, "转换成功完成", 1, 1)
                else:
                    self.conversion_finished.emit(False, "转换过程中出现错误", 0, 1)
        except Exception as e:
            self.conversion_finished.emit(False, f"转换失败: {str(e)}", 0, 0)

    def cancel(self):
        """取消转换"""
        self._is_cancelled = True
        self.log_message.emit("用户取消转换")

    def process_files(self):
        """处理文件的主要逻辑"""
        # 检查是否已取消
        if self._is_cancelled:
            return False

        self.progress_updated.emit(0, "开始处理...")

        # 检查是否已取消
        if self._is_cancelled:
            return False

        # 处理文件列表（多选文件模式）
        if self.input_paths:
            total_files = len(self.input_paths)
            self.log_message.emit(f"找到 {total_files} 个文件待处理")
            success_count = 0

            for i, file_path in enumerate(self.input_paths, 1):
                if self._is_cancelled:
                    return False

                progress = int((i / total_files) * 100)
                self.progress_updated.emit(progress, f"处理文件 {i}/{total_files}: {os.path.basename(file_path)}")

                file_ext = os.path.splitext(file_path)[1].lower()

                if file_ext == '.docx':
                    try:
                        result = convert_docx_file(
                            file_path, self.output_folder, self.conversion_type,
                            self.preserve_format,
                            self.convert_footnotes,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        )
                        if result:
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                elif file_ext == '.txt':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.force_encoding,
                        self.segment_mode
                    ):
                        success_count += 1

                elif file_ext == '.md':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.srt':
                    if convert_srt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext in ['.ass', '.ssa']:
                    if convert_ass_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.lrc':
                    if convert_lrc_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.epub':
                    try:
                        if convert_epub_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                elif file_ext == '.pdf':
                    try:
                        if convert_pdf_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled,
                            self.highlight_pdf
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                else:
                    self.log_message.emit(f"跳过不支持的文件: {os.path.basename(file_path)}")

            self.log_message.emit(f"处理完成！成功转换 {success_count}/{total_files} 个文件")
            self.progress_updated.emit(100, "转换完成!")
            self._success_count = success_count
            self._total_files = total_files
            return success_count == total_files

        # 处理单个文件
        if os.path.isfile(self.input_path):
            file_ext = os.path.splitext(self.input_path)[1].lower()

            # 检查是否已取消
            if self._is_cancelled:
                return False

            if file_ext == '.docx':
                result = convert_docx_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    self.preserve_format,
                    self.convert_footnotes,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.txt':
                result = convert_txt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled,
                    self.force_encoding,
                    self.segment_mode
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.md':
                result = convert_txt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.srt':
                result = convert_srt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext in ['.ass', '.ssa']:
                result = convert_ass_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.lrc':
                result = convert_lrc_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.epub':
                result = convert_epub_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.pdf':
                try:
                    result = convert_pdf_file(
                        self.input_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.highlight_pdf
                    )
                except Exception as e:
                    self.log_message.emit(f"处理 {os.path.basename(self.input_path)} 时出错: {str(e)}")
                    result = False
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            else:
                self.log_message.emit("错误：不支持的文件格式，仅支持docx、txt、md、srt、ass、ssa、lrc、epub、pdf文件")
                return False

        # 处理文件夹
        elif os.path.isdir(self.input_path):
            # 获取所有支持的文件
            supported_files = []
            for f in os.listdir(self.input_path):
                # 检查是否已取消
                if self._is_cancelled:
                    return False

                file_ext = os.path.splitext(f)[1].lower()
                if file_ext in ['.docx', '.txt', '.md', '.srt', '.ass', '.ssa', '.lrc', '.epub', '.pdf']:
                    supported_files.append(f)

            if not supported_files:
                self.log_message.emit("在指定文件夹中未找到支持的docx、txt、md、srt、ass、ssa、lrc、epub、pdf文件")
                return False

            self.log_message.emit(f"找到 {len(supported_files)} 个文件待处理")

            success_count = 0
            total_files = len(supported_files)

            for i, filename in enumerate(supported_files, 1):
                # 检查是否已取消
                if self._is_cancelled:
                    return False

                progress = int((i / total_files) * 100)
                self.progress_updated.emit(progress, f"处理文件 {i}/{total_files}: {filename}")

                file_path = os.path.join(self.input_path, filename)
                file_ext = os.path.splitext(filename)[1].lower()

                if file_ext == '.docx':
                    # 对于docx文件，使用转换器类
                    try:
                        result = convert_docx_file(
                            file_path, self.output_folder, self.conversion_type,
                            self.preserve_format,
                            self.convert_footnotes,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        )
                        if result:
                            success_count += 1

                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

                elif file_ext == '.txt':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.force_encoding,
                        self.segment_mode
                    ):
                        success_count += 1

                elif file_ext == '.md':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.srt':
                    if convert_srt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext in ['.ass', '.ssa']:
                    if convert_ass_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.lrc':
                    if convert_lrc_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.epub':
                    try:
                        if convert_epub_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

                elif file_ext == '.pdf':
                    try:
                        if convert_pdf_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled,
                            self.highlight_pdf
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

            self.log_message.emit(f"处理完成！成功转换 {success_count}/{total_files} 个文件")
            self.progress_updated.emit(100, "转换完成!")
            self._success_count = success_count
            self._total_files = total_files
            return success_count == total_files

        else:
            self.log_message.emit("错误：输入的路径既不是有效的文件也不是文件夹")
            return False


class ModernUI(QMainWindow):
    def __init__(self):
        super().__init__()

        # 初始化设置存储
        self.settings = QSettings("TraditionalConverter", "AppSettings")

        # 从设置中加载主题，如果不存在则使用默认的暗色主题
        saved_theme = self.settings.value("theme", "dark")
        self.current_theme = saved_theme

        # 存储多选文件列表
        self.selected_files = []
        self.init_ui()

    def init_ui(self):
        # 设置窗口属性，版本号使用 VERSION 常量
        self.setWindowTitle(f"规范繁体字形转换器 V{VERSION} for Linux")
        self.setGeometry(100, 100, 900, 750)
        self.setMinimumSize(800, 600)

        # 设置窗口图标 - 新增的logo功能
        self.setWindowIcon(QIcon(self.get_logo_path()))

        # 应用默认主题
        self.apply_theme(self.current_theme)

        # 创建主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 创建主布局
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(15)

        # 标题区域
        title_label = QLabel("规范繁体字形转换器")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("titleLabel")
        main_layout.addWidget(title_label)

        # 创建选项卡
        tab_widget = QTabWidget()
        tab_widget.addTab(self.create_conversion_tab(), "文件转换")
        tab_widget.addTab(self.create_text_tab(), "文字转换")
        tab_widget.addTab(self.create_settings_tab(), "设置")
        tab_widget.addTab(self.create_about_tab(), "关于")
        main_layout.addWidget(tab_widget)

        # 状态栏
        self.statusBar().showMessage("就绪")

    def get_logo_path(self):
        """
        获取logo文件的路径
        程序会按以下顺序查找logo文件：
        1. 与程序同目录下的"logo.ico"
        2. 与程序同目录下的"logo.png"
        3. 程序内部资源（如果没有外部文件，则返回空）
        """
        # 尝试查找logo.ico文件
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
        if os.path.exists(logo_path):
            return logo_path

        # 尝试查找logo.png文件
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(logo_path):
            return logo_path

        # 如果没有找到外部文件，可以创建一个临时的logo
        # 这里我们创建一个简单的程序内建图标作为fallback
        return ""

    def create_settings_tab(self):
        """创建设置选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(20)

        # 主题设置区域
        theme_group = QGroupBox("主题设置")
        theme_layout = QVBoxLayout(theme_group)
        theme_layout.setSpacing(15)
        theme_layout.setContentsMargins(15, 20, 15, 15)

        # 主题选择说明
        theme_label = QLabel("选择您喜欢的界面主题:")
        theme_layout.addWidget(theme_label)

        # 主题选择按钮
        theme_button_layout = QHBoxLayout()

        # 创建复选框（互斥：勾选一个自动取消另一个）
        self.dark_theme_cb = QCheckBox("暗色主题")
        self.light_theme_cb = QCheckBox("浅色主题")

        # 根据保存的主题设置默认选中
        if self.current_theme == "dark":
            self.dark_theme_cb.setChecked(True)
        else:
            self.light_theme_cb.setChecked(True)

        # 将复选框添加到布局
        theme_button_layout.addWidget(self.dark_theme_cb)
        theme_button_layout.addWidget(self.light_theme_cb)
        theme_button_layout.addStretch()

        theme_layout.addLayout(theme_button_layout)

        # 连接信号：勾选一个时自动取消另一个（互斥行为）
        self.dark_theme_cb.stateChanged.connect(lambda state: self.on_theme_changed("dark", state))
        self.light_theme_cb.stateChanged.connect(lambda state: self.on_theme_changed("light", state))

        layout.addWidget(theme_group)

        # 文件编码检测设置区域
        encoding_group = QGroupBox("编码检测设置")
        encoding_group_layout = QVBoxLayout(encoding_group)
        encoding_group_layout.setSpacing(15)
        encoding_group_layout.setContentsMargins(15, 20, 15, 15)

        # 编码检测说明
        encoding_desc = QLabel(
            "指定读取TXT等文本文件时使用的编码，默认为自动检测。\n"
            "如果自动检测识别错误（例如Big5编码被误识别为GB18030），\n"
            "可在此手动强制指定编码。"
        )
        encoding_desc.setWordWrap(True)
        encoding_group_layout.addWidget(encoding_desc)

        # 编码选择
        encoding_select_layout = QHBoxLayout()
        encoding_select_layout.setSpacing(10)
        encoding_select_layout.addWidget(QLabel("强制编码:"))

        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem("自动检测", None)
        self.encoding_combo.addItem("强制使用 GB18030", "gb18030")
        self.encoding_combo.addItem("强制使用 GBK", "gbk")
        self.encoding_combo.addItem("强制使用 GB2312", "gb2312")
        self.encoding_combo.addItem("强制使用 UTF-8", "utf-8")
        self.encoding_combo.addItem("强制使用 Big5", "cp950")
        self.encoding_combo.addItem("强制使用 Big5-HKSCS", "big5-hkscs")
        self.encoding_combo.setToolTip(
            "对于TXT等文本文件，指定读取时使用的编码。\n"
            "默认为自动检测；如果自动检测识别错误，可手动强制指定。"
        )

        # 从设置中恢复编码选择
        saved_encoding_index = self.settings.value("encoding_index", 0, type=int)
        if 0 <= saved_encoding_index < self.encoding_combo.count():
            self.encoding_combo.setCurrentIndex(saved_encoding_index)

        self.encoding_combo.currentIndexChanged.connect(self.on_encoding_changed)
        encoding_select_layout.addWidget(self.encoding_combo)
        encoding_group_layout.addLayout(encoding_select_layout)

        layout.addWidget(encoding_group)

        # 分词设置区域
        segment_group = QGroupBox("分词设置")
        segment_layout = QVBoxLayout(segment_group)
        segment_layout.setSpacing(15)
        segment_layout.setContentsMargins(15, 20, 15, 15)

        # 分词说明
        segment_desc = QLabel(
            "分词功能可以在转换前对文本进行分词预处理，以提高转换准确性。"
        )
        segment_desc.setWordWrap(True)
        segment_layout.addWidget(segment_desc)

        # 分词选项
        segment_options_layout = QVBoxLayout()

        # 不分词选项
        self.no_segment_cb = QCheckBox("不分词（默认）")
        self.no_segment_cb.setChecked(True)
        self.no_segment_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("none", state))

        # 结巴分词（现代汉语）选项
        self.jieba_modern_cb = QCheckBox("转换前使用结巴分词进行文本分词预处理")
        self.jieba_modern_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("jieba_modern", state))

        # 结巴分词（古汉语）选项
        self.jieba_ancient_cb = QCheckBox("转换前使用结巴分词对古汉语文本进行分词预处理")
        self.jieba_ancient_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("jieba_ancient", state))

        segment_options_layout.addWidget(self.no_segment_cb)
        segment_options_layout.addWidget(self.jieba_modern_cb)
        segment_options_layout.addWidget(self.jieba_ancient_cb)
        segment_layout.addLayout(segment_options_layout)

        # 从设置中恢复分词选择
        saved_segment_mode = self.settings.value("segment_mode", "none")
        self._apply_segment_mode(saved_segment_mode)

        layout.addWidget(segment_group)

        # PDF校对模式设置区域
        pdf_group = QGroupBox("PDF校对模式")
        pdf_group_layout = QVBoxLayout(pdf_group)
        pdf_group_layout.setSpacing(15)
        pdf_group_layout.setContentsMargins(15, 20, 15, 15)

        # 校对模式说明
        pdf_desc = QLabel(
            "勾选后，PDF转换输出的文档中被转换修改的汉字将以黄色底纹标出，便于校对。"
        )
        pdf_desc.setWordWrap(True)
        pdf_group_layout.addWidget(pdf_desc)

        self.highlight_pdf_cb = QCheckBox("PDF转换后为被修改的字添加黄色高亮")
        self.highlight_pdf_cb.setToolTip("仅对PDF文件生效：转换中被修改的汉字以黄色底纹标出")
        self.highlight_pdf_cb.stateChanged.connect(self.on_pdf_highlight_changed)
        pdf_group_layout.addWidget(self.highlight_pdf_cb)

        # 从设置中恢复PDF校对模式
        saved_pdf_highlight = self.settings.value("pdf_highlight", False, type=bool)
        self.highlight_pdf_cb.setChecked(saved_pdf_highlight)

        layout.addWidget(pdf_group)

        layout.addStretch()
        return tab

    def _apply_segment_mode(self, mode):
        """应用分词模式设置"""
        self.no_segment_cb.setChecked(False)
        self.jieba_modern_cb.setChecked(False)
        self.jieba_ancient_cb.setChecked(False)

        if mode == "jieba_modern":
            self.jieba_modern_cb.setChecked(True)
        elif mode == "jieba_ancient":
            self.jieba_ancient_cb.setChecked(True)
        else:
            self.no_segment_cb.setChecked(True)

    def on_segment_mode_changed(self, mode, state):
        """分词模式更改事件处理（互斥逻辑）"""
        if state != Qt.CheckState.Checked.value:
            return

        # 互斥：勾选一个时取消其他
        if mode == "none":
            self.jieba_modern_cb.setChecked(False)
            self.jieba_ancient_cb.setChecked(False)
        elif mode == "jieba_modern":
            self.no_segment_cb.setChecked(False)
            self.jieba_ancient_cb.setChecked(False)
        elif mode == "jieba_ancient":
            self.no_segment_cb.setChecked(False)
            self.jieba_modern_cb.setChecked(False)

        # 保存设置
        self.settings.setValue("segment_mode", mode)
        mode_display = {
            "none": "不分词",
            "jieba_modern": "结巴分词（现代汉语）",
            "jieba_ancient": "结巴分词（古汉语）"
        }
        self.statusBar().showMessage(f"分词设置已更改为: {mode_display.get(mode, '不分词')}")

    def on_encoding_changed(self, index):
        """编码设置更改事件处理"""
        self.settings.setValue("encoding_index", index)
        encoding_name = self.encoding_combo.currentText()
        self.statusBar().showMessage(f"编码设置已更改为: {encoding_name}")

    def on_pdf_highlight_changed(self, state):
        """PDF校对模式（修改字黄色高亮）更改事件处理"""
        checked = self.highlight_pdf_cb.isChecked()
        self.settings.setValue("pdf_highlight", checked)
        self.statusBar().showMessage(
            f"PDF校对模式（修改字黄色高亮）已{'开启' if checked else '关闭'}")

    def on_theme_changed(self, theme, state):
        """主题更改事件处理（复选框互斥逻辑）"""
        if state != Qt.CheckState.Checked.value:
            return
        # 互斥：勾选一个时取消另一个
        if theme == "dark":
            self.light_theme_cb.setChecked(False)
        else:
            self.dark_theme_cb.setChecked(False)
        self.change_theme(theme)

    def apply_theme(self, theme):
        """应用指定主题"""
        if theme == "dark":
            self.apply_dark_theme()
        else:
            self.apply_light_theme()

    def apply_dark_theme(self):
        """应用暗色主题"""
        self.current_theme = "dark"

        # 设置暗色调色板
        dark_palette = QPalette()
        dark_palette.setColor(QPalette.Window, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.WindowText, Qt.white)
        dark_palette.setColor(QPalette.Base, QColor(30, 30, 30))
        dark_palette.setColor(QPalette.AlternateBase, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.ToolTipBase, Qt.white)
        dark_palette.setColor(QPalette.ToolTipText, Qt.black)
        dark_palette.setColor(QPalette.Text, Qt.white)
        dark_palette.setColor(QPalette.Button, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.ButtonText, Qt.white)
        dark_palette.setColor(QPalette.BrightText, Qt.red)
        dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.HighlightedText, Qt.black)
        QApplication.instance().setPalette(dark_palette)

        # 设置暗色样式表
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
            }
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
                font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            }
            QPushButton {
                background-color: #375a7f;
                border: none;
                color: white;
                padding: 10px;
                font-size: 14px;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #4a77a8;
            }
            QPushButton:pressed {
                background-color: #2c4a69;
            }
            QPushButton#startButton {
                background-color: #00bc8c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#startButton:hover {
                background-color: #00e6ac;
            }
            QPushButton#aiButton {
                background-color: #64b5f6;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#aiButton:hover {
                background-color: #90caf9;
            }
            QPushButton#browseButton {
                background-color: #3498db;
            }
            QPushButton#browseButton:hover {
                background-color: #5dade2;
            }
            QLineEdit, QTextEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 8px;
                border-radius: 4px;
            }
            QGroupBox {
                border: 1px solid #555555;
                border-radius: 5px;
                margin-top: 1ex;
                font-weight: bold;
                color: #3498db;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #3498db;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                height: 20px;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #00bc8c;
                width: 20px;
            }
            QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 5px;
                border-radius: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: white;
            }
            QLabel {
                color: #aaaaaa;
            }
            QLabel#titleLabel {
                font-size: 24px;
                font-weight: bold;
                color: #00bc8c;
                margin: 10px;
            }
            QListWidget {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
            }
            QCheckBox:disabled {
                color: #777777;
            }
            QTabWidget::pane {
                border: 1px solid #555555;
                background-color: #2b2b2b;
            }
            QTabBar::tab {
                background-color: #3c3c3c;
                color: #aaaaaa;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #375a7f;
                color: white;
            }
            QTabBar::tab:hover:!selected {
                background-color: #4a77a8;
            }

            QPushButton#cancelButton {
                background-color: #e74c3c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#cancelButton:hover {
                background-color: #c0392b;
            }
        """)

    def apply_light_theme(self):
        """应用浅色主题"""
        self.current_theme = "light"

        # 设置浅色调色板
        light_palette = QPalette()
        light_palette.setColor(QPalette.Window, QColor(240, 240, 240))
        light_palette.setColor(QPalette.WindowText, Qt.black)
        light_palette.setColor(QPalette.Base, Qt.white)
        light_palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
        light_palette.setColor(QPalette.ToolTipBase, Qt.white)
        light_palette.setColor(QPalette.ToolTipText, Qt.black)
        light_palette.setColor(QPalette.Text, Qt.black)
        light_palette.setColor(QPalette.Button, QColor(240, 240, 240))
        light_palette.setColor(QPalette.ButtonText, Qt.black)
        light_palette.setColor(QPalette.BrightText, Qt.red)
        light_palette.setColor(QPalette.Link, QColor(0, 120, 215))
        light_palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
        light_palette.setColor(QPalette.HighlightedText, Qt.white)
        QApplication.instance().setPalette(light_palette)

        # 设置浅色样式表
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QWidget {
                background-color: #f0f0f0;
                color: #333333;
                font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            }
            QPushButton {
                background-color: #4a86e8;
                border: none;
                color: white;
                padding: 10px;
                font-size: 14px;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #6a9ce8;
            }
            QPushButton:pressed {
                background-color: #3a76c8;
            }
            QPushButton#startButton {
                background-color: #4caf50;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#startButton:hover {
                background-color: #66bb6a;
            }
            QPushButton#aiButton {
                background-color: #64b5f6;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#aiButton:hover {
                background-color: #90caf9;
            }
            QPushButton#browseButton {
                background-color: #2196f3;
            }
            QPushButton#browseButton:hover {
                background-color: #42a5f5;
            }
            QLineEdit, QTextEdit {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
                padding: 8px;
                border-radius: 4px;
            }
            QGroupBox {
                border: 1px solid #cccccc;
                border-radius: 5px;
                margin-top: 1ex;
                font-weight: bold;
                color: #555555;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #555555;
            }
            QProgressBar {
                border: 1px solid #cccccc;
                border-radius: 5px;
                text-align: center;
                height: 20px;
                color: #333333;
            }
            QProgressBar::chunk {
                background-color: #4caf50;
                width: 20px;
            }
            QComboBox {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
                padding: 5px;
                border-radius: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                color: #333333;
                border: 1px solid #cccccc;
            }
            QLabel {
                color: #555555;
            }
            QLabel#titleLabel {
                font-size: 24px;
                font-weight: bold;
                color: #4caf50;
                margin: 10px;
            }
            QListWidget {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
            }
            QCheckBox:disabled {
                color: #aaaaaa;
            }
            QTabWidget::pane {
                border: 1px solid #cccccc;
                background-color: #f0f0f0;
            }
            QTabBar::tab {
                background-color: #e0e0e0;
                color: #555555;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #4a86e8;
                color: white;
            }
            QTabBar::tab:hover:!selected {
                background-color: #d0d0d0;
            }

            QPushButton#cancelButton {
                background-color: #e74c3c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#cancelButton:hover {
                background-color: #c0392b;
            }
        """)

    def change_theme(self, theme):
        """更改主题"""
        if theme != self.current_theme:
            self.apply_theme(theme)
            # 保存主题设置
            self.settings.setValue("theme", theme)
            self.statusBar().showMessage(f"已切换至{theme}主题，设置已保存")

    def save_settings(self):
        """保存所有设置"""
        # 保存主题
        self.settings.setValue("theme", self.current_theme)
        # 保存编码设置
        self.settings.setValue("encoding_index", self.encoding_combo.currentIndex())
        # 保存分词设置
        if self.jieba_modern_cb.isChecked():
            segment_mode = 'jieba_modern'
        elif self.jieba_ancient_cb.isChecked():
            segment_mode = 'jieba_ancient'
        else:
            segment_mode = 'none'
        self.settings.setValue("segment_mode", segment_mode)

        # 保存PDF校对模式设置
        self.settings.setValue("pdf_highlight", self.highlight_pdf_cb.isChecked())

    def create_conversion_tab(self):
        """创建转换选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)  # 增加垂直间距
        layout.setContentsMargins(15, 15, 15, 15)  # 增加边距

        # 文件选择区域
        file_group = QGroupBox("文件选择")
        file_layout = QVBoxLayout(file_group)
        file_layout.setSpacing(12)  # 增加内部控件间距
        file_layout.setContentsMargins(15, 20, 15, 15)  # 增加内边距，顶部更多

        # 输入路径
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("请选择要转换的文件或文件夹...")
        input_browse_btn = QPushButton("浏览")
        input_browse_btn.setObjectName("browseButton")
        input_browse_btn.clicked.connect(self.browse_input)
        self.input_edit.textEdited.connect(self.on_input_edited)
        input_layout.addWidget(QLabel("输入路径:"))
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(input_browse_btn)
        file_layout.addLayout(input_layout)

        # 输出路径
        output_layout = QHBoxLayout()
        output_layout.setSpacing(10)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("请选择输出文件夹...")
        output_browse_btn = QPushButton("浏览")
        output_browse_btn.setObjectName("browseButton")
        output_browse_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(QLabel("输出路径:"))
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_browse_btn)
        file_layout.addLayout(output_layout)

        layout.addWidget(file_group)

        # 转换选项区域
        options_group = QGroupBox("转换选项")
        options_layout = QHBoxLayout(options_group)
        options_layout.setSpacing(30)  # 增加选项之间的水平间距
        options_layout.setContentsMargins(15, 20, 15, 15)  # 增加内边距

        # 转换类型选择
        type_layout = QVBoxLayout()
        type_layout.setSpacing(8)
        type_layout.addWidget(QLabel("转换类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("繁体转规范繁体")
        self.type_combo.addItem("繁体旧字形转新字形，但保留异体字不转换")
        self.type_combo.addItem("繁体转规范繁体，但保留文档内原有简体字")
        self.type_combo.addItem("繁体旧字形转新字形，但保留文档内原有简体字和异体字")
        self.type_combo.addItem("繁体转简体")
        self.type_combo.addItem("只将异体字转为简体字")
        self.type_combo.addItem("简体转规范繁体")
        type_layout.addWidget(self.type_combo)
        options_layout.addLayout(type_layout)

        # 高级选项
        advanced_layout = QVBoxLayout()
        advanced_layout.setSpacing(10)

        # 保留格式选项 - 设置为不可用
        self.preserve_format_cb = QCheckBox("尽量保留Word文档的原有格式")
        self.preserve_format_cb.setChecked(True)
        self.preserve_format_cb.setEnabled(True)  # 设置为可用
        self.preserve_format_cb.setToolTip("是否保留Word文档的原有格式")

        # 转换脚注选项 - 设置为可用
        self.convert_footnotes_cb = QCheckBox("转换Word文档里的脚注和尾注")
        self.convert_footnotes_cb.setChecked(True)
        self.convert_footnotes_cb.setEnabled(True)  # 设置为可用
        self.convert_footnotes_cb.setToolTip("是否转换文档中的脚注和尾注内容")

        advanced_layout.addWidget(self.preserve_format_cb)
        advanced_layout.addWidget(self.convert_footnotes_cb)
        options_layout.addLayout(advanced_layout)

        layout.addWidget(options_group)

        # 控制按钮区域
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_conversion)
        control_layout.addWidget(self.start_button)

        self.ai_button = QPushButton("AI转换")
        self.ai_button.setObjectName("aiButton")
        self.ai_button.clicked.connect(self.ai_conversion)
        control_layout.addWidget(self.ai_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setEnabled(False)  # 初始状态不可用
        self.cancel_button.clicked.connect(self.cancel_conversion)
        control_layout.addWidget(self.cancel_button)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        # 进度区域
        progress_group = QGroupBox("进度")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(30)
        progress_layout.setContentsMargins(15, 20, 15, 15)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("准备就绪")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(150)
        self.log_text.setReadOnly(True)

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.log_text)

        layout.addWidget(progress_group)

        return tab

    def create_about_tab(self):
        """创建关于选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # 描述区域
        desc_label = QLabel(f"""
        <h2>规范繁体字形转换器 V{VERSION} for Linux</h2>
        <p>专业的繁体字形转换工具，助您将繁体旧字形、异体字和港台标准的繁体字形转换为《通用规范汉字表》的规范繁体字形。</p>
        <p><b>主要特性:</b></p>
        <ul>
            <li>支持Word文档、TXT文本文件、EPUB文件、字幕文件的繁体字形转换</li>
            <li>有限支持PDF文件的繁体字形转换</li>
            <li>基于《通用规范汉字表》</li>
            <li>转换后保留原文档格式</li>
            <li>支持批量处理文件</li>
            <li>多种转换预设模式适合处理情况复杂的文档</li>
        </ul>
        <p><b>请从以下页面获取本工具最新版本：</p>
        <ul>
              <p>主仓库（Github）：https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards
              <p>镜像1（Gitee）：https://gitee.com/terrytian-tech/tonggui-traditional-chinese
              <p>镜像2（GitCode）：https://gitcode.com/TerryTian-tech/OpenCC-Tonggui-Traditional-Chinese
        </ul>
        <p><b>本软件遵循Apache-2.0开源协议发布。</p>
        <p>交流繁简转换相关问题可进QQ群：1055649831。</p>
        """)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignLeft)
        layout.addWidget(desc_label)
        check_update_btn = QPushButton("检查更新")
        check_update_btn.setObjectName("browseButton")
        check_update_btn.clicked.connect(self.check_for_updates)
        layout.addWidget(check_update_btn, alignment=Qt.AlignCenter)

        layout.addStretch()
        return tab

    # 检查更新方法
    def check_for_updates(self):
        """检查是否有新版本"""
        self.statusBar().showMessage("正在检查更新...")
        self.update_checker = UpdateChecker()
        self.update_checker.update_checked.connect(self.on_update_checked)
        self.update_checker.start()

    # 处理更新检查结果
    def on_update_checked(self, has_new, latest_version, url):
        if has_new:
            reply = QMessageBox.question(
                self,
                "发现新版本",
                f"当前版本：{VERSION}\n最新版本：{latest_version}\n\n是否前往下载页面？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                import webbrowser
                webbrowser.open(url)
        elif latest_version == '' and '失败' in url:
            # 错误情况
            QMessageBox.warning(self, "检查更新失败", url)
        else:
            QMessageBox.information(self, "检查更新", f"当前已是最新版本{VERSION}。")
        self.statusBar().showMessage("就绪")

    def browse_input(self):
        """浏览输入路径"""

        # 使用标准QMessageBox，但修改按钮文本
        msg_box = QMessageBox(
            QMessageBox.Question,
            "选择类型",
            "批量转换同一目录下所有文档请选择文件夹，转换单个或多个文档请选择文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            self
        )

        # 修改按钮文本
        yes_button = msg_box.button(QMessageBox.StandardButton.Yes)
        no_button = msg_box.button(QMessageBox.StandardButton.No)
        cancel_button = msg_box.button(QMessageBox.StandardButton.Cancel)
        yes_button.setText("选择文件夹")
        no_button.setText("选择文件")
        cancel_button.setText("取消")

        # 显示对话框并等待用户选择
        choice = msg_box.exec()

        if choice == QMessageBox.StandardButton.Yes:  # 文件夹
            path = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
            if path:
                self.input_edit.setText(path)
                self.selected_files = []  # 清空多选文件列表
        elif choice == QMessageBox.StandardButton.No:  # 文件
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择文件", "",
                "文档文件 (*.docx *.txt *.md *.srt *.ass *.ssa *.lrc *.epub *.pdf);;所有文件 (*)"
            )
            if paths:
                self.selected_files = paths
                if len(paths) == 1:
                    self.input_edit.setText(paths[0])
                else:
                    self.input_edit.setText(f"已选择 {len(paths)} 个文件")

    def on_input_edited(self):
        """用户手动编辑输入框时，清空多选文件列表"""
        self.selected_files = []
        # 如果当前显示的是多选占位文本，清空输入框以避免状态不同步
        text = self.input_edit.text()
        if text.startswith("已选择 ") and text.endswith(" 个文件"):
            self.input_edit.clear()
            
    def browse_output(self):
        """浏览输出路径"""
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if path:
            self.output_edit.setText(path)

    def ai_conversion(self):
        """AI转换：自动搜索当前目录下的LLMCC可执行文件并启动"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        exe_name = "LLMCC.exe" if sys.platform == "win32" else "LLMCC"
        llmcc_path = os.path.join(current_dir, "LLMCC", exe_name)

        if os.path.exists(llmcc_path):
            try:
                subprocess.Popen([llmcc_path])
                self.append_log(f"已启动AI转换工具: {llmcc_path}")
                self.statusBar().showMessage("AI转换工具已启动")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"启动AI转换工具失败:\n{str(e)}")
                self.append_log(f"启动AI转换工具失败: {str(e)}")
        else:
            QMessageBox.warning(self, "未找到", f"未找到AI转换工具:\n{llmcc_path}\n\n请确保LLMCC文件夹及{exe_name}存在于程序目录下。")
            self.append_log(f"未找到AI转换工具: {llmcc_path}")

    def start_conversion(self):
        """开始转换"""
        input_path = self.input_edit.text()
        output_path = self.output_edit.text()

        if not input_path or not output_path:
            QMessageBox.warning(self, "警告", "请输入完整的路径信息")
            return

        # 验证输入路径
        if self.selected_files:
            # 多文件模式：验证每个文件是否存在
            missing_files = [f for f in self.selected_files if not os.path.exists(f)]
            if missing_files:
                QMessageBox.critical(self, "错误", f"以下文件不存在:\n" + "\n".join(missing_files))
                return
        else:
            if not os.path.exists(input_path):
                QMessageBox.critical(self, "错误", "输入路径不存在")
                return

        # 获取转换类型
        conversion_type = CONVERSION_TYPES[self.type_combo.currentText()]

        # 获取转换选项的实际值
        preserve_format = self.preserve_format_cb.isChecked()
        convert_footnotes = self.convert_footnotes_cb.isChecked()
        highlight_pdf = self.highlight_pdf_cb.isChecked()

        # 获取强制编码选项
        force_encoding = self.encoding_combo.currentData()

        # 获取分词模式
        if self.jieba_modern_cb.isChecked():
            segment_mode = 'jieba_modern'
        elif self.jieba_ancient_cb.isChecked():
            segment_mode = 'jieba_ancient'
        else:
            segment_mode = None

        # 如果已有转换线程在运行，先取消并等待其结束
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
            
        # 启动转换线程
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.log_text.clear()

        # 在日志中显示当前设置
        segment_mode_display = {
            None: '不分词',
            'jieba_modern': '结巴分词（现代汉语）',
            'jieba_ancient': '结巴分词（古汉语）'
        }
        self.append_log(f"转换设置：保留格式={preserve_format}，转换脚注={convert_footnotes}，强制编码={force_encoding or '自动'}，分词模式={segment_mode_display.get(segment_mode, '不分词')}，PDF修改字高亮={'开' if highlight_pdf else '关'}")

        self.worker = ConversionWorker(
            input_path,
            output_path,
            conversion_type,
            preserve_format,  # 使用复选框的实际值
            convert_footnotes,  # 使用复选框的实际值
            force_encoding,
            segment_mode,
            input_paths=self.selected_files if self.selected_files else None,
            highlight_pdf=highlight_pdf
        )
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.conversion_finished.connect(self.conversion_finished)
        self.worker.log_message.connect(self.append_log)
        self.worker.start()

    def update_progress(self, value, message):
        """更新进度"""
        self.progress_bar.setValue(value)
        self.progress_label.setText(message)
        self.append_log(f"[{value}%] {message}")

    def append_log(self, message):
        """添加日志消息"""
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def cancel_conversion(self):
        """取消转换"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()  # 调用自定义的取消方法
            self.append_log("正在取消转换...")
            self.statusBar().showMessage("正在取消转换...")

    def conversion_finished(self, success, message, success_count, total_files):
        """转换完成"""
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)  # 转换完成后禁用取消按钮

        if success:
            QMessageBox.information(self, "成功", message)
            self.statusBar().showMessage("转换完成")
        else:
            if "取消" in message or "已取消" in message:
                QMessageBox.information(self, "已取消", "转换已被用户取消")
                self.statusBar().showMessage("转换已取消")
            elif "部分" in message:
                QMessageBox.warning(self, "部分失败", f"成功转换 {success_count}/{total_files} 个文件，部分文件转换失败")
                self.statusBar().showMessage("部分转换失败")
            else:
                QMessageBox.critical(self, "错误", message)
                self.statusBar().showMessage("转换失败")

    # ------------------------------------------------------------------
    # 文字转换选项卡
    # ------------------------------------------------------------------

    def create_text_tab(self):
        """创建文字转换选项卡：顶部选转换类型与候选字显示，左侧输入、右侧输出"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        # 顶部选项：转换类型 + 是否显示候选字 + 转换按钮
        options_group = QGroupBox("转换选项")
        options_layout = QHBoxLayout(options_group)
        options_layout.setSpacing(15)
        options_layout.setContentsMargins(15, 12, 15, 12)

        options_layout.addWidget(QLabel("转换类型:"))
        self.text_type_combo = QComboBox()
        for display_name in CONVERSION_TYPES:
            self.text_type_combo.addItem(display_name)
        # 恢复上次选择的转换类型（先设索引再连信号，避免初始化时误触发）
        saved_index = self.settings.value("text_conversion_index", 0, type=int)
        if 0 <= saved_index < self.text_type_combo.count():
            self.text_type_combo.setCurrentIndex(saved_index)
        self.text_type_combo.currentIndexChanged.connect(self.on_text_type_changed)
        options_layout.addWidget(self.text_type_combo, 1)

        self.show_candidates_cb = QCheckBox("显示候选字（红色波浪线标注歧义）")
        self.show_candidates_cb.setChecked(True)
        self.show_candidates_cb.setToolTip(
            "转换结果中的一对多歧义词以红色波浪线标注，\n点击波浪线可查看该词的全部候选转换值。")
        self.show_candidates_cb.stateChanged.connect(self.on_show_candidates_changed)
        options_layout.addWidget(self.show_candidates_cb)

        self.text_convert_btn = QPushButton("转换")
        self.text_convert_btn.setObjectName("startButton")
        self.text_convert_btn.clicked.connect(self.on_text_convert)
        options_layout.addWidget(self.text_convert_btn)

        layout.addWidget(options_group)

        # 中部：左输入、右输出（两侧均为纯文本、自动换行、同一字号，保证显示对齐）
        panels_layout = QHBoxLayout()
        panels_layout.setSpacing(12)

        input_group = QGroupBox("输入文字")
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(10, 12, 10, 10)
        self.text_input_edit = QTextEdit()
        self.text_input_edit.setAcceptRichText(False)  # 粘贴/拖入一律按纯文本处理
        self.text_input_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_input_edit.setPlaceholderText("输入文字，按 Ctrl+Enter 或点击“转换”开始…")
        self.text_input_edit.textChanged.connect(self.on_text_input_changed)
        for seq in (QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")):
            shortcut = QShortcut(seq, self.text_input_edit)
            shortcut.activated.connect(self.on_text_convert)
        input_layout.addWidget(self.text_input_edit)
        panels_layout.addWidget(input_group, 1)

        output_group = QGroupBox("转换结果")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(10, 12, 10, 10)
        self.text_output_edit = AmbiguityOutputEdit()
        self.text_output_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_output_edit.setPlaceholderText("转换结果显示在这里")
        self.text_output_edit.ambiguity_clicked.connect(self.show_candidate_menu)
        output_layout.addWidget(self.text_output_edit)
        panels_layout.addWidget(output_group, 1)

        layout.addLayout(panels_layout, 1)

        # 底部：操作按钮与状态信息
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        self.text_copy_btn = QPushButton("复制结果")
        self.text_copy_btn.setObjectName("browseButton")
        self.text_copy_btn.clicked.connect(self.copy_text_result)
        bottom_layout.addWidget(self.text_copy_btn)

        self.text_clear_btn = QPushButton("清空")
        self.text_clear_btn.setObjectName("browseButton")
        self.text_clear_btn.clicked.connect(self.clear_text_tab)
        bottom_layout.addWidget(self.text_clear_btn)

        bottom_layout.addStretch()

        self.text_status_label = QLabel("输入文字后点击“转换”；分词模式与“设置”页一致")
        self.text_status_label.setWordWrap(True)
        bottom_layout.addWidget(self.text_status_label, 1)

        layout.addLayout(bottom_layout)

        # 文字转换运行期状态
        self.text_worker = None
        self._text_result = None  # 最近一次转换结果（切换“显示候选字”时用于重绘）

        return tab

    def on_text_type_changed(self, index):
        """切换转换类型：保存选择并作废当前结果"""
        self.settings.setValue("text_conversion_index", index)
        self._text_result = None
        self.text_output_edit.clear_result()
        self.set_text_status("已切换转换类型，请重新转换")

    def on_text_input_changed(self):
        """输入变化后作废当前结果"""
        if self._text_result is not None:
            self._text_result = None
            self.text_output_edit.clear_result()
            self.set_text_status("输入已修改，请重新转换")

    def on_show_candidates_changed(self):
        """切换“显示候选字”后按最近一次转换结果重绘波浪线"""
        self.text_output_edit.set_mark_ambiguities(self.show_candidates_cb.isChecked())

    def on_text_convert(self):
        """执行文字转换（后台线程）"""
        text = self.text_input_edit.toPlainText()
        if not text.strip():
            self.set_text_status("请先输入要转换的文字", error=True)
            return

        config = CONVERSION_TYPES[self.text_type_combo.currentText()]
        # 分词模式与“设置”页保持一致（同文件转换）
        if self.jieba_modern_cb.isChecked():
            config += '_jieba'
        elif self.jieba_ancient_cb.isChecked():
            config += '_jieba_traditional'

        # 如果已有转换线程在运行，先取消并等待其结束
        if self.text_worker is not None and self.text_worker.isRunning():
            self.text_worker.cancel()
            self.text_worker.wait()

        self.text_convert_btn.setEnabled(False)
        self.set_text_status("正在转换…")
        self.text_worker = TextConversionWorker(text, config)
        self.text_worker.conversion_done.connect(self.on_text_convert_done)
        self.text_worker.conversion_failed.connect(self.on_text_convert_failed)
        self.text_worker.start()

    def on_text_convert_done(self, result):
        """文字转换完成：显示结果并标注歧义"""
        self.text_convert_btn.setEnabled(True)
        self._text_result = result
        self.text_output_edit.display_result(
            result["output"], result["spans"], self.show_candidates_cb.isChecked())
        notice = result.get("notice")
        if notice:
            self.set_text_status(notice, error=True)
            return
        if result["spans"]:
            message = "转换完成：%d 处一对多歧义（点击红色波浪线查看候选）" % len(result["spans"])
            if result.get("skipped"):
                message += "；另有 %d 处因与实际转换无法对齐未标注" % result["skipped"]
        else:
            message = "转换完成，未发现一对多歧义"
        self.set_text_status(message)

    def on_text_convert_failed(self, message):
        """文字转换失败"""
        self.text_convert_btn.setEnabled(True)
        self.set_text_status(message, error=True)

    def set_text_status(self, message, error=False):
        """更新文字转换页底部的状态信息"""
        self.text_status_label.setText(message)
        self.text_status_label.setStyleSheet("color: #e74c3c;" if error else "")

    def show_candidate_menu(self, span_idx, global_pos):
        """在歧义跨度处弹出候选字菜单，选中候选后原地替换"""
        span = self.text_output_edit.spans()[span_idx]
        menu = QMenu(self)
        header = QAction("原文：%s" % span["source"], menu)
        header.setEnabled(False)
        menu.addAction(header)
        current = self.text_output_edit.span_text(span_idx)
        for candidate in span["candidates"]:
            action = menu.addAction(candidate)
            action.setCheckable(True)
            action.setChecked(candidate == current)
            action.triggered.connect(
                lambda checked, c=candidate, i=span_idx:
                    self.text_output_edit.apply_candidate(i, c))
        menu.exec(global_pos)

    def copy_text_result(self):
        """复制转换结果（含手动修正）到剪贴板"""
        text = self.text_output_edit.toPlainText()
        if not text:
            self.set_text_status("暂无转换结果可复制", error=True)
            return
        QApplication.clipboard().setText(text)
        self.set_text_status("已复制转换结果")

    def clear_text_tab(self):
        """清空输入与结果"""
        self.text_input_edit.clear()
        self.text_output_edit.clear_result()
        self._text_result = None
        self.set_text_status("已清空")

    def closeEvent(self, event):
        """窗口关闭事件，保存设置"""
        # 如果转换正在进行，先取消
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        if getattr(self, 'text_worker', None) is not None and self.text_worker.isRunning():
            self.text_worker.cancel()
            self.text_worker.wait()

        # 保存当前设置
        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))  # 使用现代样式

    # 设置应用程序图标（会显示在任务栏）
    # 注意：Windows上可能还需要单独的.ico文件才能正确显示任务栏图标
    app_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
    if os.path.exists(app_icon_path):
        app.setWindowIcon(QIcon(app_icon_path))
    else:
        # 如果没有找到图标文件，可以尝试png格式
        app_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(app_icon_path):
            app.setWindowIcon(QIcon(app_icon_path))

    window = ModernUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
