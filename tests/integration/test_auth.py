"""Token, audit và `run_tool` trên DB thật với vai trò pdca_app (LLD 3.1, FR-AUTH, FR-AUD)."""

import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from apps.admin_cli.__main__ import main as admin_main
from pdca_core.authz.context import Role, UserContext
from pdca_core.authz.tokens import authenticate, generate_token, issue_token, revoke_token
from pdca_core.errors import ForbiddenOrNotFound, InvalidArgument, Unauthorized
from pdca_core.repositories.audit import PgAuditRepository
from pdca_core.repositories.tokens import PgTokenRepository
from pdca_core.tool_runner import run_tool

from .conftest import Database

pytestmark = pytest.mark.integration


def _new_user(pool: ConnectionPool, role: str = "staff", email: str | None = None) -> int:
    """Người dùng riêng cho từng test, thuộc phòng/project thử nghiệm A của seed."""
    email = email or f"{uuid.uuid4().hex[:12]}@test.invalid"
    with pool.connection() as conn:
        row = conn.execute(
            """
            insert into users (name, email, role, department_id)
            select 'Test', %s, %s, id from departments where name = 'Phòng Thử nghiệm A'
            returning id
            """,
            (email, role),
        ).fetchone()
        assert row is not None
        conn.execute(
            """
            insert into project_members (project_id, user_id)
            select id, %s from projects where name = 'Dự án thử nghiệm A'
            """,
            (row[0],),
        )
    return int(row[0])


def _project_a(pool: ConnectionPool) -> int:
    with pool.connection() as conn:
        row = conn.execute("select id from projects where name = 'Dự án thử nghiệm A'").fetchone()
    assert row is not None
    return int(row[0])


def test_issue_and_authenticate(app_pool: ConnectionPool) -> None:
    repo = PgTokenRepository(app_pool)
    user_id = _new_user(app_pool)

    issued = issue_token(repo, user_id, "laptop")
    ctx = authenticate(issued.token, repo, "req-1")

    assert ctx.user_id == user_id
    assert ctx.role is Role.STAFF
    assert ctx.project_ids == frozenset({_project_a(app_pool)})
    with app_pool.connection() as conn:
        row = conn.execute(
            "select token_hash, label, last_used_at is not null,"
            " expires_at - created_at from api_tokens where id = %s",
            (issued.token_id,),
        ).fetchone()
    assert row is not None
    stored_hash, label, touched, ttl = row
    assert stored_hash != issued.token.encode()  # chỉ lưu băm (FR-AUTH-03)
    assert len(stored_hash) == 32
    assert (label, touched) == ("laptop", True)
    assert timedelta(days=89, hours=23) < ttl <= timedelta(days=90)


def test_unknown_token_is_unauthorized(app_pool: ConnectionPool) -> None:
    with pytest.raises(Unauthorized):
        authenticate(generate_token(), PgTokenRepository(app_pool), "r")


def test_revoked_token_is_unauthorized(app_pool: ConnectionPool) -> None:
    repo = PgTokenRepository(app_pool)
    issued = issue_token(repo, _new_user(app_pool))
    revoke_token(repo, issued.token_id)
    with pytest.raises(Unauthorized):
        authenticate(issued.token, repo, "r")
    with pytest.raises(ForbiddenOrNotFound):
        revoke_token(repo, issued.token_id)  # thu hồi lần hai


def test_expired_token_is_unauthorized(app_pool: ConnectionPool) -> None:
    repo = PgTokenRepository(app_pool)
    issued = issue_token(repo, _new_user(app_pool))
    with app_pool.connection() as conn:
        conn.execute(
            "update api_tokens set expires_at = now() - interval '1 second' where id = %s",
            (issued.token_id,),
        )
    with pytest.raises(Unauthorized):
        authenticate(issued.token, repo, "r")


@pytest.mark.parametrize(
    "change", ["status = 'locked'", "deleted_at = now()"], ids=["locked", "deleted"]
)
def test_inactive_user_is_unauthorized(app_pool: ConnectionPool, change: str) -> None:
    """FR-AUTH-04: tài khoản khóa thì token cũ hết tác dụng ngay."""
    repo = PgTokenRepository(app_pool)
    user_id = _new_user(app_pool)
    issued = issue_token(repo, user_id)
    with app_pool.connection() as conn:
        conn.execute(f"update users set {change} where id = %s", (user_id,))  # noqa: S608
    with pytest.raises(Unauthorized):
        authenticate(issued.token, repo, "r")
    with pytest.raises(InvalidArgument):
        issue_token(repo, user_id)


def test_run_tool_writes_audit_rows(app_pool: ConnectionPool) -> None:
    """T-08: mọi lời gọi (kể cả token sai) đều có bản ghi audit_log."""
    tokens = PgTokenRepository(app_pool)
    audit = PgAuditRepository(app_pool)
    user_id = _new_user(app_pool)
    good = issue_token(tokens, user_id).token

    def whoami(ctx: UserContext) -> Mapping[str, Any]:
        return {"user_id": ctx.user_id}

    rid_ok, rid_bad = uuid.uuid4().hex, uuid.uuid4().hex
    out = run_tool(
        "whoami",
        {"note": "nội dung riêng tư"},
        whoami,
        authenticate=lambda rid: authenticate(good, tokens, rid),
        audit=audit,
        request_id=rid_ok,
    )
    assert out == {"user_id": user_id}
    with pytest.raises(Unauthorized):
        run_tool(
            "whoami",
            {},
            whoami,
            authenticate=lambda rid: authenticate("pdca_forged", tokens, rid),
            audit=audit,
            request_id=rid_bad,
        )

    with app_pool.connection() as conn:
        rows = conn.execute(
            "select request_id, user_id, actor_kind, result, error_code, params_redacted"
            " from audit_log where request_id in (%s, %s) order by id",
            (rid_ok, rid_bad),
        ).fetchall()
    assert rows == [
        (rid_ok, user_id, "user_mcp", "ok", None, {"note": {"len": 17}}),
        (rid_bad, None, "user_mcp", "denied", "unauthorized", {}),
    ]


def test_admin_cli_issue_and_revoke(
    db: Database,
    app_pool: ConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    email = f"{uuid.uuid4().hex[:12]}@test.invalid"
    user_id = _new_user(app_pool, email=email)
    monkeypatch.setenv("DATABASE_URL", db.conninfo("pdca_app"))

    assert admin_main(["token", "issue", "--email", email.upper(), "--days", "7"]) == 0
    printed = dict(
        line.split(":", 1) for line in capsys.readouterr().out.splitlines() if ":" in line
    )
    token = printed["token"].strip()
    token_id = int(printed["token_id"])
    assert authenticate(token, PgTokenRepository(app_pool), "r").user_id == user_id

    assert admin_main(["token", "revoke", "--id", str(token_id)]) == 0
    with pytest.raises(Unauthorized):
        authenticate(token, PgTokenRepository(app_pool), "r")

    assert admin_main(["token", "issue", "--email", "nobody@example.com"]) == 1

    with app_pool.connection() as conn:
        rows = conn.execute(
            "select tool, result, error_code, params_redacted from audit_log"
            " where actor_kind = 'admin_cli' order by id desc limit 3"
        ).fetchall()
    assert [r[:3] for r in rows] == [
        ("admin.token.issue", "error", "invalid_argument"),
        ("admin.token.revoke", "ok", None),
        ("admin.token.issue", "ok", None),
    ]
    assert rows[2][3]["email"] == {"len": len(email)}  # email không lưu nguyên văn
