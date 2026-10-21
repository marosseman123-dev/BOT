"""Export every durable result from an interrupted UnifiedBot run.

This opens SQLite independently of the crawler and writes a separate
``saved_progress_*.csv/.txt`` snapshot.  It is safe to run while the crawler is
alive: SQLite's WAL reader sees a consistent committed snapshot and never
changes queue state.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unibot.config import Config
from unibot import export
from unibot.pipeline import _export_all
from unibot.shardwriter import ShardedWriter
from unibot.storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Export saved UnifiedBot progress without resuming crawl")
    parser.add_argument("--run", default="run1", help="run folder under --out")
    parser.add_argument("--out", default="out", help="output root")
    parser.add_argument("--db", default=None, help="explicit SQLite database path (relative to project root)")
    parser.add_argument("--output-dir", default=None,
                        help="directory for recovery files; defaults to the database directory")
    parser.add_argument("--leads-only", action="store_true")
    parser.add_argument("--rows-per-shard", type=int, default=1_000_000)
    parser.add_argument("--prefix", default="saved_progress",
                        help="output basename; use a unique value to preserve older snapshots")
    parser.add_argument("--raw", action="store_true",
                        help="stream records exactly as stored; skip optional global cleanup")
    args = parser.parse_args()

    db_path = (ROOT / args.db) if args.db else (ROOT / args.out / args.run / "unified.db")
    run_dir = (ROOT / args.output_dir) if args.output_dir else db_path.parent
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(output_leads_only=args.leads_only, rows_per_shard=max(1, args.rows_per_shard))
    partial_prefix = f".{args.prefix}.partial"
    storage = Storage(str(db_path))
    try:
        if args.raw:
            writer = None
            written = 0
            try:
                for record in storage.iter_records():
                    row = export.to_row(record)
                    if writer is None:
                        writer = ShardedWriter(str(run_dir), partial_prefix, cfg.rows_per_shard,
                                               export.column_order(row))
                    writer.write(row, export.txt_block(record))
                    written += 1
            finally:
                if writer:
                    writer.close()
            print(f"raw recovery export: {written:,} durable records")
        else:
            _export_all(storage, str(run_dir), cfg, base=partial_prefix)
    finally:
        storage.close()
    # A forced stop must never leave a misleading, half-written recovery CSV
    # under the requested name.  Promote only fully closed shards.
    partials = sorted(run_dir.glob(f"{partial_prefix}_*.*"))
    if not partials:
        raise SystemExit("export produced no shards")
    for source in partials:
        target = source.with_name(source.name.replace(partial_prefix, args.prefix, 1))
        if target.exists():
            raise SystemExit(f"refusing to overwrite existing snapshot: {target}")
        source.replace(target)


if __name__ == "__main__":
    main()
