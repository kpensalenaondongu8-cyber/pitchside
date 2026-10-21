import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most max_events per per_seconds for each key."""

    def __init__(self, max_events: int, per_seconds: float):
        self.max_events = max_events
        self.per_seconds = per_seconds
        self._events = defaultdict(deque)

    def _clean(self, key):
        events = self._events[key]
        now = time.monotonic()
        while events and now - events[0] > self.per_seconds:
            events.popleft()
        return events

    def is_blocked(self, key) -> bool:
        return len(self._clean(key)) >= self.max_events

    def record(self, key) -> None:
        self._clean(key).append(time.monotonic())

    def reset(self, key) -> None:
        self._events.pop(key, None)

    def allow(self, key) -> bool:
        """Check and record in one step (used for chat messages)."""
        if self.is_blocked(key):
            return False
        self.record(key)
        return True