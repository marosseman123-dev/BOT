from __future__ import annotations

import unittest

from unibot.config import Config
from unibot.pipeline import _session


class WorkerSessionTests(unittest.IsolatedAsyncioTestCase):
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
