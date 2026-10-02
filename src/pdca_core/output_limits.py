"""Giới hạn kích thước đầu ra tool (NFR-SEC-05, LLD 4.1)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pdca_core.errors import Internal

TRUNCATED_KEY = "truncated"
_ELLIPSIS = "…"


@dataclass(frozen=True, slots=True)
class OutputLimits:
    max_rows: int = 100
    max_bytes: int = 50 * 1024
    max_text: int = 2000


def limit_output(data: Mapping[str, Any], limits: OutputLimits) -> dict[str, Any]:
    """Cắt danh sách, chuỗi dài và tổng kích thước; đánh dấu `truncated: true` nếu có cắt."""
    state = {"cut": False}
    out: dict[str, Any] = _clip(dict(data), limits, state)
    if state["cut"]:
        out[TRUNCATED_KEY] = True
    # Đo sau khi đã có cờ `truncated` để tổng kích thước thật không vượt giới hạn.
    while _size(out) > limits.max_bytes:
        longest = _longest_list(out)
        if longest is None:
            # Không còn danh sách để bớt: lỗi thiết kế tool, không trả dữ liệu dở.
            raise Internal()
        longest.pop()
        out[TRUNCATED_KEY] = True
    return out


def _clip(value: Any, limits: OutputLimits, state: dict[str, bool]) -> Any:
    if isinstance(value, str):
        if len(value) > limits.max_text:
            state["cut"] = True
            return value[: limits.max_text - 1] + _ELLIPSIS
        return value
    if isinstance(value, Mapping):
        return {k: _clip(v, limits, state) for k, v in value.items()}
    if isinstance(value, list | tuple):
        if len(value) > limits.max_rows:
            state["cut"] = True
            value = value[: limits.max_rows]
        return [_clip(v, limits, state) for v in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _longest_list(value: Any) -> list[Any] | None:
    best: list[Any] | None = None
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            if current and (best is None or _size(current) > _size(best)):
                best = current
            stack.extend(current)
    return best
