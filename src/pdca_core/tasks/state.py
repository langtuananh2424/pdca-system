"""Máy trạng thái task (SDD 4.10)."""

TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "cancelled")
OPEN_STATUSES = ("todo", "in_progress", "blocked")

_TRANSITIONS: dict[str, frozenset[str]] = {
    "todo": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"blocked", "done", "cancelled"}),
    "blocked": frozenset({"in_progress", "cancelled"}),
}


def can_transition(from_status: str, to_status: str) -> bool:
    """`done`, `cancelled` là trạng thái cuối; không có chuyển về chính nó."""
    return to_status in _TRANSITIONS.get(from_status, frozenset())
