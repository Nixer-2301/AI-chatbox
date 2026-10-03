"""请求工作线程：把同步的 client.execute 放到子线程，避免阻塞界面。"""

from __future__ import annotations

import threading
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from . import client
from .models import ModelProfile, RequestRecord
from .protocol import Delta, ParseResult, RequestSpec

RAW_FLUSH_INTERVAL = 0.2


class RequestWorker(QThread):
    """执行单次请求；通过信号把增量数据送回界面线程。"""

    delta = pyqtSignal(object)              # Delta
    raw_lines = pyqtSignal(str)             # 批量原始行
    completed = pyqtSignal(object, object)  # RequestRecord, ParseResult

    def __init__(self, spec: RequestSpec, profile: ModelProfile, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.profile = profile
        self.stop_event = threading.Event()
        self._raw_buffer: list[str] = []
        self._last_flush = 0.0

    # ------------------------------------------------------------------ API --
    def request_stop(self) -> None:
        self.stop_event.set()

    def _on_raw(self, line: str) -> None:
        self._raw_buffer.append(line)
        now = time.monotonic()
        if now - self._last_flush >= RAW_FLUSH_INTERVAL:
            self._flush_raw()
            self._last_flush = now

    def _flush_raw(self) -> None:
        if self._raw_buffer:
            self.raw_lines.emit("\n".join(self._raw_buffer))
            self._raw_buffer.clear()

    # ----------------------------------------------------------------- run --
    def run(self) -> None:  # noqa: D401
        self._last_flush = time.monotonic()
        record: Optional[RequestRecord] = None
        result = ParseResult()
        try:
            record, result = client.execute(
                self.spec,
                self.profile,
                on_delta=self.delta.emit,
                on_raw=self._on_raw,
                stop_event=self.stop_event,
            )
        except Exception as exc:  # 兜底，保证线程一定返回结果
            record = RequestRecord(
                profile_id=self.profile.id,
                profile_name=self.profile.name,
                model=self.profile.model,
                protocol=self.profile.protocol,
                method=self.spec.method,
                url=self.spec.url,
                request_body=self.spec.body,
                error=f"工作线程异常 {type(exc).__name__}: {exc}",
            )
        finally:
            self._flush_raw()
        self.completed.emit(record, result)