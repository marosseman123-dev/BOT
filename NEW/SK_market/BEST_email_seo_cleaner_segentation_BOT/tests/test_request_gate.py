from __future__ import annotations

import asyncio
import unittest

from unibot.config import Config
from unibot.fetch import _get
from unibot.request_gate import RequestGate


class _Response:
    status_code = 200
    headers = {"content-type": "text/html; charset=utf-8"}
    content = b"<html><title>ok</title></html>"
    text = "<html><title>ok</title></html>"
    http_version = "HTTP/1.1"
    extensions = {}

    def __init__(self, url: str):
        self.url = url

class _Client:
    def __init__(self):
        self.active = 0
        self.peak = 0

    async def get(self, url, **_kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            return _Response(url)
        finally:
            self.active -= 1


class RequestGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_caps_all_simultaneous_get_calls(self):
        client = _Client()
        gate = RequestGate(3)
        cfg = Config()
        results = await asyncio.gather(*[
            _get(client, f"http://example{i}.test", cfg, "example.test", 2, gate)
            for i in range(12)
        ])
        self.assertEqual(12, len(results))
        self.assertTrue(all(result and result["ok"] for result in results))
        self.assertLessEqual(client.peak, 3)
        self.assertEqual(3, gate.snapshot()["peak"])
        self.assertGreater(gate.snapshot()["homepage_wait_p50_s"], 0)

    async def test_homepage_bypasses_fallback_reserve(self):
        gate = RequestGate(2, high_reserve=1)
        order = []
        release = asyncio.Event()

        async def hold_fallback():
            async with gate.slot("fallback"):
                await release.wait()

        async def acquire(priority):
            async with gate.slot(priority):
                order.append(priority)

        first = asyncio.create_task(hold_fallback())
        second = asyncio.create_task(hold_fallback())
        await asyncio.sleep(0)
        try:
            fallback = asyncio.create_task(acquire("fallback"))
            homepage = asyncio.create_task(acquire("homepage"))
            await asyncio.sleep(0)
            release.set()
            await first
            await homepage
            self.assertEqual(["homepage"], order)
            self.assertFalse(fallback.done())
        finally:
            release.set()
            await second
        await fallback

    async def test_low_priority_borrows_unused_reserved_capacity(self):
        gate = RequestGate(4, high_reserve=1)
        entered = asyncio.Event()
        active = 0
        peak = 0

        async def acquire():
            nonlocal active, peak
            async with gate.slot("fallback"):
                active += 1
                peak = max(peak, active)
                if active == 4:
                    entered.set()
                await entered.wait()
                active -= 1

        await asyncio.wait_for(asyncio.gather(*(acquire() for _ in range(4))), 1)
        self.assertEqual(4, peak)

    async def test_homepage_does_not_starve_primary_contact(self):
        gate = RequestGate(1)
        order = []

        async def acquire(priority, marker):
            async with gate.slot(priority):
                order.append(marker)

        async with gate.slot("homepage"):
            tasks = [asyncio.create_task(acquire("homepage", "h1")),
                     asyncio.create_task(acquire("homepage", "h2")),
                     asyncio.create_task(acquire("primary", "p1"))]
            await asyncio.sleep(0)
        await asyncio.gather(*tasks)
        self.assertLessEqual(order.index("p1"), 1)

    async def test_html_limit_is_preserved(self):
        original = _Response.content
        try:
            _Response.content = b"x" * 100
            cfg = Config(max_html_bytes=10)
            result = await _get(_Client(), "http://example.test", cfg,
                                "example.test", 2, RequestGate(1))
            self.assertEqual(10, len(result["html"]))
        finally:
            _Response.content = original
