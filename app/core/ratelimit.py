"""A fixed-window counter in memory. Enough for one process; the login door
and (later) the ingest door only need "not more than N per minute from one
address", and a restart forgetting who was mid-flood is an acceptable trade."""

from __future__ import annotations

import time
from collections import defaultdict


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, now=time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._now = now
        self._buckets: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def hit(self, key: str) -> bool:
        """Count one request for `key`; True when it is still within the limit."""
        now = self._now()
        started, count = self._buckets[key]
        if now - started >= self.window:
            started, count = now, 0
        count += 1
        self._buckets[key] = (started, count)
        return count <= self.limit

    def reset(self) -> None:
        self._buckets.clear()
