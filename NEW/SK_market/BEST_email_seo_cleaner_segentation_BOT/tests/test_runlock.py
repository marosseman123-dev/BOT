from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest

from unibot.runlock import RunLock, _process_started


class RunLockTests(unittest.TestCase):
    def test_current_process_has_a_birth_time(self):
        self.assertIsInstance(_process_started(os.getpid()), float)

    def test_legacy_live_lock_is_never_auto_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = RunLock(directory)
            with open(lock.path, "w", encoding="utf-8") as file:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(), "started": 1}, file)
            self.assertFalse(lock._remove_stale_or_replace_confirmed_lock())
            self.assertTrue(os.path.exists(lock.path))
