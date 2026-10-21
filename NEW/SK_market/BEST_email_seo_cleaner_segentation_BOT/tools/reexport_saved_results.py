from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unibot.config import Config
from unibot.pipeline import _export_all
from unibot.storage import Storage


parser = argparse.ArgumentParser(description="Re-export completed SQLite results without crawling.")
parser.add_argument("--db", default="out/run1/unified.db")
parser.add_argument("--out", default="out/run1/recovered_export_2026")
args = parser.parse_args()

os.makedirs(args.out, exist_ok=True)
cfg = Config(out_dir=str(Path(args.out).parent), run_name=Path(args.out).name,
             dedup_max_inmemory=100_000, progress=False)
storage = Storage(args.db, checkpoint_every=10**9)
try:
    summary = _export_all(storage, args.out, cfg)
    print(summary)
finally:
    storage.close()
