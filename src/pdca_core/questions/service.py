"""Nghiệp vụ hỏi cấp trên (UC-18, FR-ASK; LLD 4.2, 5.10).

Bản P1: người nhận tự viết câu trả lời; chưa có agent soạn nháp (FR-ASK-09, P2).
Người nhận luôn do `resolve_recipient` chọn từ cơ cấu tổ chức, không bao giờ từ
tham số (FR-ASK-02, bất biến 3). Câu trả lời chỉ tới người hỏi khi người nhận tự gọi
`answer` (FR-ASK-04); nội dung câu hỏi/trả lời không có trong tin báo hay audit (FR-ASK-08).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.policy import Action, Resource, require, require_project_member
from pdca_core.errors import Conflict, ForbiddenOrNotFound, InvalidArgument, RateLimited
from pdca_core.outreach.compose import Draft
from pdca_core.questions import notices
from pdca_core.questions.delivery import next_delivery
from pdca_core.repositories import outreach as outbox_repo
from pdca_core.repositories import questions as repo
from pdca_core.repositories.questions import DeliveryProfile, OrgNode, QuestionRow
from pdca_core.validation import (
    choice,
    clip_text,
    decode_cursor,
    encode_cursor,
    page_limit,
    require_text,
)

QUESTION_STATUSES = ("open", "answered", "declined", "expired", "cancelled")
SIDES = ("asker", "recipient")
ANSWER_ACTIONS = ("send", "decline")
MAX_CHAIN_STEPS = 3  # LLD 4.2: số bước tối đa đi theo `manager_id`
RECIPIENT_ROLES = ("dept_head", "director")


@dataclass(frozen=True)
class QuestionSettings:
    channel: str
    ttl_days: int = 3
    max_open: int = 5
    max_per_day: int = 10


def pick_recipient(chain: Sequence[OrgNode], asker_id: int) -> OrgNode | None:
    """Chọn người nhận (LLD 4.2 `resolve_recipient`, FR-ASK-02, OI-11).

    `chain`: các cấp trên theo `manager_id`, gần nhất trước (đã cắt ở `MAX_CHAIN_STEPS`).
    1) Người đầu tiên đang hoạt động, vai trò trưởng phòng/giám đốc. Trưởng phòng trực thuộc
       còn hoạt động thì dừng ngay; vị trí đó trống (khóa/xóa/không có) thì đi tiếp lên cấp
       kế tiếp — hỏi vượt cấp. Nghỉ phép (`away_until`) không làm vị trí trống.
    2) Chuỗi đứt (không ai đủ điều kiện trong `chain`): `None`. Không tìm người nhận ngoài chuỗi
       quản lý của người hỏi (FR-ASK-02); quản trị phải cấu hình `manager_id`.
    """
    for node in chain:
        if node.user_id != asker_id and node.active and node.role in RECIPIENT_ROLES:
            return node
    return None


def resolve_recipient(conn: Connection[Any], asker_id: int) -> OrgNode:
    chain: list[OrgNode] = []
    seen = {asker_id}
    current = repo.org_node(conn, asker_id)
    while current is not None and current.manager_id is not None and len(chain) < MAX_CHAIN_STEPS:
        if current.manager_id in seen:  # vòng quản lý do dữ liệu sai: dừng
            break
        seen.add(current.manager_id)
        current = repo.org_node(conn, current.manager_id)
        if current is not None:
            chain.append(current)
    recipient = pick_recipient(chain, asker_id)
    if recipient is None:
        raise InvalidArgument("no recipient available for this question")
    return recipient


def ask(
    ctx: UserContext,
    pool: ConnectionPool,
    settings: QuestionSettings,
    *,
    body: str,
    project_id: int | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Gửi câu hỏi tới cấp trên do hệ thống chọn (FR-ASK-01, FR-ASK-02, FR-ASK-07)."""
    require(ctx, Action.QUESTION_ASK, Resource(owner_id=ctx.user_id))
    text = require_text("body", body)
    if task_id is not None and project_id is None:
        raise InvalidArgument("task_id requires project_id")
    if project_id is not None:
        require_project_member(ctx, project_id)

    with pool.connection() as conn, conn.transaction():
        if task_id is not None:
            found = repo.task_assignee_and_project(conn, task_id)
            if found is None or found != (ctx.user_id, project_id):
                raise ForbiddenOrNotFound()
        repo.lock_asker(conn, ctx.user_id)
        if (
            repo.count_open(conn, ctx.user_id) >= settings.max_open
            or repo.count_asked_today(conn, ctx.user_id) >= settings.max_per_day
        ):
            raise RateLimited()
        recipient = resolve_recipient(conn, ctx.user_id)
        asker = _profile(conn, ctx.user_id)
        question = repo.insert(
            conn,
            asker_id=ctx.user_id,
            recipient_id=recipient.user_id,
            project_id=project_id,
            task_id=task_id,
            body=text,
            ttl_days=settings.ttl_days,
        )
        _notify(
            conn,
            settings,
            user_id=recipient.user_id,
            kind="question_notice",
            dedupe_key=f"question_notice:{question.id}",
            draft=notices.question_notice(
                recipient_name=recipient.name, asker_name=asker.name, question_id=question.id
            ),
        )

    return {
        "question_id": question.id,
        "recipient_name": recipient.name,
        "status": question.status,
        "due_at": question.due_at.isoformat(),
    }


def list_mine(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    side: str,
    status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Câu hỏi của chính người gọi (FR-ASK-03): chỉ phía người hỏi hoặc người nhận."""
    require(ctx, Action.QUESTION_READ_OWN, Resource(owner_id=ctx.user_id))
    choice("side", side, SIDES)
    if status is not None:
        choice("status", status, QUESTION_STATUSES)
    size = page_limit(limit)
    after = _decode_cursor(cursor) if cursor else None

    with pool.connection() as conn:
        rows = repo.list_for(
            conn, ctx.user_id, side=side, status=status, after_id=after, limit=size + 1
        )

    page = rows[:size]
    next_cursor = encode_cursor([page[-1].id]) if len(rows) > size else None
    return {"items": [_row_dict(r) for r in page], "next_cursor": next_cursor}


def answer(
    ctx: UserContext,
    pool: ConnectionPool,
    settings: QuestionSettings,
    *,
    question_id: int,
    action: str,
    body: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Người nhận gửi trả lời hoặc từ chối (FR-ASK-04). Gọi sau khi người dùng đã xác nhận."""
    choice("action", action, ANSWER_ACTIONS)
    text = require_text("body", body) if action == "send" else None
    why = require_text("reason", reason) if action == "decline" else None

    with pool.connection() as conn, conn.transaction():
        question = repo.lock(conn, question_id)
        if question is None:
            raise ForbiddenOrNotFound()
        require(ctx, Action.QUESTION_ANSWER, Resource(owner_id=question.recipient_id))
        if question.status != "open":
            raise Conflict(f"question is {question.status}")
        if text is not None:  # action == "send"
            repo.insert_sent_answer(conn, question.id, ctx.user_id, text)
            repo.set_status(conn, question.id, "answered")
            outcome = "answered"
        else:
            repo.set_status(conn, question.id, "declined", why)
            outcome = "declined"
        _notify_outcome(
            conn, settings, question.id, question.asker_id, question.recipient_id, outcome
        )

    return {"question_id": question.id, "status": outcome}


def cancel(ctx: UserContext, pool: ConnectionPool, *, question_id: int) -> dict[str, Any]:
    """Người hỏi rút câu hỏi khi còn `open` (UC-18 luồng thay thế 1a)."""
    with pool.connection() as conn, conn.transaction():
        question = repo.lock(conn, question_id)
        if question is None:
            raise ForbiddenOrNotFound()
        require(ctx, Action.QUESTION_ASK, Resource(owner_id=question.asker_id))
        if question.status != "open":
            raise Conflict(f"question is {question.status}")
        repo.set_status(conn, question.id, "cancelled")
    return {"question_id": question.id, "status": "cancelled"}


def expire_overdue(pool: ConnectionPool, settings: QuestionSettings) -> list[int]:
    """Job `question_expire` (LLD 5.10): `open` quá hạn → `expired`, báo người hỏi.

    Không sinh câu trả lời thay người nhận (FR-ASK-05). Chạy lại không báo trùng nhờ `dedupe_key`.
    """
    with pool.connection() as conn, conn.transaction():
        expired = repo.expire_overdue(conn)
        for item in expired:
            _notify_outcome(
                conn, settings, item.question_id, item.asker_id, item.recipient_id, "expired"
            )
    return [item.question_id for item in expired]


def _profile(conn: Connection[Any], user_id: int) -> DeliveryProfile:
    profile = repo.delivery_profile(conn, user_id)
    if profile is None:  # khóa ngoại đảm bảo người dùng tồn tại
        raise RuntimeError(f"user {user_id} not found")
    return profile


def _notify_outcome(
    conn: Connection[Any],
    settings: QuestionSettings,
    question_id: int,
    asker_id: int,
    recipient_id: int,
    outcome: str,
) -> None:
    asker = _profile(conn, asker_id)
    recipient = _profile(conn, recipient_id)
    _notify(
        conn,
        settings,
        user_id=asker_id,
        kind="answer_notice",
        dedupe_key=f"answer_notice:{question_id}",
        draft=notices.answer_notice(
            asker_name=asker.name,
            recipient_name=recipient.name,
            question_id=question_id,
            outcome=outcome,
        ),
    )


def _notify(
    conn: Connection[Any],
    settings: QuestionSettings,
    *,
    user_id: int,
    kind: str,
    dedupe_key: str,
    draft: Draft,
) -> None:
    """Xếp tin báo; hoãn tới đầu giờ làm nếu người nhận đang ngoài giờ hoặc nghỉ phép."""
    profile = _profile(conn, user_id)
    deliver_at = next_delivery(
        datetime.now(UTC),
        profile.timezone,
        profile.work_start,
        profile.work_end,
        profile.away_until,
    )
    outbox_repo.enqueue(
        conn,
        user_id=user_id,
        channel=settings.channel,
        kind=kind,
        payload={"subject": draft.subject, "body": draft.body},
        dedupe_key=dedupe_key,
        next_attempt_at=deliver_at,
    )


def _row_dict(row: QuestionRow) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row.id,
        "asker_name": row.asker_name,
        "recipient_name": row.recipient_name,
        "body": clip_text(row.body),
        "project_id": row.project_id,
        "task_id": row.task_id,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "due_at": row.due_at.isoformat(),
    }
    if row.answer_body is not None and row.answer_sent_at is not None:
        item["answer"] = {"body": row.answer_body, "sent_at": row.answer_sent_at.isoformat()}
    if row.decline_reason is not None:
        item["decline_reason"] = row.decline_reason
    return item


def _decode_cursor(cursor: str) -> int:
    match decode_cursor(cursor):
        case [int() as last_id] if not isinstance(last_id, bool):
            return last_id
        case _:
            raise InvalidArgument("invalid cursor")
