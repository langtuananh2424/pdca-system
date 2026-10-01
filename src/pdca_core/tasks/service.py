"""Nghiệp vụ Do: task của người gọi và ghi hoạt động (LLD 4.2, FR-TASK, FR-ACT)."""

from datetime import date
from typing import Any

from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.policy import Action, Resource, require, require_project_member
from pdca_core.errors import Conflict, ForbiddenOrNotFound, InvalidArgument
from pdca_core.repositories import tasks as repo
from pdca_core.tasks.state import OPEN_STATUSES, TASK_STATUSES, can_transition
from pdca_core.validation import (
    choice,
    clip_text,
    decode_cursor,
    encode_cursor,
    page_limit,
    parse_date,
    require_text,
)

# get_my_tasks chỉ lọc theo các trạng thái này (LLD 4.2).
LISTABLE_STATUSES = ("todo", "in_progress", "blocked", "done")
ACTIVITY_SOURCES = ("user", "agent_approved", "hook_approved")


def list_mine(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    status: str | None = None,
    project_id: int | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Task giao cho chính người gọi; mặc định các trạng thái mở."""
    require(ctx, Action.TASK_READ_OWN, Resource(owner_id=ctx.user_id))
    statuses = (choice("status", status, LISTABLE_STATUSES),) if status else OPEN_STATUSES
    size = page_limit(limit)
    after = _decode_task_cursor(cursor) if cursor else None

    with pool.connection() as conn:
        rows = repo.list_for_assignee(conn, ctx.user_id, statuses, project_id, after, size + 1)

    page = rows[:size]
    next_cursor = None
    if len(rows) > size:
        last = page[-1]
        next_cursor = encode_cursor([last.due_date.isoformat() if last.due_date else None, last.id])
    return {
        "items": [
            {
                "id": t.id,
                "title": t.title,
                "project_id": t.project_id,
                "plan_id": t.plan_id,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "status": t.status,
            }
            for t in page
        ],
        "next_cursor": next_cursor,
    }


def update_status(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    task_id: int,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Đổi trạng thái task của chính mình theo SDD 4.10; ghi `task_events`."""
    to_status = choice("status", status, TASK_STATUSES)
    clean_note = clip_text(note.strip()) if note and note.strip() else None

    with pool.connection() as conn, conn.transaction():
        task = repo.lock(conn, task_id)
        if task is None:
            raise ForbiddenOrNotFound()
        require(ctx, Action.TASK_UPDATE_OWN, Resource(owner_id=task.assignee_id))
        if not can_transition(task.status, to_status):
            raise Conflict(f"cannot change status from {task.status} to {to_status}")
        repo.set_status(conn, task.id, task.status, to_status, ctx.user_id, clean_note)

    return {"task_id": task.id, "from_status": task.status, "status": to_status}


def log_activity(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    project_id: int,
    summary: str,
    task_id: int | None = None,
    source: str = "user",
) -> dict[str, Any]:
    """Ghi hoạt động người dùng đã đồng ý ghi (FR-ACT). Quyền: thành viên project.

    `source` do nơi gọi quyết định (MCP luôn là `user`), không lấy từ model.
    """
    require_project_member(ctx, project_id)
    choice("source", source, ACTIVITY_SOURCES)
    text = require_text("summary", summary)

    with pool.connection() as conn:
        if task_id is not None:
            task = repo.get(conn, task_id)
            if task is None or task.project_id != project_id:
                raise ForbiddenOrNotFound()
        activity = repo.insert_activity(conn, ctx.user_id, project_id, task_id, text, source)

    return {"activity_id": activity.id, "at": activity.at.isoformat()}


def _decode_task_cursor(cursor: str) -> tuple[date | None, int]:
    key = decode_cursor(cursor)
    match key:
        case [str() | None as due, int() as last_id] if not isinstance(last_id, bool):
            return (parse_date("cursor", due) if due else None, last_id)
        case _:
            raise InvalidArgument("invalid cursor")
