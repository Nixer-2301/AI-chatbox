"""上下文面板：把「即将发出的报文」摊开给用户看，并允许直接编辑。

三个页签：
    上下文编辑  可启用/禁用、增删、重排、逐条编辑本次要发送的上下文
    最终请求体  原样发送到服务端的报文体
    请求元信息  方法、URL、请求头（密钥可掩码/揭示）
"""

from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import tokenizer
from ..context import DRAFT_ORIGIN_LABELS, ContextBundle, normalize_draft
from ..models import new_id
from .theme import COLORS, mono_font

ROLE_ITEMS = (("system", "系统"), ("user", "用户"), ("assistant", "助手"))


def _pretty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


class ContextPanel(QWidget):
    """上下文编辑器 + 报文预览。"""

    draft_changed = pyqtSignal()      # 草稿被改动，需要重新组装预览
    rebuild_requested = pyqtSignal()  # 请求按会话历史重建草稿

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[dict] = []
        self._bundle: ContextBundle | None = None
        self._loading = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(260)
        self._debounce.timeout.connect(self.draft_changed.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_edit_tab(), "上下文编辑")
        self.tabs.addTab(self._build_payload_tab(), "最终请求体")
        self.tabs.addTab(self._build_meta_tab(), "请求元信息")
        layout.addWidget(self.tabs, 1)

        self._refresh_status()

    # --------------------------------------------------------------- 构建 --
    def _build_edit_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.btn_rebuild = QPushButton("从会话重建")
        self.btn_rebuild.setToolTip("按「参数 → 上下文裁剪策略」重新生成一整份草稿")
        self.btn_rebuild.clicked.connect(self.rebuild_requested.emit)
        bar.addWidget(self.btn_rebuild)

        self.btn_add = QPushButton("添加条目")
        self.btn_add.clicked.connect(self._on_add)
        bar.addWidget(self.btn_add)

        self.btn_delete = QPushButton("删除")
        self.btn_delete.setProperty("danger", "true")
        self.btn_delete.clicked.connect(self._on_delete)
        bar.addWidget(self.btn_delete)

        bar.addSpacing(8)
        self.btn_up = QPushButton("上移")
        self.btn_up.clicked.connect(lambda: self._move(-1))
        bar.addWidget(self.btn_up)
        self.btn_down = QPushButton("下移")
        self.btn_down.clicked.connect(lambda: self._move(1))
        bar.addWidget(self.btn_down)

        bar.addSpacing(8)
        self.btn_enable = QPushButton("全部启用")
        self.btn_enable.clicked.connect(lambda: self._set_all_enabled(True))
        bar.addWidget(self.btn_enable)
        self.btn_disable = QPushButton("全部禁用")
        self.btn_disable.clicked.connect(lambda: self._set_all_enabled(False))
        bar.addWidget(self.btn_disable)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setProperty("danger", "true")
        self.btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        box.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_editor())
        splitter.setSizes([340, 220])
        box.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setProperty("hint", "true")
        self.status_label.setWordWrap(True)
        box.addWidget(self.status_label)
        return page

    def _build_table(self) -> QWidget:
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["启用", "#", "角色", "tokens", "内容"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed)
        header = self.table.horizontalHeader()
        for column, mode in ((0, QHeaderView.ResizeMode.Fixed),
                             (1, QHeaderView.ResizeMode.ResizeToContents),
                             (2, QHeaderView.ResizeMode.Fixed),
                             (3, QHeaderView.ResizeMode.ResizeToContents),
                             (4, QHeaderView.ResizeMode.Stretch)):
            header.setSectionResizeMode(column, mode)
        self.table.setColumnWidth(0, 46)
        self.table.setColumnWidth(2, 86)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        return self.table

    def _build_editor(self) -> QWidget:
        box = QGroupBox("选中条目")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(9, 6, 9, 9)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("角色"))
        self.edit_role = QComboBox()
        for key, label in ROLE_ITEMS:
            self.edit_role.addItem(label, key)
        self.edit_role.setFixedWidth(110)
        self.edit_role.currentIndexChanged.connect(self._on_editor_role)
        top.addWidget(self.edit_role)
        self.edit_origin = QLabel("")
        self.edit_origin.setProperty("hint", "true")
        top.addWidget(self.edit_origin)
        top.addStretch(1)
        layout.addLayout(top)

        self.edit_content = QPlainTextEdit()
        self.edit_content.setFont(mono_font(9))
        self.edit_content.setPlaceholderText("选中上方任意一行，在这里编辑它将发送的完整内容。")
        self.edit_content.textChanged.connect(self._on_editor_text)
        layout.addWidget(self.edit_content, 1)
        return box

    def _build_payload_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(4)

        row = QHBoxLayout()
        hint = QLabel("这是将原样发送到服务端的请求体。")
        hint.setProperty("hint", "true")
        row.addWidget(hint)
        row.addStretch(1)
        btn_copy = QPushButton("复制")
        btn_copy.clicked.connect(self._copy_payload)
        row.addWidget(btn_copy)
        box.addLayout(row)

        self.payload_view = QPlainTextEdit()
        self.payload_view.setReadOnly(True)
        self.payload_view.setFont(mono_font(9))
        box.addWidget(self.payload_view, 1)
        return page

    def _build_meta_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(4)

        row = QHBoxLayout()
        self.reveal_btn = QPushButton("显示完整密钥")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.toggled.connect(self._refresh_meta)
        row.addWidget(self.reveal_btn)
        row.addStretch(1)
        box.addLayout(row)

        self.meta_view = QPlainTextEdit()
        self.meta_view.setReadOnly(True)
        self.meta_view.setFont(mono_font(9))
        box.addWidget(self.meta_view, 1)
        return page

    # --------------------------------------------------------- 草稿读写 --
    def set_draft(self, entries: Any) -> None:
        """载入一整份草稿（会重建表格，尽量保留选中项与滚动位置）。"""
        selected_ids = {self._entries[r]["id"] for r in self._selected_rows()}
        scroll = self.table.verticalScrollBar().value() if self.table.rowCount() else 0

        self._entries = normalize_draft(entries)
        self._rebuild_table()

        if selected_ids:
            for row, entry in enumerate(self._entries):
                if entry["id"] in selected_ids:
                    self.table.selectRow(row)
                    break
        self.table.verticalScrollBar().setValue(scroll)
        self._refresh_status()

    def draft(self) -> list[dict]:
        """返回当前草稿的深拷贝。"""
        return [
            {
                "id": entry["id"],
                "role": entry["role"],
                "content": entry["content"],
                "enabled": entry["enabled"],
                "origin": entry["origin"],
                "source_id": entry["source_id"],
            }
            for entry in self._entries
        ]

    def _rebuild_table(self) -> None:
        self._loading = True
        try:
            self.table.setRowCount(0)
            self.table.setRowCount(len(self._entries))
            for row, entry in enumerate(self._entries):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                               | Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(
                    Qt.CheckState.Checked if entry["enabled"] else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, entry["id"])
                self.table.setItem(row, 0, check)

                index_item = QTableWidgetItem(str(row + 1))
                index_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 1, index_item)

                combo = QComboBox()
                for key, label in ROLE_ITEMS:
                    combo.addItem(label, key)
                position = combo.findData(entry["role"])
                combo.setCurrentIndex(max(0, position))
                combo.currentIndexChanged.connect(
                    lambda _i, eid=entry["id"], widget=combo:
                    self._on_role_changed(eid, widget))
                self.table.setCellWidget(row, 2, combo)

                token_item = QTableWidgetItem("")
                token_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                token_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, 3, token_item)

                content_item = QTableWidgetItem(entry["content"])
                content_item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                content_item.setToolTip(entry["content"])
                self.table.setItem(row, 4, content_item)

                self._paint_row(row, entry)
            self._refresh_tokens()
        finally:
            self._loading = False

    def _paint_row(self, row: int, entry: dict) -> None:
        dim = not entry["enabled"]
        for column in (1, 4):
            item = self.table.item(row, column)
            if item is None:
                continue
            color = COLORS["text_dim"] if dim else COLORS.get(entry["role"], COLORS["text"])
            item.setForeground(QColor(color))

    def _index_of(self, entry_id: str) -> int:
        for index, entry in enumerate(self._entries):
            if entry["id"] == entry_id:
                return index
        return -1

    def _selected_rows(self) -> list[int]:
        if not self.table.selectionModel():
            return []
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    # --------------------------------------------------------- 单元格交互 --
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        index = self._index_of(entry_id) if entry_id else -1
        if index < 0:
            return
        entry = self._entries[index]
        if item.column() == 0:
            entry["enabled"] = item.checkState() == Qt.CheckState.Checked
        elif item.column() == 4:
            entry["content"] = item.text()
            item.setToolTip(item.text())
        else:
            return
        self._paint_row(index, entry)
        self._refresh_tokens()
        self._touch()

    def _on_role_changed(self, entry_id: str, widget: QComboBox) -> None:
        if self._loading:
            return
        index = self._index_of(entry_id)
        if index < 0:
            return
        self._entries[index]["role"] = widget.currentData()
        self._paint_row(index, self._entries[index])
        self._touch()

    def _on_row_selected(self) -> None:
        rows = self._selected_rows()
        self._loading = True
        try:
            if not rows:
                self.edit_content.clear()
                self.edit_origin.setText("")
                return
            entry = self._entries[rows[0]]
            position = self.edit_role.findData(entry["role"])
            self.edit_role.setCurrentIndex(max(0, position))
            self.edit_content.setPlainText(entry["content"])
            self.edit_origin.setText(
                f"来源：{DRAFT_ORIGIN_LABELS.get(entry['origin'], entry['origin'])}　"
                f"（共选中 {len(rows)} 条）")
        finally:
            self._loading = False

    def _on_editor_role(self, _index: int) -> None:
        if self._loading:
            return
        rows = self._selected_rows()
        if not rows:
            return
        role = self.edit_role.currentData()
        index = rows[0]
        self._entries[index]["role"] = role
        self._loading = True
        try:
            combo = self.table.cellWidget(index, 2)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(max(0, combo.findData(role)))
        finally:
            self._loading = False
        self._paint_row(index, self._entries[index])
        self._touch()

    def _on_editor_text(self) -> None:
        if self._loading:
            return
        rows = self._selected_rows()
        if not rows:
            return
        index = rows[0]
        text = self.edit_content.toPlainText()
        self._entries[index]["content"] = text
        self._loading = True
        try:
            item = self.table.item(index, 4)
            if item is not None:
                item.setText(text)
                item.setToolTip(text)
        finally:
            self._loading = False
        self._refresh_tokens()
        self._touch()

    # ----------------------------------------------------------- 结构操作 --
    def _on_add(self) -> None:
        entry = {
            "id": new_id("dft_"),
            "role": "user",
            "content": "",
            "enabled": True,
            "origin": "manual",
            "source_id": "",
        }
        rows = self._selected_rows()
        position = rows[0] + 1 if rows else len(self._entries)
        self._entries.insert(position, entry)
        self._rebuild_table()
        self.table.selectRow(position)
        self._refresh_status()
        self._emit_now()

    def _on_delete(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in reversed(rows):
            self._entries.pop(row)
        self._rebuild_table()
        self._loading = True
        try:
            self.edit_content.clear()
            self.edit_origin.setText("")
        finally:
            self._loading = False
        self._refresh_status()
        self._emit_now()

    def _move(self, delta: int) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        row = rows[0]
        target = row + delta
        if not (0 <= target < len(self._entries)):
            return
        self._entries[row], self._entries[target] = \
            self._entries[target], self._entries[row]
        self._rebuild_table()
        self.table.selectRow(target)
        self._refresh_status()
        self._emit_now()

    def _set_all_enabled(self, enabled: bool) -> None:
        if not self._entries:
            return
        for entry in self._entries:
            entry["enabled"] = enabled
        self._rebuild_table()
        self._refresh_status()
        self._emit_now()

    def _on_clear(self) -> None:
        if not self._entries:
            return
        self._entries = []
        self._rebuild_table()
        self._refresh_status()
        self._emit_now()

    # --------------------------------------------------------------- 状态 --
    def _touch(self) -> None:
        self._refresh_status()
        self._debounce.start()

    def _emit_now(self) -> None:
        self._debounce.stop()
        self.draft_changed.emit()

    def _refresh_tokens(self) -> None:
        for row, entry in enumerate(self._entries):
            item = self.table.item(row, 3)
            if item is not None:
                item.setText(str(tokenizer.estimate(entry["content"])))

    def _refresh_status(self) -> None:
        enabled = sum(1 for e in self._entries if e["enabled"])
        tokens = sum(tokenizer.estimate(e["content"]) for e in self._entries if e["enabled"])
        parts = []
        if self._bundle is not None:
            parts.append(self._summary_header())
        parts.append(f"共 {len(self._entries)} 条，启用 {enabled} 条，预计 {tokens} tokens。")
        parts.append("勾选=发送，取消勾选=本次不发送（不影响本地历史）。")
        self.status_label.setText("　".join(parts))

    def _summary_header(self) -> str:
        bundle = self._bundle
        return (f"协议 {bundle.profile.protocol if bundle.profile else '-'}　|　"
                f"模型 {bundle.profile.model if bundle.profile else '-'}　|　"
                f"流式 {'是' if bundle.stream else '否'}　|　"
                f"上下文 ≈ {bundle.total_tokens} tokens"
                f"（系统 {bundle.system_tokens} + 消息 {bundle.message_tokens}）　|　"
                f"发送消息 {len(bundle.sent_messages)} 条")

    # --------------------------------------------------------------- 预览 --
    def show_bundle(self, bundle: ContextBundle) -> None:
        self._bundle = bundle
        if bundle is None:
            self.clear_preview()
            return
        self._refresh_status()
        self.payload_view.setPlainText(_pretty(bundle.payload()))
        self._refresh_meta()

    def clear_preview(self) -> None:
        self._bundle = None
        self.payload_view.clear()
        self.meta_view.clear()
        self._refresh_status()

    def clear(self) -> None:
        self._entries = []
        self._bundle = None
        self._rebuild_table()
        self.payload_view.clear()
        self.meta_view.clear()
        self.status_label.setText("暂无上下文。配置好模型后，这里会实时显示即将发送的内容。")

    # --------------------------------------------------------------- 交互 --
    def _copy_payload(self) -> None:
        QApplication.clipboard().setText(self.payload_view.toPlainText())

    def _refresh_meta(self) -> None:
        if not self._bundle or not self._bundle.spec:
            self.meta_view.clear()
            return
        reveal = self.reveal_btn.isChecked()
        spec = self._bundle.spec
        lines = [
            f"{spec.method} {self._bundle.url_masked(reveal=reveal)}",
            "",
            "—— 请求头 ——",
        ]
        lines.extend(f"{k}: {v}" for k, v in self._bundle.headers_masked(reveal).items())
        if spec.query:
            lines.append("")
            lines.append("—— 查询参数 ——")
            lines.extend(f"{k}={v}" for k, v in spec.query.items())
        lines.append("")
        lines.append(f"流式模式：{spec.stream_mode}")
        self.meta_view.setPlainText("\n".join(lines))