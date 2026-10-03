"""参数面板：请求的全部可调参数，空值表示「不发送该字段」。

常用项直接展开，复杂/低频项收进折叠分组，避免整页都是输入框。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..context import CONTEXT_MODES
from ..models import RequestParams
from .widgets import CollapsibleSection

AUTO_HINT = "auto（不发送）"


def _mark(widget: QLineEdit, invalid: bool) -> None:
    widget.setProperty("invalid", "true" if invalid else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _hint(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setProperty("hint", "true")
    label.setWordWrap(wrap)
    return label


class ParamsPanel(QWidget):
    """编辑 RequestParams，任何改动都会防抖后发出 changed 信号。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._editors: dict[str, QLineEdit] = {}

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.changed.emit)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_system_section())
        layout.addWidget(self._build_common_section())
        layout.addWidget(self._build_sampling_section())
        layout.addWidget(self._build_reasoning_section())
        layout.addWidget(self._build_context_section())
        layout.addWidget(self._build_output_section())

        footer = QHBoxLayout()
        self.btn_reset = QPushButton("恢复出厂参数")
        self.btn_save_default = QPushButton("设为默认参数")
        footer.addWidget(self.btn_reset)
        footer.addWidget(self.btn_save_default)
        footer.addStretch(1)
        layout.addLayout(footer)
        layout.addStretch(1)

        self.btn_reset.clicked.connect(self._on_reset)

        self.set_params(RequestParams())

    # ------------------------------------------------------------- 构建界面 --
    def _opt_edit(self, key: str, hint: str = AUTO_HINT) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(hint)
        edit.setProperty("mono", "true")
        edit.textChanged.connect(self._on_change)
        self._editors[key] = edit
        return edit

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        return form

    def _build_system_section(self) -> CollapsibleSection:
        section = CollapsibleSection("系统提示词", expanded=True)
        section.body.addWidget(_hint(
            "默认留空。留空时，请求报文中不会出现 system 角色字段。"))
        self.system_prompt = QPlainTextEdit()
        self.system_prompt.setPlaceholderText("（留空）")
        self.system_prompt.setFixedHeight(84)
        self.system_prompt.textChanged.connect(self._on_change)
        section.body.addWidget(self.system_prompt)
        return section

    def _build_common_section(self) -> CollapsibleSection:
        section = CollapsibleSection("常用参数", expanded=True)
        form = self._form()
        form.addRow("temperature", self._opt_edit("temperature", f"{AUTO_HINT}  0 ~ 2"))
        form.addRow("top_p", self._opt_edit("top_p", f"{AUTO_HINT}  0 ~ 1"))
        form.addRow("max_tokens", self._opt_edit("max_tokens", f"{AUTO_HINT}  输出上限"))
        section.body.addLayout(form)

        self.stream_combo = QComboBox()
        self.stream_combo.addItem("跟随模型档案", None)
        self.stream_combo.addItem("强制流式", True)
        self.stream_combo.addItem("强制非流式", False)
        self.stream_combo.currentIndexChanged.connect(self._on_change)
        stream_form = self._form()
        stream_form.addRow("流式", self.stream_combo)
        section.body.addLayout(stream_form)

        self.reasoning_enabled = QCheckBox("启用思维模式")
        self.reasoning_enabled.toggled.connect(self._on_change)
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(["minimal", "low", "medium", "high"])
        self.effort_combo.currentIndexChanged.connect(self._on_change)
        reason_form = self._form()
        reason_form.addRow("", self.reasoning_enabled)
        reason_form.addRow("思维强度", self.effort_combo)
        section.body.addLayout(reason_form)
        return section

    def _build_sampling_section(self) -> CollapsibleSection:
        section = CollapsibleSection("采样细节", expanded=False)
        form = self._form()
        for key, label, hint in (
            ("top_k", "top_k", "整数"),
            ("min_tokens", "min_tokens", "输出下限（部分服务支持）"),
            ("presence_penalty", "presence_penalty", "-2 ~ 2"),
            ("frequency_penalty", "frequency_penalty", "-2 ~ 2"),
            ("repetition_penalty", "repetition_penalty", "部分服务支持"),
            ("seed", "seed", "整数"),
            ("n", "n", "候选数"),
            ("top_logprobs", "top_logprobs", "0 ~ 20"),
        ):
            form.addRow(label, self._opt_edit(key, f"{AUTO_HINT}  {hint}"))

        self.stop_edit = QLineEdit()
        self.stop_edit.setPlaceholderText("逗号或换行分隔，留空则不发送")
        self.stop_edit.textChanged.connect(self._on_change)
        form.addRow("stop", self.stop_edit)

        self.logprobs_combo = QComboBox()
        self.logprobs_combo.addItems(["auto", "true", "false"])
        self.logprobs_combo.currentIndexChanged.connect(self._on_change)
        form.addRow("logprobs", self.logprobs_combo)
        section.body.addLayout(form)
        return section

    def _build_reasoning_section(self) -> CollapsibleSection:
        section = CollapsibleSection("思维强度 · 高级", expanded=False)
        form = self._form()
        form.addRow("预算 tokens", self._opt_edit(
            "thinking_budget_tokens", "auto（按强度自动换算）"))
        self.include_thoughts = QCheckBox("要求服务端返回思维内容")
        self.include_thoughts.toggled.connect(self._on_change)
        form.addRow("", self.include_thoughts)
        section.body.addLayout(form)
        section.body.addWidget(_hint(
            "映射规则：OpenAI → reasoning_effort；Anthropic → thinking.budget_tokens；"
            "Gemini → thinkingConfig.thinkingBudget；自定义 HTTP → params.thinking_budget_tokens。"))
        return section

    def _build_context_section(self) -> CollapsibleSection:
        section = CollapsibleSection("上下文裁剪策略", expanded=False)
        form = self._form()
        self.context_mode = QComboBox()
        for key, label in CONTEXT_MODES.items():
            self.context_mode.addItem(label, key)
        self.context_mode.currentIndexChanged.connect(self._on_change)
        form.addRow("裁剪策略", self.context_mode)

        self.last_n = QSpinBox()
        self.last_n.setRange(1, 9999)
        self.last_n.valueChanged.connect(self._on_change)
        form.addRow("最近 N 条", self.last_n)

        self.token_budget = QSpinBox()
        self.token_budget.setRange(64, 2_000_000)
        self.token_budget.setSingleStep(256)
        self.token_budget.valueChanged.connect(self._on_change)
        form.addRow("Token 预算", self.token_budget)

        self.include_reasoning = QCheckBox("把历史思维链内联回传（<thinking> 标签）")
        self.include_reasoning.toggled.connect(self._on_change)
        form.addRow("", self.include_reasoning)
        section.body.addLayout(form)
        section.body.addWidget(_hint(
            "此策略只在「上下文 → 从会话重建」时生效，用来决定哪些历史默认启用。"
            "日常发送以「上下文编辑」里的勾选为准，本地存储的完整历史不会被删除。"))
        return section

    def _build_output_section(self) -> CollapsibleSection:
        section = CollapsibleSection("高级透传", expanded=False)
        section.body.addWidget(_hint(
            "extra_body 会深合并进最终请求体，可透传任意服务商私有字段。"))
        section.body.addWidget(QLabel("extra_body"))
        self.extra_body = QPlainTextEdit()
        self.extra_body.setPlaceholderText('{"chat_template_kwargs": {"enable_thinking": true}}')
        self.extra_body.setFixedHeight(70)
        self.extra_body.textChanged.connect(self._on_change)
        section.body.addWidget(self.extra_body)

        section.body.addWidget(QLabel("extra_headers"))
        self.extra_headers = QPlainTextEdit()
        self.extra_headers.setPlaceholderText('{"X-Trace-Id": "abc"}')
        self.extra_headers.setFixedHeight(62)
        self.extra_headers.textChanged.connect(self._on_change)
        section.body.addWidget(self.extra_headers)
        return section

    # --------------------------------------------------------------- 交互 --
    def _on_change(self, *args) -> None:
        if not self._loading:
            self._debounce.start()

    def _on_reset(self) -> None:
        self.set_params(RequestParams())
        self.changed.emit()

    # --------------------------------------------------------- 数据读写 --
    @staticmethod
    def _read_opt(edit: QLineEdit, cast) -> Optional[Any]:
        text = edit.text().strip()
        if not text:
            _mark(edit, False)
            return None
        try:
            value = cast(text)
        except (TypeError, ValueError):
            _mark(edit, True)
            return None
        _mark(edit, False)
        return value

    def _read_json(self, widget: QPlainTextEdit) -> dict:
        text = widget.toPlainText().strip()
        if not text:
            widget.setProperty("invalid", "false")
            return {}
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            widget.setProperty("invalid", "true")
            return {}
        widget.setProperty("invalid", "false")
        return data if isinstance(data, dict) else {}

    def params(self) -> RequestParams:
        params = RequestParams()
        casts = {
            "temperature": float, "top_p": float, "top_k": int, "max_tokens": int,
            "min_tokens": int, "presence_penalty": float, "frequency_penalty": float,
            "repetition_penalty": float, "seed": int, "n": int, "top_logprobs": int,
        }
        for key, cast in casts.items():
            setattr(params, key, self._read_opt(self._editors[key], cast))

        raw_stop = self.stop_edit.text().replace("\n", ",")
        params.stop = [part.strip() for part in raw_stop.split(",") if part.strip()]

        logprobs = self.logprobs_combo.currentText()
        params.logprobs = None if logprobs == "auto" else logprobs == "true"

        params.reasoning_enabled = self.reasoning_enabled.isChecked()
        params.reasoning_effort = self.effort_combo.currentText()
        params.thinking_budget_tokens = self._read_opt(
            self._editors["thinking_budget_tokens"], int)
        params.include_thoughts = self.include_thoughts.isChecked()

        params.system_prompt = self.system_prompt.toPlainText()
        params.context_mode = self.context_mode.currentData()
        params.context_last_n = self.last_n.value()
        params.context_token_budget = self.token_budget.value()
        params.context_include_reasoning = self.include_reasoning.isChecked()

        params.stream = self.stream_combo.currentData()
        params.extra_body = self._read_json(self.extra_body)
        params.extra_headers = self._read_json(self.extra_headers)
        return params

    def set_params(self, params: RequestParams) -> None:
        self._loading = True
        try:
            defs = RequestParams()
            for key, edit in self._editors.items():
                value = getattr(params, key, None)
                default_value = getattr(defs, key, None)
                if value is None or value == default_value:
                    edit.setText("")
                else:
                    edit.setText(str(value))
                _mark(edit, False)

            self.stop_edit.setText(", ".join(params.stop or []))
            index = self.logprobs_combo.findText(
                "auto" if params.logprobs is None else str(params.logprobs).lower())
            self.logprobs_combo.setCurrentIndex(max(0, index))

            self.reasoning_enabled.setChecked(params.reasoning_enabled)
            idx = self.effort_combo.findText(params.reasoning_effort)
            self.effort_combo.setCurrentIndex(idx if idx >= 0 else 2)
            self.include_thoughts.setChecked(params.include_thoughts)

            self.system_prompt.setPlainText(params.system_prompt or "")

            mode_index = self.context_mode.findData(params.context_mode)
            self.context_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            self.last_n.setValue(max(1, params.context_last_n))
            self.token_budget.setValue(max(64, params.context_token_budget))
            self.include_reasoning.setChecked(params.context_include_reasoning)

            stream_index = self.stream_combo.findData(params.stream)
            self.stream_combo.setCurrentIndex(stream_index if stream_index >= 0 else 0)

            self.extra_body.setPlainText(
                json.dumps(params.extra_body, ensure_ascii=False, indent=2)
                if params.extra_body else "")
            self.extra_headers.setPlainText(
                json.dumps(params.extra_headers, ensure_ascii=False, indent=2)
                if params.extra_headers else "")
        finally:
            self._loading = False