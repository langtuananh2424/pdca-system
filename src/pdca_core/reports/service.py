"""Nghiệp vụ Check: dữ liệu chốt ngày, nộp và xem báo cáo của chính mình (LLD 4.2, FR-CHK).

Bất biến 6: `submit` chỉ được gọi sau khi người dùng đã xác nhận bản nháp;
bản nháp do agent soạn (`draft_by_agent`) không bao giờ được trộn vào báo cáo
`submitted` — nộp đè lên bản nháp/`not_reported` luôn ghi nội dung mới.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.policy import Action, Resource, require, require_project_member
from pdca_core.errors import Conflict, InvalidArgument
from pdca_core.repositories import reports as repo
from pdca_core.repositories import tasks as task_repo
from pdca_core.repositories.reports import BlockerItem, ReportRow
from pdca_core.tasks.state import OPEN_STATUSES
from pdca_core.validation import MAX_TEXT, choice, clip_text, parse_date

MODES = ("create", "append", "replace")
BLOCKER_KINDS = ("technical", "people", "external", "schedule", "other")
SEVERITIES = ("low", "medium", "high")
MAX_BLOCKER_ITEMS = 20
MAX_REPORT_RANGE_DAYS = 93
DAY_CONTEXT_ROWS = 100


@dataclass(frozen=True)
class ReportContent:
    done: str = ""
    blockers: str = ""
    schedule_conflicts: str = ""
    items: Sequence[BlockerItem] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not (self.done or self.blockers or self.schedule_conflicts or self.items)


# ---- Logic thuần (unit test) -------------------------------------------------


def parse_blocker_items(raw: Sequence[Mapping[str, Any]] | None) -> list[BlockerItem]:
    """FR-CHK-06: mỗi vướng mắc có loại, mức độ (mặc định medium) và nội dung."""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_BLOCKER_ITEMS:
        raise InvalidArgument(f"blocker_items must be a list of at most {MAX_BLOCKER_ITEMS}")
    items = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise InvalidArgument("each blocker item must be an object")
        kind = choice("blocker_items.kind", str(item.get("kind", "")), BLOCKER_KINDS)
        severity = choice(
            "blocker_items.severity", str(item.get("severity") or "medium"), SEVERITIES
        )
        text = str(item.get("text") or "").strip()
        if not text:
            raise InvalidArgument("blocker_items.text is required")
        items.append(BlockerItem(kind=kind, severity=severity, text=clip_text(text)))
    return items


def decide_action(existing_status: str | None, mode: str) -> str:
    """Trả `created` | `appended` | `replaced`; `conflict` khi `create` trùng báo cáo đã nộp."""
    if existing_status is None:
        return "created"
    if existing_status != "submitted":
        # draft_by_agent / not_reported → submitted (SDD 4.10): ghi nội dung người dùng vừa duyệt.
        return "replaced"
    if mode == "create":
        raise Conflict(
            "a report for this project and date is already submitted;"
            " use mode=append to add or mode=replace to overwrite"
        )
    return "appended" if mode == "append" else "replaced"


def merge_text(name: str, old: str, new: str) -> str:
    merged = f"{old}\n{new}" if old and new else (old or new)
    if len(merged) > MAX_TEXT:
        raise InvalidArgument(
            f"{name} would exceed {MAX_TEXT} characters after append; use mode=replace"
        )
    return merged


def approved_text(content: ReportContent) -> str:
    """Văn bản người dùng đã duyệt, lưu vào `raw_text_approved` (DR-04, NFR-PRV-01)."""
    lines = [f"Đã làm: {content.done}"]
    if content.blockers or content.items:
        lines.append(f"Vướng mắc: {content.blockers}".rstrip())
        lines += [f"- [{i.kind}/{i.severity}] {i.text}" for i in content.items]
    if content.schedule_conflicts:
        lines.append(f"Xung đột lịch: {content.schedule_conflicts}")
    return "\n".join(lines)


def _clean(value: str | None) -> str:
    return clip_text(value.strip()) if value else ""


def _fields(content: ReportContent) -> dict[str, str]:
    return {
        "done": content.done,
        "blockers": content.blockers,
        "schedule_conflicts": content.schedule_conflicts,
        "raw": approved_text(content),
    }


# ---- Nghiệp vụ ---------------------------------------------------------------


def submit(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    project_id: int,
    done: str,
    blockers: str | None = None,
    schedule_conflicts: str | None = None,
    report_date: str | None = None,
    mode: str = "create",
    blocker_items: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Nộp báo cáo ngày (UC-04). Quyền: thành viên project + `report.submit.own`."""
    require_project_member(ctx, project_id)
    require(ctx, Action.REPORT_SUBMIT_OWN, Resource(owner_id=ctx.user_id, project_id=project_id))
    choice("mode", mode, MODES)
    incoming = ReportContent(
        done=_clean(done),
        blockers=_clean(blockers),
        schedule_conflicts=_clean(schedule_conflicts),
        items=parse_blocker_items(blocker_items),
    )
    if incoming.is_empty():
        raise InvalidArgument("report is empty")

    with pool.connection() as conn, conn.transaction():
        today = repo.user_today(conn, ctx.user_id)
        day = parse_date("report_date", report_date) if report_date else today
        if day > today:
            raise InvalidArgument("report_date cannot be in the future")

        existing = repo.lock_report(conn, ctx.user_id, project_id, day)
        action = decide_action(existing.status if existing else None, mode)
        if action != "appended" and not incoming.done:
            raise InvalidArgument("done is required")

        if existing is None:
            report_id = repo.insert_report(conn, ctx.user_id, project_id, day, _fields(incoming))
            if report_id is not None:
                repo.add_blockers(conn, report_id, incoming.items)
                return {"report_id": report_id, "status": "submitted", "action": action}
            # Một lời gọi khác vừa tạo báo cáo cùng (người, project, ngày): xử lý như đã tồn tại.
            existing = repo.lock_report(conn, ctx.user_id, project_id, day)
            if existing is None:
                raise Conflict("report is being modified concurrently; retry")
            action = decide_action(existing.status, mode)

        content = _append(conn, existing, incoming) if action == "appended" else incoming
        if action != "appended":
            repo.retire_blockers(conn, existing.id)
        repo.update_report(conn, existing.id, _fields(content))
        repo.add_blockers(conn, existing.id, incoming.items)

    return {"report_id": existing.id, "status": "submitted", "action": action}


def _append(conn: Connection[Any], existing: ReportRow, incoming: ReportContent) -> ReportContent:
    current_items = [
        BlockerItem(b.kind, b.severity, b.text) for b in repo.blockers_for(conn, [existing.id])
    ]
    if len(current_items) + len(incoming.items) > MAX_BLOCKER_ITEMS:
        raise InvalidArgument(f"a report can have at most {MAX_BLOCKER_ITEMS} blocker items")
    return ReportContent(
        done=merge_text("done", existing.done, incoming.done),
        blockers=merge_text("blockers", existing.blockers, incoming.blockers),
        schedule_conflicts=merge_text(
            "schedule_conflicts", existing.schedule_conflicts, incoming.schedule_conflicts
        ),
        items=[*current_items, *incoming.items],
    )


def list_mine(
    ctx: UserContext,
    pool: ConnectionPool,
    *,
    from_date: str,
    to_date: str,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Báo cáo của chính người gọi trong khoảng ngày (mọi trạng thái)."""
    require(ctx, Action.REPORT_READ_OWN, Resource(owner_id=ctx.user_id))
    start, end = parse_date("from_date", from_date), parse_date("to_date", to_date)
    if start > end:
        raise InvalidArgument("from_date must not be after to_date")
    if end - start > timedelta(days=MAX_REPORT_RANGE_DAYS):
        raise InvalidArgument(f"date range must be at most {MAX_REPORT_RANGE_DAYS} days")

    with pool.connection() as conn:
        rows = repo.list_reports(conn, ctx.user_id, start, end, project_id, DAY_CONTEXT_ROWS + 1)
        items = _with_blockers(conn, rows[:DAY_CONTEXT_ROWS])
    result: dict[str, Any] = {"items": items}
    if len(rows) > DAY_CONTEXT_ROWS:
        result["truncated"] = True
    return result


def day_context(
    ctx: UserContext, pool: ConnectionPool, *, day: str | None = None
) -> dict[str, Any]:
    """Dữ liệu để trợ lý soạn bản nháp chốt ngày (FR-CHK-02). Chỉ dữ liệu của chính mình."""
    require(ctx, Action.TASK_READ_OWN, Resource(owner_id=ctx.user_id))
    require(ctx, Action.REPORT_READ_OWN, Resource(owner_id=ctx.user_id))

    with pool.connection() as conn:
        target = parse_date("date", day) if day else repo.user_today(conn, ctx.user_id)
        open_tasks = task_repo.list_for_assignee(
            conn, ctx.user_id, OPEN_STATUSES, None, None, DAY_CONTEXT_ROWS
        )
        events = repo.task_events_on(conn, ctx.user_id, target, DAY_CONTEXT_ROWS)
        activities = repo.activities_on(conn, ctx.user_id, target, DAY_CONTEXT_ROWS)
        reports = _with_blockers(
            conn, repo.list_reports(conn, ctx.user_id, target, target, None, DAY_CONTEXT_ROWS)
        )

    return {
        "date": target.isoformat(),
        "tasks_open": [
            {
                "id": t.id,
                "title": t.title,
                "project_id": t.project_id,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "status": t.status,
            }
            for t in open_tasks
        ],
        "tasks_changed_today": [
            {
                "task_id": e.task_id,
                "title": e.title,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "note": e.note,
                "at": e.at.isoformat(),
            }
            for e in events
        ],
        "activities_today": [
            {
                "id": a.id,
                "project_id": a.project_id,
                "task_id": a.task_id,
                "summary": a.summary,
                "at": a.at.isoformat(),
            }
            for a in activities
        ],
        "reports_today": reports,
    }


def _with_blockers(conn: Connection[Any], rows: Sequence[ReportRow]) -> list[dict[str, Any]]:
    items_by_report: dict[int, list[dict[str, str]]] = {}
    for b in repo.blockers_for(conn, [r.id for r in rows]):
        items_by_report.setdefault(b.report_id, []).append(
            {"kind": b.kind, "severity": b.severity, "text": b.text}
        )
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "report_date": r.report_date.isoformat(),
            "status": r.status,
            "source": r.source,
            "done": r.done,
            "blockers": r.blockers,
            "schedule_conflicts": r.schedule_conflicts,
            "blocker_items": items_by_report.get(r.id, []),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]
