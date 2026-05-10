"""In-memory pub-sub bus for stroll SSE push.

Single-process. One global bus. Ring buffer of recent events keyed by
monotonic id so reconnecting clients can replay via Last-Event-ID.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Iterator

_RING_SIZE = 200


class StrollEventBus:
    def __init__(self, ring_size: int = _RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buffer: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._next_id = 1

    def publish(self, event_type: str, data: dict[str, Any]) -> int:
        with self._cond:
            ev_id = self._next_id
            self._next_id += 1
            self._buffer.append({
                "id": ev_id,
                "event": event_type,
                "data": data,
                "ts": time.time(),
            })
            self._cond.notify_all()
            return ev_id

    def replay(self, after_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._buffer if e["id"] > after_id]

    def subscribe(
        self,
        after_id: int = 0,
        timeout: float = 30.0,
    ) -> Iterator[dict[str, Any]]:
        """Yield events with id > after_id; block up to ``timeout`` for the
        next one. Caller is responsible for re-subscribing in a loop and
        handling client disconnects."""
        # Replay any missed events first.
        for ev in self.replay(after_id):
            yield ev
            after_id = ev["id"]
        deadline = time.time() + timeout
        with self._cond:
            while True:
                # Re-check after waking — events may have been published.
                fresh = [e for e in self._buffer if e["id"] > after_id]
                if fresh:
                    for ev in fresh:
                        yield ev
                        after_id = ev["id"]
                    return
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                self._cond.wait(timeout=remaining)


_BUS: StrollEventBus | None = None
_BUS_LOCK = threading.Lock()


def get_bus() -> StrollEventBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = StrollEventBus()
        return _BUS
