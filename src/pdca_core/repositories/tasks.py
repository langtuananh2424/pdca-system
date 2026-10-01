"""Truy cập `tasks`, `task_events`, `activities` (LLD 2.2)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row


@dataclass(frozen=True, slots=True)
class TaskRow:
    id: int
    title: str
    project_id: int
    plan_id: int | None
    due_date: date | None
    status: str
    assignee_id: int


@dataclass(frozen=True, slots=True)
class ActivityRow:
    id: int
    at: datetime


_TASK_COLUMNS = "id, title, project_id, plan_id, due_date, status, assignee_id"


def list_for_assignee(
    conn: Connection[Any],
    assignee_id: int,
    statuses: Sequence[str],
    project_id: int | None,
    after: tuple[date | None, int] | None,
    limit: int,
) -> list[TaskRow]:
    """Sắp theo hạn (không hạn xếp cuối) rồi id; phân trang keyset sau `after`."""
    after_due, after_id = after if after is not None else (None, 0)
    with conn.cursor(row_factory=class_row(TaskRow)) as cur:
        return cur.execute(
            f"""
            select {_TASK_COLUMNS}
            from tasks
            where assignee_id = %(assignee)s
              and deleted_at is null
              and status = any(%(statuses)s)
              and (%(project)s::bigint is null or project_id = %(project)s)
              and (not %(paged)s
                   or (coalesce(due_date, 'infinity'::date), id)
                      > (coalesce(%(after_due)s::date, 'infinity'::date), %(after_id)s))
            order by coalesce(due_date, 'infinity'::date), id
            limit %(limit)s
            """,  # noqa: S608 — chỉ chèn hằng danh sách cột
            {
                "assignee": assignee_id,
                "statuses": list(statuses),
                "project": project_id,
                "paged": after is not None,
                "after_due": after_due,
                "after_id": after_id,
                "limit": limit,
            },
        ).fetchall()


def lock(conn: Connection[Any], task_id: int) -> TaskRow | None:
    """Khóa dòng task trong giao dịch hiện tại để đổi trạng thái tuần tự."""
    with conn.cursor(row_factory=class_row(TaskRow)) as cur:
        return cur.execute(
            f"select {_TASK_COLUMNS} from tasks"  # noqa: S608 — hằng danh sách cột
            " where id = %s and deleted_at is null for update",
            (task_id,),
        ).fetchone()


def get(conn: Connection[Any], task_id: int) -> TaskRow | None:
    with conn.cursor(row_factory=class_row(TaskRow)) as cur:
        return cur.execute(
            f"select {_TASK_COLUMNS} from tasks"  # noqa: S608 — hằng danh sách cột
            " where id = %s and deleted_at is null",
            (task_id,),
        ).fetchone()


def set_status(
    conn: Connection[Any],
    task_id: int,
    from_status: str,
    to_status: str,
    by_user_id: int,
    note: str | None,
) -> None:
    conn.execute(
        "update tasks set status = %s, updated_at = now() where id = %s", (to_status, task_id)
    )
    conn.execute(
        """
        insert into task_events (task_id, from_status, to_status, by_user_id, note)
        values (%s, %s, %s, %s, %s)
        """,
        (task_id, from_status, to_status, by_user_id, note),
    )


def insert_activity(
    conn: Connection[Any],
    user_id: int,
    project_id: int,
    task_id: int | None,
    summary: str,
    source: str,
) -> ActivityRow:
    with conn.cursor(row_factory=class_row(ActivityRow)) as cur:
        row = cur.execute(
            """
            insert into activities (user_id, project_id, task_id, summary, source)
            values (%s, %s, %s, %s, %s)
            returning id, at
            """,
            (user_id, project_id, task_id, summary, source),
        ).fetchone()
    if row is None:  # insert ... returning luôn trả một dòng
        raise RuntimeError("insert into activities returned no row")
    return row
