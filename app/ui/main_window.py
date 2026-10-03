"""主窗口：把会话、参数、上下文、请求日志串成一个工作台。"""

from __future__ import annotations

import json
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from ..context import build_context_from_draft, draft_from_messages, draft_state, sync_draft
from ..models import ChatMessage, RequestParams, Session, fmt_ts
from ..workers import RequestWorker
from .chat_view import ChatView, Composer
from .context_panel import ContextPanel
from .log_panel import LogPanel
from .model_dialog import ModelDialog
from .params_panel import ParamsPanel

MIN_CALIBRATION = 0.3
MAX_CALIBRATION = 3.0


class MainWindow(QMainWindow):
    def __init__(self, config, storage):
        super().__init__()
        self.config = config
        self.storage = storage

        self.session: Optional[Session] = None
        self.profile_id: str = config.get_setting("last_profile_id", "") or ""
        self.worker: Optional[RequestWorker] = None
        self._last_bundle = None
        self._loading_sessions = False
        self._draft_upto = 0
        self._calibration = float(config.get_setting("token_calibration", 1.0) or 1.0)

        self.setWindowTitle("AIChat · API 对话工作台")
        self.resize(1600, 980)

        self._build_central()
        self._build_docks()
        self._build_actions()
        self._restore_geometry()

        self._reload_profiles()
        self._reload_sessions()
        if self.storage.list_sessions():
            last = self.config.get_setting("last_session_id", "")
            self._select_session(last)
        else:
            self._new_session()

    # --------------------------------------------------------------- 构建 --
    def _build_central(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.chat = ChatView()
        splitter.addWidget(self.chat)

        self.composer = Composer()
        self.composer.submitted.connect(self._on_submit)
        self.composer.stop_requested.connect(self._on_stop)
        splitter.addWidget(self.composer)
        splitter.setSizes([700, 160])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

    def _build_docks(self) -> None:
        # ---- 左：会话列表 ----
        self.session_dock = QDockWidget("会话", self)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(6, 6, 6, 6)
        left_box.setSpacing(4)

        self.session_list = QListWidget()
        self.session_list.setEditTriggers(
            QListWidget.EditTrigger.DoubleClicked | QListWidget.EditTrigger.EditKeyPressed)
        self.session_list.currentRowChanged.connect(self._on_session_row)
        self.session_list.itemChanged.connect(self._on_session_renamed)
        left_box.addWidget(self.session_list, 1)

        buttons = QHBoxLayout()
        btn_new = QPushButton("新建")
        btn_new.clicked.connect(self._new_session)
        btn_delete = QPushButton("删除")
        btn_delete.setProperty("danger", "true")
        btn_delete.clicked.connect(self._delete_session)
        buttons.addWidget(btn_new)
        buttons.addWidget(btn_delete)
        left_box.addLayout(buttons)

        self.session_dock.setWidget(left)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.session_dock)

        # ---- 右：参数 / 上下文 ----
        self.inspect_dock = QDockWidget("检查器", self)
        self.inspect_tabs = QTabWidget()
        self.params_panel = ParamsPanel()
        self.params_panel.changed.connect(self._refresh_context_preview)
        self.params_panel.btn_save_default.clicked.connect(self._save_default_params)
        self.context_panel = ContextPanel()
        self.context_panel.draft_changed.connect(self._on_draft_changed)
        self.context_panel.rebuild_requested.connect(self._rebuild_draft)
        self.inspect_tabs.addTab(self.params_panel, "参数")
        self.inspect_tabs.addTab(self.context_panel, "上下文")
        self.inspect_dock.setWidget(self.inspect_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspect_dock)

        # ---- 下：请求日志 ----
        self.log_dock = QDockWidget("请求日志（每次请求的完整报文）", self)
        self.log_panel = LogPanel(self.storage)
        self.log_panel.status_message.connect(self._set_status)
        self.log_dock.setWidget(self.log_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)

        self.resizeDocks([self.session_dock, self.inspect_dock], [240, 470],
                         Qt.Orientation.Horizontal)
        self.resizeDocks([self.log_dock], [230], Qt.Orientation.Vertical)

    def _build_actions(self) -> None:
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(" 模型 "))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(300)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        toolbar.addWidget(self.profile_combo)

        action_manage = QAction("模型管理…", self)
        action_manage.triggered.connect(self._open_model_dialog)
        toolbar.addAction(action_manage)

        toolbar.addSeparator()

        action_new = QAction("新建会话", self)
        action_new.setShortcut(QKeySequence("Ctrl+N"))
        action_new.triggered.connect(self._new_session)
        toolbar.addAction(action_new)

        action_clear = QAction("清空当前会话消息", self)
        action_clear.triggered.connect(self._clear_messages)
        toolbar.addAction(action_clear)

        action_export = QAction("导出会话", self)
        action_export.triggered.connect(self._export_session)
        toolbar.addAction(action_export)

        action_log = QAction("刷新上下文预览", self)
        action_log.setShortcut(QKeySequence("Ctrl+R"))
        action_log.triggered.connect(self._refresh_context_preview)
        toolbar.addAction(action_log)

        # 状态栏
        self.status_model = QLabel("模型：-")
        self.status_context = QLabel("上下文：-")
        self.status_state = QLabel("就绪")
        self.statusBar().addWidget(self.status_model)
        self.statusBar().addWidget(self.status_context)
        self.statusBar().addPermanentWidget(self.status_state)

    # --------------------------------------------------------------- 会话 --
    def _reload_sessions(self) -> None:
        self._loading_sessions = True
        try:
            self.session_list.clear()
            for session in self.storage.list_sessions():
                item = QListWidgetItem(session.title)
                item.setData(Qt.ItemDataRole.UserRole, session.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(f"创建于 {fmt_ts(session.created_at)}")
                self.session_list.addItem(item)
        finally:
            self._loading_sessions = False
        self._sync_session_selection()

    def _sync_session_selection(self) -> None:
        if not self.session:
            return
        for row in range(self.session_list.count()):
            if self.session_list.item(row).data(Qt.ItemDataRole.UserRole) == self.session.id:
                self.session_list.blockSignals(True)
                self.session_list.setCurrentRow(row)
                self.session_list.blockSignals(False)
                return

    def _select_session(self, session_id: str) -> None:
        session = self.storage.get_session(session_id) if session_id else None
        if session is None:
            sessions = self.storage.list_sessions()
            session = sessions[0] if sessions else None
        if session is None:
            self._new_session()
            return
        self._load_session(session)

    def _load_session(self, session: Session) -> None:
        # 切换前先把当前会话的参数与草稿落盘，避免编辑丢失
        if self.session is not None and self.session.id != session.id:
            self._store_session_params()
            self._persist_session()

        self.session = session
        self._sync_session_selection()

        if session.profile_id and self.config.get_profile(session.profile_id):
            self.profile_id = session.profile_id
            index = self.profile_combo.findData(session.profile_id)
            if index >= 0:
                self.profile_combo.blockSignals(True)
                self.profile_combo.setCurrentIndex(index)
                self.profile_combo.blockSignals(False)

        params = (RequestParams.from_dict(session.params) if session.params
                  else self.config.default_request_params())
        self.params_panel.set_params(params)

        history = self.storage.get_messages(session.id)
        raw_draft = session.params.get("context_draft") \
            if isinstance(session.params, dict) else None
        state = draft_from_messages(history, params) if raw_draft is None \
            else draft_state(raw_draft)
        self._draft_upto = state["synced_upto"]
        self.context_panel.set_draft(state["entries"])

        self.chat.clear_messages()
        for message in history:
            meta = self._message_meta(message)
            self.chat.append_message(message.role, message.content,
                                     message.reasoning, meta)

        self.log_panel.set_session(session.id)
        self._refresh_context_preview()
        self._set_status("已载入会话")

    def _message_meta(self, message: ChatMessage) -> str:
        meta = message.meta or {}
        bits = []
        if meta.get("model"):
            bits.append(str(meta["model"]))
        usage = meta.get("usage") or {}
        if usage.get("total_tokens"):
            bits.append(f"{usage['total_tokens']} tokens")
        if meta.get("latency_ms"):
            bits.append(f"{meta['latency_ms']} ms")
        bits.append(fmt_ts(message.created_at, "%m-%d %H:%M:%S"))
        return "　".join(bits)

    def _on_session_row(self, row: int) -> None:
        if row < 0 or self._loading_sessions:
            return
        session_id = self.session_list.item(row).data(Qt.ItemDataRole.UserRole)
        if self.session and session_id == self.session.id:
            return
        session = self.storage.get_session(session_id)
        if session:
            self._load_session(session)

    def _on_session_renamed(self, item: QListWidgetItem) -> None:
        if self._loading_sessions:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        session = self.storage.get_session(session_id)
        if not session:
            return
        title = item.text().strip() or "未命名会话"
        if title == session.title:
            return
        session.title = title
        self.storage.update_session(session)
        if self.session and self.session.id == session_id:
            self.session.title = title

    def _new_session(self) -> None:
        params = self.params_panel.params()
        session = self.storage.create_session(
            title="新会话", profile_id=self.profile_id, params=params.to_dict())
        self._reload_sessions()
        self._load_session(session)

    def _delete_session(self) -> None:
        if not self.session:
            return
        answer = QMessageBox.question(
            self, "删除会话",
            f"确定删除「{self.session.title}」及其全部消息与请求日志？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_session(self.session.id)
        self.session = None
        self._reload_sessions()
        remaining = self.storage.list_sessions()
        if remaining:
            self._load_session(remaining[0])
        else:
            self._new_session()

    def _clear_messages(self) -> None:
        if not self.session:
            return
        self.storage.delete_messages_from(self.session.id, 0)
        self.chat.clear_messages()
        self.context_panel.set_draft([])
        self._draft_upto = 0
        self._store_session_params()
        self._persist_session()
        self._refresh_context_preview()
        self._set_status("已清空当前会话的消息与上下文草稿")

    def _export_session(self) -> None:
        if not self.session:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出会话", f"{self.session.title}.json", "JSON (*.json)")
        if not path:
            return
        messages = self.storage.get_messages(self.session.id)
        records = self.storage.list_requests(self.session.id, limit=9999)
        payload = {
            "session": self.session.to_dict(),
            "messages": [m.to_dict() for m in messages],
            "requests": [r.to_dict() for r in records],
        }
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        self._set_status(f"已导出到 {path}")

    # --------------------------------------------------------------- 模型 --
    def _reload_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.config.profiles:
            self.profile_combo.addItem(f"{profile.name}　·　{profile.model}", profile.id)
        self.profile_combo.blockSignals(False)

        index = self.profile_combo.findData(self.profile_id)
        if index < 0 and self.profile_combo.count():
            index = 0
        self.profile_combo.setCurrentIndex(index)
        self.profile_id = self.profile_combo.currentData() or ""
        self._on_profile_changed(self.profile_combo.currentIndex())

    def current_profile(self):
        profile = self.config.get_profile(self.profile_id)
        if profile is None and self.config.profiles:
            profile = self.config.profiles[0]
            self.profile_id = profile.id
        return profile

    def _on_profile_changed(self, index: int) -> None:
        if index < 0:
            return
        self.profile_id = self.profile_combo.itemData(index) or ""
        self.config.set_setting("last_profile_id", self.profile_id)
        if self.session:
            self.session.profile_id = self.profile_id
            self._store_session_params()
            self.storage.update_session(self.session)
        profile = self.current_profile()
        self.status_model.setText(
            f"模型：{profile.name if profile else '-'}　|　"
            f"{profile.protocol if profile else '-'}")
        self._refresh_context_preview()

    def _open_model_dialog(self) -> None:
        dialog = ModelDialog(self.config, self)
        dialog.exec()
        self._reload_profiles()
        self._set_status("模型配置已更新")

    # --------------------------------------------------------------- 预览 --
    def _refresh_context_preview(self) -> None:
        profile = self.current_profile()
        if profile is None or self.session is None:
            self.context_panel.clear()
            return
        params = self.params_panel.params()
        self._store_session_params(params)
        stream = params.stream if params.stream is not None else profile.stream
        try:
            bundle = build_context_from_draft(
                profile, params, self.context_panel.draft(), stream, self._calibration)
        except Exception as exc:
            self.context_panel.clear_preview()
            self._set_status(f"上下文组装失败：{exc}")
            return
        self._last_bundle = bundle
        self.context_panel.show_bundle(bundle)
        self.status_context.setText(
            f"上下文：≈{bundle.total_tokens} tokens / {len(bundle.sent_messages)} 条消息")

    # ----------------------------------------------------------- 上下文草稿 --
    def _store_session_params(self, params: RequestParams | None = None) -> None:
        """把参数面板与上下文草稿一起写进内存中的会话对象。"""
        if self.session is None:
            return
        data = (params if params is not None else self.params_panel.params()).to_dict()
        data["context_draft"] = {
            "entries": self.context_panel.draft(),
            "synced_upto": self._draft_upto,
        }
        self.session.params = data

    def _persist_session(self) -> None:
        if self.session is not None:
            self.storage.update_session(self.session)

    def _on_draft_changed(self) -> None:
        self._store_session_params()
        self._persist_session()
        self._refresh_context_preview()

    def _rebuild_draft(self) -> None:
        if self.session is None:
            return
        params = self.params_panel.params()
        history = self.storage.get_messages(self.session.id)
        state = draft_from_messages(history, params)
        self._draft_upto = state["synced_upto"]
        self.context_panel.set_draft(state["entries"])
        self._store_session_params(params)
        self._persist_session()
        self._refresh_context_preview()
        self._set_status(f"已按当前策略重建上下文草稿（{len(state['entries'])} 条）")

    def _sync_draft_with_history(self) -> None:
        """把会话里新出现的消息并入草稿，同时保留手工修改。"""
        if self.session is None:
            return
        params = self.params_panel.params()
        history = self.storage.get_messages(self.session.id)
        state = sync_draft(
            {"entries": self.context_panel.draft(), "synced_upto": self._draft_upto},
            history, params)
        self._draft_upto = state["synced_upto"]
        self.context_panel.set_draft(state["entries"])

    def _save_default_params(self) -> None:
        self.config.set_default_request_params(self.params_panel.params())
        self._set_status("已保存为默认参数")

    # --------------------------------------------------------------- 发送 --
    def _on_submit(self, text: str) -> None:
        profile = self.current_profile()
        if profile is None:
            QMessageBox.warning(self, "缺少模型", "请先在「模型管理」中添加一个模型。")
            return
        if self.worker is not None and self.worker.isRunning():
            self._set_status("已有请求正在进行，请先停止。")
            return
        if not self.session:
            self._new_session()

        user_message = ChatMessage(session_id=self.session.id, role="user", content=text)
        self.storage.add_message(user_message)
        self.chat.append_message("user", text, meta=fmt_ts(user_message.created_at, "%H:%M:%S"))

        if self.session.title == "新会话":
            self.session.title = text.strip().replace("\n", " ")[:28]
            self.storage.update_session(self.session)
            self._reload_sessions()

        self.composer.clear()
        self._sync_draft_with_history()
        self._store_session_params()
        self._refresh_context_preview()
        self._start_request(profile)

    def _start_request(self, profile) -> None:
        bundle = self._last_bundle
        if bundle is None or bundle.spec is None:
            self._refresh_context_preview()
            bundle = self._last_bundle
        if bundle is None or bundle.spec is None:
            self._set_status("上下文组装失败，请求未发送。")
            return

        self.worker = RequestWorker(bundle.spec, profile, self)
        self.worker.delta.connect(self._on_delta)
        self.worker.completed.connect(self._on_completed)
        self.chat.begin_stream("assistant", meta=f"{profile.name}　"
                                                 f"{'流式' if bundle.stream else '非流式'}")
        self.composer.set_busy(True)
        self._set_status("请求中…")
        self.worker.start()

    def _on_delta(self, delta) -> None:
        if delta.kind in ("content", "reasoning") and delta.text:
            self.chat.append_stream(delta.text, delta.kind)

    def _on_stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self._set_status("正在中断…")

    def _on_completed(self, record, result) -> None:
        self.composer.set_busy(False)
        self.chat.end_stream()
        if record is None:
            self._set_status("请求异常结束。")
            return

        record.session_id = self.session.id if self.session else ""
        self.storage.add_request(record)

        if record.error and not record.content:
            self.chat.append_notice(f"请求失败：{record.error}", "error")
        elif record.error:
            self.chat.append_notice(f"注意：{record.error}", "warn")

        meta = {
            "model": record.model,
            "usage": record.usage,
            "latency_ms": record.latency_ms,
            "ttft_ms": record.ttft_ms,
            "finish_reason": record.finish_reason,
            "request_id": record.id,
            "streamed": record.streamed,
        }
        assistant = ChatMessage(
            session_id=record.session_id, role="assistant",
            content=record.content or "", reasoning=record.reasoning or "", meta=meta)
        self.storage.add_message(assistant)

        self._calibrate(record)
        self._sync_draft_with_history()
        self._store_session_params()
        self._persist_session()
        self.log_panel.set_session(self.session.id if self.session else None)
        self._refresh_context_preview()

        usage = record.usage or {}
        self._set_status(
            f"{'完成' if record.ok else '失败'}　HTTP {record.status}　"
            f"延迟 {record.latency_ms} ms　首字 {record.ttft_ms or '-'} ms　"
            f"tokens {usage.get('input_tokens', '-')}→{usage.get('output_tokens', '-')}")

    def _calibrate(self, record) -> None:
        """用服务端返回的输入 token 校准本地估算系数。"""
        actual = (record.usage or {}).get("input_tokens")
        estimated = self._last_bundle.total_tokens if self._last_bundle else 0
        if not actual or not estimated:
            return
        ratio = actual / max(1, estimated)
        if MIN_CALIBRATION <= ratio <= MAX_CALIBRATION:
            # 指数平滑，避免单次异常把系数带偏
            self._calibration = round(self._calibration * 0.7 + ratio * 0.3, 4)
            self.config.set_setting("token_calibration", self._calibration)

    # --------------------------------------------------------------- 杂项 --
    def _set_status(self, text: str) -> None:
        self.status_state.setText(text)

    def _restore_geometry(self) -> None:
        encoded = self.config.get_setting("window_geometry", "")
        if encoded:
            try:
                from PyQt6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(encoded.encode("ascii")))
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(3000)
        if self.session:
            self.config.set_setting("last_session_id", self.session.id)
            self._store_session_params()
            self._persist_session()
        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.config.set_setting("window_geometry", geometry)
        except Exception:
            pass
        self.config.save()
        self.storage.close()
        super().closeEvent(event)