"""Job liên lạc chủ động (LLD 5.1–5.3, SDD 4.11.3, FR-NTF-01..05).

Mỗi job chỉ xếp hàng `outbound_messages` (hoặc ghi báo cáo `not_reported`);
việc gửi do `outbox.send_pending` đảm nhiệm. Chạy lại job không gửi trùng nhờ
`dedupe_key` duy nhất (NFR-REL-03, T-04).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from pdca_core.outreach.compose import Draft, MessageComposer, MessageContext
from pdca_core.repositories import outreach as repo
from pdca_core.repositories.outreach import Recipient

TASKS_PER_MESSAGE = 20

DraftFn = Callable[[MessageContext], Draft]


@dataclass(frozen=True)
class OutreachSettings:
    channel: str
    max_messages_per_day: int = 3


def dedupe_key(kind: str, user_id: int, local_date: date, *, reminder: bool = False) -> str:
    """LLD 5.3: `{kind}:{user_id}:{local_date}`, tin nhắc lại thêm `:r1`."""
    key = f"{kind}:{user_id}:{local_date.isoformat()}"
    return f"{key}:r1" if reminder else key


@dataclass(frozen=True)
class JobResult:
    enqueued: list[int]  # user_id được xếp tin mới
    skipped: list[int]  # user_id đủ điều kiện nhưng không cần/đã có tin

    def detail(self) -> dict[str, Any]:
        return {"enqueued": len(self.enqueued), "skipped": len(self.skipped)}


def morning_nudge(
    pool: ConnectionPool, now: datetime, settings: OutreachSettings, composer: MessageComposer
) -> JobResult:
    """Nhắc việc đầu ngày cho người đủ điều kiện (FR-NTF-02)."""
    return _run(
        pool,
        now,
        settings,
        kind="morning_nudge",
        draft=composer.morning_nudge,
        needs_report=False,
    )


def progress_ask(
    pool: ConnectionPool, now: datetime, settings: OutreachSettings, composer: MessageComposer
) -> JobResult:
    """Hỏi tiến độ cuối ngày; bỏ qua người đã nộp đủ báo cáo mọi project (SDD 4.11.3)."""
    return _run(
        pool,
        now,
        settings,
        kind="progress_ask",
        draft=composer.progress_ask,
        needs_report=True,
    )


def progress_remind(
    pool: ConnectionPool, now: datetime, settings: OutreachSettings, composer: MessageComposer
) -> JobResult:
    """Nhắc lại đúng một lần cho người đã được hỏi nhưng chưa trả lời (FR-NTF-05)."""
    return _run(
        pool,
        now,
        settings,
        kind="progress_ask",
        draft=composer.progress_remind,
        needs_report=True,
        reminder=True,
    )


def mark_not_reported(pool: ConnectionPool, local_date: date) -> JobResult:
    """FR-NTF-05, T-05: người đã được hỏi mà vẫn chưa có báo cáo → `not_reported`, nội dung rỗng.

    Không đè bản nháp `draft_by_agent` hay báo cáo đã nộp; không suy đoán nội dung.
    """
    marked: list[int] = []
    skipped: list[int] = []
    with pool.connection() as conn:
        for user_id in repo.asked_users_on(conn, local_date, unanswered=False):
            created = [
                repo.insert_not_reported(conn, user_id, p.project_id, local_date)
                for p in repo.project_needs(conn, user_id, local_date)
                if not p.submitted
            ]
            (marked if any(created) else skipped).append(user_id)
    return JobResult(enqueued=marked, skipped=skipped)


def _run(
    pool: ConnectionPool,
    now: datetime,
    settings: OutreachSettings,
    *,
    kind: str,
    draft: DraftFn,
    needs_report: bool,
    reminder: bool = False,
) -> JobResult:
    enqueued: list[int] = []
    skipped: list[int] = []
    with pool.connection() as conn:
        recipients = repo.eligible_recipients(conn, now, settings.max_messages_per_day)
        if reminder:
            # Ngày địa phương có thể khác nhau giữa người dùng ở múi giờ khác nhau.
            asked: dict[date, set[int]] = {}
            for day in {r.local_date for r in recipients}:
                asked[day] = set(repo.asked_users_on(conn, day, unanswered=True))
            recipients = [r for r in recipients if r.user_id in asked[r.local_date]]
        for recipient in recipients:
            message_id = _enqueue_one(
                conn, recipient, settings, kind, draft, needs_report, reminder
            )
            (enqueued if message_id is not None else skipped).append(recipient.user_id)
    return JobResult(enqueued=enqueued, skipped=skipped)


def _enqueue_one(
    conn: Connection[Any],
    recipient: Recipient,
    settings: OutreachSettings,
    kind: str,
    draft: DraftFn,
    needs_report: bool,
    reminder: bool,
) -> int | None:
    projects = repo.project_needs(conn, recipient.user_id, recipient.local_date)
    if needs_report and all(p.submitted for p in projects):
        return None
    ctx = MessageContext(
        name=recipient.name,
        manager_name=recipient.manager_name,
        local_date=recipient.local_date,
        tasks=repo.open_tasks(conn, recipient.user_id, TASKS_PER_MESSAGE),
        projects=projects,
    )
    content = draft(ctx)
    return repo.enqueue(
        conn,
        user_id=recipient.user_id,
        channel=settings.channel,
        kind="reminder" if reminder else kind,
        payload={
            "subject": content.subject,
            "body": content.body,
            "local_date": recipient.local_date.isoformat(),
            "project_ids": [p.project_id for p in projects if not p.submitted],
        },
        dedupe_key=dedupe_key(kind, recipient.user_id, recipient.local_date, reminder=reminder),
    )
