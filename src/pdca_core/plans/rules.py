"""Quy tắc cây kế hoạch (FR-PLAN-01..03, LLD 4.2 `create_plan`/`update_plan`)."""

from datetime import date

from pdca_core.errors import Conflict, InvalidArgument

LEVELS = ("year", "month", "week", "day")
PLAN_STATUSES = ("draft", "active", "done", "cancelled")
CLOSED_STATUSES = frozenset({"done", "cancelled"})

_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "cancelled"}),
    "active": frozenset({"done", "cancelled"}),
}


def child_level(parent_level: str) -> str | None:
    """Mức ngay dưới (year → month → week → day); `day` không có mức con."""
    index = LEVELS.index(parent_level)
    return LEVELS[index + 1] if index + 1 < len(LEVELS) else None


def check_child(parent_level: str, child: str) -> None:
    expected = child_level(parent_level)
    if expected is None:
        raise InvalidArgument("a day plan cannot have child plans")
    if child != expected:
        raise InvalidArgument(f"child of a {parent_level} plan must have level {expected}")


def check_range(start: date, end: date) -> None:
    if end < start:
        raise InvalidArgument("end_date must not be before start_date")


def check_within(start: date, end: date, parent_start: date, parent_end: date) -> None:
    """FR-PLAN-03: khoảng thời gian của con nằm trong khoảng của cha."""
    if start < parent_start or end > parent_end:
        raise InvalidArgument(
            f"plan dates must be within the parent plan"
            f" ({parent_start.isoformat()}..{parent_end.isoformat()})"
        )


def check_status_change(current: str, new: str) -> None:
    if new == current:
        return
    if new not in _STATUS_TRANSITIONS.get(current, frozenset()):
        raise Conflict(f"cannot change plan status from {current} to {new}")
