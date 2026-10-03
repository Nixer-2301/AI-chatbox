"""模型管理对话框：增删改查 API 模型档案，并支持连通性测试。"""

from __future__ import annotations

import json
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout,
    QWidget,
)

from ..models import AUTH_SCHEMES, PROTOCOL_LABELS, ModelProfile, RequestParams
from ..protocol import get_adapter
from ..workers import RequestWorker
from .theme import mono_font
from .widgets import CollapsibleSection

RAW_HINT = (
    "占位符：{{model}} {{base_url}} {{api_key}} {{system}} {{stream}} "
    "{{messages}} {{params}}\n"
    "· {{key}} 会被替换为字符串值\n"
    "· {{#key}} 会被替换为 JSON 原文（用于数组 / 对象 / 布尔）"
)


class ModelDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("模型管理")
        self.resize(1080, 720)
        self._current: Optional[ModelProfile] = None
        self._loading = False
        self._test_worker: Optional[RequestWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # ---- 左侧列表 ----
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(4)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        left_box.addWidget(self.list, 1)

        list_buttons = QHBoxLayout()
        for text, slot in (("新建", self._on_new), ("复制", self._on_duplicate),
                           ("删除", self._on_delete)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            list_buttons.addWidget(button)
        left_box.addLayout(list_buttons)

        io_buttons = QHBoxLayout()
        btn_import = QPushButton("导入")
        btn_import.clicked.connect(self._on_import)
        btn_export = QPushButton("导出")
        btn_export.clicked.connect(self._on_export)
        io_buttons.addWidget(btn_import)
        io_buttons.addWidget(btn_export)
        left_box.addLayout(io_buttons)
        splitter.addWidget(left)

        # ---- 右侧编辑区 ----
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(6)

        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        self.editor_scroll.setWidget(container)
        sections = QVBoxLayout(container)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(8)
        sections.addWidget(self._build_basic_section())
        sections.addWidget(self._build_conn_section())
        sections.addWidget(self._build_passthrough_section())
        sections.addWidget(self._build_raw_section())
        sections.addWidget(self._build_notes_section())
        sections.addStretch(1)
        right_box.addWidget(self.editor_scroll, 1)

        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.test_label.setProperty("hint", "true")
        right_box.addWidget(self.test_label)

        actions = QHBoxLayout()
        self.btn_test = QPushButton("测试连通性")
        self.btn_test.clicked.connect(self._on_test)
        actions.addWidget(self.btn_test)
        actions.addStretch(1)
        btn_save = QPushButton("保存")
        btn_save.setProperty("accent", "true")
        btn_save.clicked.connect(self._on_save)
        actions.addWidget(btn_save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        actions.addWidget(btn_close)
        right_box.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([260, 820])

        self._reload_list()
        if self.config.profiles:
            self.list.setCurrentRow(0)

    # --------------------------------------------------------------- 构建 --
    def _build_basic_section(self) -> CollapsibleSection:
        section = CollapsibleSection("基础", expanded=True)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self.name_edit = QLineEdit()
        form.addRow("显示名称", self.name_edit)

        self.protocol_combo = QComboBox()
        for key, label in PROTOCOL_LABELS.items():
            self.protocol_combo.addItem(label, key)
        form.addRow("请求协议", self.protocol_combo)

        self.model_edit = QLineEdit()
        self.model_edit.setProperty("mono", "true")
        form.addRow("模型名 (model)", self.model_edit)

        self.stream_default = QCheckBox("默认使用流式请求")
        form.addRow("", self.stream_default)
        section.body.addLayout(form)

        hint = QLabel("模型名会原样写入请求体的 model 字段；自定义 HTTP 协议下由模板决定。")
        hint.setProperty("hint", "true")
        hint.setWordWrap(True)
        section.body.addWidget(hint)
        return section

    def _build_conn_section(self) -> CollapsibleSection:
        section = CollapsibleSection("连接与鉴权", expanded=True)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setProperty("mono", "true")
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        self.path_edit = QLineEdit()
        self.path_edit.setProperty("mono", "true")
        self.path_edit.setPlaceholderText("留空使用协议默认端点")
        form.addRow("端点路径覆盖", self.path_edit)

        key_row = QWidget()
        key_box = QHBoxLayout(key_row)
        key_box.setContentsMargins(0, 0, 0, 0)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setProperty("mono", "true")
        key_box.addWidget(self.api_key_edit, 1)
        toggle = QPushButton("显示")
        toggle.setCheckable(True)
        toggle.toggled.connect(
            lambda on: self.api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_box.addWidget(toggle)
        form.addRow("API Key", key_row)

        self.auth_combo = QComboBox()
        for key, label in AUTH_SCHEMES.items():
            self.auth_combo.addItem(label, key)
        form.addRow("鉴权方式", self.auth_combo)

        self.api_version_edit = QLineEdit()
        self.api_version_edit.setProperty("mono", "true")
        form.addRow("anthropic-version", self.api_version_edit)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setSuffix(" 秒")
        form.addRow("超时", self.timeout_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        form.addRow("重试次数", self.retries_spin)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890（留空不使用代理）")
        form.addRow("代理", self.proxy_edit)

        self.verify_ssl = QCheckBox("校验 TLS 证书")
        form.addRow("", self.verify_ssl)
        section.body.addLayout(form)
        return section

    def _build_passthrough_section(self) -> CollapsibleSection:
        section = CollapsibleSection("透传（高级）", expanded=False)
        hint = QLabel("这些字段会合并进最终报文，用于透传服务商私有参数。")
        hint.setProperty("hint", "true")
        hint.setWordWrap(True)
        section.body.addWidget(hint)

        for label, attr, placeholder in (
            ("额外请求头 (JSON)", "extra_headers_view", '{"X-Trace-Id": "abc"}'),
            ("额外查询参数 (JSON)", "extra_query_view", '{"api-version": "2024-06-01"}'),
            ("额外请求体 (JSON，深合并)", "extra_body_view",
             '{"chat_template_kwargs": {"enable_thinking": true}}'),
        ):
            section.body.addWidget(QLabel(label))
            view = QPlainTextEdit()
            view.setFont(mono_font(9))
            view.setPlaceholderText(placeholder)
            view.setFixedHeight(90)
            setattr(self, attr, view)
            section.body.addWidget(view)
        return section

    def _build_raw_section(self) -> CollapsibleSection:
        section = CollapsibleSection("自定义 HTTP（高级）", expanded=False)

        hint = QLabel(RAW_HINT)
        hint.setProperty("hint", "true")
        hint.setWordWrap(True)
        hint.setFont(mono_font(8))
        section.body.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self.raw_method = QComboBox()
        self.raw_method.addItems(["POST", "GET", "PUT", "PATCH", "DELETE"])
        form.addRow("方法", self.raw_method)

        self.raw_url_edit = QLineEdit()
        self.raw_url_edit.setProperty("mono", "true")
        self.raw_url_edit.setPlaceholderText("{{base_url}}/v1/chat/completions")
        form.addRow("URL 模板", self.raw_url_edit)

        self.raw_stream_combo = QComboBox()
        self.raw_stream_combo.addItem("SSE (text/event-stream)", "sse")
        self.raw_stream_combo.addItem("NDJSON (每行一个 JSON)", "ndjson")
        form.addRow("流式模式", self.raw_stream_combo)

        self.raw_content_path = QLineEdit()
        self.raw_content_path.setProperty("mono", "true")
        self.raw_content_path.setPlaceholderText("choices[0].message.content")
        form.addRow("正文路径", self.raw_content_path)

        self.raw_delta_path = QLineEdit()
        self.raw_delta_path.setProperty("mono", "true")
        self.raw_delta_path.setPlaceholderText("choices[0].delta.content")
        form.addRow("流式增量路径", self.raw_delta_path)

        self.raw_usage_path = QLineEdit()
        self.raw_usage_path.setProperty("mono", "true")
        self.raw_usage_path.setPlaceholderText("usage")
        form.addRow("用量路径", self.raw_usage_path)
        section.body.addLayout(form)

        section.body.addWidget(QLabel("请求头模板 (JSON)"))
        self.raw_headers_view = QPlainTextEdit()
        self.raw_headers_view.setFont(mono_font(9))
        self.raw_headers_view.setFixedHeight(80)
        section.body.addWidget(self.raw_headers_view)

        section.body.addWidget(QLabel("请求体模板"))
        self.raw_body_view = QPlainTextEdit()
        self.raw_body_view.setFont(mono_font(9))
        self.raw_body_view.setFixedHeight(150)
        section.body.addWidget(self.raw_body_view)
        return section

    def _build_notes_section(self) -> CollapsibleSection:
        section = CollapsibleSection("备注", expanded=False)
        self.notes_view = QPlainTextEdit()
        self.notes_view.setPlaceholderText("备注、文档链接、注意事项…")
        self.notes_view.setFixedHeight(120)
        section.body.addWidget(self.notes_view)
        return section

    # --------------------------------------------------------------- 列表 --
    def _reload_list(self, select_id: Optional[str] = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for profile in self.config.profiles:
            item = QListWidgetItem(f"{profile.name}\n{PROTOCOL_LABELS.get(profile.protocol, '')}")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if select_id:
            for row in range(self.list.count()):
                if self.list.item(row).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.list.setCurrentRow(row)
                    return

    def _on_select(self, row: int) -> None:
        if row < 0:
            return
        self._stash_current()
        profile_id = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        profile = self.config.get_profile(profile_id)
        if profile:
            self._load(profile)

    def _stash_current(self) -> None:
        """切换条目前，把界面上未保存的修改写回内存中的档案。"""
        if self._current is None or self._loading:
            return
        self._collect(self._current)

    # --------------------------------------------------------- 表单 <-> 对象 --
    def _load(self, profile: ModelProfile) -> None:
        self._loading = True
        try:
            self._current = profile
            self.name_edit.setText(profile.name)
            index = self.protocol_combo.findData(profile.protocol)
            self.protocol_combo.setCurrentIndex(max(0, index))
            self.model_edit.setText(profile.model)
            self.stream_default.setChecked(profile.stream)

            self.base_url_edit.setText(profile.base_url)
            self.path_edit.setText(profile.path)
            self.api_key_edit.setText(profile.api_key)
            auth_index = self.auth_combo.findData(profile.auth_scheme)
            self.auth_combo.setCurrentIndex(max(0, auth_index))
            self.api_version_edit.setText(profile.api_version)
            self.timeout_spin.setValue(profile.timeout)
            self.retries_spin.setValue(profile.max_retries)
            self.proxy_edit.setText(profile.proxy)
            self.verify_ssl.setChecked(profile.verify_ssl)

            self.extra_headers_view.setPlainText(self._dump(profile.extra_headers))
            self.extra_query_view.setPlainText(self._dump(profile.extra_query))
            self.extra_body_view.setPlainText(self._dump(profile.extra_body))

            method_index = self.raw_method.findText(profile.raw_method)
            self.raw_method.setCurrentIndex(max(0, method_index))
            self.raw_url_edit.setText(profile.raw_url)
            stream_index = self.raw_stream_combo.findData(profile.raw_stream_mode)
            self.raw_stream_combo.setCurrentIndex(max(0, stream_index))
            self.raw_content_path.setText(profile.raw_content_path)
            self.raw_delta_path.setText(profile.raw_delta_path)
            self.raw_usage_path.setText(profile.raw_usage_path)
            self.raw_headers_view.setPlainText(self._dump(profile.raw_headers))
            self.raw_body_view.setPlainText(profile.raw_body_template)

            self.notes_view.setPlainText(profile.notes)
        finally:
            self._loading = False

    @staticmethod
    def _dump(value) -> str:
        if not value:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _parse_json(view: QPlainTextEdit) -> dict:
        text = view.toPlainText().strip()
        if not text:
            view.setProperty("invalid", "false")
            return {}
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            view.setProperty("invalid", "true")
            return {}
        view.setProperty("invalid", "false")
        return data if isinstance(data, dict) else {}

    def _collect(self, profile: ModelProfile) -> ModelProfile:
        profile.name = self.name_edit.text().strip() or "未命名模型"
        profile.protocol = self.protocol_combo.currentData()
        profile.model = self.model_edit.text().strip()
        profile.stream = self.stream_default.isChecked()

        profile.base_url = self.base_url_edit.text().strip()
        profile.path = self.path_edit.text().strip()
        profile.api_key = self.api_key_edit.text().strip()
        profile.auth_scheme = self.auth_combo.currentData()
        profile.api_version = self.api_version_edit.text().strip() or "2023-06-01"
        profile.timeout = self.timeout_spin.value()
        profile.max_retries = self.retries_spin.value()
        profile.proxy = self.proxy_edit.text().strip()
        profile.verify_ssl = self.verify_ssl.isChecked()

        profile.extra_headers = self._parse_json(self.extra_headers_view)
        profile.extra_query = self._parse_json(self.extra_query_view)
        profile.extra_body = self._parse_json(self.extra_body_view)

        profile.raw_method = self.raw_method.currentText()
        profile.raw_url = self.raw_url_edit.text().strip()
        profile.raw_stream_mode = self.raw_stream_combo.currentData()
        profile.raw_content_path = self.raw_content_path.text().strip()
        profile.raw_delta_path = self.raw_delta_path.text().strip()
        profile.raw_usage_path = self.raw_usage_path.text().strip()
        profile.raw_headers = self._parse_json(self.raw_headers_view)
        profile.raw_body_template = self.raw_body_view.toPlainText()

        profile.notes = self.notes_view.toPlainText()
        return profile

    # --------------------------------------------------------------- 动作 --
    def _on_new(self) -> None:
        self._stash_current()
        profile = ModelProfile(name="新模型")
        self.config.upsert_profile(profile)
        self._reload_list(profile.id)

    def _on_duplicate(self) -> None:
        if not self._current:
            return
        self._stash_current()
        self.config.upsert_profile(self._current)
        clone = self.config.duplicate_profile(self._current.id)
        if clone:
            self._reload_list(clone.id)

    def _on_delete(self) -> None:
        if not self._current:
            return
        answer = QMessageBox.question(
            self, "删除模型", f"确定删除「{self._current.name}」？此操作不可撤销。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.delete_profile(self._current.id)
        self._current = None
        self._reload_list()
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_save(self) -> None:
        if not self._current:
            return
        self._collect(self._current)
        self.config.upsert_profile(self._current)
        self._reload_list(self._current.id)
        self.test_label.setProperty("ok", "true")
        self.test_label.setText("已保存。")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入模型配置", "", "JSON (*.json)")
        if not path:
            return
        try:
            count = self.config.import_profiles(path)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._reload_list()
        self.test_label.setText(f"已导入 {count} 个模型。")

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出模型配置", "profiles.json",
                                              "JSON (*.json)")
        if not path:
            return
        from pathlib import Path
        count = self.config.export_profiles(Path(path))
        self.test_label.setText(f"已导出 {count} 个模型。")

    # ---------------------------------------------------------- 连通性测试 --
    def _on_test(self) -> None:
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        profile = self._collect(self._current) if self._current else ModelProfile()
        params = RequestParams(max_tokens=16, stream=False)
        try:
            adapter = get_adapter(profile.protocol)
            spec = adapter.build(profile, "", [{"role": "user", "content": "ping"}],
                                 params, False)
        except Exception as exc:
            self.test_label.setProperty("ok", "false")
            self.test_label.setText(f"构建请求失败：{exc}")
            return

        self.test_label.setProperty("ok", "false")
        self.test_label.setText("正在请求…")
        self.btn_test.setEnabled(False)
        self._test_worker = RequestWorker(spec, profile, self)
        self._test_worker.completed.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, record, result) -> None:
        self.btn_test.setEnabled(True)
        if record is None:
            self.test_label.setText("测试未返回结果。")
            return
        if record.error:
            self.test_label.setProperty("ok", "false")
            self.test_label.setText(
                f"失败：HTTP {record.status}　{record.error}　（{record.latency_ms} ms）")
        else:
            preview = (record.content or "").replace("\n", " ")[:160]
            self.test_label.setProperty("ok", "true")
            self.test_label.setText(
                f"成功：HTTP {record.status}　{record.latency_ms} ms　"
                f"tokens {record.usage.get('total_tokens', '-')}　返回：{preview}")

    # --------------------------------------------------------------- 关闭 --
    def closeEvent(self, event) -> None:  # noqa: N802
        worker = self._test_worker
        if worker is not None and worker.isRunning():
            worker.request_stop()
            worker.wait(3000)
        super().closeEvent(event)