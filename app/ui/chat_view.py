"""对话区：消息渲染与输入框。"""

from __future__ import annotations

from html import escape

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from .theme import COLORS

ROLE_LABELS = {"user": "用户", "assistant": "助手", "system": "系统"}


class _ComposerEdit(QPlainTextEdit):
    submitted = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if is_enter and ctrl:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class ChatView(QTextEdit):
    """只读的消息记录区，支持流式增量追加。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setPlaceholderText("会话内容将在这里显示。")
        self.auto_scroll = True
        self._stream_open = False
        self._stream_kind = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    # --------------------------------------------------------------- 工具 --
    def _end_cursor(self) -> QTextCursor:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        return cursor

    @staticmethod
    def _fmt(kind: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if kind == "reasoning":
            fmt.setForeground(QColor(COLORS["reasoning"]))
            fmt.setFontItalic(True)
        else:
            fmt.setForeground(QColor(COLORS["text"]))
        return fmt

    @staticmethod
    def _header_html(role: str, meta: str = "") -> str:
        color = COLORS.get(role, COLORS["text"])
        label = ROLE_LABELS.get(role, role)
        suffix = ""
        if meta:
            suffix = (f' <span style="color:{COLORS["text_dim"]};'
                      f'font-weight:400;font-size:8pt;">{escape(meta)}</span>')
        return (f'<div style="color:{color};font-weight:700;margin-top:8px;">'
                f'{label}{suffix}</div>')

    def _scroll(self) -> None:
        if self.auto_scroll:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _ensure_break(self, cursor: QTextCursor) -> None:
        if self.document().characterCount() > 1:
            cursor.insertText("\n")

    # --------------------------------------------------------------- 渲染 --
    def clear_messages(self) -> None:
        self.clear()
        self._stream_open = False
        self._stream_kind = None

    def append_message(self, role: str, content: str,
                       reasoning: str = "", meta: str = "") -> None:
        cursor = self._end_cursor()
        self._ensure_break(cursor)
        cursor.insertHtml(self._header_html(role, meta))
        if reasoning:
            cursor.insertText("[思维链]\n", self._fmt("reasoning"))
            cursor.insertText(reasoning, self._fmt("reasoning"))
            cursor.insertText("\n", self._fmt("content"))
        if content:
            cursor.insertText(content, self._fmt("content"))
        self._scroll()

    def begin_stream(self, role: str = "assistant", meta: str = "") -> None:
        cursor = self._end_cursor()
        self._ensure_break(cursor)
        cursor.insertHtml(self._header_html(role, meta))
        self._stream_open = True
        self._stream_kind = None

    def append_stream(self, text: str, kind: str = "content") -> None:
        if not text:
            return
        if not self._stream_open:
            self.begin_stream()
        cursor = self._end_cursor()
        if kind != self._stream_kind:
            if self._stream_kind == "reasoning" and kind == "content":
                cursor.insertText("\n", self._fmt("content"))
            elif kind == "reasoning" and self._stream_kind is not None:
                cursor.insertText("\n", self._fmt("reasoning"))
            if kind == "reasoning":
                cursor.insertText("[思维链] ", self._fmt("reasoning"))
            self._stream_kind = kind
        cursor.insertText(text, self._fmt(kind))
        self._scroll()

    def end_stream(self) -> None:
        if self._stream_open:
            self._end_cursor().insertText("\n", self._fmt("content"))
        self._stream_open = False
        self._stream_kind = None
        self._scroll()

    def append_notice(self, text: str, level: str = "warn") -> None:
        cursor = self._end_cursor()
        self._ensure_break(cursor)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(COLORS.get(level, COLORS["warn"])))
        fmt.setFontItalic(True)
        cursor.insertText(f"〔{text}〕", fmt)
        self._scroll()

    # --------------------------------------------------------------- 菜单 --
    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("复制全部", self._copy_all)
        menu.addAction("清空显示", self.clear_messages)
        menu.exec(self.mapToGlobal(pos))

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self.toPlainText())


class Composer(QWidget):
    """输入区。"""

    submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.edit = _ComposerEdit()
        self.edit.setPlaceholderText("输入消息…　Ctrl+Enter 发送")
        self.edit.setFixedHeight(88)
        self.edit.submitted.connect(self._submit)
        layout.addWidget(self.edit)

        row = QHBoxLayout()
        self.hint = QLabel("Ctrl+Enter 发送")
        self.hint.setProperty("hint", "true")
        row.addWidget(self.hint)
        row.addStretch(1)

        self.btn_clear = QPushButton("清空输入")
        self.btn_clear.clicked.connect(self.edit.clear)
        row.addWidget(self.btn_clear)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.btn_stop)

        self.btn_send = QPushButton("发送")
        self.btn_send.setProperty("accent", "true")
        self.btn_send.clicked.connect(self._submit)
        row.addWidget(self.btn_send)
        layout.addLayout(row)

    def _submit(self) -> None:
        text = self.edit.toPlainText().strip()
        if text:
            self.submitted.emit(text)

    def text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, text: str) -> None:
        self.edit.setPlainText(text)

    def clear(self) -> None:
        self.edit.clear()

    def set_busy(self, busy: bool) -> None:
        self.btn_send.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.hint.setText("请求进行中…" if busy else "Ctrl+Enter 发送")