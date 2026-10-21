from __future__ import annotations

import unittest
from unittest.mock import patch

from unibot.throttle import Throttle


class ThrottleTests(unittest.TestCase):
    def test_slower_input_segment_does_not_reduce_concurrency(self):
        throttle = Throttle(24, 32, check_every=0, latency_window=3)
        throttle._warmup_samples = 3
        with patch("unibot.throttle.time.monotonic") as clock:
            now = 100.0
            clock.return_value = now
            for latency in (0.5, 0.5, 0.5):
                throttle.record_latency(latency)
                throttle.record(False)
            throttle._target_since = 0
            throttle.record(False)
            self.assertGreater(throttle.target, 24)

            previous = throttle.target
            for latency in (3.0, 3.0, 3.0):
                now += 4
                clock.return_value = now
                throttle.record_latency(latency)
                throttle.record(False)
            self.assertGreaterEqual(throttle.target, previous)

    def test_confirmed_block_rate_still_backs_off(self):
        throttle = Throttle(24, 32, window=20, check_every=0, latency_window=3)
        throttle.target = 32
        for _ in range(20):
            throttle.record(True)
        self.assertEqual(24, throttle.target)
