"""Truy vấn mức nhóm: tình trạng project và vướng mắc (LLD 4.2 `get_project_status`,
`get_team_blockers`). Chỉ đọc báo cáo `submitted` (bất biến 7, NFR-PRV)."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
_RANK_SQL = "case b.severity when 'high' then 3 when 'medium' then 2 else 1 end"


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: int
    name: str
    department_id: int
    status: str


@dataclass(frozen=True, slots=True)
class MemberReport:
    user_id: int
    name: str
    away: bool
    submitted: bool


@dataclass(frozen=True, slots=True)
class BlockerRow:
    report_id: int
    report_date: date
    user_id: int
    user_name: str
    project_id: int
    project_name: str
    kind: str
    severity: str
    text: str


def project_info(conn: Connection[Any], project_id: int) -> ProjectInfo | None:
    with conn.cursor(row_factory=class_row(ProjectInfo)) as cur:
        return cur.execute(
            "select id, name, department_id, status from projects"
            " where id = %s and deleted_at is null",
            (project_id,),
        ).fetchone()


def department_exists(conn: Connection[Any], department_id: int) -> bool:
    return (
        conn.execute("select 1 from departments where id = %s", (department_id,)).fetchone()
        is not None
    )


def open_plans_by_level(conn: Connection[Any], project_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        select level, count(*) from plans
        where project_id = %s and deleted_at is null and status in ('draft', 'active')
        group by level
        """,
        (project_id,),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def tasks_by_status(conn: Connection[Any], project_id: int) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) from tasks where project_id = %s and deleted_at is null"
        " group by status",
        (project_id,),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def member_reports(conn: Connection[Any], project_id: int, day: date) -> list[MemberReport]:
    """Thành viên đang hoạt động và việc đã nộp báo cáo `submitted` cho ngày `day` chưa."""
    with conn.cursor(row_factory=class_row(MemberReport)) as cur:
        return cur.execute(
            """
            select u.id as user_id, u.name,
                   coalesce(u.away_until >= %(day)s, false) as away,
                   exists (
                     select 1 from reports r
                     where r.user_id = u.id and r.project_id = %(project)s
                       and r.report_date = %(day)s and r.status = 'submitted'
                   ) as submitted
            from project_members pm
            join users u on u.id = pm.user_id
            where pm.project_id = %(project)s and u.status = 'active' and u.deleted_at is null
            order by u.name, u.id
            """,
            {"project": project_id, "day": day},
        ).fetchall()


def blockers(
    conn: Connection[Any],
    *,
    day: date,
    project_id: int | None = None,
    department_id: int | None = None,
    user_id: int | None = None,
    min_rank: int = 1,
    limit: int = 100,
) -> list[BlockerRow]:
    """Vướng mắc chưa xóa của báo cáo `submitted` trong ngày, nặng trước."""
    with conn.cursor(row_factory=class_row(BlockerRow)) as cur:
        return cur.execute(
            f"""
            select r.id as report_id, r.report_date, u.id as user_id, u.name as user_name,
                   p.id as project_id, p.name as project_name, b.kind, b.severity, b.text
            from blockers b
            join reports r on r.id = b.report_id
            join users u on u.id = r.user_id
            join projects p on p.id = r.project_id
            where b.deleted_at is null
              and r.status = 'submitted'
              and r.report_date = %(day)s
              and p.deleted_at is null
              and (%(project)s::bigint is null or p.id = %(project)s)
              and (%(dept)s::bigint is null or p.department_id = %(dept)s)
              and (%(user)s::bigint is null or r.user_id = %(user)s)
              and {_RANK_SQL} >= %(rank)s
            order by {_RANK_SQL} desc, r.id, b.id
            limit %(limit)s
            """,  # noqa: S608 — chỉ chèn hằng
            {
                "day": day,
                "project": project_id,
                "dept": department_id,
                "user": user_id,
                "rank": min_rank,
                "limit": limit,
            },
        ).fetchall()
