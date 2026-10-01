"""Tool MCP bước 4 qua HTTP thật (uvicorn + Streamable HTTP + Postgres thật).

Bao gồm T-01 (không ép được user_id), T-08 (mọi lời gọi có audit) cho các
tool `whoami`, `get_my_tasks`, `update_task_status`, `log_activity`.
"""

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import uvicorn
from mcp import Client

# create_mcp_http_client định nghĩa ở mô-đun riêng tư mcp.shared._httpx_utils.
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)
from psycopg_pool import ConnectionPool

from apps.mcp_server.__main__ import create_app
from apps.mcp_server.settings import Settings
from pdca_core.authz.tokens import issue_token
from pdca_core.repositories.tokens import PgTokenRepository
from tests.db_fixtures import Database

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Person:
    user_id: int
    token: str
    project_id: int


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def mcp_url(db: Database, app_pool: ConnectionPool) -> Iterator[str]:
    port = _free_port()
    settings = Settings.from_env(
        {
            "DATABASE_URL": db.conninfo("pdca_app"),
            "MCP_PORT": str(port),
            "RATE_LIMIT_PER_MIN": "1000",
        }
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, app_pool), host="127.0.0.1", port=port, log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


def _person(pool: ConnectionPool, project_name: str, role: str = "staff") -> Person:
    with pool.connection() as conn:
        row = conn.execute(
            """
            with p as (select id, department_id from projects where name = %s),
                 u as (
                   insert into users (name, email, role, department_id)
                   select 'MCP test', %s, %s, department_id from p returning id
                 ),
                 m as (insert into project_members (project_id, user_id)
                       select p.id, u.id from p, u)
            select u.id, p.id from u, p
            """,
            (project_name, f"{uuid.uuid4().hex[:12]}@test.invalid", role),
        ).fetchone()
    assert row is not None
    token = issue_token(PgTokenRepository(pool), int(row[0])).token
    return Person(user_id=int(row[0]), token=token, project_id=int(row[1]))


def _task(
    pool: ConnectionPool, who: Person, title: str, due: str | None, status: str = "todo"
) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """
            insert into tasks (project_id, assignee_id, created_by, title, due_date, status)
            values (%s, %s, %s, %s, %s, %s) returning id
            """,
            (who.project_id, who.user_id, who.user_id, title, due, status),
        ).fetchone()
    assert row is not None
    return int(row[0])


async def _call(
    url: str, token: str | None, tool: str, args: dict[str, Any] | None = None
) -> tuple[bool, Any]:
    """Trả (is_error, dữ liệu có cấu trúc hoặc văn bản lỗi)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        result = await client.call_tool(tool, args or {})
    if result.is_error:
        text: str = result.content[0].text  # type: ignore[union-attr]
        # SDK thêm tiền tố "Error executing tool <tên>: " trước thông điệp của ToolError.
        return True, text.removeprefix(f"Error executing tool {tool}: ")
    data = result.structured_content
    if data is None:
        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
    return False, data


def _audit(pool: ConnectionPool, user_id: int | None, tool: str) -> list[tuple[Any, ...]]:
    with pool.connection() as conn:
        return conn.execute(
            "select result, error_code from audit_log"
            " where user_id is not distinct from %s and tool = %s order by id",
            (user_id, tool),
        ).fetchall()


async def test_whoami(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = _person(app_pool, "Dự án thử nghiệm A")
    is_error, data = await _call(mcp_url, me.token, "whoami")
    assert not is_error
    assert data["user_id"] == me.user_id
    assert data["role"] == "staff"
    assert data["department"]["name"] == "Phòng Thử nghiệm A"
    assert data["projects"] == [
        {"id": me.project_id, "name": "Dự án thử nghiệm A", "project_role": "member"}
    ]
    assert _audit(app_pool, me.user_id, "whoami") == [("ok", None)]


@pytest.mark.parametrize(
    "token", [None, "pdca_" + "x" * 43, "garbage"], ids=["missing", "unknown", "malformed"]
)
async def test_bad_token_is_unauthorized_and_audited(
    mcp_url: str, app_pool: ConnectionPool, token: str | None
) -> None:
    before = len(_audit(app_pool, None, "get_my_tasks"))
    is_error, text = await _call(mcp_url, token, "get_my_tasks")
    assert (is_error, text) == (True, "unauthorized")
    assert _audit(app_pool, None, "get_my_tasks")[before:] == [("denied", "unauthorized")]


async def test_get_my_tasks_pages_and_filters(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = _person(app_pool, "Dự án thử nghiệm A")
    t_late = _task(app_pool, me, "không hạn", None)
    t1 = _task(app_pool, me, "sớm", "2026-10-02")
    t2 = _task(app_pool, me, "sau", "2026-10-05", "in_progress")
    t3 = _task(app_pool, me, "cùng ngày", "2026-10-05", "blocked")
    _task(app_pool, me, "xong", "2026-10-01", "done")

    _, page1 = await _call(mcp_url, me.token, "get_my_tasks", {"limit": 2})
    assert [t["id"] for t in page1["items"]] == [t1, t2]
    assert page1["items"][0] == {
        "id": t1,
        "title": "sớm",
        "project_id": me.project_id,
        "plan_id": None,
        "due_date": "2026-10-02",
        "status": "todo",
    }
    _, page2 = await _call(
        mcp_url, me.token, "get_my_tasks", {"limit": 2, "cursor": page1["next_cursor"]}
    )
    assert [t["id"] for t in page2["items"]] == [t3, t_late]
    assert page2["next_cursor"] is None

    _, done = await _call(mcp_url, me.token, "get_my_tasks", {"status": "done"})
    assert [t["title"] for t in done["items"]] == ["xong"]

    for bad in ({"status": "cancelled"}, {"limit": 0}, {"cursor": "not-a-cursor"}):
        is_error, text = await _call(mcp_url, me.token, "get_my_tasks", bad)
        assert is_error and text.startswith("invalid_argument"), bad


async def test_cannot_read_other_users_tasks(mcp_url: str, app_pool: ConnectionPool) -> None:
    """T-01: tham số lạ kiểu user_id không đổi được người gọi."""
    me = _person(app_pool, "Dự án thử nghiệm A")
    other = _person(app_pool, "Dự án thử nghiệm B")
    _task(app_pool, other, "việc của B", "2026-10-03")

    is_error, data = await _call(
        mcp_url, me.token, "get_my_tasks", {"user_id": other.user_id, "assignee_id": other.user_id}
    )
    if not is_error:
        assert data["items"] == []
    _, data = await _call(mcp_url, me.token, "get_my_tasks", {"project_id": other.project_id})
    assert data["items"] == []


async def test_update_task_status_flow(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = _person(app_pool, "Dự án thử nghiệm A")
    task_id = _task(app_pool, me, "làm báo cáo", "2026-10-03")

    is_error, data = await _call(
        mcp_url,
        me.token,
        "update_task_status",
        {"task_id": task_id, "status": "in_progress", "note": "bắt đầu"},
    )
    assert not is_error
    assert data == {"task_id": task_id, "from_status": "todo", "status": "in_progress"}

    is_error, text = await _call(
        mcp_url, me.token, "update_task_status", {"task_id": task_id, "status": "todo"}
    )
    assert is_error and text.startswith("conflict")

    await _call(mcp_url, me.token, "update_task_status", {"task_id": task_id, "status": "done"})
    with app_pool.connection() as conn:
        events = conn.execute(
            "select from_status, to_status, by_user_id, note from task_events"
            " where task_id = %s order by id",
            (task_id,),
        ).fetchall()
    assert events == [
        ("todo", "in_progress", me.user_id, "bắt đầu"),
        ("in_progress", "done", me.user_id, None),
    ]
    assert [r[0] for r in _audit(app_pool, me.user_id, "update_task_status")] == [
        "ok",
        "error",
        "ok",
    ]


async def test_cannot_update_someone_elses_task(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = _person(app_pool, "Dự án thử nghiệm A")
    peer = _person(app_pool, "Dự án thử nghiệm A")
    task_id = _task(app_pool, peer, "việc của đồng nghiệp", None)

    for target in (task_id, 999_999_999):  # của người khác / không tồn tại: cùng một lỗi
        is_error, text = await _call(
            mcp_url, me.token, "update_task_status", {"task_id": target, "status": "cancelled"}
        )
        assert (is_error, text) == (True, "forbidden_or_not_found")
    with app_pool.connection() as conn:
        row = conn.execute("select status from tasks where id = %s", (task_id,)).fetchone()
    assert row == ("todo",)


async def test_log_activity(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = _person(app_pool, "Dự án thử nghiệm A")
    other = _person(app_pool, "Dự án thử nghiệm B")
    my_task = _task(app_pool, me, "việc A", None)
    other_task = _task(app_pool, other, "việc B", None)

    is_error, data = await _call(
        mcp_url,
        me.token,
        "log_activity",
        {"project_id": me.project_id, "summary": "x" * 2500, "task_id": my_task},
    )
    assert not is_error
    with app_pool.connection() as conn:
        row = conn.execute(
            "select user_id, task_id, char_length(summary), source from activities where id = %s",
            (data["activity_id"],),
        ).fetchone()
    assert row == (me.user_id, my_task, 2000, "user")

    cases = [
        ({"project_id": other.project_id, "summary": "a"}, "forbidden_or_not_found"),
        (
            {"project_id": me.project_id, "summary": "a", "task_id": other_task},
            "forbidden_or_not_found",
        ),
        ({"project_id": me.project_id, "summary": "   "}, "invalid_argument: summary is required"),
    ]
    for args, expected in cases:
        assert await _call(mcp_url, me.token, "log_activity", args) == (True, expected)

    with app_pool.connection() as conn:
        params = conn.execute(
            "select params_redacted from audit_log where user_id = %s and tool = 'log_activity'"
            " order by id limit 1",
            (me.user_id,),
        ).fetchone()
    assert params == ({"project_id": me.project_id, "summary": {"len": 2500}, "task_id": my_task},)


async def test_admin_has_no_task_access(mcp_url: str, app_pool: ConnectionPool) -> None:
    admin = _person(app_pool, "Dự án thử nghiệm A", role="admin")
    assert await _call(mcp_url, admin.token, "get_my_tasks") == (True, "forbidden_or_not_found")
