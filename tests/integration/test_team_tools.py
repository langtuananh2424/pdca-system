"""get_project_status, get_team_blockers qua HTTP thật (UC-06, FR-NTF-06).

T-02 (nhân viên gọi get_team_blockers → từ chối), T-03 (trưởng phòng A xem
project phòng B → từ chối), T-07 (chỉ báo cáo `submitted` đi lên).
"""

import uuid
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from tests.mcp_helpers import Person, call_tool, person

pytestmark = pytest.mark.integration

DEPT_A, DEPT_B = "Phòng Thử nghiệm A", "Phòng Thử nghiệm B"


def _project(pool: ConnectionPool, department: str) -> str:
    name = f"Team {uuid.uuid4().hex[:8]}"
    with pool.connection() as conn:
        conn.execute(
            "insert into projects (name, department_id)"
            " select %s, id from departments where name = %s",
            (name, department),
        )
    return name


def _today(pool: ConnectionPool) -> str:
    with pool.connection() as conn:
        row = conn.execute("select (now() at time zone 'Asia/Bangkok')::date::text").fetchone()
    assert row is not None
    return str(row[0])


async def _ok(url: str, who: Person, tool: str, args: dict[str, Any]) -> Any:
    is_error, data = await call_tool(url, who.token, tool, args)
    assert not is_error, data
    return data


async def _denied(url: str, who: Person, tool: str, args: dict[str, Any]) -> None:
    assert await call_tool(url, who.token, tool, args) == (True, "forbidden_or_not_found")


@pytest.fixture
async def team(mcp_url: str, app_pool: ConnectionPool) -> dict[str, Any]:
    """Project mới ở phòng A: trưởng phòng + 4 nhân viên với các tình huống báo cáo."""
    name = _project(app_pool, DEPT_A)
    head = person(app_pool, name, role="dept_head")
    reported, drafted, away, silent = (person(app_pool, name) for _ in range(4))

    await _ok(
        mcp_url,
        reported,
        "submit_report",
        {
            "project_id": reported.project_id,
            "done": "Xong API",
            "blocker_items": [
                {"kind": "technical", "severity": "high", "text": "CI hỏng"},
                {"kind": "people", "severity": "low", "text": "Chờ review"},
            ],
        },
    )
    with app_pool.connection() as conn:
        row = conn.execute(
            """
            insert into reports (user_id, project_id, report_date, done, source, status)
            values (%s, %s, (now() at time zone 'Asia/Bangkok')::date, 'AI đoán',
                    'channel_reply', 'draft_by_agent')
            returning id
            """,
            (drafted.user_id, drafted.project_id),
        ).fetchone()
        assert row is not None
        conn.execute(
            "insert into blockers (report_id, kind, severity, text)"
            " values (%s, 'other', 'high', 'nháp AI chưa duyệt')",
            (row[0],),
        )
        conn.execute(
            "update users set away_until = current_date + 3 where id = %s", (away.user_id,)
        )
        conn.execute(
            "insert into tasks (project_id, assignee_id, created_by, title, status)"
            " values (%s, %s, %s, 't1', 'todo'), (%s, %s, %s, 't2', 'blocked')",
            (head.project_id, silent.user_id, head.user_id) * 2,
        )
        conn.execute(
            "insert into plans (project_id, level, goal, start_date, end_date, owner_id)"
            " values (%s, 'month', 'g', '2026-10-01', '2026-10-31', %s)",
            (head.project_id, head.user_id),
        )
    return {
        "name": name,
        "head": head,
        "reported": reported,
        "drafted": drafted,
        "away": away,
        "silent": silent,
    }


async def test_project_status_for_head(mcp_url: str, team: dict[str, Any]) -> None:
    head: Person = team["head"]
    data = await _ok(mcp_url, head, "get_project_status", {"project_id": head.project_id})

    assert data["project"]["name"] == team["name"]
    assert data["plans_open_by_level"] == {"month": 1}
    assert data["tasks_by_status"] == {"todo": 1, "blocked": 1}
    today = data["reports_today"]
    assert (today["submitted"], today["not_reported"], today["away"]) == (1, 3, 1)
    assert [m["user_id"] for m in today["submitted_members"]] == [team["reported"].user_id]
    assert {m["user_id"] for m in today["not_reported_members"]} == {
        head.user_id,
        team["drafted"].user_id,  # bản nháp AI không tính là đã báo cáo
        team["silent"].user_id,
    }
    assert [m["user_id"] for m in today["away_members"]] == [team["away"].user_id]
    # Nặng trước; vướng mắc trong bản nháp AI không đi lên (T-07).
    assert [(b["severity"], b["text"]) for b in data["top_blockers"]] == [
        ("high", "CI hỏng"),
        ("low", "Chờ review"),
    ]


async def test_project_status_for_members(mcp_url: str, team: dict[str, Any]) -> None:
    reported: Person = team["reported"]
    silent: Person = team["silent"]
    mine = await _ok(mcp_url, reported, "get_project_status", {"project_id": reported.project_id})
    other = await _ok(mcp_url, silent, "get_project_status", {"project_id": silent.project_id})

    for data in (mine, other):
        assert set(data["reports_today"]) == {"date", "submitted", "not_reported", "away"}
        assert data["reports_today"]["submitted"] == 1
    assert [b["text"] for b in mine["top_blockers"]] == ["CI hỏng", "Chờ review"]
    assert other["top_blockers"] == []  # không thấy vướng mắc của đồng nghiệp


async def test_project_status_access(
    mcp_url: str, app_pool: ConnectionPool, team: dict[str, Any]
) -> None:
    project_id = team["head"].project_id
    outsider_same_dept = person(app_pool, "Dự án thử nghiệm A")
    head_b = person(app_pool, "Dự án thử nghiệm B", role="dept_head")
    admin = person(app_pool, "Dự án thử nghiệm A", role="admin")
    director = person(app_pool, "Dự án thử nghiệm B", role="director")

    for who in (outsider_same_dept, head_b, admin):  # T-03 cho head_b
        await _denied(mcp_url, who, "get_project_status", {"project_id": project_id})
    await _denied(mcp_url, director, "get_project_status", {"project_id": 999_999_999})
    data = await _ok(mcp_url, director, "get_project_status", {"project_id": project_id})
    assert "not_reported_members" in data["reports_today"]


async def test_team_blockers(mcp_url: str, app_pool: ConnectionPool, team: dict[str, Any]) -> None:
    head: Person = team["head"]
    by_project = await _ok(
        mcp_url, head, "get_team_blockers", {"scope": "project", "scope_id": head.project_id}
    )
    assert [(b["user_id"], b["severity"]) for b in by_project["items"]] == [
        (team["reported"].user_id, "high"),
        (team["reported"].user_id, "low"),
    ]
    assert by_project["items"][0]["project"]["name"] == team["name"]

    high = await _ok(
        mcp_url,
        head,
        "get_team_blockers",
        {"scope": "project", "scope_id": head.project_id, "severity_min": "high"},
    )
    assert [b["text"] for b in high["items"]] == ["CI hỏng"]

    # Phạm vi phòng gồm cả project khác cùng phòng.
    sibling = person(app_pool, _project(app_pool, DEPT_A))
    await _ok(
        mcp_url,
        sibling,
        "submit_report",
        {
            "project_id": sibling.project_id,
            "done": "x",
            "blocker_items": [{"kind": "external", "severity": "medium", "text": "Đối tác chậm"}],
        },
    )
    with app_pool.connection() as conn:
        dept_a = conn.execute("select id from departments where name = %s", (DEPT_A,)).fetchone()
    assert dept_a is not None
    dept = await _ok(
        mcp_url, head, "get_team_blockers", {"scope": "department", "scope_id": dept_a[0]}
    )
    texts = [b["text"] for b in dept["items"]]
    assert {"CI hỏng", "Đối tác chậm"} <= set(texts)
    assert "nháp AI chưa duyệt" not in texts

    other_day = await _ok(
        mcp_url,
        head,
        "get_team_blockers",
        {"scope": "project", "scope_id": head.project_id, "date": "2026-01-01"},
    )
    assert other_day["items"] == []
    assert by_project["date"] == _today(app_pool)

    is_error, text = await call_tool(
        mcp_url, head.token, "get_team_blockers", {"scope": "company", "scope_id": 1}
    )
    assert is_error and text.startswith("invalid_argument")


async def test_team_blockers_access(
    mcp_url: str, app_pool: ConnectionPool, team: dict[str, Any]
) -> None:
    project = {"scope": "project", "scope_id": team["head"].project_id}
    head_b = person(app_pool, "Dự án thử nghiệm B", role="dept_head")
    with app_pool.connection() as conn:
        row = conn.execute("select id from departments where name = %s", (DEPT_A,)).fetchone()
    assert row is not None
    department = {"scope": "department", "scope_id": row[0]}

    for args in (project, department):
        await _denied(mcp_url, team["reported"], "get_team_blockers", args)  # T-02
        await _denied(mcp_url, head_b, "get_team_blockers", args)  # T-03
    await _denied(
        mcp_url, team["head"], "get_team_blockers", {"scope": "department", "scope_id": 999_999}
    )
