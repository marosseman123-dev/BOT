from __future__ import annotations

import unittest
from unittest.mock import patch

from unibot.config import Config
from unibot.pipeline import PoolBox, _pick_input, _session


class _FakePool:
    def __init__(self):
        self._processes = {}
        self.shutdown_calls = []

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)


class WorkerSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_recycle_replaces_the_parser_pool_after_a_timeout(self):
        pools = []

        def factory():
            pool = _FakePool()
            pools.append(pool)
            return pool

        box = PoolBox(factory)
        first = box.pool
        self.assertTrue(await box.recycle(expected=first, reason="test timeout"))
        self.assertIsNot(first, box.pool)
        self.assertEqual(2, len(pools))
        self.assertFalse(await box.recycle(expected=first, reason="stale timeout"))

    def test_resume_never_waits_for_the_interactive_input_picker(self):
        with patch("unibot.pipeline.sys.stdin") as stdin:
            stdin.isatty.side_effect = AssertionError("resume must not inspect TTY")
            _pick_input(Config(assume_resume=True))

    async def test_worker_cookie_jars_are_isolated_and_clearable(self):
        first = _session(Config())
        second = _session(Config())
        try:
            first.cookies.set("session", "one", domain="first.example")
            second.cookies.set("session", "two", domain="second.example")
            first.cookies.clear()

            self.assertEqual(0, len(first.cookies.jar))
            self.assertEqual(1, len(second.cookies.jar))
        finally:
            await first.aclose()
            await second.aclose()

    async def test_each_worker_connection_pool_is_small(self):
        client = _session(Config(max_contact_pages=3, max_http_concurrency=28))
        try:
            pool = client._transport._pool
            self.assertEqual(5, pool._max_connections)
            self.assertEqual(2, pool._max_keepalive_connections)
        finally:
            await client.aclose()
