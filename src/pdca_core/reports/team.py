"""Nghiệp vụ mức nhóm: tình trạng project, vướng mắc của nhóm (LLD 4.2, UC-06, FR-NTF-06).

Chỉ dữ liệu từ báo cáo `submitted` đi lên (bất biến 7). Thành viên thường chỉ
thấy số liệu tổng và vướng mắc của chính mình; danh sách ai đã/chưa báo cáo và
vướng mắc của người khác cần `report.read.team` / `blockers.read.team`.
"""

from typing import Any

from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.policy import Action, Resource, can, require
from pdca_core.errors import ForbiddenOrNotFound
from pdca_core.repositories import reports as report_repo
from pdca_core.repositories import team as repo
from pdca_core.repositories.team import SEVERITY_RANK, BlockerRow
from pdca_core.validation import choice, parse_date

SCOPES = ("project", "department")
TOP_BLOCKERS = 10
MAX_BLOCKERS = 100


def project_status(ctx: UserContext, pool: ConnectionPool, *, project_id: int) -> dict[str, Any]:
    """Quyền: thành viên project, trưởng phòng của phòng chứa project, hoặc giám đốc."""
    with pool.connection() as conn:
        project = repo.project_info(conn, project_id)
        if project is None:
            raise ForbiddenOrNotFound()
        scope = Resource(project_id=project.id, department_id=project.department_id)
        sees_team = can(ctx, Action.REPORT_READ_TEAM, scope)
        if project.id not in ctx.project_ids and not sees_team:
            raise ForbiddenOrNotFound()
        sees_blockers = can(ctx, Action.BLOCKERS_READ_TEAM, scope)

        today = report_repo.user_today(conn, ctx.user_id)
        plans = repo.open_plans_by_level(conn, project.id)
        tasks = repo.tasks_by_status(conn, project.id)
        members = repo.member_reports(conn, project.id, today)
        top = repo.blockers(
            conn,
            day=today,
            project_id=project.id,
            user_id=None if sees_blockers else ctx.user_id,
            limit=TOP_BLOCKERS,
        )

    submitted = [m for m in members if m.submitted]
    away = [m for m in members if m.away and not m.submitted]
    missing = [m for m in members if not m.submitted and not m.away]
    reports_today: dict[str, Any] = {
        "date": today.isoformat(),
        "submitted": len(submitted),
        "not_reported": len(missing),
        "away": len(away),
    }
    if sees_team:
        # FR-NTF-06: chỉ hiển thị danh sách; leo thang do con người quyết định.
        reports_today["submitted_members"] = [_member(m.user_id, m.name) for m in submitted]
        reports_today["not_reported_members"] = [_member(m.user_id, m.name) for m in missing]
        reports_today["away_members"] = [_member(m.user_id, m.name) for m in away]

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "department_id": project.department_id,
            "status": project.status,
        },
        "plans_open_by_level": plans,
        "tasks_by_status": tasks,
        "reports_today": reports_today,
        "top_blockers": [_blocker(b) for b in top],
    }


def team_blockers(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    scope: str,
    scope_id: int,
    day: str | None = None,
    severity_min: str | None = None,
) -> dict[str, Any]:
    """Vướng mắc của nhóm từ báo cáo đã nộp (UC-06). Quyền `blockers.read.team`."""
    choice("scope", scope, SCOPES)
    min_rank = 1
    if severity_min:
        min_rank = SEVERITY_RANK[choice("severity_min", severity_min, tuple(SEVERITY_RANK))]

    with pool.connection() as conn:
        if scope == "project":
            project = repo.project_info(conn, scope_id)
            if project is None:
                raise ForbiddenOrNotFound()
            resource = Resource(project_id=project.id, department_id=project.department_id)
            filters: dict[str, int] = {"project_id": project.id}
        else:
            if not repo.department_exists(conn, scope_id):
                raise ForbiddenOrNotFound()
            resource = Resource(department_id=scope_id)
            filters = {"department_id": scope_id}
        require(ctx, Action.BLOCKERS_READ_TEAM, resource)

        target = parse_date("date", day) if day else report_repo.user_today(conn, ctx.user_id)
        rows = repo.blockers(conn, day=target, min_rank=min_rank, limit=MAX_BLOCKERS + 1, **filters)

    result: dict[str, Any] = {
        "date": target.isoformat(),
        "items": [_blocker(b) for b in rows[:MAX_BLOCKERS]],
    }
    if len(rows) > MAX_BLOCKERS:
        result["truncated"] = True
    return result


def _member(user_id: int, name: str) -> dict[str, Any]:
    return {"user_id": user_id, "name": name}


def _blocker(b: BlockerRow) -> dict[str, Any]:
    return {
        "report_id": b.report_id,
        "report_date": b.report_date.isoformat(),
        "user_id": b.user_id,
        "user_name": b.user_name,
        "project": {"id": b.project_id, "name": b.project_name},
        "kind": b.kind,
        "severity": b.severity,
        "text": b.text,
    }
