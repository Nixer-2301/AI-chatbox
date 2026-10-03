"""协议适配器层。

把统一的 ``(system_prompt, messages, params)`` 编译为具体 HTTP 报文，
并把响应（普通 / SSE 流）解析回统一结构。

内置协议：
    openai_chat        OpenAI 及一切兼容 /chat/completions 的服务
    openai_responses   OpenAI Responses API
    anthropic_messages Anthropic Messages API
    google_gemini      Google Gemini generateContent
    raw_http           完全自定义报文模板（万能兜底）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import ModelProfile, Protocol, RequestParams

# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #

SECRET_HEADER_KEYS = {
    "authorization", "x-api-key", "api-key", "x-goog-api-key",
    "cookie", "proxy-authorization", "x-auth-token",
}

EFFORT_BUDGET_ANTHROPIC = {"minimal": 1024, "low": 2048, "medium": 4096, "high": 10000}
EFFORT_BUDGET_GEMINI = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}


def join_url(base: str, path: str) -> str:
    base = (base or "").rstrip("/")
    path = (path or "").strip()
    if not path:
        return base
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def deep_merge(target: dict, patch: Any) -> dict:
    """递归合并 patch 到 target（就地修改并返回）。"""
    if not isinstance(patch, dict):
        return target
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def mask_headers(headers: dict, reveal: bool = False) -> dict:
    """把密钥类请求头替换为掩码，便于安全地在界面上展示。"""
    out = {}
    for key, value in (headers or {}).items():
        if not reveal and key.lower() in SECRET_HEADER_KEYS and value:
            text = str(value)
            out[key] = (text[:10] + "…" + text[-4:]) if len(text) > 16 else "***已隐藏***"
        else:
            out[key] = value
    return out


def mask_url(url: str, reveal: bool = False) -> str:
    if reveal or not url:
        return url
    for marker in ("key=", "api_key=", "api-key=", "access_token="):
        idx = url.find(marker)
        while idx != -1:
            start = idx + len(marker)
            end = url.find("&", start)
            end = len(url) if end == -1 else end
            url = url[:start] + "***已隐藏***" + url[end:]
            idx = url.find(marker, start)
    return url


def _paths(path: str) -> list:
    """把 ``choices[0].delta.content`` 拆成 ["choices", 0, "delta", "content"]。"""
    tokens: list = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
                buf = ""
        elif ch == "[":
            if buf:
                tokens.append(buf)
                buf = ""
            end = path.find("]", i)
            if end == -1:
                buf += path[i:]
                break
            inner = path[i + 1:end].strip().strip("\"'")
            tokens.append(int(inner) if inner.lstrip("-").isdigit() else inner)
            i = end
        else:
            buf += ch
        i += 1
    if buf:
        tokens.append(buf)
    return tokens


def extract_path(obj: Any, path: str) -> Any:
    """按点号/下标路径取值，取不到返回 None。"""
    if not path:
        return None
    current = obj
    for token in _paths(path):
        if current is None:
            return None
        if isinstance(token, int):
            if isinstance(current, list) and -len(current) <= token < len(current):
                current = current[token]
            else:
                return None
        else:
            if isinstance(current, dict) and token in current:
                current = current[token]
            else:
                return None
    return current


def normalize_usage(raw: Any) -> dict:
    """把各家不同的 usage 字段归一化。"""
    if not isinstance(raw, dict) or not raw:
        return {}

    def pick(*keys):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    def nested(container, *keys):
        node = raw.get(container)
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
        return None

    input_tokens = pick("prompt_tokens", "input_tokens", "promptTokenCount")
    output_tokens = pick("completion_tokens", "output_tokens", "candidatesTokenCount")
    total_tokens = pick("total_tokens", "totalTokenCount")
    reasoning_tokens = nested("completion_tokens_details", "reasoning_tokens") \
        or nested("output_tokens_details", "reasoning_tokens") \
        or pick("thoughtsTokenCount")

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
        "raw": raw,
    }
    return {k: v for k, v in result.items() if v is not None}


def text_of(value: Any) -> str:
    """把 content 字段（可能是 str 或分段列表）压平成纯文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for part in value:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                chunks.append(str(part.get("text", part.get("content", "")) or ""))
        return "".join(chunks)
    return str(value)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class Delta:
    kind: str                 # content | reasoning | usage | finish | error | raw
    text: str = ""
    data: Any = None


@dataclass
class RequestSpec:
    method: str = "POST"
    url: str = ""
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: Any = None
    stream_mode: str = "none"       # sse | ndjson | none
    warnings: list = field(default_factory=list)

    def full_url(self) -> str:
        if not self.query:
            return self.url
        from urllib.parse import urlencode
        sep = "&" if "?" in self.url else "?"
        return self.url + sep + urlencode(self.query, doseq=True)


@dataclass
class ParseResult:
    content: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 适配器基类
# --------------------------------------------------------------------------- #

class Adapter:
    protocol: str = ""
    label: str = ""
    default_path: str = ""
    supports_stream: bool = True

    def build(self, profile: ModelProfile, system_prompt: str,
              messages: list[dict], params: RequestParams,
              stream: bool) -> RequestSpec:
        raise NotImplementedError

    def parse(self, body: Any, profile: Optional[ModelProfile] = None) -> ParseResult:
        raise NotImplementedError

    def stream_deltas(self, event: str, data: Any,
                      profile: Optional[ModelProfile] = None) -> list[Delta]:
        return []

    # -- 共用：采样参数 → 目标字段 ----------------------------------------
    @staticmethod
    def _apply_sampling(body: dict, params: RequestParams, mapping: dict) -> None:
        for src, dst in mapping.items():
            value = getattr(params, src, None)
            if value is not None:
                body[dst] = value
        if params.stop:
            body[mapping.get("__stop__", "stop")] = list(params.stop)


def _apply_auth(headers: dict, query: dict, profile: ModelProfile) -> None:
    key = (profile.api_key or "").strip()
    if not key:
        return
    scheme = profile.auth_scheme
    if scheme == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif scheme == "x-api-key":
        headers["x-api-key"] = key
    elif scheme == "api-key":
        headers["api-key"] = key
    elif scheme == "query":
        query["key"] = key


# --------------------------------------------------------------------------- #
# 1. OpenAI Chat Completions
# --------------------------------------------------------------------------- #

class OpenAIChatAdapter(Adapter):
    protocol = Protocol.OPENAI_CHAT.value
    label = "OpenAI Chat Completions"
    default_path = "/chat/completions"

    def build(self, profile, system_prompt, messages, params, stream) -> RequestSpec:
        spec = RequestSpec(
            method="POST",
            url=join_url(profile.base_url, profile.path or self.default_path),
            stream_mode="sse" if stream else "none",
        )
        spec.headers["Content-Type"] = "application/json"
        _apply_auth(spec.headers, spec.query, profile)

        body: dict = {"model": profile.model}
        chat_messages = []
        if system_prompt.strip():
            chat_messages.append({"role": "system", "content": system_prompt})
        chat_messages.extend(messages)
        body["messages"] = chat_messages

        self._apply_sampling(body, params, {
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "max_tokens": "max_tokens",
            "min_tokens": "min_tokens",
            "presence_penalty": "presence_penalty",
            "frequency_penalty": "frequency_penalty",
            "repetition_penalty": "repetition_penalty",
            "seed": "seed",
            "n": "n",
            "logprobs": "logprobs",
            "top_logprobs": "top_logprobs",
            "__stop__": "stop",
        })
        body["stream"] = stream
        if stream:
            body["stream_options"] = {"include_usage": True}

        if params.reasoning_enabled:
            body["reasoning_effort"] = params.reasoning_effort

        deep_merge(body, profile.extra_body)
        deep_merge(body, params.extra_body)
        spec.body = body
        spec.query.update(profile.extra_query)
        spec.headers.update(profile.extra_headers)
        spec.headers.update(params.extra_headers)
        return spec

    def parse(self, body, profile=None) -> ParseResult:
        result = ParseResult()
        if not isinstance(body, dict):
            result.content = text_of(body)
            return result
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            message = choice.get("message") or {}
            result.content = text_of(message.get("content"))
            result.reasoning = text_of(
                message.get("reasoning_content") or message.get("reasoning")
            )
            result.finish_reason = choice.get("finish_reason") or ""
            if message.get("tool_calls"):
                result.extra["tool_calls"] = message["tool_calls"]
        result.usage = normalize_usage(body.get("usage"))
        if body.get("error"):
            result.extra["error"] = body["error"]
        return result

    def stream_deltas(self, event, data, profile=None) -> list[Delta]:
        out: list[Delta] = []
        if not isinstance(data, dict):
            return out
        if data.get("error"):
            out.append(Delta("error", str(data["error"])))
            return out
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            delta = choice.get("delta") or {}
            content = text_of(delta.get("content"))
            if content:
                out.append(Delta("content", content))
            reasoning = text_of(delta.get("reasoning_content") or delta.get("reasoning"))
            if reasoning:
                out.append(Delta("reasoning", reasoning))
            if choice.get("finish_reason"):
                out.append(Delta("finish", str(choice["finish_reason"])))
        if data.get("usage"):
            out.append(Delta("usage", data=data["usage"]))
        return out


# --------------------------------------------------------------------------- #
# 2. OpenAI Responses
# --------------------------------------------------------------------------- #

class OpenAIResponsesAdapter(Adapter):
    protocol = Protocol.OPENAI_RESPONSES.value
    label = "OpenAI Responses"
    default_path = "/responses"

    def build(self, profile, system_prompt, messages, params, stream) -> RequestSpec:
        spec = RequestSpec(
            method="POST",
            url=join_url(profile.base_url, profile.path or self.default_path),
            stream_mode="sse" if stream else "none",
        )
        spec.headers["Content-Type"] = "application/json"
        _apply_auth(spec.headers, spec.query, profile)

        items = []
        for message in messages:
            role = message.get("role")
            text = text_of(message.get("content"))
            if role == "assistant":
                items.append({"role": "assistant",
                              "content": [{"type": "output_text", "text": text}]})
            else:
                items.append({"role": "user",
                              "content": [{"type": "input_text", "text": text}]})

        body: dict = {"model": profile.model, "input": items}
        if system_prompt.strip():
            body["instructions"] = system_prompt
        if params.max_tokens is not None:
            body["max_output_tokens"] = params.max_tokens
        for src, dst in (("temperature", "temperature"), ("top_p", "top_p"),
                         ("seed", "seed"), ("top_logprobs", "top_logprobs")):
            value = getattr(params, src, None)
            if value is not None:
                body[dst] = value
        if params.reasoning_enabled:
            body["reasoning"] = {"effort": params.reasoning_effort, "summary": "auto"}
        body["stream"] = stream

        deep_merge(body, profile.extra_body)
        deep_merge(body, params.extra_body)
        spec.body = body
        spec.query.update(profile.extra_query)
        spec.headers.update(profile.extra_headers)
        spec.headers.update(params.extra_headers)
        return spec

    def parse(self, body, profile=None) -> ParseResult:
        result = ParseResult()
        if not isinstance(body, dict):
            result.content = text_of(body)
            return result
        for item in body.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                        result.content += text_of(part.get("text"))
            elif item.get("type") == "reasoning":
                for summary in item.get("summary") or []:
                    if isinstance(summary, dict):
                        result.reasoning += text_of(summary.get("text"))
        result.finish_reason = body.get("status") or ""
        incomplete = body.get("incomplete_details")
        if isinstance(incomplete, dict) and incomplete.get("reason"):
            result.finish_reason = str(incomplete["reason"])
        result.usage = normalize_usage(body.get("usage"))
        if body.get("error"):
            result.extra["error"] = body["error"]
        return result

    def stream_deltas(self, event, data, profile=None) -> list[Delta]:
        out: list[Delta] = []
        if not isinstance(data, dict):
            return out
        if event in ("response.output_text.delta",) or data.get("type") == "response.output_text.delta":
            out.append(Delta("content", text_of(data.get("delta"))))
        elif event in ("response.reasoning_summary_text.delta",
                       "response.reasoning_text.delta") or \
                str(data.get("type", "")).startswith("response.reasoning") and data.get("delta"):
            out.append(Delta("reasoning", text_of(data.get("delta"))))
        elif event == "response.completed" or data.get("type") == "response.completed":
            response = data.get("response") or {}
            if isinstance(response, dict):
                out.append(Delta("usage", data=response.get("usage")))
                if response.get("status"):
                    out.append(Delta("finish", str(response["status"])))
        elif data.get("type") in ("error", "response.failed"):
            out.append(Delta("error", str(data.get("error") or data.get("message") or data)))
        return out


# --------------------------------------------------------------------------- #
# 3. Anthropic Messages
# --------------------------------------------------------------------------- #

class AnthropicAdapter(Adapter):
    protocol = Protocol.ANTHROPIC.value
    label = "Anthropic Messages"
    default_path = "/messages"

    def build(self, profile, system_prompt, messages, params, stream) -> RequestSpec:
        spec = RequestSpec(
            method="POST",
            url=join_url(profile.base_url, profile.path or self.default_path),
            stream_mode="sse" if stream else "none",
        )
        spec.headers["Content-Type"] = "application/json"
        spec.headers["anthropic-version"] = profile.api_version or "2023-06-01"
        _apply_auth(spec.headers, spec.query, profile)

        budget = params.thinking_budget_tokens
        if budget is None:
            budget = EFFORT_BUDGET_ANTHROPIC.get(params.reasoning_effort, 4096)

        max_tokens = params.max_tokens
        if max_tokens is None:
            max_tokens = max(4096, budget + 1024) if params.reasoning_enabled else 4096
        elif params.reasoning_enabled and max_tokens <= budget:
            max_tokens = budget + 1024
            spec.warnings.append(
                f"max_tokens 必须大于思维预算，已自动调整为 {max_tokens}。"
            )

        body: dict = {
            "model": profile.model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.get("role"), "content": text_of(m.get("content"))}
                         for m in messages],
        }
        if system_prompt.strip():
            body["system"] = system_prompt

        if params.reasoning_enabled:
            body["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
            if any(getattr(params, k) is not None
                   for k in ("temperature", "top_p", "top_k")):
                spec.warnings.append(
                    "Anthropic 在开启 thinking 时不接受 temperature/top_p/top_k，已从报文中移除。"
                )
        else:
            for src, dst in (("temperature", "temperature"), ("top_p", "top_p"),
                             ("top_k", "top_k")):
                value = getattr(params, src, None)
                if value is not None:
                    body[dst] = value
        if params.stop:
            body["stop_sequences"] = list(params.stop)
        body["stream"] = stream

        deep_merge(body, profile.extra_body)
        deep_merge(body, params.extra_body)
        spec.body = body
        spec.query.update(profile.extra_query)
        spec.headers.update(profile.extra_headers)
        spec.headers.update(params.extra_headers)
        return spec

    def parse(self, body, profile=None) -> ParseResult:
        result = ParseResult()
        if not isinstance(body, dict):
            result.content = text_of(body)
            return result
        for block in body.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                result.content += text_of(block.get("text"))
            elif block.get("type") == "thinking":
                result.reasoning += text_of(block.get("thinking"))
        result.finish_reason = body.get("stop_reason") or ""
        result.usage = normalize_usage(body.get("usage"))
        if body.get("type") == "error":
            result.extra["error"] = body.get("error")
        return result

    def stream_deltas(self, event, data, profile=None) -> list[Delta]:
        out: list[Delta] = []
        if not isinstance(data, dict):
            return out
        etype = event or data.get("type") or ""
        if etype == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "thinking_delta":
                out.append(Delta("reasoning", text_of(delta.get("thinking"))))
            elif delta.get("type") == "text_delta":
                out.append(Delta("content", text_of(delta.get("text"))))
            elif delta.get("type") == "input_json_delta":
                out.append(Delta("raw", text_of(delta.get("partial_json"))))
        elif etype == "message_start":
            message = data.get("message") or {}
            out.append(Delta("usage", data=message.get("usage")))
        elif etype == "message_delta":
            if data.get("usage"):
                out.append(Delta("usage", data=data["usage"]))
            delta = data.get("delta") or {}
            if delta.get("stop_reason"):
                out.append(Delta("finish", str(delta["stop_reason"])))
        elif etype == "error":
            out.append(Delta("error", str(data.get("error") or data)))
        return out


# --------------------------------------------------------------------------- #
# 4. Google Gemini
# --------------------------------------------------------------------------- #

class GeminiAdapter(Adapter):
    protocol = Protocol.GEMINI.value
    label = "Google Gemini"
    default_path = "/models/{model}:generateContent"

    def build(self, profile, system_prompt, messages, params, stream) -> RequestSpec:
        default_path = (f"/models/{profile.model}:streamGenerateContent"
                        if stream else f"/models/{profile.model}:generateContent")
        url = join_url(profile.base_url, profile.path or default_path)
        spec = RequestSpec(method="POST", url=url,
                           stream_mode="sse" if stream else "none")
        spec.headers["Content-Type"] = "application/json"
        _apply_auth(spec.headers, spec.query, profile)
        if stream:
            spec.query["alt"] = "sse"

        contents = []
        for message in messages:
            role = "model" if message.get("role") == "assistant" else "user"
            contents.append({"role": role,
                             "parts": [{"text": text_of(message.get("content"))}]})

        generation: dict = {}
        for src, dst in (("temperature", "temperature"), ("top_p", "topP"),
                         ("top_k", "topK"), ("max_tokens", "maxOutputTokens"),
                         ("seed", "seed"), ("n", "candidateCount")):
            value = getattr(params, src, None)
            if value is not None:
                generation[dst] = value
        if params.stop:
            generation["stopSequences"] = list(params.stop)
        if params.reasoning_enabled:
            budget = params.thinking_budget_tokens
            if budget is None:
                budget = EFFORT_BUDGET_GEMINI.get(params.reasoning_effort, 8192)
            generation["thinkingConfig"] = {
                "thinkingBudget": int(budget),
                "includeThoughts": bool(params.include_thoughts),
            }

        body: dict = {"contents": contents}
        if system_prompt.strip():
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if generation:
            body["generationConfig"] = generation

        deep_merge(body, profile.extra_body)
        deep_merge(body, params.extra_body)
        spec.body = body
        spec.query.update(profile.extra_query)
        spec.headers.update(profile.extra_headers)
        spec.headers.update(params.extra_headers)
        return spec

    def parse(self, body, profile=None) -> ParseResult:
        result = ParseResult()
        if not isinstance(body, dict):
            result.content = text_of(body)
            return result
        for candidate in (body.get("candidates") or [])[:1]:
            if not isinstance(candidate, dict):
                continue
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("thought"):
                    result.reasoning += text_of(part.get("text"))
                else:
                    result.content += text_of(part.get("text"))
            result.finish_reason = candidate.get("finishReason") or ""
        result.usage = normalize_usage(body.get("usageMetadata"))
        if body.get("error"):
            result.extra["error"] = body["error"]
        return result

    def stream_deltas(self, event, data, profile=None) -> list[Delta]:
        out: list[Delta] = []
        if not isinstance(data, dict):
            return out
        for candidate in (data.get("candidates") or [])[:1]:
            if not isinstance(candidate, dict):
                continue
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("thought"):
                    out.append(Delta("reasoning", text_of(part.get("text"))))
                else:
                    out.append(Delta("content", text_of(part.get("text"))))
            if candidate.get("finishReason"):
                out.append(Delta("finish", str(candidate["finishReason"])))
        if data.get("usageMetadata"):
            out.append(Delta("usage", data=data["usageMetadata"]))
        if data.get("error"):
            out.append(Delta("error", str(data["error"])))
        return out


# --------------------------------------------------------------------------- #
# 5. 自定义 HTTP
# --------------------------------------------------------------------------- #

class RawHTTPAdapter(Adapter):
    protocol = Protocol.RAW.value
    label = "自定义 HTTP"
    default_path = ""
    supports_stream = True

    @staticmethod
    def _render(template: str, context: dict) -> str:
        rendered = template or ""
        for key, value in context.items():
            raw_token = "{{#" + key + "}}"
            if raw_token in rendered:
                rendered = rendered.replace(
                    raw_token, json.dumps(value, ensure_ascii=False, default=str)
                )
            str_token = "{{" + key + "}}"
            if str_token in rendered:
                rendered = rendered.replace(str_token, str(value))
        return rendered

    def build(self, profile, system_prompt, messages, params, stream) -> RequestSpec:
        context = {
            "model": profile.model,
            "base_url": (profile.base_url or "").rstrip("/"),
            "api_key": profile.api_key or "",
            "system": system_prompt,
            "stream": stream,
            "messages": messages,
            "params": self._params_payload(params),
        }

        url_template = profile.raw_url or (profile.base_url + "/")
        url = self._render(url_template, context)
        headers = {}
        for key, value in (profile.raw_headers or {}).items():
            headers[key] = self._render(str(value), context)

        spec = RequestSpec(
            method=(profile.raw_method or "POST").upper(),
            url=url,
            stream_mode=profile.raw_stream_mode if stream else "none",
        )
        spec.headers.update(headers)

        if profile.raw_body_template.strip():
            rendered = self._render(profile.raw_body_template, context)
            try:
                spec.body = json.loads(rendered)
            except (TypeError, ValueError):
                spec.body = rendered
                spec.warnings.append("请求体模板不是合法 JSON，已按纯文本原文发送。")
        elif profile.raw_method.upper() in ("GET", "DELETE", "HEAD"):
            spec.body = None
        else:
            spec.body = None
            spec.warnings.append("未配置请求体模板，将发送空请求体。")

        spec.query.update(profile.extra_query)
        return spec

    @staticmethod
    def _params_payload(params: RequestParams) -> dict:
        payload = {}
        for key, value in params.to_dict().items():
            if key in ("system_prompt", "context_mode", "context_last_n",
                       "context_token_budget", "context_include_reasoning",
                       "extra_body", "extra_headers", "stream"):
                continue
            if value is None:
                continue
            if key == "stop" and not value:
                continue
            payload[key] = value
        return payload

    def parse(self, body, profile=None) -> ParseResult:
        result = ParseResult()
        if profile is None:
            result.content = text_of(body)
            return result
        content = extract_path(body, profile.raw_content_path) \
            if profile.raw_content_path else None
        if content is None and not profile.raw_content_path:
            content = text_of(body)
        result.content = text_of(content)
        usage = extract_path(body, profile.raw_usage_path) \
            if profile.raw_usage_path else None
        result.usage = normalize_usage(usage)
        return result

    def stream_deltas(self, event, data, profile=None) -> list[Delta]:
        if profile is None:
            return [Delta("content", text_of(data))]
        path = profile.raw_delta_path
        if not path:
            return [Delta("content", text_of(data))]
        value = extract_path(data, path)
        if value is None:
            return []
        return [Delta("content", text_of(value))]


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

ADAPTERS: dict[str, Adapter] = {
    a.protocol: a for a in (
        OpenAIChatAdapter(), OpenAIResponsesAdapter(), AnthropicAdapter(),
        GeminiAdapter(), RawHTTPAdapter(),
    )
}


def get_adapter(protocol: str) -> Adapter:
    adapter = ADAPTERS.get(protocol)
    if adapter is None:
        raise ValueError(f"未知协议：{protocol}")
    return adapter