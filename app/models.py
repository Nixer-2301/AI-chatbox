"""核心数据模型。

所有可持久化对象统一提供 `to_dict()` / `from_dict()`，
以便配置文件和 SQLite 之间无损往返。
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def now_ts() -> float:
    return time.time()


def fmt_ts(ts: float, pattern: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not ts:
        return "-"
    return time.strftime(pattern, time.localtime(ts))


def dataclass_from_dict(cls, data: Any):
    """宽容地从 dict 构造 dataclass，忽略多余字段、补齐缺失字段。"""
    if not isinstance(data, dict):
        return cls()
    names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in names}
    try:
        return cls(**kwargs)
    except TypeError:
        return cls()


def json_dumps(obj: Any, indent: Optional[int] = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=str)


# --------------------------------------------------------------------------- #
# 协议
# --------------------------------------------------------------------------- #

class Protocol(str, Enum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC = "anthropic_messages"
    GEMINI = "google_gemini"
    RAW = "raw_http"


PROTOCOL_LABELS = {
    Protocol.OPENAI_CHAT.value: "OpenAI Chat Completions  (/chat/completions)",
    Protocol.OPENAI_RESPONSES.value: "OpenAI Responses  (/responses)",
    Protocol.ANTHROPIC.value: "Anthropic Messages  (/messages)",
    Protocol.GEMINI.value: "Google Gemini  (generateContent)",
    Protocol.RAW.value: "自定义 HTTP  (自由模板)",
}

# 鉴权方式（内置协议使用）
AUTH_SCHEMES = {
    "bearer": "Authorization: Bearer <key>",
    "x-api-key": "x-api-key: <key>",
    "api-key": "api-key: <key>",
    "query": "URL 查询参数 ?key=<key>",
    "none": "不携带密钥",
}


# --------------------------------------------------------------------------- #
# 模型档案（用户添加的一个 API 模型）
# --------------------------------------------------------------------------- #

@dataclass
class ModelProfile:
    id: str = field(default_factory=lambda: new_id("mdl_"))
    name: str = "未命名模型"
    protocol: str = Protocol.OPENAI_CHAT.value

    # 连接
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    api_key: str = ""
    auth_scheme: str = "bearer"
    stream: bool = True            # 该模型的默认输出方式
    path: str = ""                 # 覆盖协议默认端点路径，留空使用默认
    api_version: str = "2023-06-01"  # Anthropic 版本头
    timeout: int = 120
    max_retries: int = 0
    proxy: str = ""
    verify_ssl: bool = True

    # 透传
    extra_headers: dict = field(default_factory=dict)
    extra_query: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)

    # 自定义 HTTP 协议专用
    raw_method: str = "POST"
    raw_url: str = ""
    raw_headers: dict = field(default_factory=dict)
    raw_body_template: str = ""
    raw_stream_mode: str = "sse"     # sse | ndjson | none
    raw_content_path: str = ""
    raw_delta_path: str = ""
    raw_usage_path: str = ""

    notes: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ModelProfile":
        return dataclass_from_dict(cls, data)

    @property
    def protocol_label(self) -> str:
        return PROTOCOL_LABELS.get(self.protocol, self.protocol)


# --------------------------------------------------------------------------- #
# 请求参数
# --------------------------------------------------------------------------- #

@dataclass
class RequestParams:
    # ---- 采样 ----
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    min_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    seed: Optional[int] = None
    stop: list = field(default_factory=list)
    n: Optional[int] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None

    # ---- 思维强度 ----
    reasoning_enabled: bool = False
    reasoning_effort: str = "medium"          # minimal | low | medium | high
    thinking_budget_tokens: Optional[int] = None
    include_thoughts: bool = True

    # ---- 上下文 ----
    system_prompt: str = ""                   # 默认留空：请求中不携带系统提示词
    context_mode: str = "full"                # full | last_n | token_budget
    context_last_n: int = 20
    context_token_budget: int = 8000
    context_include_reasoning: bool = False   # 历史消息是否回传思维链

    # ---- 输出 ----
    stream: Optional[bool] = None             # None → 跟随模型档案
    extra_body: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "RequestParams":
        return dataclass_from_dict(cls, data)


# --------------------------------------------------------------------------- #
# 会话与消息
# --------------------------------------------------------------------------- #

@dataclass
class ChatMessage:
    id: str = field(default_factory=lambda: new_id("msg_"))
    session_id: str = ""
    seq: int = 0
    role: str = "user"           # user | assistant | system
    content: str = ""
    reasoning: str = ""
    created_at: float = field(default_factory=now_ts)
    meta: dict = field(default_factory=dict)
    tokens: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ChatMessage":
        return dataclass_from_dict(cls, data)


@dataclass
class Session:
    id: str = field(default_factory=lambda: new_id("ses_"))
    title: str = "新会话"
    profile_id: str = ""
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Session":
        return dataclass_from_dict(cls, data)


# --------------------------------------------------------------------------- #
# 请求记录（完整可审计）
# --------------------------------------------------------------------------- #

@dataclass
class RequestRecord:
    id: str = field(default_factory=lambda: new_id("req_"))
    session_id: str = ""
    profile_id: str = ""
    profile_name: str = ""
    model: str = ""
    protocol: str = ""
    created_at: float = field(default_factory=now_ts)

    method: str = "POST"
    url: str = ""
    request_headers: dict = field(default_factory=dict)
    request_body: Any = None

    status: Optional[int] = None
    response_headers: dict = field(default_factory=dict)
    response_body: Any = None
    raw_stream: str = ""

    content: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""

    latency_ms: int = 0
    ttft_ms: int = 0
    streamed: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        for key in ("request_headers", "request_body", "response_headers",
                    "response_body", "usage"):
            if not isinstance(d.get(key), (dict, list)):
                d[key] = {}
        return d

    @classmethod
    def from_dict(cls, data: Any) -> "RequestRecord":
        return dataclass_from_dict(cls, data)

    @property
    def ok(self) -> bool:
        return not self.error and (self.status is None or 200 <= self.status < 300)