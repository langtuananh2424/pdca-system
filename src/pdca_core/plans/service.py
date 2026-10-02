"""Nghiệp vụ Plan: xem, tạo, sửa kế hoạch theo cây (LLD 4.2, FR-PLAN-01..06).

Phạm vi đọc (`plan.read`): staff theo project, dept_head trong phòng, director
toàn bộ; ngoài ra chủ sở hữu luôn đọc được kế hoạch của mình (staff được viết
kế hoạch "của mình" theo LLD 3.2 nên phải đọc lại được). Danh sách được lọc
bằng SQL theo vai trò rồi kiểm lại từng dòng bằng `can` để không lệch ma trận.
"""

from dataclasses import replace
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import Role, UserContext
from pdca_core.authz.policy import Action, Resource, can, require
from pdca_core.errors import Conflict, ForbiddenOrNotFound, InvalidArgument
from pdca_core.plans.rules import (
    CLOSED_STATUSES,
    LEVELS,
    PLAN_STATUSES,
    check_child,
    check_range,
    check_status_change,
    check_within,
)
from pdca_core.repositories import plans as repo
from pdca_core.repositories.plans import PlanFilter, PlanRow
from pdca_core.validation import (
    choice,
    clip_text,
    decode_cursor,
    encode_cursor,
    page_limit,
    parse_date,
    require_text,
)

CHILD_LIMIT = 100
TASK_LIMIT = 100


def plan_resource(plan: PlanRow) -> Resource:
    return Resource(
        owner_id=plan.owner_id, project_id=plan.project_id, department_id=plan.department_id
    )


def can_read(ctx: UserContext, plan: PlanRow) -> bool:
    return plan.owner_id == ctx.user_id or can(ctx, Action.PLAN_READ, plan_resource(plan))


def _require_read(ctx: UserContext, plan: PlanRow | None) -> PlanRow:
    if plan is None or not can_read(ctx, plan):
        raise ForbiddenOrNotFound()
    return plan


def plan_dict(plan: PlanRow) -> dict[str, Any]:
    return {
        "id": plan.id,
        "project_id": plan.project_id,
        "parent_id": plan.parent_id,
        "level": plan.level,
        "goal": plan.goal,
        "start_date": plan.start_date.isoformat(),
        "end_date": plan.end_date.isoformat(),
        "owner_id": plan.owner_id,
        "status": plan.status,
        "version": plan.version,
    }


def _read_scope(ctx: UserContext) -> tuple[str, dict[str, Any]]:
    """Điều kiện SQL tương ứng `can_read` cho từng vai trò."""
    params: dict[str, Any] = {"me": ctx.user_id}
    match ctx.role:
        case Role.DIRECTOR:
            return "true", params
        case Role.DEPT_HEAD if ctx.department_id is not None:
            params["dept"] = ctx.department_id
            return (
                "p.owner_id = %(me)s or coalesce(pr.department_id, ow.department_id) = %(dept)s",
                params,
            )
        case Role.STAFF:
            params["projects"] = sorted(ctx.project_ids)
            return "p.owner_id = %(me)s or p.project_id = any(%(projects)s)", params
        case _:
            return "p.owner_id = %(me)s", params


def list_plans(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    project_id: int | None = None,
    level: str | None = None,
    parent_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    filters = PlanFilter(
        project_id=project_id,
        level=choice("level", level, LEVELS) if level else None,
        parent_id=parent_id,
        from_date=parse_date("from_date", from_date) if from_date else None,
        to_date=parse_date("to_date", to_date) if to_date else None,
    )
    size = page_limit(limit)
    after = _decode_plan_cursor(cursor) if cursor else None
    scope_sql, scope_params = _read_scope(ctx)

    with pool.connection() as conn:
        rows = repo.search(conn, filters, scope_sql, scope_params, after, size + 1)

    page = rows[:size]
    next_cursor = (
        encode_cursor([page[-1].start_date.isoformat(), page[-1].id]) if len(rows) > size else None
    )
    return {
        "items": [plan_dict(p) for p in page if can_read(ctx, p)],
        "next_cursor": next_cursor,
    }


def get_plan(ctx: UserContext, pool: ConnectionPool, *, plan_id: int) -> dict[str, Any]:
    """Kế hoạch, kế hoạch con trực tiếp (được đọc), task gắn với kế hoạch."""
    with pool.connection() as conn:
        plan = _require_read(ctx, repo.get(conn, plan_id))
        kids = [p for p in repo.children(conn, plan_id, CHILD_LIMIT) if can_read(ctx, p)]
        counts = repo.task_counts_of_plan(conn, plan_id)
        tasks = repo.tasks_of_plan(conn, plan_id, TASK_LIMIT)

    # Người giao việc trong phạm vi (task.assign) thấy mọi task; người khác chỉ thấy task của mình.
    task_scope = Resource(project_id=plan.project_id, department_id=plan.department_id)
    if not can(ctx, Action.TASK_ASSIGN, task_scope):
        tasks = [t for t in tasks if t["assignee_id"] == ctx.user_id]
    return {
        "plan": plan_dict(plan),
        "children": [plan_dict(p) for p in kids],
        "tasks": tasks,
        "task_counts": counts,
    }


def create_plan(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    level: str,
    goal: str,
    start_date: str,
    end_date: str,
    project_id: int | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Tạo kế hoạch do chính người gọi sở hữu (FR-PLAN-02, FR-PLAN-03)."""
    choice("level", level, LEVELS)
    text = require_text("goal", goal)
    start, end = parse_date("start_date", start_date), parse_date("end_date", end_date)
    check_range(start, end)

    with pool.connection() as conn, conn.transaction():
        department_id = ctx.department_id
        if project_id is not None:
            project = repo.project_scope(conn, project_id)
            project_res = Resource(
                project_id=project_id, department_id=project.department_id if project else None
            )
            if project is None or not can(ctx, Action.PLAN_READ, project_res):
                raise ForbiddenOrNotFound()
            if project.status == "closed":
                raise Conflict("project is closed")
            department_id = project.department_id

        if parent_id is not None:
            parent = _require_read(ctx, repo.get(conn, parent_id, lock=True))
            if parent.status in CLOSED_STATUSES:
                raise Conflict(f"parent plan is {parent.status}")
            if parent.project_id is not None and parent.project_id != project_id:
                raise InvalidArgument("child plan must belong to the parent's project")
            check_child(parent.level, level)
            check_within(start, end, parent.start_date, parent.end_date)

        require(
            ctx,
            Action.PLAN_WRITE,
            Resource(owner_id=ctx.user_id, project_id=project_id, department_id=department_id),
        )
        plan_id = repo.insert(
            conn,
            project_id=project_id,
            parent_id=parent_id,
            level=level,
            goal=text,
            start_date=start,
            end_date=end,
            owner_id=ctx.user_id,
        )
        created = repo.get(conn, plan_id)
        if created is None:
            raise RuntimeError("plan disappeared after insert")
        repo.insert_version(conn, created, ctx.user_id, "created")

    return plan_dict(created)


def update_plan(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    plan_id: int,
    expected_version: int,
    reason: str,
    goal: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Sửa kế hoạch có khóa lạc quan và ghi `plan_versions` (FR-PLAN-04, FR-PLAN-06)."""
    why = require_text("reason", reason)
    if goal is None and start_date is None and end_date is None and status is None:
        raise InvalidArgument("nothing to update")
    if status is not None:
        choice("status", status, PLAN_STATUSES)

    with pool.connection() as conn, conn.transaction():
        plan = _require_read(ctx, repo.get(conn, plan_id, lock=True))
        require(ctx, Action.PLAN_WRITE, plan_resource(plan))
        if plan.version != expected_version:
            raise Conflict(
                f"plan was changed by someone else (current version {plan.version}); reload"
            )
        if plan.status in CLOSED_STATUSES:
            raise Conflict(f"plan is {plan.status}")

        new_goal = require_text("goal", goal) if goal is not None else plan.goal
        new_start = parse_date("start_date", start_date) if start_date else plan.start_date
        new_end = parse_date("end_date", end_date) if end_date else plan.end_date
        new_status = status or plan.status
        check_range(new_start, new_end)
        check_status_change(plan.status, new_status)
        if (new_start, new_end) != (plan.start_date, plan.end_date):
            _check_dates_fit_tree(conn, plan, new_start, new_end)

        updated = replace(
            plan,
            goal=clip_text(new_goal),
            start_date=new_start,
            end_date=new_end,
            status=new_status,
            version=plan.version + 1,
        )
        repo.update(
            conn,
            plan.id,
            goal=updated.goal,
            start_date=new_start,
            end_date=new_end,
            status=new_status,
            version=updated.version,
        )
        repo.insert_version(conn, updated, ctx.user_id, why)

    return plan_dict(updated)


def _check_dates_fit_tree(conn: Connection[Any], plan: PlanRow, start: date, end: date) -> None:
    if plan.parent_id is not None:
        parent = repo.get(conn, plan.parent_id)
        if parent is not None:
            check_within(start, end, parent.start_date, parent.end_date)
    outside = repo.children_outside(conn, plan.id, start, end)
    if outside:
        raise InvalidArgument(f"{outside} child plan(s) would fall outside the new dates")


def _decode_plan_cursor(cursor: str) -> tuple[date, int]:
    match decode_cursor(cursor):
        case [str() as start, int() as last_id] if not isinstance(last_id, bool):
            return parse_date("cursor", start), last_id
        case _:
            raise InvalidArgument("invalid cursor")
