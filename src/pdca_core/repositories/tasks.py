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
class ProjectTaskRow:
    id: int
    title: str
    plan_id: int | None
    due_date: date | None
    status: str
    assignee_id: int
    assignee_name: str
    updated_at: datetime


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


def list_for_project(
    conn: Connection[Any],
    project_id: int,
    statuses: Sequence[str],
    assignee_id: int | None,
    after: tuple[date | None, int] | None,
    limit: int,
) -> list[ProjectTaskRow]:
    """Task của một project (mọi người thực hiện); cùng thứ tự và khóa phân trang với
    `list_for_assignee`."""
    after_due, after_id = after if after is not None else (None, 0)
    with conn.cursor(row_factory=class_row(ProjectTaskRow)) as cur:
        return cur.execute(
            """
            select t.id, t.title, t.plan_id, t.due_date, t.status, t.assignee_id,
                   u.name as assignee_name, t.updated_at
            from tasks t
            join users u on u.id = t.assignee_id
            where t.project_id = %(project)s
              and t.deleted_at is null
              and t.status = any(%(statuses)s)
              and (%(assignee)s::bigint is null or t.assignee_id = %(assignee)s)
              and (not %(paged)s
                   or (coalesce(t.due_date, 'infinity'::date), t.id)
                      > (coalesce(%(after_due)s::date, 'infinity'::date), %(after_id)s))
            order by coalesce(t.due_date, 'infinity'::date), t.id
            limit %(limit)s
            """,
            {
                "project": project_id,
                "statuses": list(statuses),
                "assignee": assignee_id,
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


def insert_task(
    conn: Connection[Any],
    *,
    project_id: int,
    plan_id: int | None,
    assignee_id: int,
    created_by: int,
    title: str,
    detail: str | None,
    due_date: date | None,
) -> int:
    """Tạo task `todo` và ghi sự kiện tạo vào `task_events` (FR-TASK-01, FR-TASK-05)."""
    row = conn.execute(
        """
        insert into tasks (project_id, plan_id, assignee_id, created_by, title, detail, due_date)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (project_id, plan_id, assignee_id, created_by, title, detail, due_date),
    ).fetchone()
    if row is None:
        raise RuntimeError("insert into tasks returned no row")
    task_id = int(row[0])
    conn.execute(
        "insert into task_events (task_id, from_status, to_status, by_user_id, note)"
        " values (%s, null, 'todo', %s, 'created')",
        (task_id, created_by),
    )
    return task_id


def reassign(conn: Connection[Any], task: TaskRow, assignee_id: int, by_user_id: int) -> None:
    """Đổi người thực hiện; lịch sử ghi vào `task_events` (trạng thái giữ nguyên)."""
    conn.execute(
        "update tasks set assignee_id = %s, updated_at = now() where id = %s",
        (assignee_id, task.id),
    )
    conn.execute(
        "insert into task_events (task_id, from_status, to_status, by_user_id, note)"
        " values (%s, %s, %s, %s, %s)",
        (
            task.id,
            task.status,
            task.status,
            by_user_id,
            f"reassigned: {task.assignee_id} -> {assignee_id}",
        ),
    )
