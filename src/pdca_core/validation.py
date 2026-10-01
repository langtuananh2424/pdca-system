"""Kiểm tra tham số đầu vào dùng chung cho mọi tool (LLD 4.1).

Tham số được kiểm tra trong `pdca_core` (không dựa vào lược đồ của SDK) để
mọi đầu vào sai đều đi qua `run_tool` và có bản ghi audit.
"""

import base64
import binascii
import json
from collections.abc import Collection
from datetime import date
from typing import Any

from pdca_core.errors import InvalidArgument

MAX_TEXT = 2000
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def clip_text(value: str, max_len: int = MAX_TEXT) -> str:
    """Cắt văn bản dài ở `max_len` ký tự khi ghi (LLD 4.1)."""
    return value if len(value) <= max_len else value[:max_len]


def require_text(name: str, value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidArgument(f"{name} is required")
    return clip_text(text)


def choice(name: str, value: str, allowed: Collection[str]) -> str:
    if value not in allowed:
        raise InvalidArgument(f"{name} must be one of: {', '.join(allowed)}")
    return value


def page_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidArgument(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def parse_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidArgument(f"{name} must be YYYY-MM-DD") from None


def encode_cursor(key: list[Any]) -> str:
    raw = json.dumps(key, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    """Cursor là khóa keyset mã hóa base64url; mọi cursor hỏng là `invalid_argument`."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        key = json.loads(raw)
    except (binascii.Error, ValueError):
        raise InvalidArgument("invalid cursor") from None
    if not isinstance(key, list):
        raise InvalidArgument("invalid cursor")
    return key
