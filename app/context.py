"""上下文组装。

职责是把存储中的消息，按照用户设定的策略裁剪、转换，
再交给协议适配器编译成「即将发出的真实报文」，
并把整个过程的数据暴露给界面，做到完全可见。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import tokenizer
from .models import ChatMessage, ModelProfile, RequestParams, new_id
from .protocol import RequestSpec, mask_headers, mask_url, get_adapter

CONTEXT_MODES = {
    "full": "完整上下文（发送全部历史）",
    "last_n": "最近 N 条消息",
    "token_budget": "Token 预算截断（从最新往回取）",
}

REASONING_TAG_OPEN = "<thinking>"
REASONING_TAG_CLOSE = "</thinking>"

# 草稿条目允许的角色；草稿是「本次要发送的上下文」的可编辑副本
DRAFT_ROLES = ("system", "user", "assistant")
DRAFT_ORIGIN_LABELS = {"message": "会话消息", "manual": "手动添加"}


@dataclass
class ContextBundle:
    """一次请求的完整上下文快照。"""

    profile: Optional[ModelProfile] = None
    params: Optional[RequestParams] = None
    stream: bool = False

    system_prompt: str = ""
    sent_messages: list = field(default_factory=list)   # 实际发送的 {role, content}
    dropped: list = field(default_factory=list)         # 被裁剪掉的消息

    system_tokens: int = 0
    message_tokens: int = 0
    total_tokens: int = 0

    spec: Optional[RequestSpec] = None
    warnings: list = field(default_factory=list)

    def payload(self) -> Any:
        return self.spec.body if self.spec else None

    def headers_masked(self, reveal: bool = False) -> dict:
        if not self.spec:
            return {}
        return mask_headers(self.spec.headers, reveal=reveal)

    def url_masked(self, reveal: bool = False) -> str:
        return mask_url(self.spec.full_url(), reveal=reveal) if self.spec else ""


def preview_text(text: str, limit: int = 160) -> str:
    """把多行文本压成单行预览。"""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + " …"


def select_messages(messages: list[ChatMessage], params: RequestParams) -> tuple[list, list]:
    """按策略选出要发送的消息，返回 (选中, 被丢弃)。"""
    usable = [m for m in messages if (m.content or "").strip()
              or (params.context_include_reasoning and (m.reasoning or "").strip())]

    if params.context_mode == "last_n":
        count = max(1, int(params.context_last_n or 1))
        if len(usable) > count:
            return usable[-count:], usable[:-count]
        return usable, []

    if params.context_mode == "token_budget":
        budget = max(64, int(params.context_token_budget or 1000))
        picked: list[ChatMessage] = []
        used = 0
        for message in reversed(usable):
            cost = tokenizer.estimate(message.content) \
                + (tokenizer.estimate(message.reasoning)
                   if params.context_include_reasoning else 0) + 4
            if picked and used + cost > budget:
                break
            picked.append(message)
            used += cost
        picked.reverse()
        dropped = usable[:len(usable) - len(picked)]
        return picked, dropped

    return usable, []


def to_payload_messages(messages: list[ChatMessage],
                        params: RequestParams) -> list[dict]:
    """ChatMessage → 适配器需要的 {role, content}。"""
    payload = []
    for message in messages:
        content = message.content or ""
        if params.context_include_reasoning and (message.reasoning or "").strip():
            content = (f"{REASONING_TAG_OPEN}\n{message.reasoning}\n"
                       f"{REASONING_TAG_CLOSE}\n\n{content}")
        payload.append({"role": message.role, "content": content})
    return payload


def build_context(profile: ModelProfile, params: RequestParams,
                  messages: list[ChatMessage], stream: bool,
                  calibration: float = 1.0) -> ContextBundle:
    """组装上下文并编译出真实请求报文。"""
    bundle = ContextBundle(profile=profile, params=params, stream=stream)

    selected, dropped = select_messages(messages, params)
    bundle.dropped = dropped
    bundle.system_prompt = params.system_prompt or ""
    bundle.sent_messages = to_payload_messages(selected, params)

    bundle.system_tokens = tokenizer.estimate(bundle.system_prompt, calibration)
    bundle.message_tokens = tokenizer.estimate_messages(
        bundle.sent_messages, calibration
    )
    bundle.total_tokens = bundle.system_tokens + bundle.message_tokens

    adapter = get_adapter(profile.protocol)
    spec = adapter.build(profile, bundle.system_prompt,
                         bundle.sent_messages, params, stream)
    bundle.spec = spec
    bundle.warnings = list(spec.warnings)

    if not bundle.system_prompt.strip():
        bundle.warnings.append("未设置系统提示词，请求中不会携带 system 角色。")
    if dropped:
        bundle.warnings.append(
            f"上下文策略「{CONTEXT_MODES.get(params.context_mode, params.context_mode)}」"
            f"已裁剪 {len(dropped)} 条历史消息。"
        )
    return bundle


def diff_against_full(bundle: ContextBundle) -> str:
    """给出一句话的上下文摘要，用于状态栏。"""
    return (f"上下文 ≈ {bundle.total_tokens} tokens "
            f"（系统 {bundle.system_tokens} / 消息 {bundle.message_tokens}），"
            f"消息 {len(bundle.sent_messages)} 条")


# --------------------------------------------------------------------------- #
# 上下文草稿：把「本次要发送的上下文」变成可编辑的对象
# --------------------------------------------------------------------------- #

def _inline_reasoning(message: ChatMessage, params: RequestParams) -> str:
    """按参数决定是否把历史思维链内联进正文。"""
    content = message.content or ""
    if params.context_include_reasoning and (message.reasoning or "").strip():
        content = (f"{REASONING_TAG_OPEN}\n{message.reasoning}\n"
                   f"{REASONING_TAG_CLOSE}\n\n{content}")
    return content


def normalize_draft(raw: Any) -> list:
    """把任意来源的草稿数据规整为合法条目列表。"""
    entries = []
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        if role not in DRAFT_ROLES:
            role = "user"
        content = item.get("content")
        entries.append({
            "id": str(item.get("id") or new_id("dft_")),
            "role": role,
            "content": "" if content is None else str(content),
            "enabled": bool(item.get("enabled", True)),
            "origin": str(item.get("origin") or "manual"),
            "source_id": str(item.get("source_id") or ""),
        })
    return entries


def draft_state(raw: Any) -> dict:
    """读取持久化的草稿状态；结构为 {entries, synced_upto}。"""
    if not isinstance(raw, dict):
        return {"entries": [], "synced_upto": 0}
    return {
        "entries": normalize_draft(raw.get("entries")),
        "synced_upto": max(0, int(raw.get("synced_upto") or 0)),
    }


def draft_from_messages(messages: list[ChatMessage], params: RequestParams) -> dict:
    """按当前裁剪策略，从会话历史生成一份草稿。

    策略选中的消息默认启用，被裁掉的默认禁用（仍然可见，可手动打开）。
    """
    selected, _dropped = select_messages(messages, params)
    selected_ids = {m.id for m in selected}
    entries = []
    upto = 0
    for message in messages:
        upto = max(upto, int(message.seq or 0))
        content = _inline_reasoning(message, params)
        if not content.strip():
            continue
        entries.append({
            "id": new_id("dft_"),
            "role": message.role if message.role in DRAFT_ROLES else "user",
            "content": content,
            "enabled": message.id in selected_ids,
            "origin": "message",
            "source_id": message.id,
        })
    return {"entries": entries, "synced_upto": upto}


def sync_draft(state: dict, messages: list[ChatMessage],
               params: RequestParams) -> dict:
    """把尚未折入草稿的新消息追加进来，同时保留用户的手工修改。"""
    entries = list(state.get("entries") or [])
    upto = int(state.get("synced_upto") or 0)
    max_seq = max((int(m.seq or 0) for m in messages), default=0)
    if max_seq < upto:      # 历史被清空/截断过，重新开始计数
        upto = 0
    known = {entry.get("source_id") for entry in entries if entry.get("source_id")}
    for message in sorted(messages, key=lambda m: int(m.seq or 0)):
        seq = int(message.seq or 0)
        if seq <= upto or message.id in known:
            continue
        content = _inline_reasoning(message, params)
        if not content.strip():
            continue
        entries.append({
            "id": new_id("dft_"),
            "role": message.role if message.role in DRAFT_ROLES else "user",
            "content": content,
            "enabled": True,
            "origin": "message",
            "source_id": message.id,
        })
    return {"entries": entries, "synced_upto": max(upto, max_seq)}


def build_context_from_draft(profile: ModelProfile, params: RequestParams,
                             entries: Any, stream: bool,
                             calibration: float = 1.0) -> ContextBundle:
    """按可编辑草稿组装上下文并编译真实请求报文。"""
    bundle = ContextBundle(profile=profile, params=params, stream=stream)

    system_parts = []
    base = (params.system_prompt or "").strip()
    if base:
        system_parts.append(base)

    sent: list[dict] = []
    dropped: list[dict] = []
    for entry in normalize_draft(entries):
        content = entry.get("content") or ""
        if not entry.get("enabled", True):
            dropped.append(entry)
            continue
        if not content.strip():
            continue
        if entry["role"] == "system":
            system_parts.append(content)
        else:
            sent.append({"role": entry["role"], "content": content})

    bundle.system_prompt = "\n\n".join(system_parts)
    bundle.sent_messages = sent
    bundle.dropped = dropped
    bundle.system_tokens = tokenizer.estimate(bundle.system_prompt, calibration)
    bundle.message_tokens = tokenizer.estimate_messages(sent, calibration)
    bundle.total_tokens = bundle.system_tokens + bundle.message_tokens

    adapter = get_adapter(profile.protocol)
    spec = adapter.build(profile, bundle.system_prompt, sent, params, stream)
    bundle.spec = spec
    bundle.warnings = list(spec.warnings)

    if not bundle.system_prompt.strip():
        bundle.warnings.append("未设置系统提示词，请求中不会携带 system 角色。")
    if dropped:
        bundle.warnings.append(f"有 {len(dropped)} 条上下文被禁用，不会随本次请求发送。")
    if not sent:
        bundle.warnings.append("当前没有任何启用的消息内容，请求体中的消息列表为空。")
    return bundle


CONTEXT_KEYS = ("messages", "input", "contents", "conversation")


def find_messages_in_body(body: Any) -> list:
    """从任意协议的请求体里定位消息数组，用于回看历史请求的上下文。"""
    if isinstance(body, str):
        try:
            import json
            body = json.loads(body)
        except (TypeError, ValueError):
            return []
    if not isinstance(body, dict):
        return []
    for key in CONTEXT_KEYS:
        value = body.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def flatten_message(item: dict) -> tuple:
    """把一条消息压平成 (role, text)，兼容各种分段结构。"""
    if not isinstance(item, dict):
        return ("?", str(item))
    role = str(item.get("role") or item.get("type") or "?")
    content = item.get("content", item.get("parts", item.get("text", "")))
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
    else:
        parts.append(str(content))
    return (role, "".join(parts))