"""`run_tool`: lớp bao chung cho mọi lời gọi tool (LLD 4.3, SDD 4.9).

Thứ tự: xác thực → giới hạn tần suất → gọi nghiệp vụ → giới hạn đầu ra →
ghi audit (ok/denied/error) → log. Xác thực nằm bên trong để cả token sai
cũng được audit (FR-AUTH-04, FR-AUD-01). Không phụ thuộc SDK MCP: adapter
chỉ việc cung cấp hàm xác thực và ánh xạ `ToolError` sang lỗi giao thức.
"""

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from pdca_core.audit.log import ActorKind, AuditEntry, AuditResult, AuditWriter, redact_params
from pdca_core.authz.context import UserContext
from pdca_core.errors import ErrorCode, Internal, RateLimited, ToolError
from pdca_core.output_limits import OutputLimits, limit_output
from pdca_core.ratelimit import RateLimiter

logger = logging.getLogger("pdca.tool")

_DENIED_CODES = frozenset(
    {ErrorCode.UNAUTHORIZED, ErrorCode.FORBIDDEN_OR_NOT_FOUND, ErrorCode.RATE_LIMITED}
)

Authenticator = Callable[[str], UserContext]
"""Nhận request_id, trả `UserContext` hoặc ném `Unauthorized`."""


def new_request_id() -> str:
    return uuid.uuid4().hex


def run_tool(
    tool: str,
    params: Mapping[str, Any],
    call: Callable[[UserContext], Mapping[str, Any]],
    *,
    authenticate: Authenticator,
    audit: AuditWriter,
    actor_kind: ActorKind = ActorKind.USER_MCP,
    limits: OutputLimits | None = None,
    rate_limiter: RateLimiter | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    request_id = request_id or new_request_id()
    started = time.perf_counter()
    user_id: int | None = None
    output: dict[str, Any] | None = None
    error: ToolError | None = None

    try:
        ctx = authenticate(request_id)
        user_id = ctx.user_id
        if rate_limiter is not None and not rate_limiter.allow(ctx.user_id):
            raise RateLimited()
        output = limit_output(call(ctx), limits or OutputLimits())
    except ToolError as exc:
        error = exc
    except Exception:
        logger.exception("tool failed", extra={"request_id": request_id, "tool": tool})
        error = Internal()

    if error is None:
        result = AuditResult.OK
    elif error.code in _DENIED_CODES:
        result = AuditResult.DENIED
    else:
        result = AuditResult.ERROR

    entry = AuditEntry(
        request_id=request_id,
        user_id=user_id,
        actor_kind=actor_kind,
        tool=tool,
        params_redacted=redact_params(params),
        result=result,
        error_code=error.code.value if error else None,
    )
    try:
        audit.write(entry)
    except Exception:
        # Không ghi được audit thì không trả kết quả (LLD 3.3 mục 4).
        logger.exception("audit write failed", extra={"request_id": request_id, "tool": tool})
        error, result = Internal(), AuditResult.ERROR

    logger.info(
        "tool call",
        extra={
            "request_id": request_id,
            "user_id": user_id,
            "tool": tool,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "result": result.value,
            "error_code": error.code.value if error else None,
        },
    )
    if error is not None or output is None:
        raise error or Internal()
    return output
