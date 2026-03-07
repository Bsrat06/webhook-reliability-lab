import threading
import time
from collections import defaultdict, deque

from app.core.config import get_settings


class SlidingWindowLimiter:
    """Per-process limiter. Adequate for a single API instance; swap for Redis when scaling out."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


guest_session_limiter = SlidingWindowLimiter(limit=get_settings().guest_sessions_per_ip, window_seconds=600)
