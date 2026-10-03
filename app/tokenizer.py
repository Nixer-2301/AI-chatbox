"""轻量 token 估算。

不引入外部 tokenizer 依赖：
- CJK / 假名 / 谚文按 1 字符 ≈ 1 token 估算
- 其余字符按 3.8 字符 ≈ 1 token 估算

请求返回后，界面会用服务端 usage 计算校准系数，让后续估算更贴近真实值。
"""

from __future__ import annotations

from typing import Iterable

_CJK_RANGES = (
    (0x1100, 0x11FF),    # 谚文字母
    (0x2E80, 0x2EFF),    # CJK 部首
    (0x3040, 0x30FF),    # 平假名 / 片假名
    (0x3130, 0x318F),    # 谚文兼容
    (0x31F0, 0x31FF),    # 片假名扩展
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xA960, 0xA97F),    # 谚文扩展 A
    (0xAC00, 0xD7AF),    # 谚文音节
    (0xF900, 0xFAFF),    # CJK 兼容表意
    (0xFF00, 0xFFEF),    # 全角
    (0x20000, 0x2FA1F),  # CJK 扩展 B-F
)

_LATIN_CHARS_PER_TOKEN = 3.8


def _is_wide(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def estimate(text: str, calibration: float = 1.0) -> int:
    """估算一段文本的 token 数。"""
    if not text:
        return 0
    wide = 0
    for ch in text:
        if _is_wide(ch):
            wide += 1
    narrow = len(text) - wide
    tokens = wide + narrow / _LATIN_CHARS_PER_TOKEN + 1.0
    if calibration <= 0:
        calibration = 1.0
    return int(round(tokens * calibration))


def estimate_messages(messages: Iterable[dict], calibration: float = 1.0) -> int:
    """估算一组消息的总 token（含每条消息的结构开销）。"""
    total = 0
    for msg in messages:
        total += 4  # role / 分隔符等结构开销的粗略估计
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate(content, calibration)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate(str(part.get("text", "")), calibration)
        total += estimate(str(msg.get("role", "")), calibration)
    return total + 3