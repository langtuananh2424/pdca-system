"""Truy cập `reports`, `blockers` và dữ liệu trong ngày cho chốt ngày (LLD 2.2, 4.2).

Ranh giới "một ngày" tính theo `users.timezone` ngay trong Postgres.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row


@dataclass(frozen=True, slots=True)
class ReportRow:
    id: int
    project_id: int
    report_date: date
    status: str
    source: str
    done: str
    blockers: str
    schedule_conflicts: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BlockerRow:
    report_id: int
    kind: str
    severity: str
    text: str


@dataclass(frozen=True, slots=True)
class BlockerItem:
    kind: str
    severity: str
    text: str


@dataclass(frozen=True, slots=True)
class TaskEventRow:
    task_id: int
    title: str
    from_status: str | None
    to_status: str
    note: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class ActivityRow:
    id: int
    project_id: int
    task_id: int | None
    summary: str
    at: datetime


_REPORT_COLUMNS = (
    "id, project_id, report_date, status, source, done, blockers, schedule_conflicts, updated_at"
)

# Khoảng [đầu ngày, đầu ngày hôm sau) theo múi giờ của người dùng.
_DAY_WINDOW = """
    >= (%(day)s::date)::timestamp at time zone u.timezone
    and {col} < (%(day)s::date + 1)::timestamp at time zone u.timezone
"""


def user_today(conn: Connection[Any], user_id: int) -> date:
    row = conn.execute(
        "select (now() at time zone timezone)::date from users where id = %s", (user_id,)
    ).fetchone()
    if row is None:
        raise LookupError(user_id)
    value: date = row[0]
    return value


def lock_report(
    conn: Connection[Any], user_id: int, project_id: int, report_date: date
) -> ReportRow | None:
    with conn.cursor(row_factory=class_row(ReportRow)) as cur:
        return cur.execute(
            f"select {_REPORT_COLUMNS} from reports"  # noqa: S608 — hằng danh sách cột
            " where user_id = %s and project_id = %s and report_date = %s for update",
            (user_id, project_id, report_date),
        ).fetchone()


def insert_report(
    conn: Connection[Any],
    user_id: int,
    project_id: int,
    report_date: date,
    fields: dict[str, str],
) -> int | None:
    """Trả id, hoặc None nếu bị tranh chấp với một bản ghi vừa tạo (FR-CHK-03)."""
    row = conn.execute(
        """
        insert into reports (user_id, project_id, report_date, done, blockers,
                             schedule_conflicts, raw_text_approved, source, status)
        values (%(user)s, %(project)s, %(day)s, %(done)s, %(blockers)s,
                %(schedule_conflicts)s, %(raw)s, 'claude_code', 'submitted')
        on conflict (user_id, project_id, report_date) do nothing
        returning id
        """,
        {"user": user_id, "project": project_id, "day": report_date, **fields},
    ).fetchone()
    return None if row is None else int(row[0])


def update_report(conn: Connection[Any], report_id: int, fields: dict[str, str]) -> None:
    conn.execute(
        """
        update reports
        set done = %(done)s, blockers = %(blockers)s,
            schedule_conflicts = %(schedule_conflicts)s, raw_text_approved = %(raw)s,
            source = 'claude_code', status = 'submitted', confidence = null,
            updated_at = now()
        where id = %(id)s
        """,
        {"id": report_id, **fields},
    )


def retire_blockers(conn: Connection[Any], report_id: int) -> None:
    conn.execute(
        "update blockers set deleted_at = now() where report_id = %s and deleted_at is null",
        (report_id,),
    )


def add_blockers(conn: Connection[Any], report_id: int, items: Sequence[BlockerItem]) -> None:
    if not items:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "insert into blockers (report_id, kind, severity, text) values (%s, %s, %s, %s)",
            [(report_id, i.kind, i.severity, i.text) for i in items],
        )


def list_reports(
    conn: Connection[Any],
    user_id: int,
    from_date: date,
    to_date: date,
    project_id: int | None,
    limit: int,
) -> list[ReportRow]:
    with conn.cursor(row_factory=class_row(ReportRow)) as cur:
        return cur.execute(
            f"""
            select {_REPORT_COLUMNS} from reports
            where user_id = %(user)s
              and report_date between %(from)s and %(to)s
              and (%(project)s::bigint is null or project_id = %(project)s)
            order by report_date desc, project_id
            limit %(limit)s
            """,  # noqa: S608 — hằng danh sách cột
            {
                "user": user_id,
                "from": from_date,
                "to": to_date,
                "project": project_id,
                "limit": limit,
            },
        ).fetchall()


def blockers_for(conn: Connection[Any], report_ids: Sequence[int]) -> list[BlockerRow]:
    if not report_ids:
        return []
    with conn.cursor(row_factory=class_row(BlockerRow)) as cur:
        return cur.execute(
            """
            select report_id, kind, severity, text from blockers
            where report_id = any(%s) and deleted_at is null
            order by report_id, id
            """,
            (list(report_ids),),
        ).fetchall()


def task_events_on(
    conn: Connection[Any], user_id: int, day: date, limit: int
) -> list[TaskEventRow]:
    """Thay đổi trạng thái task do chính người dùng thực hiện trong ngày."""
    with conn.cursor(row_factory=class_row(TaskEventRow)) as cur:
        return cur.execute(
            f"""
            select e.task_id, t.title, e.from_status, e.to_status, e.note, e.at
            from task_events e
            join tasks t on t.id = e.task_id
            join users u on u.id = e.by_user_id
            where e.by_user_id = %(user)s and t.deleted_at is null
              and e.at {_DAY_WINDOW.format(col="e.at")}
            order by e.at, e.id
            limit %(limit)s
            """,  # noqa: S608 — chỉ chèn hằng
            {"user": user_id, "day": day, "limit": limit},
        ).fetchall()


def activities_on(conn: Connection[Any], user_id: int, day: date, limit: int) -> list[ActivityRow]:
    with conn.cursor(row_factory=class_row(ActivityRow)) as cur:
        return cur.execute(
            f"""
            select a.id, a.project_id, a.task_id, a.summary, a.at
            from activities a
            join users u on u.id = a.user_id
            where a.user_id = %(user)s
              and a.at {_DAY_WINDOW.format(col="a.at")}
            order by a.at, a.id
            limit %(limit)s
            """,  # noqa: S608 — chỉ chèn hằng
            {"user": user_id, "day": day, "limit": limit},
        ).fetchall()
