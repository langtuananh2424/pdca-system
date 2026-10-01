import logging
from collections.abc import Mapping
from typing import Any

import pytest

from pdca_core.audit.log import ActorKind, AuditEntry, AuditResult
from pdca_core.authz.context import Role, UserContext
from pdca_core.errors import (
    Conflict,
    ForbiddenOrNotFound,
    Internal,
    InvalidArgument,
    RateLimited,
    ToolError,
    Unauthorized,
)
from pdca_core.output_limits import OutputLimits
from pdca_core.ratelimit import RateLimiter
from pdca_core.tool_runner import run_tool


class Audit:
    def __init__(self, fail: bool = False) -> None:
        self.entries: list[AuditEntry] = []
        self.fail = fail

    def write(self, entry: AuditEntry) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.entries.append(entry)


def ctx_for(request_id: str) -> UserContext:
    return UserContext(7, Role.STAFF, 1, frozenset({3}), request_id)


def reject(request_id: str) -> UserContext:
    raise Unauthorized()


def ok_call(ctx: UserContext) -> Mapping[str, Any]:
    return {"user_id": ctx.user_id, "request_id": ctx.request_id}


def test_success_returns_output_and_audits_ok() -> None:
    audit = Audit()
    out = run_tool("whoami", {}, ok_call, authenticate=ctx_for, audit=audit, request_id="req-1")
    assert out == {"user_id": 7, "request_id": "req-1"}
    assert audit.entries == [
        AuditEntry(
            request_id="req-1",
            user_id=7,
            actor_kind=ActorKind.USER_MCP,
            tool="whoami",
            params_redacted={},
            result=AuditResult.OK,
            error_code=None,
        )
    ]


def test_bad_token_is_audited_as_denied_without_user() -> None:
    audit = Audit()
    calls: list[UserContext] = []

    def call(ctx: UserContext) -> Mapping[str, Any]:
        calls.append(ctx)
        return {}

    with pytest.raises(Unauthorized):
        run_tool("get_my_tasks", {"limit": 5}, call, authenticate=reject, audit=audit)
    assert calls == []
    [entry] = audit.entries
    assert (entry.user_id, entry.result, entry.error_code) == (None, "denied", "unauthorized")
    assert entry.params_redacted == {"limit": 5}
    assert len(entry.request_id) == 32


@pytest.mark.parametrize(
    ("exc", "result"),
    [
        (ForbiddenOrNotFound(), AuditResult.DENIED),
        (InvalidArgument("bad date"), AuditResult.ERROR),
        (Conflict("already reported"), AuditResult.ERROR),
    ],
)
def test_tool_errors_are_audited_and_reraised(exc: ToolError, result: AuditResult) -> None:
    audit = Audit()

    def call(ctx: UserContext) -> Mapping[str, Any]:
        raise exc

    with pytest.raises(type(exc)) as raised:
        run_tool("t", {}, call, authenticate=ctx_for, audit=audit)
    assert raised.value is exc
    [entry] = audit.entries
    assert (entry.user_id, entry.result, entry.error_code) == (7, result, exc.code.value)


def test_unexpected_exception_becomes_internal_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    audit = Audit()

    def call(ctx: UserContext) -> Mapping[str, Any]:
        raise ValueError("password=hunter2 in connection string")

    with caplog.at_level(logging.ERROR, logger="pdca.tool"), pytest.raises(Internal) as raised:
        run_tool("t", {}, call, authenticate=ctx_for, audit=audit)
    assert "hunter2" not in raised.value.message
    assert audit.entries[0].error_code == "internal"
    assert any(r.exc_info for r in caplog.records)  # chi tiết chỉ nằm trong log


def test_audit_failure_means_no_result() -> None:
    with pytest.raises(Internal):
        run_tool("whoami", {}, ok_call, authenticate=ctx_for, audit=Audit(fail=True))


def test_rate_limit_is_denied_before_call() -> None:
    audit = Audit()
    limiter = RateLimiter(per_minute=1)
    run_tool("t", {}, ok_call, authenticate=ctx_for, audit=audit, rate_limiter=limiter)
    with pytest.raises(RateLimited):
        run_tool("t", {}, ok_call, authenticate=ctx_for, audit=audit, rate_limiter=limiter)
    assert [e.result for e in audit.entries] == ["ok", "denied"]
    assert audit.entries[1].error_code == "rate_limited"


def test_output_is_limited_and_free_text_params_redacted() -> None:
    audit = Audit()

    def call(ctx: UserContext) -> Mapping[str, Any]:
        return {"items": list(range(10))}

    out = run_tool(
        "submit_report",
        {"project_id": 3, "done": "nội dung báo cáo"},
        call,
        authenticate=ctx_for,
        audit=audit,
        limits=OutputLimits(max_rows=3),
    )
    assert out == {"items": [0, 1, 2], "truncated": True}
    assert audit.entries[0].params_redacted == {"project_id": 3, "done": {"len": 16}}


def test_actor_kind_is_recorded() -> None:
    audit = Audit()
    run_tool("t", {}, ok_call, authenticate=ctx_for, audit=audit, actor_kind=ActorKind.AGENT)
    assert audit.entries[0].actor_kind is ActorKind.AGENT
