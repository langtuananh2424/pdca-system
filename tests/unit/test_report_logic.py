import pytest

from pdca_core.errors import Conflict, InvalidArgument
from pdca_core.reports.service import (
    ReportContent,
    approved_text,
    decide_action,
    merge_text,
    parse_blocker_items,
)
from pdca_core.repositories.reports import BlockerItem


@pytest.mark.parametrize(
    ("existing", "mode", "expected"),
    [
        (None, "create", "created"),
        (None, "append", "created"),
        (None, "replace", "created"),
        ("submitted", "append", "appended"),
        ("submitted", "replace", "replaced"),
        # Không trộn bản nháp AI chưa xác nhận vào báo cáo đã nộp (bất biến 6).
        ("draft_by_agent", "create", "replaced"),
        ("draft_by_agent", "append", "replaced"),
        ("not_reported", "create", "replaced"),
        ("not_reported", "append", "replaced"),
    ],
)
def test_decide_action(existing: str | None, mode: str, expected: str) -> None:
    assert decide_action(existing, mode) == expected


def test_create_on_submitted_report_is_conflict() -> None:
    with pytest.raises(Conflict, match="mode=append"):
        decide_action("submitted", "create")


def test_parse_blocker_items() -> None:
    items = parse_blocker_items(
        [
            {"kind": "technical", "severity": "high", "text": " API lỗi "},
            {"kind": "people", "text": "chờ duyệt"},
        ]
    )
    assert items == [
        BlockerItem("technical", "high", "API lỗi"),
        BlockerItem("people", "medium", "chờ duyệt"),
    ]
    assert parse_blocker_items(None) == []
    assert parse_blocker_items([]) == []


@pytest.mark.parametrize(
    "raw",
    [
        [{"kind": "weather", "text": "x"}],
        [{"kind": "other", "severity": "urgent", "text": "x"}],
        [{"kind": "other", "text": "  "}],
        ["not an object"],
        [{"kind": "other", "text": "x"}] * 21,
    ],
)
def test_bad_blocker_items(raw: list[object]) -> None:
    with pytest.raises(InvalidArgument):
        parse_blocker_items(raw)  # type: ignore[arg-type]


def test_merge_text() -> None:
    assert merge_text("done", "", "b") == "b"
    assert merge_text("done", "a", "") == "a"
    assert merge_text("done", "a", "b") == "a\nb"
    with pytest.raises(InvalidArgument, match="mode=replace"):
        merge_text("done", "a" * 1500, "b" * 600)


def test_approved_text() -> None:
    content = ReportContent(
        done="Sửa lỗi đăng nhập",
        blockers="Chờ API",
        schedule_conflicts="Họp trùng 15h",
        items=[BlockerItem("external", "high", "API đối tác chậm")],
    )
    assert approved_text(content) == (
        "Đã làm: Sửa lỗi đăng nhập\n"
        "Vướng mắc: Chờ API\n"
        "- [external/high] API đối tác chậm\n"
        "Xung đột lịch: Họp trùng 15h"
    )
    assert approved_text(ReportContent(done="x")) == "Đã làm: x"
