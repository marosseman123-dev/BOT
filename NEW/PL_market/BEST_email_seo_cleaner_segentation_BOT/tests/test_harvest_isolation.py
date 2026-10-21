from __future__ import annotations

import unittest

from unibot import fetch


class HarvestIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_harvest_runs_outside_the_event_loop_process(self):
        try:
            found = await fetch._harvest_bounded(
                '<a href="mailto:info@example.test">contact</a>', "example.test", "test_")
            self.assertTrue(any(row["email"] == "info@example.test" for row in found))
            self.assertIsNotNone(fetch._HARVEST_POOL.pool)
        finally:
            pool = fetch._HARVEST_POOL.pool
            if pool is not None:
                fetch._HARVEST_POOL._kill(pool)
                fetch._HARVEST_POOL.pool = None
