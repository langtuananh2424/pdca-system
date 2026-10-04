import json

import pytest

from pdca_core.errors import Internal
from pdca_core.output_limits import OutputLimits, limit_output


def test_small_output_is_untouched() -> None:
    data = {"items": [{"id": 1, "title": "a"}], "next_cursor": None}
    assert limit_output(data, OutputLimits()) == data


def test_rows_are_capped() -> None:
    out = limit_output({"items": list(range(150))}, OutputLimits(max_rows=100))
    assert out["items"] == list(range(100))
    assert out["truncated"] is True


def test_long_text_is_cut_to_limit() -> None:
    out = limit_output({"done": "x" * 2500}, OutputLimits(max_text=2000))
    assert len(out["done"]) == 2000
    assert out["done"].endswith("…")
    assert out["truncated"] is True


def test_total_bytes_are_capped_by_dropping_rows() -> None:
    rows = [{"id": i, "text": "ệ" * 200} for i in range(80)]
    out = limit_output({"items": rows}, OutputLimits(max_bytes=5000))
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 5000
    assert 0 < len(out["items"]) < 80
    assert out["items"][0]["id"] == 0
    assert out["truncated"] is True


def test_oversized_output_without_lists_is_internal_error() -> None:
    with pytest.raises(Internal):
        limit_output({"a": "x" * 1000, "b": "y" * 1000}, OutputLimits(max_bytes=500))


def test_input_is_not_mutated() -> None:
    data = {"items": list(range(5))}
    limit_output(data, OutputLimits(max_rows=2))
    assert data == {"items": list(range(5))}
