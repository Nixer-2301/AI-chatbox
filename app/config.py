"""应用配置存储：模型档案、默认参数、界面设置。

配置以 JSON 明文保存，方便专业用户直接查看与版本管理。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Iterable, Optional

from .models import ModelProfile, RequestParams


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "AIChat"


def _seed_profiles() -> list[ModelProfile]:
    return [
        ModelProfile(
            name="OpenAI 兼容端点",
            protocol="openai_chat",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            auth_scheme="bearer",
            notes="适用于 OpenAI 及一切兼容 /chat/completions 的服务（vLLM、Ollama、OneAPI 等）。",
        ),
        ModelProfile(
            name="Anthropic Claude",
            protocol="anthropic_messages",
            base_url="https://api.anthropic.com/v1",
            model="claude-sonnet-4-20250514",
            auth_scheme="x-api-key",
            api_version="2023-06-01",
            notes="思维强度映射为 thinking.budget_tokens。",
        ),
        ModelProfile(
            name="Google Gemini",
            protocol="google_gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-2.5-pro",
            auth_scheme="query",
            notes="思维强度映射为 generationConfig.thinkingConfig。",
        ),
        ModelProfile(
            name="本地 vLLM",
            protocol="openai_chat",
            base_url="http://127.0.0.1:8000/v1",
            model="Qwen3-32B",
            auth_scheme="none",
            notes="本地推理服务示例，可携带 extra_body 透传 vLLM 专属字段。",
        ),
        ModelProfile(
            name="自定义 HTTP 端点",
            protocol="raw_http",
            base_url="https://example.com",
            model="custom-model",
            auth_scheme="bearer",
            raw_method="POST",
            raw_url="{{base_url}}/v1/chat",
            raw_headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {{api_key}}",
            },
            raw_body_template=(
                '{\n'
                '  "model": "{{model}}",\n'
                '  "stream": {{#stream}},\n'
                '  "messages": {{#messages}},\n'
                '  "options": {{#params}}\n'
                '}'
            ),
            raw_stream_mode="sse",
            raw_content_path="choices[0].message.content",
            raw_delta_path="choices[0].delta.content",
            raw_usage_path="usage",
            notes="完全自定义报文。占位符：{{key}} 按字符串转义，{{#key}} 按 JSON 原文插入。",
        ),
    ]


class ConfigStore:
    """配置中心的读写入口，线程安全。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.json"
        self.db_path = self.data_dir / "aichat.db"

        self._lock = threading.RLock()
        self.profiles: list[ModelProfile] = []
        self.default_params: dict = RequestParams().to_dict()
        self.settings: dict = {
            "reveal_secrets": False,
            "save_raw_stream": True,
            "font_size": 13,
            "auto_scroll": True,
            "max_context_chars": 4000,
            "last_profile_id": "",
            "last_session_id": "",
        }
        self.load()

    # ---------------------------------------------------------------- 读写 --
    def load(self) -> None:
        with self._lock:
            if not self.config_path.exists():
                self.profiles = _seed_profiles()
                self.save()
                return
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            profiles = raw.get("profiles")
            if isinstance(profiles, list) and profiles:
                self.profiles = [ModelProfile.from_dict(p) for p in profiles]
            else:
                self.profiles = _seed_profiles()
            params = raw.get("default_params")
            merged = RequestParams().to_dict()
            if isinstance(params, dict):
                merged.update({k: v for k, v in params.items() if k in merged})
            self.default_params = merged
            settings = raw.get("settings")
            if isinstance(settings, dict):
                self.settings.update(settings)

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "profiles": [p.to_dict() for p in self.profiles],
                "default_params": self.default_params,
                "settings": self.settings,
            }
            tmp = self.config_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.config_path)

    # -------------------------------------------------------------- 模型库 --
    def get_profile(self, profile_id: str) -> Optional[ModelProfile]:
        with self._lock:
            for p in self.profiles:
                if p.id == profile_id:
                    return p
        return None

    def profile_names(self) -> list[str]:
        with self._lock:
            return [p.name for p in self.profiles]

    def upsert_profile(self, profile: ModelProfile) -> None:
        with self._lock:
            for idx, p in enumerate(self.profiles):
                if p.id == profile.id:
                    self.profiles[idx] = profile
                    break
            else:
                self.profiles.append(profile)
            self.save()

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            self.profiles = [p for p in self.profiles if p.id != profile_id]
            self.save()

    def duplicate_profile(self, profile_id: str) -> Optional[ModelProfile]:
        src = self.get_profile(profile_id)
        if not src:
            return None
        clone = ModelProfile.from_dict(src.to_dict())
        clone.id = ModelProfile().id
        clone.name = src.name + " 副本"
        self.upsert_profile(clone)
        return clone

    def reorder_profiles(self, ordered_ids: Iterable[str]) -> None:
        with self._lock:
            index = {pid: i for i, pid in enumerate(ordered_ids)}
            self.profiles.sort(key=lambda p: index.get(p.id, 1 << 30))
            self.save()

    # ---------------------------------------------------------------- 设置 --
    def get_setting(self, key: str, default=None):
        with self._lock:
            return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            self.settings[key] = value

    def save_settings(self) -> None:
        self.save()

    def default_request_params(self) -> RequestParams:
        with self._lock:
            return RequestParams.from_dict(self.default_params)

    def set_default_request_params(self, params: RequestParams) -> None:
        with self._lock:
            self.default_params = params.to_dict()
            self.save()

    # ------------------------------------------------------------ 导入导出 --
    def export_profiles(self, path: Path, ids: Optional[list[str]] = None) -> int:
        with self._lock:
            items = [p for p in self.profiles if ids is None or p.id in ids]
            path.write_text(
                json.dumps([p.to_dict() for p in items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return len(items)

    def import_profiles(self, path: Path, rename_on_conflict: bool = True) -> int:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("profiles", [])
        count = 0
        for item in raw or []:
            profile = ModelProfile.from_dict(item)
            if self.get_profile(profile.id):
                profile.id = ModelProfile().id
                if rename_on_conflict:
                    profile.name = profile.name + " (导入)"
            self.upsert_profile(profile)
            count += 1
        return count