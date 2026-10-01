"""Truy cập `plans`, `plan_versions` và phạm vi project/thành viên (LLD 2.2, FR-PLAN).

Phòng của một kế hoạch = phòng của project; kế hoạch không gắn project thì
lấy phòng của chủ sở hữu (null nếu là kế hoạch cấp công ty).
"""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True, slots=True)
class PlanRow:
    id: int
    project_id: int | None
    parent_id: int | None
    level: str
    goal: str
    start_date: date
    end_date: date
    owner_id: int
    status: str
    version: int
    department_id: int | None


@dataclass(frozen=True, slots=True)
class ProjectScope:
    id: int
    department_id: int
    status: str


@dataclass(frozen=True, slots=True)
class PlanFilter:
    project_id: int | None = None
    level: str | None = None
    parent_id: int | None = None
    from_date: date | None = None
    to_date: date | None = None


_PLAN_SELECT = """
    select p.id, p.project_id, p.parent_id, p.level, p.goal, p.start_date, p.end_date,
           p.owner_id, p.status, p.version,
           coalesce(pr.department_id, ow.department_id) as department_id
    from plans p
    left join projects pr on pr.id = p.project_id
    join users ow on ow.id = p.owner_id
"""


def project_scope(conn: Connection[Any], project_id: int) -> ProjectScope | None:
    with conn.cursor(row_factory=class_row(ProjectScope)) as cur:
        return cur.execute(
            "select id, department_id, status from projects where id = %s and deleted_at is null",
            (project_id,),
        ).fetchone()


def is_active_member(conn: Connection[Any], project_id: int, user_id: int) -> bool:
    row = conn.execute(
        """
        select 1 from project_members pm
        join users u on u.id = pm.user_id
        where pm.project_id = %s and pm.user_id = %s
          and u.status = 'active' and u.deleted_at is null
        """,
        (project_id, user_id),
    ).fetchone()
    return row is not None


def get(conn: Connection[Any], plan_id: int, *, lock: bool = False) -> PlanRow | None:
    sql = _PLAN_SELECT + " where p.id = %s and p.deleted_at is null"
    if lock:
        sql += " for update of p"
    with conn.cursor(row_factory=class_row(PlanRow)) as cur:
        return cur.execute(sql, (plan_id,)).fetchone()


def children(conn: Connection[Any], plan_id: int, limit: int) -> list[PlanRow]:
    with conn.cursor(row_factory=class_row(PlanRow)) as cur:
        return cur.execute(
            _PLAN_SELECT + " where p.parent_id = %s and p.deleted_at is null"
            " order by p.start_date, p.id limit %s",
            (plan_id, limit),
        ).fetchall()


def children_outside(conn: Connection[Any], plan_id: int, start: date, end: date) -> int:
    row = conn.execute(
        """
        select count(*) from plans
        where parent_id = %s and deleted_at is null
          and (start_date < %s or end_date > %s)
        """,
        (plan_id, start, end),
    ).fetchone()
    return int(row[0]) if row else 0


def search(
    conn: Connection[Any],
    filters: PlanFilter,
    scope_sql: str,
    scope_params: dict[str, Any],
    after: tuple[date, int] | None,
    limit: int,
) -> list[PlanRow]:
    """`scope_sql` là điều kiện phạm vi đọc do tầng nghiệp vụ dựng (hằng, có tham số)."""
    after_start, after_id = after if after is not None else (None, 0)
    params: dict[str, Any] = {
        **scope_params,
        "project": filters.project_id,
        "level": filters.level,
        "parent": filters.parent_id,
        "from": filters.from_date,
        "to": filters.to_date,
        "paged": after is not None,
        "after_start": after_start,
        "after_id": after_id,
        "limit": limit,
    }
    sql = (
        _PLAN_SELECT
        + f"""
        where p.deleted_at is null
          and ({scope_sql})
          and (%(project)s::bigint is null or p.project_id = %(project)s)
          and (%(level)s::text is null or p.level = %(level)s)
          and (%(parent)s::bigint is null or p.parent_id = %(parent)s)
          and (%(from)s::date is null or p.end_date >= %(from)s)
          and (%(to)s::date is null or p.start_date <= %(to)s)
          and (not %(paged)s or (p.start_date, p.id) > (%(after_start)s::date, %(after_id)s))
        order by p.start_date, p.id
        limit %(limit)s
        """
    )
    with conn.cursor(row_factory=class_row(PlanRow)) as cur:
        return cur.execute(sql, params).fetchall()


def insert(
    conn: Connection[Any],
    *,
    project_id: int | None,
    parent_id: int | None,
    level: str,
    goal: str,
    start_date: date,
    end_date: date,
    owner_id: int,
) -> int:
    row = conn.execute(
        """
        insert into plans (project_id, parent_id, level, goal, start_date, end_date, owner_id)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (project_id, parent_id, level, goal, start_date, end_date, owner_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("insert into plans returned no row")
    return int(row[0])


def update(
    conn: Connection[Any],
    plan_id: int,
    *,
    goal: str,
    start_date: date,
    end_date: date,
    status: str,
    version: int,
) -> None:
    conn.execute(
        """
        update plans
        set goal = %s, start_date = %s, end_date = %s, status = %s, version = %s,
            updated_at = now()
        where id = %s
        """,
        (goal, start_date, end_date, status, version, plan_id),
    )


def snapshot(plan: PlanRow) -> dict[str, Any]:
    data = asdict(plan)
    data.pop("department_id")
    data["start_date"] = plan.start_date.isoformat()
    data["end_date"] = plan.end_date.isoformat()
    return data


def insert_version(
    conn: Connection[Any], plan: PlanRow, changed_by: int, reason: str | None
) -> None:
    """FR-PLAN-04: ai, lúc nào, trạng thái sau thay đổi, lý do."""
    conn.execute(
        """
        insert into plan_versions (plan_id, version, snapshot, changed_by, reason)
        values (%s, %s, %s, %s, %s)
        """,
        (plan.id, plan.version, Jsonb(snapshot(plan)), changed_by, reason),
    )


def tasks_of_plan(conn: Connection[Any], plan_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id, title, assignee_id, status, due_date
        from tasks where plan_id = %s and deleted_at is null
        order by coalesce(due_date, 'infinity'::date), id
        limit %s
        """,
        (plan_id, limit),
    ).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "assignee_id": r[2],
            "status": r[3],
            "due_date": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


def task_counts_of_plan(conn: Connection[Any], plan_id: int) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) from tasks where plan_id = %s and deleted_at is null"
        " group by status",
        (plan_id,),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}
