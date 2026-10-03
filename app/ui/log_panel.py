"""请求日志面板：每一次请求的完整报文都能回看。"""

from __future__ import annotations

import json
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..context import find_messages_in_body, flatten_message
from ..models import RequestRecord, fmt_ts
from ..protocol import mask_headers
from .theme import COLORS, mono_font


def _pretty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _mono_view(readonly: bool = True) -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setReadOnly(readonly)
    view.setFont(mono_font(9))
    return view


class RequestDetailDialog(QDialog):
    """单次请求的完整审计视图。"""

    def __init__(self, record: RequestRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.setWindowTitle(f"请求详情 · {record.id}")
        self.resize(960, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QLabel(self._header_text())
        header.setWordWrap(True)
        header.setProperty("mono", "true")
        if record.error:
            header.setProperty("error", "true")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.reveal = QCheckBox("显示完整密钥")
        self.reveal.toggled.connect(self._rebuild)

        self.tabs.addTab(self._build_context_tab(), "上下文")
        self.tabs.addTab(self._build_request_tab(), "请求")
        self.tabs.addTab(self._build_response_tab(), "响应")
        self.tabs.addTab(self._build_stream_tab(), "原始流")
        self.tabs.addTab(self._build_usage_tab(), "用量与时间")
        self._rebuild()

        footer = QHBoxLayout()
        footer.addWidget(self.reveal)
        footer.addStretch(1)
        btn_copy = QPushButton("复制全部")
        btn_copy.clicked.connect(self._copy_all)
        btn_save = QPushButton("导出为 JSON")
        btn_save.clicked.connect(self._save_json)
        btn_close = QPushButton("关闭")
        btn_close.setProperty("accent", "true")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_copy)
        footer.addWidget(btn_save)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

    # --------------------------------------------------------------- 构建 --
    def _build_context_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)

        hint = QLabel("下面是这次请求实际发送给模型的消息数组（原样摘自请求体）。")
        hint.setProperty("hint", "true")
        box.addWidget(hint)

        self.ctx_table = QTableWidget(0, 3)
        self.ctx_table.setHorizontalHeaderLabels(["#", "角色", "内容"])
        self.ctx_table.verticalHeader().setVisible(False)
        self.ctx_table.setAlternatingRowColors(True)
        self.ctx_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        head = self.ctx_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.ctx_table.itemSelectionChanged.connect(self._on_ctx_selected)
        box.addWidget(self.ctx_table, 3)

        self.ctx_detail = _mono_view()
        self.ctx_detail.setPlaceholderText("选中上方消息查看完整原文。")
        box.addWidget(self.ctx_detail, 2)
        return page

    def _build_request_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        self.request_view = _mono_view()
        box.addWidget(self.request_view)
        return page

    def _build_response_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        self.response_view = _mono_view()
        box.addWidget(self.response_view)
        return page

    def _build_stream_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        hint = QLabel("SSE / NDJSON 原始数据流（最多保留 400 万字符）。")
        hint.setProperty("hint", "true")
        box.addWidget(hint)
        self.stream_view = _mono_view()
        box.addWidget(self.stream_view)
        return page

    def _build_usage_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        self.usage_view = _mono_view()
        box.addWidget(self.usage_view)
        return page

    # --------------------------------------------------------------- 渲染 --
    def _header_text(self) -> str:
        record = self.record
        status = record.status if record.status is not None else "-"
        parts = [
            f"{record.method} {record.url}",
            f"状态 {status}　协议 {record.protocol}　模型 {record.model}"
            f"　流式 {'是' if record.streamed else '否'}",
            f"总延迟 {record.latency_ms} ms　首字延迟 {record.ttft_ms or '-'} ms"
            f"　结束原因 {record.finish_reason or '-'}",
        ]
        if record.error:
            parts.append(f"错误：{record.error}")
        return "\n".join(parts)

    def _rebuild(self) -> None:
        reveal = self.reveal.isChecked()
        record = self.record

        headers = mask_headers(record.request_headers, reveal=reveal)
        request_lines = [
            f"{record.method} {record.url}",
            "",
            "—— 请求头 ——",
        ]
        request_lines.extend(f"{k}: {v}" for k, v in headers.items())
        request_lines.append("")
        request_lines.append("—— 请求体 ——")
        request_lines.append(_pretty(record.request_body))
        self.request_view.setPlainText("\n".join(request_lines))

        response_lines = [
            f"HTTP {record.status if record.status is not None else '-'}",
            "",
            "—— 响应头 ——",
        ]
        response_lines.extend(f"{k}: {v}" for k, v in (record.response_headers or {}).items())
        response_lines.append("")
        response_lines.append("—— 响应体 ——")
        response_lines.append(_pretty(record.response_body))
        self.response_view.setPlainText("\n".join(response_lines))

        self.stream_view.setPlainText(record.raw_stream or "（本次请求没有流式数据）")

        usage_lines = [
            "—— 用量 ——",
            _pretty(record.usage or {}),
            "",
            "—— 解析结果 ——",
            f"正文长度：{len(record.content or '')} 字符",
            f"思维链长度：{len(record.reasoning or '')} 字符",
            f"结束原因：{record.finish_reason or '-'}",
            f"总延迟：{record.latency_ms} ms",
            f"首字延迟：{record.ttft_ms} ms",
            f"创建时间：{fmt_ts(record.created_at)}",
        ]
        self.usage_view.setPlainText("\n".join(usage_lines))

        messages = find_messages_in_body(record.request_body)
        self.ctx_table.setRowCount(len(messages))
        for row, item in enumerate(messages):
            role, text = flatten_message(item)
            for col, value in enumerate((str(row + 1), role, " ".join(text.split())[:300])):
                cell = QTableWidgetItem(value)
                if col == 1:
                    cell.setForeground(QColor(COLORS.get(role, COLORS["text"])))
                self.ctx_table.setItem(row, col, cell)

    def _on_ctx_selected(self) -> None:
        rows = self.ctx_table.selectionModel().selectedRows()
        if not rows:
            return
        messages = find_messages_in_body(self.record.request_body)
        index = rows[0].row()
        if 0 <= index < len(messages):
            _, text = flatten_message(messages[index])
            self.ctx_detail.setPlainText(text)

    def as_dict(self) -> dict:
        record = self.record
        return {
            "id": record.id,
            "created_at": fmt_ts(record.created_at),
            "profile": record.profile_name,
            "protocol": record.protocol,
            "model": record.model,
            "method": record.method,
            "url": record.url,
            "status": record.status,
            "request_headers": record.request_headers,
            "request_body": record.request_body,
            "response_headers": record.response_headers,
            "response_body": record.response_body,
            "raw_stream": record.raw_stream,
            "content": record.content,
            "reasoning": record.reasoning,
            "usage": record.usage,
            "finish_reason": record.finish_reason,
            "latency_ms": record.latency_ms,
            "ttft_ms": record.ttft_ms,
            "error": record.error,
        }

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(_pretty(self.as_dict()))

    def _save_json(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "导出请求记录", f"{self.record.id}.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(_pretty(self.as_dict()))


class LogPanel(QWidget):
    """请求日志表格。"""

    status_message = pyqtSignal(str)

    COLUMNS = ["时间", "模型", "协议", "状态", "流式", "延迟ms", "首字ms",
               "输入", "输出", "错误"]

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._session_id: Optional[str] = None
        self._records: list[RequestRecord] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self.only_session = QCheckBox("只看当前会话")
        self.only_session.setChecked(True)
        self.only_session.toggled.connect(self.reload)
        row.addWidget(self.only_session)
        row.addStretch(1)

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.reload)
        btn_detail = QPushButton("查看详情")
        btn_detail.clicked.connect(self._open_selected)
        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(self._delete_selected)
        btn_clear = QPushButton("清空日志")
        btn_clear.setProperty("danger", "true")
        btn_clear.clicked.connect(self._clear_all)
        for button in (btn_refresh, btn_detail, btn_delete, btn_clear):
            row.addWidget(button)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        for index in range(len(self.COLUMNS) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("共 0 条记录")
        self.count_label.setProperty("hint", "true")
        layout.addWidget(self.count_label)

    # --------------------------------------------------------------- 数据 --
    def set_session(self, session_id: Optional[str]) -> None:
        self._session_id = session_id
        self.reload()

    def reload(self) -> None:
        session_id = self._session_id if self.only_session.isChecked() else None
        self._records = self.storage.list_requests(session_id, limit=500)
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            status = "-" if record.status is None else str(record.status)
            usage = record.usage or {}
            values = [
                fmt_ts(record.created_at, "%H:%M:%S"),
                record.model,
                record.protocol,
                status,
                "是" if record.streamed else "否",
                str(record.latency_ms),
                str(record.ttft_ms or "-"),
                str(usage.get("input_tokens", "-")),
                str(usage.get("output_tokens", "-")),
                record.error or "",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 3 and record.status is not None:
                    if 200 <= record.status < 300:
                        cell.setForeground(QColor(COLORS["ok"]))
                    else:
                        cell.setForeground(QColor(COLORS["error"]))
                if col == 9 and value:
                    cell.setForeground(QColor(COLORS["error"]))
                self.table.setItem(row, col, cell)
        self.count_label.setText(f"共 {len(self._records)} 条记录")

    def add_record(self, record: RequestRecord) -> None:
        self.reload()

    def _selected(self) -> Optional[RequestRecord]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._records):
            return self._records[index]
        return None

    # --------------------------------------------------------------- 交互 --
    def _open_selected(self) -> None:
        record = self._selected()
        if record is None:
            self.status_message.emit("请先选中一条请求记录。")
            return
        RequestDetailDialog(record, self).exec()

    def _delete_selected(self) -> None:
        record = self._selected()
        if record is None:
            self.status_message.emit("请先选中一条请求记录。")
            return
        self.storage.delete_request(record.id)
        self.reload()

    def _clear_all(self) -> None:
        self.storage.clear_requests()
        self.reload()
        self.status_message.emit("请求日志已清空。")