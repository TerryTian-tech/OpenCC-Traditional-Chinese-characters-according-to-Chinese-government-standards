# -*- coding: utf-8 -*-
"""“文字转换”选项卡的界面组件：一对多歧义标注输出框与后台转换线程。"""

import bisect

from PySide2.QtCore import Qt, QThread, Signal
from PySide2.QtGui import (QColor, QFontMetricsF, QPainter, QPainterPath, QPen,
                           QTextCharFormat, QTextCursor)
from PySide2.QtWidgets import QTextEdit

from ambiguity_engine import (
    AmbiguityEngineError,
    ConversionCancelled,
    convert_with_ambiguities,
    plain_convert,
)


def utf16_positions(text):
    """返回数组 p，p[i] 为 text 前 i 个字符（code point 计）的 UTF-16 码元长度。

    QTextDocument 的光标位置按 UTF-16 码元计，而转换结果的跨度索引按 code
    point 计（扩展平面字符每字 2 个码元），两套索引必须换算。
    """
    positions = [0] * (len(text) + 1)
    u16 = 0
    for i, ch in enumerate(text):
        positions[i] = u16
        u16 += 2 if ord(ch) > 0xFFFF else 1
    positions[len(text)] = u16
    return positions


class TextConversionWorker(QThread):
    """
    文字转换工作线程（带一对多歧义标注），避免转换时阻塞 UI
    """
    conversion_done = Signal(dict)  # 完成信号（转换结果：output/spans/skipped/notice）
    conversion_failed = Signal(str)  # 失败信号（错误信息）

    def __init__(self, text, config):
        super().__init__()
        self.text = text
        self.config = config
        self._cancelled = False

    def cancel(self):
        """取消转换"""
        self._cancelled = True

    def run(self):
        try:
            result = convert_with_ambiguities(
                self.text, self.config,
                cancel_check=lambda: self._cancelled)
            self.conversion_done.emit(result)
        except ConversionCancelled:
            self.conversion_failed.emit("转换已取消")
        except AmbiguityEngineError as e:
            # 词典数据不可用（如仅安装了 ocd2 词典）：退化为只转换、不标注歧义
            try:
                self.conversion_done.emit({
                    "output": plain_convert(self.text, self.config),
                    "spans": [],
                    "skipped": 0,
                    "notice": "已转换，但未能标注一对多歧义：%s" % e,
                })
            except Exception as e2:
                self.conversion_failed.emit("转换失败：%s" % e2)
        except Exception as e:
            if "jieba" in str(e).lower():
                self.conversion_failed.emit(
                    "转换失败：OpenCC 结巴分词插件未安装（Windows 需按 README 安装 opencc-jieba 插件）")
            else:
                self.conversion_failed.emit("转换失败：%s" % e)


class AmbiguityOutputEdit(QTextEdit):
    """
    转换结果输出框：一对多歧义跨度自绘红色波浪线（比内置下划线更粗），
    点击波浪线弹出候选字菜单
    """
    ambiguity_clicked = Signal(int, object)  # (歧义跨度序号, 弹出菜单的全局坐标 QPoint)

    WAVE_COLOR = QColor("#e11d48")
    WAVE_PEN_WIDTH = 2.0  # 波浪线粗细（内置下划线无法调粗，故自绘）
    WAVE_AMPLITUDE = 2.0  # 波幅（像素）
    WAVE_HALF_WAVE = 4.0  # 半个波浪的长度（像素）

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self._spans = []  # [{"start","length","source","candidates","resolved"}]（code point 索引）
        self._mark_ambiguities = False
        self._u16_map = [0]  # code point 位置 -> UTF-16 码元位置

    def display_result(self, text, spans, mark_ambiguities):
        """显示转换结果；mark_ambiguities 为真时给歧义跨度加红色波浪线"""
        self._spans = [dict(span, resolved=False) for span in spans]
        self._mark_ambiguities = mark_ambiguities
        self.setPlainText(text)
        self._rebuild_u16_map()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        self.viewport().update()

    def clear_result(self):
        """清空结果与标注"""
        self._spans = []
        self._mark_ambiguities = False
        self.clear()
        self._u16_map = [0]

    def set_mark_ambiguities(self, mark):
        """仅切换是否绘制波浪线（如“显示候选字”开关变化时）"""
        self._mark_ambiguities = mark
        self.viewport().update()

    def _rebuild_u16_map(self):
        """文本变化后重建 code point -> UTF-16 位置映射"""
        self._u16_map = utf16_positions(self.toPlainText())

    def _u16(self, cp_pos):
        """code point 位置 -> QTextCursor 位置（UTF-16 码元）"""
        return self._u16_map[cp_pos]

    def _cp(self, u16_pos):
        """QTextCursor 位置（UTF-16 码元）-> code point 位置"""
        return bisect.bisect_right(self._u16_map, u16_pos) - 1

    def spans(self):
        return self._spans

    def span_text(self, span_idx):
        span = self._spans[span_idx]
        return self.toPlainText()[span["start"]:span["start"] + span["length"]]

    def _span_at(self, pos):
        """返回坐标 pos 处的歧义跨度序号；不在任何跨度内返回 None"""
        position = self._cp(self.cursorForPosition(pos).position())
        for i, span in enumerate(self._spans):
            if span["start"] <= position < span["start"] + span["length"]:
                return i
        # 点击落在跨度最后一个字符的右边界时 position 恰为 end，回退一位再试
        if position > 0:
            position -= 1
            for i, span in enumerate(self._spans):
                if span["start"] <= position < span["start"] + span["length"]:
                    return i
        return None

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and event.modifiers() == Qt.NoModifier
                and not self.textCursor().hasSelection()):
            span_idx = self._span_at(event.pos())
            if span_idx is not None and not self._spans[span_idx]["resolved"]:
                self.ambiguity_clicked.emit(
                    span_idx, event.globalPos())
                return
        super().mouseReleaseEvent(event)

    def apply_candidate(self, span_idx, candidate):
        """用选中的候选值替换对应歧义跨度的文本，并调整后续跨度偏移"""
        span = self._spans[span_idx]
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._u16(span["start"]))
        cursor.setPosition(self._u16(span["start"] + span["length"]), QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(candidate, QTextCharFormat())
        delta = len(candidate) - span["length"]
        span["length"] = len(candidate)
        span["resolved"] = True
        for other in self._spans[span_idx + 1:]:
            other["start"] += delta
        self._rebuild_u16_map()
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._mark_ambiguities and self._spans):
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.WAVE_COLOR, self.WAVE_PEN_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        text = self.toPlainText()
        for span in self._spans:
            if span["resolved"]:
                continue
            self._draw_span_wave(painter, span, text)
        painter.end()

    def _draw_span_wave(self, painter, span, text):
        """在跨度下方绘制波浪线；逐字符取位置，跨行（自动换行）时分行绘制"""
        viewport = self.viewport()
        visible_top, visible_bottom = -40, viewport.height() + 40
        metrics = QFontMetricsF(self.font())
        run_x1 = run_x2 = run_y = None
        for pos in range(span["start"], span["start"] + span["length"]):
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._u16(pos))
            rect = self.cursorRect(cursor)
            if run_y is not None and (rect.bottom() < visible_top or rect.top() > visible_bottom):
                break
            char_width = metrics.horizontalAdvance(text[pos]) if pos < len(text) else rect.height()
            if run_y is not None and abs(rect.bottom() - run_y) <= 1:
                # 同一行，延伸当前波浪段
                run_x2 = rect.x() + char_width
            else:
                # 新的一行（或首个字符）：先画完上一段
                if run_y is not None:
                    self._paint_wave_segment(painter, run_x1, run_x2, run_y)
                run_x1, run_x2 = rect.x(), rect.x() + char_width
                run_y = rect.bottom() - 2
        if run_y is not None:
            self._paint_wave_segment(painter, run_x1, run_x2, run_y)

    def _paint_wave_segment(self, painter, x1, x2, y):
        """绘制 [x1, x2] 区间、基线为 y 的一段波浪线"""
        if x2 <= x1:
            return
        path = QPainterPath()
        path.moveTo(x1, y)
        x, up = x1, True
        while x < x2:
            next_x = min(x + self.WAVE_HALF_WAVE, x2)
            ctrl_y = y - self.WAVE_AMPLITUDE if up else y + self.WAVE_AMPLITUDE
            path.quadTo((x + next_x) / 2, ctrl_y, next_x, y)
            x, up = next_x, not up
        painter.drawPath(path)
