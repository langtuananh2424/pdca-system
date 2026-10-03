"""Truy vấn hỏi cấp trên (LLD 2.2 V6, 4.2; FR-ASK)."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row


@dataclass(frozen=True, slots=True)
class OrgNode:
    """Một người trong chuỗi quản lý, đủ để quyết định có chọn làm người nhận không."""

    user_id: int
    role: str
    name: str
    manager_id: int | None
    active: bool  # status = 'active' và chưa xóa mềm


@dataclass(frozen=True, slots=True)
class Question:
    id: int
    asker_id: int
    recipient_id: int
    project_id: int | None
    task_id: int | None
    body: str
    status: str
    decline_reason: str | None
    due_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QuestionRow:
    """Dòng hiển thị của `get_my_questions`: câu hỏi + tên hai bên + câu trả lời đã gửi."""

    id: int
    asker_name: str
    recipient_name: str
    project_id: int | None
    task_id: int | None
    body: str
    status: str
    decline_reason: str | None
    due_at: datetime
    created_at: datetime
    answer_body: str | None
    answer_sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    """Giờ làm việc / nghỉ phép của người nhận tin (FR-NTF-04)."""

    name: str
    timezone: str
    work_start: time
    work_end: time
    away_until: date | None


@dataclass(frozen=True, slots=True)
class Expired:
    question_id: int
    asker_id: int
    recipient_id: int


_QUESTION_COLUMNS = (
    "id, asker_id, recipient_id, project_id, task_id, body, status, decline_reason,"
    " due_at, created_at"
)


def org_node(conn: Connection[Any], user_id: int) -> OrgNode | None:
    """Hồ sơ tổ chức của một người, kể cả người đã khóa/xóa (chuỗi vẫn đi qua họ)."""
    with conn.cursor(row_factory=class_row(OrgNode)) as cur:
        return cur.execute(
            """
            select id as user_id, role, name, manager_id,
                   (status = 'active' and deleted_at is null) as active
            from users where id = %s
            """,
            (user_id,),
        ).fetchone()


def active_directors(conn: Connection[Any], exclude_user_id: int) -> list[OrgNode]:
    with conn.cursor(row_factory=class_row(OrgNode)) as cur:
        return cur.execute(
            """
            select id as user_id, role, name, manager_id, true as active
            from users
            where role = 'director' and status = 'active' and deleted_at is null and id <> %s
            order by id
            """,
            (exclude_user_id,),
        ).fetchall()


def lock_asker(conn: Connection[Any], asker_id: int) -> None:
    """Tuần tự hóa các lần hỏi của một người để đếm hạn mức không bị vượt khi chạy song song."""
    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"question_ask:{asker_id}",)
    )


def count_open(conn: Connection[Any], asker_id: int) -> int:
    row = conn.execute(
        "select count(*) from questions where asker_id = %s and status = 'open'", (asker_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def count_asked_today(conn: Connection[Any], asker_id: int) -> int:
    """Số câu hỏi đã gửi trong ngày địa phương (theo `users.timezone`) của người hỏi."""
    row = conn.execute(
        """
        select count(*) from questions q join users u on u.id = q.asker_id
        where q.asker_id = %s
          and q.created_at
              >= date_trunc('day', now() at time zone u.timezone) at time zone u.timezone
        """,
        (asker_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def task_assignee_and_project(conn: Connection[Any], task_id: int) -> tuple[int, int] | None:
    row = conn.execute(
        "select assignee_id, project_id from tasks where id = %s and deleted_at is null",
        (task_id,),
    ).fetchone()
    return None if row is None else (int(row[0]), int(row[1]))


def insert(
    conn: Connection[Any],
    *,
    asker_id: int,
    recipient_id: int,
    project_id: int | None,
    task_id: int | None,
    body: str,
    ttl_days: int,
) -> Question:
    with conn.cursor(row_factory=class_row(Question)) as cur:
        row = cur.execute(
            f"""
            insert into questions (asker_id, recipient_id, project_id, task_id, body, due_at)
            values (%s, %s, %s, %s, %s, now() + make_interval(days => %s))
            returning {_QUESTION_COLUMNS}
            """,  # noqa: S608 — cột là hằng số của module
            (asker_id, recipient_id, project_id, task_id, body, ttl_days),
        ).fetchone()
    if row is None:  # insert ... returning luôn trả một dòng
        raise RuntimeError("insert into questions returned no row")
    return row


def lock(conn: Connection[Any], question_id: int) -> Question | None:
    with conn.cursor(row_factory=class_row(Question)) as cur:
        return cur.execute(
            f"select {_QUESTION_COLUMNS} from questions where id = %s for update",  # noqa: S608
            (question_id,),
        ).fetchone()


def set_status(
    conn: Connection[Any], question_id: int, status: str, decline_reason: str | None = None
) -> None:
    conn.execute(
        "update questions set status = %s, decline_reason = %s, updated_at = now() where id = %s",
        (status, decline_reason, question_id),
    )


def insert_sent_answer(conn: Connection[Any], question_id: int, author_id: int, body: str) -> int:
    row = conn.execute(
        """
        insert into question_answers (question_id, author_id, status, body, sent_at)
        values (%s, %s, 'sent', %s, now()) returning id
        """,
        (question_id, author_id, body),
    ).fetchone()
    if row is None:
        raise RuntimeError("insert into question_answers returned no row")
    return int(row[0])


def list_for(
    conn: Connection[Any],
    user_id: int,
    *,
    side: str,
    status: str | None,
    after_id: int | None,
    limit: int,
) -> list[QuestionRow]:
    """Câu hỏi người dùng đã hỏi (`asker`) hoặc nhận được (`recipient`), mới nhất trước.

    Chỉ trả câu trả lời `sent`; bản nháp `draft_by_agent` (P2) không bao giờ lọt vào đây.
    """
    column = "asker_id" if side == "asker" else "recipient_id"
    with conn.cursor(row_factory=class_row(QuestionRow)) as cur:
        return cur.execute(
            f"""
            select q.id, a.name as asker_name, r.name as recipient_name, q.project_id,
                   q.task_id, q.body, q.status, q.decline_reason, q.due_at, q.created_at,
                   qa.body as answer_body, qa.sent_at as answer_sent_at
            from questions q
            join users a on a.id = q.asker_id
            join users r on r.id = q.recipient_id
            left join question_answers qa on qa.question_id = q.id and qa.status = 'sent'
            where q.{column} = %(user)s
              and (%(status)s::text is null or q.status = %(status)s)
              and (%(after)s::bigint is null or q.id < %(after)s)
            order by q.id desc
            limit %(limit)s
            """,  # noqa: S608 — `column` chỉ nhận hai giá trị cố định ở trên
            {"user": user_id, "status": status, "after": after_id, "limit": limit},
        ).fetchall()


def delivery_profile(conn: Connection[Any], user_id: int) -> DeliveryProfile | None:
    with conn.cursor(row_factory=class_row(DeliveryProfile)) as cur:
        return cur.execute(
            "select name, timezone, work_start, work_end, away_until from users where id = %s",
            (user_id,),
        ).fetchone()


def expire_overdue(conn: Connection[Any]) -> list[Expired]:
    """`open` quá `due_at` → `expired` (FR-ASK-05). Khóa dòng nên không tranh với `answer`."""
    with conn.cursor(row_factory=class_row(Expired)) as cur:
        return cur.execute(
            """
            update questions set status = 'expired', updated_at = now()
            where id in (
              select id from questions where status = 'open' and due_at < now()
              order by id for update skip locked)
            returning id as question_id, asker_id, recipient_id
            """
        ).fetchall()
