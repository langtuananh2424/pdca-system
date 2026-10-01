"""Truy vấn cho nhắc việc / hỏi tiến độ và hàng đợi gửi tin (LLD 5, SDD 4.11.3).

"Ngày" và "giờ làm việc" tính theo `users.timezone` của từng người tại thời
điểm `now` truyền vào (job chạy theo Asia/Bangkok nhưng người dùng có thể ở
múi giờ khác — LLD 11).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

# Vai trò nhận tin chủ động: nhân viên và trưởng phòng (giám đốc, quản trị không nhận).
RECIPIENT_ROLES = ["staff", "dept_head"]


@dataclass(frozen=True, slots=True)
class Recipient:
    user_id: int
    name: str
    manager_name: str | None
    local_date: date
    nearest_due: date | None


@dataclass(frozen=True, slots=True)
class OpenTask:
    id: int
    title: str
    project_name: str
    due_date: date | None
    status: str


@dataclass(frozen=True, slots=True)
class ProjectNeed:
    project_id: int
    project_name: str
    config: dict[str, Any]
    submitted: bool


@dataclass(frozen=True, slots=True)
class PendingMessage:
    id: int
    user_id: int
    channel: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    dedupe_key: str


_ELIGIBLE = """
    with u as (
      select u.id, u.name, u.timezone, u.work_start, u.work_end, u.away_until,
             m.name as manager_name,
             (%(now)s::timestamptz at time zone u.timezone) as local_ts
      from users u
      left join users m on m.id = u.manager_id and m.deleted_at is null
      where u.status = 'active' and u.deleted_at is null and u.role = any(%(roles)s)
    )
    select u.id as user_id, u.name, u.manager_name, u.local_ts::date as local_date,
           (select min(t.due_date) from tasks t
             where t.assignee_id = u.id and t.deleted_at is null
               and t.status in ('todo', 'in_progress', 'blocked')) as nearest_due
    from u
    where exists (
            select 1 from project_members pm join projects p on p.id = pm.project_id
            where pm.user_id = u.id and p.deleted_at is null and p.status = 'active')
      and (u.away_until is null or u.away_until < u.local_ts::date)
      and u.local_ts::time between u.work_start and u.work_end
      and (select count(*) from outbound_messages o
            where o.user_id = u.id
              and o.payload->>'local_date' = (u.local_ts::date)::text)
          < %(cap)s  -- FR-NTF-04: hạn mức tin/người/ngày (ngày địa phương của tin)
"""


def eligible_recipients(conn: Connection[Any], now: datetime, cap: int) -> list[Recipient]:
    """SDD 4.11.3: active, có project đang chạy, không nghỉ, trong giờ làm, chưa quá hạn mức."""
    with conn.cursor(row_factory=class_row(Recipient)) as cur:
        return cur.execute(
            _ELIGIBLE + " order by nearest_due nulls last, u.id",
            {"now": now, "roles": RECIPIENT_ROLES, "cap": cap},
        ).fetchall()


def open_tasks(conn: Connection[Any], user_id: int, limit: int) -> list[OpenTask]:
    with conn.cursor(row_factory=class_row(OpenTask)) as cur:
        return cur.execute(
            """
            select t.id, t.title, p.name as project_name, t.due_date, t.status
            from tasks t join projects p on p.id = t.project_id
            where t.assignee_id = %s and t.deleted_at is null and p.deleted_at is null
              and t.status in ('todo', 'in_progress', 'blocked')
            order by coalesce(t.due_date, 'infinity'::date), t.id
            limit %s
            """,
            (user_id, limit),
        ).fetchall()


def project_needs(conn: Connection[Any], user_id: int, day: date) -> list[ProjectNeed]:
    """Project đang chạy của người dùng và đã có báo cáo `submitted` cho ngày `day` chưa."""
    with conn.cursor(row_factory=class_row(ProjectNeed)) as cur:
        return cur.execute(
            """
            select p.id as project_id, p.name as project_name, p.config,
                   exists (select 1 from reports r
                           where r.user_id = %(user)s and r.project_id = p.id
                             and r.report_date = %(day)s and r.status = 'submitted') as submitted
            from project_members pm join projects p on p.id = pm.project_id
            where pm.user_id = %(user)s and p.deleted_at is null and p.status = 'active'
            order by p.name, p.id
            """,
            {"user": user_id, "day": day},
        ).fetchall()


def enqueue(
    conn: Connection[Any],
    *,
    user_id: int,
    channel: str,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> int | None:
    """Xếp hàng tin; trả None nếu `dedupe_key` đã có (chạy lại job không gửi trùng)."""
    row = conn.execute(
        """
        insert into outbound_messages (user_id, channel, kind, payload, dedupe_key)
        values (%s, %s, %s, %s, %s)
        on conflict (dedupe_key) do nothing
        returning id
        """,
        (user_id, channel, kind, Jsonb(payload), dedupe_key),
    ).fetchone()
    return None if row is None else int(row[0])


def asked_users_on(conn: Connection[Any], local_date: date, *, unanswered: bool) -> list[int]:
    """Người đã được hỏi tiến độ cho ngày `local_date` (tin đã gửi; `unanswered`: chưa trả lời).

    Ngày lấy từ `dedupe_key` (`progress_ask:{user}:{ngày}`, LLD 5.3), không từ `created_at`.
    """
    statuses = ["sent"] if unanswered else ["sent", "replied"]
    rows = conn.execute(
        "select distinct user_id from outbound_messages"
        " where kind = 'progress_ask' and status = any(%s) and dedupe_key like %s"
        " order by user_id",
        (statuses, f"progress_ask:%:{local_date.isoformat()}"),
    ).fetchall()
    return [int(r[0]) for r in rows]


def insert_not_reported(conn: Connection[Any], user_id: int, project_id: int, day: date) -> bool:
    """FR-NTF-05/T-05: bản ghi rỗng `not_reported`; không đè bản nháp hay báo cáo đã có."""
    row = conn.execute(
        """
        insert into reports (user_id, project_id, report_date, source, status)
        values (%s, %s, %s, 'system', 'not_reported')
        on conflict (user_id, project_id, report_date) do nothing
        returning id
        """,
        (user_id, project_id, day),
    ).fetchone()
    return row is not None


def claim_pending(
    conn: Connection[Any], channels: list[str], max_attempts: int, limit: int
) -> list[PendingMessage]:
    """Lấy tin `queued`/`failed` còn lượt thử của các kênh đang cấu hình; khóa dòng để
    hai bộ gửi chạy song song không gửi trùng."""
    with conn.cursor(row_factory=class_row(PendingMessage)) as cur:
        return cur.execute(
            """
            select id, user_id, channel, kind, payload, attempts, dedupe_key
            from outbound_messages
            where status in ('queued', 'failed') and attempts < %s and channel = any(%s)
            order by id
            limit %s
            for update skip locked
            """,
            (max_attempts, channels, limit),
        ).fetchall()


def recipient_address(conn: Connection[Any], user_id: int) -> tuple[str, str] | None:
    row = conn.execute(
        "select name, email from users where id = %s and status = 'active' and deleted_at is null",
        (user_id,),
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


def mark_sent(conn: Connection[Any], message_id: int, external_id: str | None) -> None:
    conn.execute(
        "update outbound_messages set status = 'sent', attempts = attempts + 1,"
        " external_id = %s, sent_at = now() where id = %s",
        (external_id, message_id),
    )


def mark_failed(conn: Connection[Any], message_id: int, *, dead: bool) -> None:
    conn.execute(
        "update outbound_messages set status = %s, attempts = attempts + 1 where id = %s",
        ("dead" if dead else "failed", message_id),
    )
