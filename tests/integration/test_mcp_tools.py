"""Tool MCP bước 4 qua HTTP thật (uvicorn + Streamable HTTP + Postgres thật).

Bao gồm T-01 (không ép được user_id), T-08 (mọi lời gọi có audit) cho các
tool `whoami`, `get_my_tasks`, `update_task_status`, `log_activity`.
"""

import pytest
from psycopg_pool import ConnectionPool

from tests.mcp_helpers import audit_rows as _audit
from tests.mcp_helpers import call_tool as _call
from tests.mcp_helpers import person as _person
from tests.mcp_helpers import task as _task

pytestmark = pytest.mark.integration


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
