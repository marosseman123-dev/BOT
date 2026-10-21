"""Adaptive concurrency controller: the answer to "fast, but must not get
banned, must not wait around unnecessarily, and must not slow down as the run
goes on."

Two independent, self-calibrating signals feed one live 'target' concurrency
that the worker pool polls before picking up each new domain -- there is no
hardcoded "right" concurrency number, because there isn't one: it depends on
the user's own upload/download capacity, which this code has no way to know
in advance and must discover live.

1. BLOCK RATE -- a single home IP has no proxy pool to fall back on; if a
   shared host or a WAF starts returning 403/429/challenge pages, hammering
   it harder only makes things worse (and looks exactly like an attack).
   error rate >= backoff_rate -> cut target sharply
   error rate <= recover_rate -> raise target gradually

2. CAPACITY -- raw website latency and settled-domain rate are diagnostics,
   not congestion signals. A heterogeneous input naturally gets slower when
   it reaches slower sites, so comparing that segment with the first fast
   segment caused false backoffs despite zero internal HTTP queue wait. The
   fixed, machine-sized request gate is the socket/network safety ceiling.

Starting target is min_conc, not max_conc. It ramps gradually to the
machine-profile ceiling and backs off only on confirmed blocking. The separate
request gate prevents that domain-level ramp from exceeding the measured safe
number of simultaneous HTTP requests.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

log = logging.getLogger("unibot")


class Throttle:
    def __init__(self, min_conc: int, max_conc: int, window: int = 200,
                 backoff_rate: float = 0.30, recover_rate: float = 0.10,
                 check_every: float = 3.0, log_every: float = 30.0,
                 latency_window: int = 60, latency_backoff_ratio: float = 2.2,
                 latency_recover_ratio: float = 1.4,
                 stale_relax_after: int = 15, stale_relax_factor: float = 1.15):
        self.min_conc = max(1, min_conc)
        self.max_conc = max(self.min_conc, max_conc)
        self.target = self.min_conc   # ramp UP from a safe floor, not down from the ceiling
        self.window = window
        self.backoff_rate = backoff_rate
        self.recover_rate = recover_rate
        self.check_every = check_every
        self.log_every = log_every
        self._outcomes: deque = deque(maxlen=window)
        self._latencies: deque = deque(maxlen=latency_window)
        self._settled_at: deque = deque(maxlen=512)
        self._best_latency: float | None = None
        self._target_since = time.monotonic()
        self._promotion_hold_s = 20.0
        self._cooldown_until = 0.0
        self._congestion_cooldown_s = 90.0
        self._rate_gate_open = True
        self._last_rate_gate_check = 0.0
        self._rate_gate_interval_s = 30.0
        self._warmup_samples = max(30, latency_window)
        self._floor_baseline_alpha = 0.35
        self.latency_backoff_ratio = latency_backoff_ratio
        self.latency_recover_ratio = latency_recover_ratio
        # A single lucky low-latency sample early in the run (e.g. one fast CDN
        # site) can permanently set _best_latency far below what's actually
        # achievable once real traffic mixes in. Because recovery requires
        # latency <= best*recover_ratio, an unrepresentative best silently locks
        # the target at min_conc forever -- block_rate can sit at 0-6% (nowhere
        # near backoff_rate) while throughput still never climbs off the floor.
        # Confirmed in production: 20+ minutes pinned at min_conc with
        # block_rate<6% because best_latency was set from a single early 0.3-0.4s
        # sample never matched again. If we go this many consecutive checks
        # without EITHER a fresh best OR a successful ramp-up, treat the
        # baseline itself as stale and relax it toward current reality so
        # recovery becomes reachable again.
        #
        # CAUTION -- this relax mechanism previously fired unconditionally
        # whenever checks went stale, REGARDLESS of what target currently was.
        # Confirmed in production this creates a runaway feedback loop under
        # REAL, sustained congestion (not a bad baseline): target climbs to
        # 46-72, latency genuinely degrades right along with it (this exact
        # network measurably gets worse above ~24 concurrent, see module
        # docstring), throttle correctly cuts back down -- but relax then
        # fires anyway (it was stuck-without-improving, same as the floor
        # case) and inflates best_latency upward WHILE AT HIGH CONCURRENCY.
        # Repeat a few times and best_latency drifts from 2.8s to 5.9s, at
        # which point genuinely-bad 8s latency no longer even LOOKS congested
        # (8/5.9 < the 2.2x cut ratio), so the throttle happily sits at
        # max_conc=72 forever with throughput near zero -- the exact
        # congestion-collapse this whole class was built to prevent. Fix:
        # relax only fires AT THE FLOOR (target == min_conc). At the floor,
        # target is by definition not itself the cause of high latency (it
        # can't go any lower), so "the true achievable minimum is higher than
        # we thought" is always a safe conclusion there. Above the floor,
        # climbing OUT of a real jam is the congested-cut path's job, not
        # this one -- relaxing there is exactly what let a self-inflicted
        # traffic jam disguise itself as "the new normal". Verified via
        # simulation: this alone recovers a genuinely stale floor-lock (the
        # original bug) while leaving a real sustained-congestion climb
        # (target scaling latency up with it) to settle on its own instead of
        # drifting the baseline until it stops noticing. _true_best is kept
        # only as a diagnostic (the lowest latency ever actually measured,
        # untouched by relax) -- not used to bound relax, since doing so
        # (tried, then measured) blocked the very recovery this exists for.
        self.stale_relax_after = max(1, stale_relax_after)
        self.stale_relax_factor = max(1.01, stale_relax_factor)
        self._true_best: float | None = None
        self._stale_checks = 0
        self._last_check = 0.0
        self._last_log = 0.0
        self._adjustments = 0

    def record(self, blocked: bool) -> None:
        """Feed one outcome: True if the site actively blocked/challenged us
        (403, 429, bot-challenge page) -- the real ban signal. A dead/unreachable
        domain is NOT passed here; that's expected content, not a block."""
        self._outcomes.append(1 if blocked else 0)
        self._maybe_adjust()

    def record_latency(self, seconds: float) -> None:
        """Feed one time-to-real-response sample (only for domains that
        actually answered -- a dead domain's fast failure isn't 'good
        latency', it's a different thing entirely and would mask real
        congestion)."""
        if seconds > 0:
            self._latencies.append(seconds)

    def record_settled(self) -> None:
        """Record one final domain result for a rolling throughput diagnostic."""
        self._settled_at.append(time.monotonic())

    def settled_rate(self, window_s: float = 60.0) -> float:
        if len(self._settled_at) < 2:
            return 0.0
        now = time.monotonic()
        while len(self._settled_at) > 2 and now - self._settled_at[0] > window_s:
            self._settled_at.popleft()
        span = self._settled_at[-1] - self._settled_at[0]
        return (len(self._settled_at) - 1) / span if span > 0 else 0.0

    def _interval_rate(self, start: float, end: float) -> float:
        if end <= start:
            return 0.0
        return sum(1 for t in self._settled_at if start < t <= end) / (end - start)

    def _rate_allows_ramp(self, now: float) -> bool:
        """Use local adjacent windows, never a lifetime throughput record.

        A mixed domain list naturally changes speed over time.  When its most
        recent 30 seconds are materially slower than the immediately previous
        30 seconds, adding more connections is unlikely to help and often
        worsens timeouts.  This is a gate on *increases only*; it never drops
        valid work or changes e-mail/SEO coverage.
        """
        if now - self._last_rate_gate_check < self._rate_gate_interval_s:
            return self._rate_gate_open
        self._last_rate_gate_check = now
        span = self._rate_gate_interval_s
        current = self._interval_rate(now - span, now)
        previous = self._interval_rate(now - 2 * span, now - span)
        # Do not infer a trend from an almost-empty sample (startup/tail).
        if previous >= 1.0:
            self._rate_gate_open = current >= previous * 0.85
        else:
            self._rate_gate_open = True
        return self._rate_gate_open

    def block_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(self._outcomes) / len(self._outcomes)

    def latency_p50(self) -> float:
        if not self._latencies:
            return 0.0
        v = sorted(self._latencies)
        return v[len(v) // 2]

    def _maybe_adjust(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self.check_every:
            return
        self._last_check = now

        block_ok = len(self._outcomes) >= max(20, self.window // 4)
        rate = self.block_rate() if block_ok else 0.0

        lat = self.latency_p50()
        lat_ok = len(self._latencies) >= self._warmup_samples
        if lat_ok and lat > 0:
            if self._true_best is None or lat < self._true_best:
                self._true_best = lat
            # Establish a robust baseline from a full rolling window at the
            # safe floor.  A single fast CDN must never define the throughput
            # budget for the rest of a long, heterogeneous crawl.
            if self._best_latency is None:
                self._best_latency = lat
        old = self.target
        if block_ok and rate >= self.backoff_rate:
            self.target = max(self.min_conc, int(self.target * 0.6))
            reason = f"block_rate={rate*100:.0f}%"
            self._cooldown_until = now + self._congestion_cooldown_s
        elif self._best_latency is None:
            reason = "latency warm-up at safe floor"
        elif now < self._cooldown_until:
            reason = f"congestion cooldown {self._cooldown_until - now:.0f}s"
        elif ((not block_ok or rate <= self.recover_rate)
              and now - self._target_since >= self._promotion_hold_s):
            self.target = min(self.max_conc, self.target + max(1, self.target // 8))
            reason = "healthy -> ramping up"
        else:
            reason = "holding"

        if reason == "healthy -> ramping up" or self._best_latency is None or lat < self._best_latency:
            self._stale_checks = 0
        else:
            self._stale_checks += 1
            # block_rate says we're fine (well under backoff_rate) but the
            # latency baseline never lets us recover -- it's the baseline that's
            # wrong, not the network. Relax it toward the latency we're actually
            # seeing so ramp-up becomes reachable again; if conditions are
            # genuinely still bad, the very next congestion check cuts target
            # right back down, so this costs nothing when the baseline was right.
            # Only at the FLOOR (see class docstring -- this used to also fire
            # while cycling at high concurrency under real congestion, which
            # is how it caused a production congestion-collapse).
            # A baseline measured under a healthy request queue is immutable
            # for this run. Raising it at the floor normalizes a local backlog
            # into "healthy" latency and recreates the congestion cycle.

        if self.target != old:
            self._target_since = now
            self._adjustments += 1
            log.info("throttle: concurrency %d -> %d (%s)", old, self.target, reason)
        elif now - self._last_log >= self.log_every:
            self._last_log = now
            log.info("throttle: concurrency=%d settled_rate_60s=%.2f/s settled_rate_30s=%.2f/s rate_gate=%s block_rate=%.0f%% latency_p50=%.2fs baseline=%s cooldown=%.0fs",
                     self.target, self.settled_rate(), self._interval_rate(now - 30.0, now),
                     "open" if self._rate_allows_ramp(now) else "hold", rate * 100, lat,
                     f"{self._best_latency:.2f}s" if self._best_latency else "n/a",
                     max(0.0, self._cooldown_until - now))


class Gate:
    """Caps how many workers are ACTUALLY processing a domain at once to
    throttle.target, independent of how many worker coroutines exist. Unlike
    asyncio.Semaphore, this can shrink its effective capacity at runtime (a
    semaphore can only ever grow once permits are released)."""

    def __init__(self, throttle: Throttle):
        self.throttle = throttle
        self.active = 0
        self._lock = asyncio.Lock()
        self._poll_interval = 0.05

    async def acquire(self, stop: asyncio.Event) -> bool:
        """Blocks until a slot is free or `stop` is set. Returns False if it
        gave up because of `stop` (caller should not proceed)."""
        while not stop.is_set():
            async with self._lock:
                if self.active < self.throttle.target:
                    self.active += 1
                    return True
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass
        return False

    async def release(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)
