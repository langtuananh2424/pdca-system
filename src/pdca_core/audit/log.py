"""Bản ghi audit và rút gọn tham số (FR-AUD-01, SDD 4.5.2)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ActorKind(StrEnum):
    USER_MCP = "user_mcp"
    AGENT = "agent"
    ADMIN_CLI = "admin_cli"
    SYSTEM = "system"


class AuditResult(StrEnum):
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    request_id: str
    user_id: int | None
    actor_kind: ActorKind
    tool: str
    params_redacted: dict[str, Any]
    result: AuditResult
    error_code: str | None = None


class AuditWriter(Protocol):
    """Chỉ thêm (FR-AUD-02): không có thao tác sửa/xóa."""

    def write(self, entry: AuditEntry) -> None: ...


# Tham số dạng chuỗi được giữ nguyên: định danh, liệt kê, ngày. Mọi chuỗi khác
# (nội dung người dùng: done, blockers, summary, note...) chỉ giữ độ dài.
_VERBATIM_KEY = re.compile(r"(^|_)(id|ids|date|status|mode|level|kind|severity|cursor)$")


def redact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Bỏ nội dung văn bản, giữ định danh và độ dài (SDD 4.5.2)."""
    return {key: _redact_value(key, value) for key, value in params.items()}


def _redact_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if _VERBATIM_KEY.search(key) else {"len": len(value)}
    if isinstance(value, list | tuple):
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return list(value)
        return {"count": len(value)}
    if isinstance(value, Mapping):
        return redact_params(value)
    return {"type": type(value).__name__}
