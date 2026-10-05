"""list_project_tasks, list_project_members qua HTTP thật (FR-TASK-02, FR-ORG-03, UC-10).

Hai tool để trưởng phòng tra `task_id`, `assignee_id` từ tên/tiêu đề thay vì nhớ id
giữa các phiên: chỉ trả dữ liệu trong phạm vi quyền (`task.read.team`, thành viên project).
"""

import uuid
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from tests.mcp_helpers import Person, audit_rows, call_tool, person, task

pytestmark = pytest.mark.integration

DEPT_A, DEPT_B = "Phòng Thử nghiệm A", "Phòng Thử nghiệm B"


def _project(pool: ConnectionPool, department: str) -> str:
    name = f"List {uuid.uuid4().hex[:8]}"
    with pool.connection() as conn:
        conn.execute(
            "insert into projects (name, department_id)"
            " select %s, id from departments where name = %s",
            (name, department),
        )
    return name


async def _ok(url: str, who: Person, tool: str, args: dict[str, Any]) -> Any:
    is_error, data = await call_tool(url, who.token, tool, args)
    assert not is_error, data
    return data


async def _denied(url: str, who: Person, tool: str, args: dict[str, Any]) -> None:
    assert await call_tool(url, who.token, tool, args) == (True, "forbidden_or_not_found")


@pytest.fixture
def squad(app_pool: ConnectionPool) -> dict[str, Any]:
    """Project mới ở phòng A: trưởng phòng, hai nhân viên và task đủ trạng thái."""
    name = _project(app_pool, "Phòng Thử nghiệm A")
    head = person(app_pool, name, role="dept_head")
    first, second = person(app_pool, name), person(app_pool, name)
    return {
        "name": name,
        "head": head,
        "first": first,
        "second": second,
        "project_id": head.project_id,
        "t_late": task(app_pool, first, "Việc gấp", "2026-10-06"),
        "t_later": task(app_pool, second, "Việc sau", "2026-10-20"),
        "t_open": task(app_pool, first, "Việc không hạn", None),
        "t_done": task(app_pool, second, "Việc xong", "2026-10-01", status="done"),
    }


async def test_head_lists_open_tasks_of_everyone(mcp_url: str, squad: dict[str, Any]) -> None:
    data = await _ok(
        mcp_url, squad["head"], "list_project_tasks", {"project_id": squad["project_id"]}
    )
    items = data["items"]
    # Mặc định chỉ trạng thái mở; theo hạn tăng dần, không hạn xếp cuối.
    assert [t["id"] for t in items] == [squad["t_late"], squad["t_later"], squad["t_open"]]
    assert items[0]["assignee_id"] == squad["first"].user_id
    assert items[0]["assignee_name"] == "MCP test"
    assert set(items[0]) == {
        "id",
        "title",
        "assignee_id",
        "assignee_name",
        "plan_id",
        "due_date",
        "status",
        "updated_at",
    }
    assert data["next_cursor"] is None


async def test_filters_by_status_and_assignee(mcp_url: str, squad: dict[str, Any]) -> None:
    base = {"project_id": squad["project_id"]}
    done = await _ok(mcp_url, squad["head"], "list_project_tasks", {**base, "status": "done"})
    assert [t["id"] for t in done["items"]] == [squad["t_done"]]

    mine = await _ok(
        mcp_url,
        squad["head"],
        "list_project_tasks",
        {**base, "assignee_id": squad["first"].user_id},
    )
    assert [t["id"] for t in mine["items"]] == [squad["t_late"], squad["t_open"]]

    bad = await call_tool(
        mcp_url, squad["head"].token, "list_project_tasks", {**base, "status": "archived"}
    )
    assert bad[0] is True
    assert str(bad[1]).startswith("invalid_argument")


async def test_pagination_keyset(mcp_url: str, squad: dict[str, Any]) -> None:
    args = {"project_id": squad["project_id"], "limit": 2}
    page1 = await _ok(mcp_url, squad["head"], "list_project_tasks", args)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    page2 = await _ok(
        mcp_url, squad["head"], "list_project_tasks", {**args, "cursor": page1["next_cursor"]}
    )
    ids = [t["id"] for t in page1["items"] + page2["items"]]
    assert ids == [squad["t_late"], squad["t_later"], squad["t_open"]]
    assert page2["next_cursor"] is None


async def test_list_project_tasks_denied_outside_scope(
    mcp_url: str, app_pool: ConnectionPool, squad: dict[str, Any]
) -> None:
    args = {"project_id": squad["project_id"]}
    # Nhân viên cùng project: không có task.read.team (chỉ xem task của mình).
    await _denied(mcp_url, squad["first"], "list_project_tasks", args)
    # Trưởng phòng khác phòng (T-03 áp dụng cho tool mới).
    other_head = person(app_pool, _project(app_pool, DEPT_B), role="dept_head")
    await _denied(mcp_url, other_head, "list_project_tasks", args)
    # Project không tồn tại: cùng mã lỗi, không lộ "không có" với "không được xem".
    await _denied(mcp_url, squad["head"], "list_project_tasks", {"project_id": 2_000_000_000})

    denied = audit_rows(app_pool, squad["first"].user_id, "list_project_tasks")
    assert denied, "lời gọi bị từ chối phải có bản ghi audit"


async def test_director_lists_tasks_of_any_department(
    mcp_url: str, app_pool: ConnectionPool, squad: dict[str, Any]
) -> None:
    director = person(app_pool, _project(app_pool, DEPT_B), role="director")
    data = await _ok(mcp_url, director, "list_project_tasks", {"project_id": squad["project_id"]})
    assert len(data["items"]) == 3


async def test_members_visible_to_member_and_head_hide_inactive(
    mcp_url: str, app_pool: ConnectionPool, squad: dict[str, Any]
) -> None:
    with app_pool.connection() as conn:
        locked = conn.execute(
            """
            with u as (
              insert into users (name, email, role, department_id, status)
              select 'Đã khóa', %s, 'staff', department_id, 'locked'
              from projects where id = %s returning id)
            insert into project_members (project_id, user_id) select %s, id from u
            returning user_id
            """,
            (f"{uuid.uuid4().hex[:12]}@test.invalid", squad["project_id"], squad["project_id"]),
        ).fetchone()
    assert locked is not None

    for viewer in (squad["first"], squad["head"]):
        data = await _ok(
            mcp_url, viewer, "list_project_members", {"project_id": squad["project_id"]}
        )
        ids = [m["user_id"] for m in data["members"]]
        assert set(ids) == {
            squad["head"].user_id,
            squad["first"].user_id,
            squad["second"].user_id,
        }
        assert int(locked[0]) not in ids
        # Không lộ email hay trạng thái nghỉ phép; trưởng nhóm xếp trước.
        assert set(data["members"][0]) == {"user_id", "name", "role", "project_role"}


async def test_members_denied_for_outsider(
    mcp_url: str, app_pool: ConnectionPool, squad: dict[str, Any]
) -> None:
    outsider = person(app_pool, _project(app_pool, DEPT_B))
    await _denied(mcp_url, outsider, "list_project_members", {"project_id": squad["project_id"]})
    await _denied(mcp_url, squad["first"], "list_project_members", {"project_id": 2_000_000_000})
