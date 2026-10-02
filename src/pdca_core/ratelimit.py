"""Giới hạn tần suất theo người dùng tại tool (NFR-SEC-07, `RATE_LIMIT_PER_MIN`).

Cửa sổ trượt trong bộ nhớ của tiến trình: đủ cho một instance MCP ở P1.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        window_seconds: float = 60.0,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self._limit = per_minute
        self._window = window_seconds
        self._clock = clock
        self._hits: defaultdict[int, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits[user_id]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
