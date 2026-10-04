"""Tool Plan/giao việc bước 7 qua HTTP thật: list_plans, get_plan, create_plan,
update_plan, create_task, assign_task (FR-PLAN-01..06, FR-TASK-01..02, FR-TASK-05)."""

from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from tests.mcp_helpers import Person, call_tool, person, task

pytestmark = pytest.mark.integration

A, B = "Dự án thử nghiệm A", "Dự án thử nghiệm B"


async def _ok(url: str, who: Person, tool: str, args: dict[str, Any]) -> Any:
    is_error, data = await call_tool(url, who.token, tool, args)
    assert not is_error, data
    return data


async def _err(url: str, who: Person, tool: str, args: dict[str, Any]) -> str:
    is_error, text = await call_tool(url, who.token, tool, args)
    assert is_error, text
    return str(text)


def _month(project_id: int | None = None, **extra: Any) -> dict[str, Any]:
    args = {
        "level": "month",
        "goal": "Hoàn thành bản demo",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        **extra,
    }
    if project_id is not None:
        args["project_id"] = project_id
    return args


async def test_plan_tree_rules(mcp_url: str, app_pool: ConnectionPool) -> None:
    head = person(app_pool, A, role="dept_head")
    month = await _ok(mcp_url, head, "create_plan", _month(head.project_id))
    assert (month["level"], month["version"], month["owner_id"]) == ("month", 1, head.user_id)

    week = await _ok(
        mcp_url,
        head,
        "create_plan",
        {
            "level": "week",
            "goal": "Tuần 1",
            "start_date": "2026-10-05",
            "end_date": "2026-10-11",
            "project_id": head.project_id,
            "parent_id": month["id"],
        },
    )
    assert week["parent_id"] == month["id"]

    bad_cases: list[tuple[dict[str, Any], str]] = [
        ({"level": "day"}, "must have level week"),
        ({"start_date": "2026-09-28"}, "within the parent"),
        ({"end_date": "2026-11-02"}, "within the parent"),
        ({"project_id": None}, "parent's project"),
    ]
    for override, expected in bad_cases:
        args = {
            "level": "week",
            "goal": "x",
            "start_date": "2026-10-05",
            "end_date": "2026-10-11",
            "project_id": head.project_id,
            "parent_id": month["id"],
            **override,
        }
        args = {k: v for k, v in args.items() if v is not None}
        assert expected in await _err(mcp_url, head, "create_plan", args), override

    assert "end_date must not be before" in await _err(
        mcp_url, head, "create_plan", _month(head.project_id, end_date="2026-09-01")
    )
    assert "goal is required" in await _err(
        mcp_url, head, "create_plan", _month(head.project_id, goal="  ")
    )

    detail = await _ok(mcp_url, head, "get_plan", {"plan_id": month["id"]})
    assert [c["id"] for c in detail["children"]] == [week["id"]]


async def test_update_plan_versioning(mcp_url: str, app_pool: ConnectionPool) -> None:
    head = person(app_pool, A, role="dept_head")
    plan = await _ok(mcp_url, head, "create_plan", _month(head.project_id))
    await _ok(
        mcp_url,
        head,
        "create_plan",
        {
            "level": "week",
            "goal": "Tuần cuối",
            "start_date": "2026-10-26",
            "end_date": "2026-10-31",
            "project_id": head.project_id,
            "parent_id": plan["id"],
        },
    )

    updated = await _ok(
        mcp_url,
        head,
        "update_plan",
        {
            "plan_id": plan["id"],
            "expected_version": 1,
            "goal": "Demo + tài liệu",
            "reason": "Thêm tài liệu theo yêu cầu khách",
        },
    )
    assert (updated["version"], updated["goal"]) == (2, "Demo + tài liệu")

    # Khóa lạc quan: version cũ → conflict, không đổi gì (FR-PLAN-06, SDD 4.6).
    stale = await _err(
        mcp_url,
        head,
        "update_plan",
        {"plan_id": plan["id"], "expected_version": 1, "goal": "ghi đè", "reason": "x"},
    )
    assert stale.startswith("conflict") and "current version 2" in stale

    assert "child plan(s) would fall outside" in await _err(
        mcp_url,
        head,
        "update_plan",
        {"plan_id": plan["id"], "expected_version": 2, "end_date": "2026-10-20", "reason": "rút"},
    )
    assert "reason is required" in await _err(
        mcp_url,
        head,
        "update_plan",
        {"plan_id": plan["id"], "expected_version": 2, "goal": "y", "reason": " "},
    )
    assert "nothing to update" in await _err(
        mcp_url, head, "update_plan", {"plan_id": plan["id"], "expected_version": 2, "reason": "x"}
    )

    done = await _ok(
        mcp_url,
        head,
        "update_plan",
        {"plan_id": plan["id"], "expected_version": 2, "status": "done", "reason": "Xong"},
    )
    assert (done["status"], done["version"]) == ("done", 3)
    assert (
        await _err(
            mcp_url,
            head,
            "update_plan",
            {"plan_id": plan["id"], "expected_version": 3, "goal": "z", "reason": "x"},
        )
    ).startswith("conflict: plan is done")

    with app_pool.connection() as conn:
        versions = conn.execute(
            "select version, changed_by, reason, snapshot->>'goal', snapshot->>'status'"
            " from plan_versions where plan_id = %s order by version",
            (plan["id"],),
        ).fetchall()
    assert versions == [
        (1, head.user_id, "created", "Hoàn thành bản demo", "active"),
        (2, head.user_id, "Thêm tài liệu theo yêu cầu khách", "Demo + tài liệu", "active"),
        (3, head.user_id, "Xong", "Demo + tài liệu", "done"),
    ]


async def test_plan_visibility_and_ownership(mcp_url: str, app_pool: ConnectionPool) -> None:
    head_a = person(app_pool, A, role="dept_head")
    staff_a = person(app_pool, A)
    staff_b = person(app_pool, B)
    director = person(app_pool, A, role="director")
    with app_pool.connection() as conn:  # giám đốc không thuộc phòng nào (như seed)
        conn.execute("update users set department_id = null where id = %s", (director.user_id,))

    project_plan = await _ok(mcp_url, head_a, "create_plan", _month(head_a.project_id))
    dept_plan = await _ok(mcp_url, head_a, "create_plan", _month(goal="Kế hoạch phòng"))
    company = await _ok(
        mcp_url,
        director,
        "create_plan",
        {"level": "year", "goal": "Năm 2027", "start_date": "2027-01-01", "end_date": "2027-12-31"},
    )
    own = await _ok(mcp_url, staff_a, "create_plan", _month(goal="Kế hoạch cá nhân"))

    async def visible(who: Person) -> set[int]:
        data = await _ok(mcp_url, who, "list_plans", {"limit": 100, "from_date": "2026-10-01"})
        return {p["id"] for p in data["items"]}

    ids = {project_plan["id"], dept_plan["id"], company["id"], own["id"]}
    assert (await visible(staff_a)) & ids == {project_plan["id"], own["id"]}
    assert (await visible(staff_b)) & ids == set()
    assert (await visible(head_a)) & ids == {project_plan["id"], dept_plan["id"], own["id"]}
    assert (await visible(director)) & ids == ids

    # Không xem được → cùng một lỗi với "không tồn tại".
    for plan_id in (dept_plan["id"], 999_999_999):
        assert await _err(mcp_url, staff_b, "get_plan", {"plan_id": plan_id}) == (
            "forbidden_or_not_found"
        )

    # Staff chỉ sửa kế hoạch của mình (plan.write "của mình").
    upd = {"expected_version": 1, "goal": "sửa", "reason": "thử"}
    assert await _err(mcp_url, staff_a, "update_plan", {"plan_id": project_plan["id"], **upd}) == (
        "forbidden_or_not_found"
    )
    await _ok(mcp_url, staff_a, "update_plan", {"plan_id": own["id"], **upd})

    # Trưởng phòng không gắn được kế hoạch con vào kế hoạch cấp công ty (không đọc được cha).
    child = {
        "level": "month",
        "goal": "Tháng 1",
        "start_date": "2027-01-01",
        "end_date": "2027-01-31",
        "parent_id": company["id"],
    }
    assert await _err(mcp_url, head_a, "create_plan", child) == "forbidden_or_not_found"
    await _ok(mcp_url, director, "create_plan", child)

    # Staff không tạo kế hoạch trong project không tham gia.
    assert await _err(mcp_url, staff_b, "create_plan", _month(head_a.project_id)) == (
        "forbidden_or_not_found"
    )


async def test_list_plans_pages_and_filters(mcp_url: str, app_pool: ConnectionPool) -> None:
    me = person(app_pool, A, role="staff")
    made = []
    for day in ("03", "01", "02"):
        made.append(
            await _ok(
                mcp_url,
                me,
                "create_plan",
                {
                    "level": "day",
                    "goal": f"Ngày {day}",
                    "start_date": f"2030-01-{day}",
                    "end_date": f"2030-01-{day}",
                },
            )
        )
    args = {"level": "day", "from_date": "2030-01-01", "to_date": "2030-01-31", "limit": 2}
    page1 = await _ok(mcp_url, me, "list_plans", args)
    page2 = await _ok(mcp_url, me, "list_plans", {**args, "cursor": page1["next_cursor"]})
    goals = [p["goal"] for p in page1["items"] + page2["items"] if p["owner_id"] == me.user_id]
    assert goals == ["Ngày 01", "Ngày 02", "Ngày 03"]
    assert "level must be one of" in await _err(mcp_url, me, "list_plans", {"level": "quarter"})


async def test_create_and_assign_tasks(mcp_url: str, app_pool: ConnectionPool) -> None:
    head = person(app_pool, A, role="dept_head")
    staff = person(app_pool, A)
    peer = person(app_pool, A)
    outsider = person(app_pool, B)
    plan = await _ok(mcp_url, head, "create_plan", _month(head.project_id))

    created = await _ok(
        mcp_url,
        head,
        "create_task",
        {
            "project_id": head.project_id,
            "title": "Viết tài liệu API",
            "assignee_id": staff.user_id,
            "plan_id": plan["id"],
            "due_date": "2026-10-15",
            "detail": "Theo mẫu OpenAPI",
        },
    )
    task_id = created["task_id"]
    assert created["status"] == "todo"

    # Người nhận thấy task trong get_my_tasks (FR-TASK-03).
    mine = await _ok(mcp_url, staff, "get_my_tasks", {})
    assert task_id in [t["id"] for t in mine["items"]]

    invalid = [
        ({"assignee_id": outsider.user_id}, "assignee must be an active member"),
        ({"assignee_id": 999_999_999}, "assignee must be an active member"),
        ({"due_date": "2026-11-15", "plan_id": plan["id"]}, "within the plan dates"),
        ({"title": " "}, "title is required"),
    ]
    for override, expected in invalid:
        args = {"project_id": head.project_id, "title": "x", "assignee_id": staff.user_id}
        assert expected in await _err(mcp_url, head, "create_task", {**args, **override})

    # Trưởng phòng A không giao việc ở project của phòng B; staff không giao việc.
    foreign = {"project_id": outsider.project_id, "title": "x", "assignee_id": outsider.user_id}
    assert await _err(mcp_url, head, "create_task", foreign) == "forbidden_or_not_found"
    own_project = {"project_id": staff.project_id, "title": "x", "assignee_id": peer.user_id}
    assert await _err(mcp_url, staff, "create_task", own_project) == "forbidden_or_not_found"

    moved = await _ok(
        mcp_url, head, "assign_task", {"task_id": task_id, "assignee_id": peer.user_id}
    )
    assert moved == {
        "task_id": task_id,
        "previous_assignee_id": staff.user_id,
        "assignee_id": peer.user_id,
        "changed": True,
    }
    same = await _ok(
        mcp_url, head, "assign_task", {"task_id": task_id, "assignee_id": peer.user_id}
    )
    assert same["changed"] is False
    assert "active member" in await _err(
        mcp_url, head, "assign_task", {"task_id": task_id, "assignee_id": outsider.user_id}
    )
    assert (
        await _err(
            mcp_url, staff, "assign_task", {"task_id": task_id, "assignee_id": staff.user_id}
        )
        == "forbidden_or_not_found"
    )

    with app_pool.connection() as conn:
        events = conn.execute(
            "select from_status, to_status, by_user_id, note from task_events"
            " where task_id = %s order by id",
            (task_id,),
        ).fetchall()
    assert events == [
        (None, "todo", head.user_id, "created"),
        ("todo", "todo", head.user_id, f"reassigned: {staff.user_id} -> {peer.user_id}"),
    ]

    done_task = task(app_pool, peer, "đã xong", None, "done")
    assert (
        await _err(
            mcp_url, head, "assign_task", {"task_id": done_task, "assignee_id": staff.user_id}
        )
    ).startswith("conflict")


async def test_get_plan_task_visibility(mcp_url: str, app_pool: ConnectionPool) -> None:
    head = person(app_pool, A, role="dept_head")
    staff = person(app_pool, A)
    peer = person(app_pool, A)
    plan = await _ok(mcp_url, head, "create_plan", _month(head.project_id))
    for who in (staff, peer):
        await _ok(
            mcp_url,
            head,
            "create_task",
            {
                "project_id": head.project_id,
                "title": f"việc {who.user_id}",
                "assignee_id": who.user_id,
                "plan_id": plan["id"],
            },
        )

    as_head = await _ok(mcp_url, head, "get_plan", {"plan_id": plan["id"]})
    as_staff = await _ok(mcp_url, staff, "get_plan", {"plan_id": plan["id"]})
    assert {t["assignee_id"] for t in as_head["tasks"]} == {staff.user_id, peer.user_id}
    assert {t["assignee_id"] for t in as_staff["tasks"]} == {staff.user_id}
    assert as_staff["task_counts"] == {"todo": 2}
