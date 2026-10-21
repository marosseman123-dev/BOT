"""Priority-aware global budget for outbound HTTP requests."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager


class RequestGate:
    """One HTTP budget with reserved capacity for discovery-critical work."""

    _HIGH = ("homepage", "primary")
    _LOW = ("fallback", "audit")

    def __init__(self, limit: int, high_reserve: int | None = None):
        self.limit = max(1, int(limit))
        self.high_reserve = min(self.limit - 1, max(1, high_reserve or self.limit // 4))
        self._low_limit = self.limit - self.high_reserve
        self.active = 0
        self.peak = 0
        self._active_low = 0
        self._active_by_priority = {name: 0 for name in (*self._HIGH, *self._LOW)}
        self._waiters = {name: deque() for name in (*self._HIGH, *self._LOW)}
        self._waits = {name: deque(maxlen=600) for name in (*self._HIGH, *self._LOW)}
        self._high_cursor = 0
        self._low_cursor = 0

    def _pop_round_robin(self, priorities: tuple[str, ...], cursor_name: str):
        """Pick fairly within a priority class and skip cancelled waiters."""
        start = getattr(self, cursor_name)
        for offset in range(len(priorities)):
            index = (start + offset) % len(priorities)
            priority = priorities[index]
            while self._waiters[priority]:
                item = self._waiters[priority].popleft()
                if item[0].cancelled():
                    continue
                setattr(self, cursor_name, (index + 1) % len(priorities))
                return priority, item
        return None

    def _next_waiter(self):
        high = self._pop_round_robin(self._HIGH, "_high_cursor")
        if high is not None:
            return high
        # Reserve capacity only while discovery-critical requests are actually
        # waiting. Otherwise let fallback/audit borrow every idle slot: a hard
        # permanent reserve left 25% of the network budget unused during the
        # contact/SEO tail of each wave.
        return self._pop_round_robin(self._LOW, "_low_cursor")

    def _drain(self) -> None:
        while self.active < self.limit:
            item = self._next_waiter()
            if item is None:
                return
            priority, (future, queued_at) = item
            if future.cancelled():
                continue
            self.active += 1
            self.peak = max(self.peak, self.active)
            self._active_by_priority[priority] += 1
            if priority in self._LOW:
                self._active_low += 1
            wait_s = time.monotonic() - queued_at
            self._waits[priority].append(wait_s)
            future.set_result(wait_s)

    def _release(self, priority: str) -> None:
        self.active = max(0, self.active - 1)
        self._active_by_priority[priority] = max(0, self._active_by_priority[priority] - 1)
        if priority in self._LOW:
            self._active_low = max(0, self._active_low - 1)
        self._drain()

    @asynccontextmanager
    async def slot(self, priority: str = "homepage"):
        if priority not in self._waiters:
            raise ValueError(f"unknown HTTP priority: {priority}")
        future = asyncio.get_running_loop().create_future()
        self._waiters[priority].append((future, time.monotonic()))
        self._drain()
        try:
            wait_s = await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # _drain already reserved a slot; return it if cancellation
                # lands between reservation and entering the context body.
                self._release(priority)
            else:
                future.cancel()
                self._drain()
            raise
        try:
            yield wait_s
        finally:
            self._release(priority)

    def snapshot(self) -> dict[str, float | int]:
        def percentile(values, fraction: float) -> float:
            values = sorted(values)
            if not values:
                return 0.0
            return values[min(len(values) - 1, int((len(values) - 1) * fraction))]
        return {
            "active": self.active, "limit": self.limit, "peak": self.peak,
            "high_reserve": self.high_reserve,
            "homepage_wait_p50_s": percentile(self._waits["homepage"], .5),
            "primary_wait_p50_s": percentile(self._waits["primary"], .5),
            "fallback_wait_p50_s": percentile(self._waits["fallback"], .5),
            "audit_wait_p50_s": percentile(self._waits["audit"], .5),
        }
