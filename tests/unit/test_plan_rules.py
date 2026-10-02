from datetime import date

import pytest

from pdca_core.errors import Conflict, InvalidArgument
from pdca_core.plans.rules import (
    PLAN_STATUSES,
    check_child,
    check_range,
    check_status_change,
    check_within,
    child_level,
)


def test_child_levels() -> None:
    assert [child_level(x) for x in ("year", "month", "week", "day")] == [
        "month",
        "week",
        "day",
        None,
    ]
    check_child("year", "month")
    for parent, child in (("year", "week"), ("month", "month"), ("week", "month")):
        with pytest.raises(InvalidArgument, match="must have level"):
            check_child(parent, child)
    with pytest.raises(InvalidArgument, match="day plan cannot have child"):
        check_child("day", "day")


def test_ranges() -> None:
    d = date.fromisoformat
    check_range(d("2026-10-01"), d("2026-10-01"))
    with pytest.raises(InvalidArgument):
        check_range(d("2026-10-02"), d("2026-10-01"))
    check_within(d("2026-10-01"), d("2026-10-31"), d("2026-10-01"), d("2026-10-31"))
    for start, end in (("2026-09-30", "2026-10-10"), ("2026-10-10", "2026-11-01")):
        with pytest.raises(InvalidArgument, match="within the parent"):
            check_within(d(start), d(end), d("2026-10-01"), d("2026-10-31"))


ALLOWED = {
    ("draft", "active"),
    ("draft", "cancelled"),
    ("active", "done"),
    ("active", "cancelled"),
}


@pytest.mark.parametrize("current", PLAN_STATUSES)
@pytest.mark.parametrize("new", PLAN_STATUSES)
def test_status_changes(current: str, new: str) -> None:
    if current == new or (current, new) in ALLOWED:
        check_status_change(current, new)
    else:
        with pytest.raises(Conflict):
            check_status_change(current, new)
