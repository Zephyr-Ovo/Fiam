"""In-memory pub-sub bus for computer-control SSE push.

Mirrors fiam_lib.stroll_events: single-process, ring buffer keyed by
monotonic id so reconnecting EventSource clients can replay via
Last-Event-ID. Used by browser-control and desktop (Atrium) action
events that the Favilla chat surfaces as a live status row.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Iterator

_RING_SIZE = 200


class ComputerEventBus:
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
        for ev in self.replay(after_id):
            yield ev
            after_id = ev["id"]
        deadline = time.time() + timeout
        with self._cond:
            while True:
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


_BUS: ComputerEventBus | None = None
_BUS_LOCK = threading.Lock()


def get_bus() -> ComputerEventBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = ComputerEventBus()
        return _BUS
