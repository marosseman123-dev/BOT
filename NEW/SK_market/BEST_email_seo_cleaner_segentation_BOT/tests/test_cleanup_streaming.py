from __future__ import annotations

import copy
import unittest

from unibot.cleanup import apply, iter_apply_streaming
from unibot.config import Config


class CleanupStreamingTests(unittest.TestCase):
    def test_streaming_matches_in_memory_cleanup(self):
        records = [
            {"domain": "one.example", "emails_list": ["shared@example.net", "info@one.example"], "is_competitor": False},
            {"domain": "two.example", "emails_list": ["shared@example.net", "podatelna@two.example"], "is_competitor": False},
            {"domain": "three.example", "emails_list": ["shared@example.net"], "is_competitor": False},
        ]
        cfg = Config(dedup_threshold=3)
        expected = copy.deepcopy(records)
        apply(expected, cfg)
        actual = list(iter_apply_streaming(lambda: iter(copy.deepcopy(records)), cfg))
        self.assertEqual(expected, actual)
