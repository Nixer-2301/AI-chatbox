"""HTTP 执行引擎。

同步实现，供工作线程调用；所有原始报文（请求头、请求体、响应体、
SSE 原始流）都会完整记录下来，满足工业级可审计需求。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

import requests

from .models import ModelProfile, RequestRecord
from .protocol import (
    Adapter, Delta, ParseResult, RequestSpec, get_adapter, mask_headers, mask_url,
)

MAX_RAW_STREAM_CHARS = 4_000_000

DeltaCallback = Callable[[Delta], None]
RawCallback = Callable[[str], None]


def extract_error(body: Any, status: Optional[int]) -> str:
    """从各家错误结构里提取可读信息。"""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or ""
            if message:
                return str(message)
            return json.dumps(error, ensure_ascii=False)[:2000]
        if isinstance(error, str):
            return error
        for key in ("message", "detail", "msg"):
            if body.get(key):
                return str(body[key])
    if isinstance(body, str) and body.strip():
        return body.strip()[:2000]
    return f"HTTP {status}"


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


def execute(spec: RequestSpec, profile: ModelProfile,
            on_delta: Optional[DeltaCallback] = None,
            on_raw: Optional[RawCallback] = None,
            stop_event: Optional[threading.Event] = None) -> tuple[RequestRecord, ParseResult]:
    """执行一次请求。

    返回 (完整请求记录, 解析结果)。
    """
    adapter: Adapter = get_adapter(profile.protocol)
    record = RequestRecord(
        profile_id=profile.id,
        profile_name=profile.name,
        model=profile.model,
        protocol=profile.protocol,
        method=spec.method,
        url=mask_url(spec.full_url()),
        request_headers=mask_headers(spec.headers),
        request_body=spec.body,
        streamed=spec.stream_mode != "none",
    )
    result = ParseResult()

    if spec.stream_mode != "none" and not adapter.supports_stream:
        record.error = f"协议 {adapter.label} 不支持流式请求，请关闭流式开关。"
        return record, result

    session = requests.Session()
    proxies = None
    if profile.proxy.strip():
        proxies = {"http": profile.proxy.strip(), "https": profile.proxy.strip()}

    body = spec.body
    json_body = body if isinstance(body, (dict, list)) else None
    text_body = body if isinstance(body, str) else None

    started = time.perf_counter()
    first_token_at: Optional[float] = None
    stream_buffer: list[str] = []

    try:
        response = session.request(
            spec.method,
            spec.full_url(),
            headers=spec.headers,
            json=json_body,
            data=text_body,
            timeout=profile.timeout or 120,
            proxies=proxies,
            verify=profile.verify_ssl,
            stream=spec.stream_mode != "none",
        )
    except requests.RequestException as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.latency_ms = int((time.perf_counter() - started) * 1000)
        return record, result

    record.status = response.status_code
    record.response_headers = dict(response.headers)

    try:
        if spec.stream_mode != "none" and response.status_code < 400:
            first_token_at = _consume_stream(
                response, spec, adapter, profile, record, result,
                on_delta, on_raw, stop_event, stream_buffer,
            )
            if stream_buffer:
                record.raw_stream = "".join(stream_buffer)
            record.content = result.content
            record.reasoning = result.reasoning
            record.finish_reason = result.finish_reason
        else:
            text = response.text
            try:
                parsed_body: Any = json.loads(text)
            except (TypeError, ValueError):
                parsed_body = text
            record.response_body = parsed_body
            if response.status_code >= 400:
                record.error = extract_error(parsed_body, response.status_code)
                if isinstance(parsed_body, dict):
                    result.extra["error"] = parsed_body.get("error")
            else:
                result = adapter.parse(parsed_body, profile)
                if result.extra.get("error"):
                    record.error = extract_error(parsed_body, response.status_code)
                record.content = result.content
                record.reasoning = result.reasoning
                record.finish_reason = result.finish_reason
    except requests.RequestException as exc:
        record.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # 解析异常也要留下痕迹
        record.error = f"解析失败 {type(exc).__name__}: {exc}"
    finally:
        try:
            response.close()
        except Exception:
            pass
        session.close()

    record.usage = result.usage or {}
    record.latency_ms = int((time.perf_counter() - started) * 1000)
    if first_token_at is not None:
        record.ttft_ms = int((first_token_at - started) * 1000)

    if record.streamed and not record.response_body:
        record.response_body = {
            "streamed": True,
            "content": result.content,
            "reasoning": result.reasoning,
            "finish_reason": result.finish_reason,
            "usage": result.usage,
        }
    if stop_event is not None and stop_event.is_set() and not record.error:
        record.error = "已被用户中断"
    return record, result


def _consume_stream(response, spec: RequestSpec, adapter: Adapter,
                    profile: ModelProfile, record: RequestRecord,
                    result: ParseResult,
                    on_delta: Optional[DeltaCallback],
                    on_raw: Optional[RawCallback],
                    stop_event: Optional[threading.Event],
                    stream_buffer: list[str]) -> Optional[float]:
    """消费 SSE / NDJSON 流，返回首个正文 token 的 perf_counter 时刻。"""
    clock = {"first": None}
    buffer_size = 0
    event_name = ""
    data_lines: list[str] = []

    def first_token() -> None:
        if clock["first"] is None:
            clock["first"] = time.perf_counter()

    def emit(raw_line: str, event: str, payload: str) -> None:
        nonlocal buffer_size
        if on_raw is not None:
            on_raw(raw_line)
        if buffer_size < MAX_RAW_STREAM_CHARS:
            stream_buffer.append(raw_line + "\n")
            buffer_size += len(raw_line) + 1

        if payload.strip() == "[DONE]":
            return
        try:
            data: Any = json.loads(payload)
        except (TypeError, ValueError):
            data = payload

        for delta in adapter.stream_deltas(event, data, profile):
            if delta.kind in ("content", "reasoning") and delta.text:
                first_token()
                if delta.kind == "content":
                    result.content += delta.text
                else:
                    result.reasoning += delta.text
            elif delta.kind == "usage":
                merged = dict(result.usage or {})
                merged.update(delta.data or {})
                from .protocol import normalize_usage
                result.usage = normalize_usage(merged)
            elif delta.kind == "finish":
                result.finish_reason = delta.text
            elif delta.kind == "error":
                record.error = delta.text
            if on_delta is not None:
                on_delta(delta)

    if spec.stream_mode == "ndjson":
        for raw in response.iter_lines(decode_unicode=False):
            if stop_event is not None and stop_event.is_set():
                return clock["first"]
            emit(_decode(raw), "", _decode(raw))
        return clock["first"]

    for raw in response.iter_lines(decode_unicode=False):
        if stop_event is not None and stop_event.is_set():
            return clock["first"]
        line = _decode(raw)
        if not line:
            if data_lines:
                emit("\n".join(data_lines), event_name, "\n".join(data_lines))
                data_lines = []
                event_name = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
            continue
        emit(line, event_name, line)

    if data_lines:
        emit("\n".join(data_lines), event_name, "\n".join(data_lines))
    return clock["first"]