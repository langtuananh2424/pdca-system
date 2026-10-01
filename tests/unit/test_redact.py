from pdca_core.audit.log import redact_params


def test_identifiers_and_enums_are_kept() -> None:
    params = {
        "project_id": 3,
        "task_id": 9,
        "status": "done",
        "mode": "append",
        "report_date": "2026-10-01",
        "cursor": "abc",
        "limit": 20,
        "ids": [1, 2],
        "flag": True,
    }
    assert redact_params(params) == params


def test_free_text_keeps_only_length() -> None:
    out = redact_params({"done": "Đã sửa lỗi đăng nhập", "summary": "", "note": "x"})
    assert out == {"done": {"len": 20}, "summary": {"len": 0}, "note": {"len": 1}}


def test_nested_items_are_counted_not_copied() -> None:
    out = redact_params(
        {"blocker_items": [{"kind": "technical", "text": "bí mật"}], "meta": {"text": "abc"}}
    )
    assert out == {"blocker_items": {"count": 1}, "meta": {"text": {"len": 3}}}


def test_id_suffix_must_be_whole_word() -> None:
    # "paid" kết thúc bằng "id" nhưng không phải định danh.
    assert redact_params({"paid": "secret"}) == {"paid": {"len": 6}}
