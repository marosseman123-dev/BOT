"""Per-domain metrics -> JSONL, for offline bottleneck analysis.

One line per processed domain with the time spent in each phase:
    dns, homepage, ancillary (robots+sitemap+tls), contacts, enrich (parse+SEO+
    email+agency), total -- plus outcome and email yield.

Analyse with:  python -m unibot.analyze [out/<run>/metrics.jsonl]
"""
from __future__ import annotations

import json
import os
import time


class MetricsWriter:
    def __init__(self, path: str, fresh: bool = False, sample: int = 1):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "w" if fresh else "a", encoding="utf-8")
        self._sample = max(1, int(sample))
        self._i = 0
        # flush() on every single call was a blocking disk syscall on the SAME
        # thread as the asyncio event loop, once per domain -- with
        # metrics_sample=1 (the default), that's one forced flush per domain,
        # stealing a slice of every second from actual crawling, on top of
        # (and independent from) the SQLite per-write commit() fixed
        # separately in storage.py. Same pattern, same fix: batch the flush
        # instead of paying for it on every write. metrics.jsonl is
        # diagnostic-only, never read back for resume/correctness, so losing
        # the last few buffered lines on a hard crash costs nothing that
        # matters.
        self._unflushed = 0
        self._flush_batch = 50
        self._flush_interval = 2.0
        self._last_flush = time.monotonic()

    def write(self, rec: dict):
        self._i += 1
        if self._i % self._sample != 0:
            return
        try:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._unflushed += 1
            now = time.monotonic()
            if (self._unflushed >= self._flush_batch
                    or (now - self._last_flush) >= self._flush_interval):
                self._f.flush()
                self._unflushed = 0
                self._last_flush = now
        except Exception:
            pass

    def close(self):
        try:
            self._f.flush()
        except Exception:
            pass
        try:
            self._f.close()
        except Exception:
            pass
