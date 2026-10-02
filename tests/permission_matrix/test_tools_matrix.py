"""Ma trận quyền mức tool (LLD 3.2 + mục "Quyền" của từng tool ở LLD 4.2).

Mỗi tool mới phải thêm ca vào đây. Gọi thẳng hàm nghiệp vụ với `UserContext`
dựng từ token thật trên DB thật, cho mọi vai trò × quan hệ với tài nguyên.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.tokens import authenticate, issue_token
from pdca_core.errors import ForbiddenOrNotFound
from pdca_core.org import service as org_service
from pdca_core.plans import service as plan_service
from pdca_core.reports import service as report_service
from pdca_core.reports import team as team_service
from pdca_core.repositories.tokens import PgTokenRepository
from pdca_core.tasks import service as task_service

pytestmark = pytest.mark.integration

ROLES = ("staff", "dept_head", "director", "admin")


@dataclass(frozen=True)
class World:
    """Người gọi (mỗi vai trò, thành viên project A) và tài nguyên quanh họ."""

    callers: dict[str, UserContext]
    own_task: dict[str, int]
    peer_task: int  # task của đồng nghiệp cùng project A
    project_a: int
    project_b: int  # caller không phải thành viên
    task_in_b: int
    peer: int
    outsider: int  # thành viên project B (phòng B)
    plan_a: int  # kế hoạch của peer trong project A
    plan_b: int  # kế hoạch của outsider trong project B
    dept_a: int
    dept_b: int


@pytest.fixture(scope="module")
def world(app_pool: ConnectionPool) -> World:
    tokens = PgTokenRepository(app_pool)
    with app_pool.connection() as conn:
        a, b = (
            int(conn.execute("select id from projects where name = %s", (n,)).fetchone()[0])  # type: ignore[index]
            for n in ("Dự án thử nghiệm A", "Dự án thử nghiệm B")
        )

        def user(role: str, project: int) -> int:
            row = conn.execute(
                """
                with u as (
                  insert into users (name, email, role, department_id)
                  select 'matrix', %s, %s, department_id from projects where id = %s
                  returning id)
                insert into project_members (project_id, user_id) select %s, id from u
                returning user_id
                """,
                (f"{uuid.uuid4().hex[:12]}@test.invalid", role, project, project),
            ).fetchone()
            return int(row[0])  # type: ignore[index]

        def task(assignee: int, project: int) -> int:
            row = conn.execute(
                "insert into tasks (project_id, assignee_id, created_by, title)"
                " values (%s, %s, %s, 'matrix') returning id",
                (project, assignee, assignee),
            ).fetchone()
            return int(row[0])  # type: ignore[index]

        ids = {role: user(role, a) for role in ROLES}
        peer = user("staff", a)
        outsider = user("staff", b)
        own_task = {role: task(uid, a) for role, uid in ids.items()}
        peer_task = task(peer, a)
        task_in_b = task(outsider, b)

        def plan(owner: int, project: int) -> int:
            row = conn.execute(
                "insert into plans (project_id, level, goal, start_date, end_date, owner_id)"
                " values (%s, 'month', 'matrix', '2026-10-01', '2026-10-31', %s) returning id",
                (project, owner),
            ).fetchone()
            return int(row[0])  # type: ignore[index]

        plan_a = plan(peer, a)
        plan_b = plan(outsider, b)

        def dept_of(project: int) -> int:
            row = conn.execute(
                "select department_id from projects where id = %s", (project,)
            ).fetchone()
            return int(row[0])  # type: ignore[index]

        dept_a, dept_b = dept_of(a), dept_of(b)

    callers = {
        role: authenticate(issue_token(tokens, uid).token, tokens, "matrix")
        for role, uid in ids.items()
    }
    return World(
        callers,
        own_task,
        peer_task,
        a,
        b,
        task_in_b,
        peer,
        outsider,
        plan_a,
        plan_b,
        dept_a,
        dept_b,
    )


def _version(pool: ConnectionPool, plan_id: int) -> int:
    with pool.connection() as conn:
        row = conn.execute("select version from plans where id = %s", (plan_id,)).fetchone()
    return int(row[0])  # type: ignore[index]


def _month(project_id: int) -> dict[str, Any]:
    return {
        "level": "month",
        "goal": "matrix",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "project_id": project_id,
    }


Call = Callable[[UserContext, ConnectionPool, World, str], Any]

# (tool, tình huống, hàm gọi, các vai trò được phép)
CASES: list[tuple[str, str, Call, set[str]]] = [
    ("whoami", "self", lambda c, p, w, r: org_service.whoami(c, p), set(ROLES)),
    (
        "get_my_tasks",
        "own",
        lambda c, p, w, r: task_service.list_mine(c, p),
        {"staff", "dept_head", "director"},
    ),
    (
        "update_task_status",
        "own_task",
        lambda c, p, w, r: task_service.update_status(
            c, p, task_id=w.own_task[r], status="in_progress"
        ),
        {"staff", "dept_head", "director"},
    ),
    (
        "update_task_status",
        "peer_task_same_project",
        lambda c, p, w, r: task_service.update_status(
            c, p, task_id=w.peer_task, status="in_progress"
        ),
        set(),  # "own": kể cả trưởng phòng, giám đốc cũng không sửa task người khác qua tool này
    ),
    (
        "update_task_status",
        "task_other_project",
        lambda c, p, w, r: task_service.update_status(
            c, p, task_id=w.task_in_b, status="in_progress"
        ),
        set(),
    ),
    (
        "log_activity",
        "member_project",
        lambda c, p, w, r: task_service.log_activity(c, p, project_id=w.project_a, summary="ok"),
        set(ROLES),  # quyền là "thành viên project", không theo vai trò
    ),
    (
        "log_activity",
        "non_member_project",
        lambda c, p, w, r: task_service.log_activity(c, p, project_id=w.project_b, summary="x"),
        set(),
    ),
    (
        "log_activity",
        "task_of_other_project",
        lambda c, p, w, r: task_service.log_activity(
            c, p, project_id=w.project_a, summary="x", task_id=w.task_in_b
        ),
        set(),
    ),
    (
        "get_my_day_context",
        "own",
        lambda c, p, w, r: report_service.day_context(c, p),
        {"staff", "dept_head", "director"},
    ),
    (
        "submit_report",
        "member_project",
        lambda c, p, w, r: report_service.submit(
            c, p, project_id=w.project_a, done="matrix", mode="replace"
        ),
        {"staff", "dept_head", "director"},  # admin là thành viên nhưng không có report.submit.own
    ),
    (
        "submit_report",
        "non_member_project",
        lambda c, p, w, r: report_service.submit(c, p, project_id=w.project_b, done="x"),
        set(),
    ),
    (
        "get_my_reports",
        "own",
        lambda c, p, w, r: report_service.list_mine(
            c, p, from_date="2026-01-01", to_date="2026-03-31"
        ),
        {"staff", "dept_head", "director"},
    ),
    # --- Bước 7: kế hoạch (plan.read / plan.write) và giao việc (task.create / task.assign)
    (
        "get_plan",
        "peer_plan_same_project",
        lambda c, p, w, r: plan_service.get_plan(c, p, plan_id=w.plan_a),
        {"staff", "dept_head", "director"},
    ),
    (
        "get_plan",
        "plan_other_department",
        lambda c, p, w, r: plan_service.get_plan(c, p, plan_id=w.plan_b),
        {"director"},
    ),
    (
        "create_plan",
        "member_project",
        lambda c, p, w, r: plan_service.create_plan(c, p, **_month(w.project_a)),
        {"staff", "dept_head", "director"},
    ),
    (
        "create_plan",
        "project_other_department",
        lambda c, p, w, r: plan_service.create_plan(c, p, **_month(w.project_b)),
        {"director"},
    ),
    (
        "update_plan",
        "peer_plan_same_project",
        lambda c, p, w, r: plan_service.update_plan(
            c, p, plan_id=w.plan_a, expected_version=_version(p, w.plan_a), goal=r, reason="m"
        ),
        {"dept_head", "director"},  # staff chỉ sửa kế hoạch của mình
    ),
    (
        "update_plan",
        "plan_other_department",
        lambda c, p, w, r: plan_service.update_plan(
            c, p, plan_id=w.plan_b, expected_version=_version(p, w.plan_b), goal=r, reason="m"
        ),
        {"director"},
    ),
    (
        "create_task",
        "project_same_department",
        lambda c, p, w, r: task_service.create_task(
            c, p, project_id=w.project_a, title="m", assignee_id=w.peer
        ),
        {"dept_head", "director"},
    ),
    (
        "create_task",
        "project_other_department",
        lambda c, p, w, r: task_service.create_task(
            c, p, project_id=w.project_b, title="m", assignee_id=w.outsider
        ),
        {"director"},
    ),
    (
        "assign_task",
        "task_same_department",
        lambda c, p, w, r: task_service.assign_task(c, p, task_id=w.peer_task, assignee_id=w.peer),
        {"dept_head", "director"},
    ),
    (
        "assign_task",
        "task_other_department",
        lambda c, p, w, r: task_service.assign_task(
            c, p, task_id=w.task_in_b, assignee_id=w.outsider
        ),
        {"director"},
    ),
    # --- Mức nhóm (UC-06): get_project_status, get_team_blockers
    (
        "get_project_status",
        "member_project",
        lambda c, p, w, r: team_service.project_status(c, p, project_id=w.project_a),
        set(ROLES),  # LLD 4.2: thành viên project (mọi vai trò), trưởng phòng, giám đốc
    ),
    (
        "get_project_status",
        "project_other_department",
        lambda c, p, w, r: team_service.project_status(c, p, project_id=w.project_b),
        {"director"},
    ),
    (
        "get_team_blockers",
        "project_same_department",
        lambda c, p, w, r: team_service.team_blockers(c, p, scope="project", scope_id=w.project_a),
        {"dept_head", "director"},  # T-02: staff bị từ chối
    ),
    (
        "get_team_blockers",
        "department_own",
        lambda c, p, w, r: team_service.team_blockers(c, p, scope="department", scope_id=w.dept_a),
        {"dept_head", "director"},
    ),
    (
        "get_team_blockers",
        "project_other_department",
        lambda c, p, w, r: team_service.team_blockers(c, p, scope="project", scope_id=w.project_b),
        {"director"},  # T-03
    ),
    (
        "get_team_blockers",
        "department_other",
        lambda c, p, w, r: team_service.team_blockers(c, p, scope="department", scope_id=w.dept_b),
        {"director"},
    ),
]


@pytest.mark.parametrize(
    ("call", "role", "allowed"),
    [
        pytest.param(call, role, role in allowed, id=f"{tool}-{situation}-{role}")
        for tool, situation, call, allowed in CASES
        for role in ROLES
    ],
)
def test_tool_permission(
    world: World, app_pool: ConnectionPool, call: Call, role: str, allowed: bool
) -> None:
    ctx = world.callers[role]
    if allowed:
        call(ctx, app_pool, world, role)
    else:
        with pytest.raises(ForbiddenOrNotFound):
            call(ctx, app_pool, world, role)
