"""Per-domain metrics -> JSONL, for offline bottleneck analysis.

One line per processed domain with the time spent in each phase:
    dns, homepage, ancillary (robots+sitemap+tls), contacts, enrich (parse+SEO+
    email+agency), total -- plus outcome and email yield.

Analyse with:  python -m unibot.analyze [out/<run>/metrics.jsonl]
"""
from __future__ import annotations

import json
import os


class MetricsWriter:
    def __init__(self, path: str, fresh: bool = False, sample: int = 1):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "w" if fresh else "a", encoding="utf-8")
        self._sample = max(1, int(sample))
        self._i = 0

    def write(self, rec: dict):
        self._i += 1
        if self._i % self._sample != 0:
            return
        try:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._f.flush()
        except Exception:
            pass

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass
