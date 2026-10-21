"""Streaming, sharded CSV + TXT writer for huge runs.

Writes rows one at a time (constant memory) and rolls to a new numbered file
every `rows_per_shard` rows so no single file exceeds Excel's ~1,048,576-row
limit or becomes an unopenable multi-GB blob:

    unified_results_00001.csv / .txt
    unified_results_00002.csv / .txt
    ...

Uses the stdlib csv writer -- never builds a DataFrame, so 100M rows stream
through without OOM.
"""
from __future__ import annotations

import csv
import os
from typing import List


class ShardedWriter:
    def __init__(self, out_dir: str, base: str, rows_per_shard: int, columns: List[str]):
        self.out_dir = out_dir
        self.base = base
        self.cap = max(1, int(rows_per_shard))
        self.cols = columns
        self.idx = 0
        self.n = 0
        self.total = 0
        self._csvf = None
        self._csvw = None
        self._txtf = None
        self._open()

    def _open(self):
        self.idx += 1
        cpath = os.path.join(self.out_dir, f"{self.base}_{self.idx:05d}.csv")
        tpath = os.path.join(self.out_dir, f"{self.base}_{self.idx:05d}.txt")
        self._csvf = open(cpath, "w", newline="", encoding="utf-8-sig")
        self._csvw = csv.DictWriter(self._csvf, fieldnames=self.cols, extrasaction="ignore")
        self._csvw.writeheader()
        self._txtf = open(tpath, "w", encoding="utf-8")
        self.n = 0

    def write(self, row: dict, txt_block: str = ""):
        if self.n >= self.cap:
            self._close_files()
            self._open()
        # csv: stringify any stray list/none
        safe = {k: (", ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v))
                for k, v in row.items()}
        self._csvw.writerow(safe)
        if txt_block:
            self._txtf.write(txt_block)
        self.n += 1
        self.total += 1

    def _close_files(self):
        for f in (self._csvf, self._txtf):
            try:
                f.close()
            except Exception:
                pass

    def close(self):
        self._close_files()
