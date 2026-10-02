import pytest

from adapters.mcp.server import bearer_token
from apps.mcp_server.settings import Settings
from pdca_core.errors import InvalidArgument
from pdca_core.tasks.service import _decode_task_cursor
from pdca_core.tasks.state import TASK_STATUSES, can_transition
from pdca_core.validation import (
    choice,
    clip_text,
    decode_cursor,
    encode_cursor,
    page_limit,
    require_text,
)

ALLOWED = {
    ("todo", "in_progress"),
    ("todo", "cancelled"),
    ("in_progress", "blocked"),
    ("in_progress", "done"),
    ("in_progress", "cancelled"),
    ("blocked", "in_progress"),
    ("blocked", "cancelled"),
}


@pytest.mark.parametrize("src", TASK_STATUSES)
@pytest.mark.parametrize("dst", TASK_STATUSES)
def test_task_transitions_follow_sdd_4_10(src: str, dst: str) -> None:
    assert can_transition(src, dst) is ((src, dst) in ALLOWED)


def test_cursor_roundtrip() -> None:
    assert decode_cursor(encode_cursor(["2026-10-05", 42])) == ["2026-10-05", 42]
    assert _decode_task_cursor(encode_cursor([None, 7])) == (None, 7)


@pytest.mark.parametrize(
    "cursor",
    [
        "%%%",
        encode_cursor({"a": 1}),  # type: ignore[arg-type]
        encode_cursor(["2026-13-01", 1]),
        encode_cursor([None, "1"]),
        encode_cursor([None, True]),
        encode_cursor([None]),
    ],
)
def test_bad_cursor_is_invalid_argument(cursor: str) -> None:
    with pytest.raises(InvalidArgument):
        _decode_task_cursor(cursor)


def test_page_limit() -> None:
    assert page_limit(None) == 20
    assert page_limit(100) == 100
    for bad in (0, 101, -1):
        with pytest.raises(InvalidArgument):
            page_limit(bad)


def test_text_helpers() -> None:
    assert clip_text("a" * 2001) == "a" * 2000
    assert require_text("summary", "  ok  ") == "ok"
    with pytest.raises(InvalidArgument, match="summary is required"):
        require_text("summary", " ")
    with pytest.raises(InvalidArgument, match="status must be one of"):
        choice("status", "x", ("a", "b"))


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"authorization": "Bearer pdca_abc"}, "pdca_abc"),
        ({"Authorization": "bearer  pdca_abc "}, "pdca_abc"),
        ({"authorization": "Basic dXNlcjpwdw=="}, None),
        ({"authorization": "Bearer"}, None),
        ({"x-other": "1"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_bearer_token(headers: dict[str, str] | None, expected: str | None) -> None:
    assert bearer_token(headers) == expected


def test_settings_from_env() -> None:
    s = Settings.from_env({"DATABASE_URL": "postgresql://x", "MCP_ALLOWED_HOSTS": "a.vn, b:*,"})
    assert s.allowed_hosts == ["a.vn", "b:*"]
    assert (s.rate_limit_per_min, s.output_max_rows, s.output_max_bytes) == (60, 100, 51200)
    with pytest.raises(SystemExit):
        Settings.from_env({})
