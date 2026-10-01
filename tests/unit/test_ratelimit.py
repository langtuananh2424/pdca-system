import pytest

from pdca_core.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_limit_per_user_within_window() -> None:
    limiter = RateLimiter(per_minute=2, clock=Clock())
    assert limiter.allow(1)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)  # người khác không bị ảnh hưởng


def test_window_slides() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)
    assert limiter.allow(1)
    clock.now = 59.9
    assert not limiter.allow(1)
    clock.now = 60.0
    assert limiter.allow(1)


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(per_minute=0)
