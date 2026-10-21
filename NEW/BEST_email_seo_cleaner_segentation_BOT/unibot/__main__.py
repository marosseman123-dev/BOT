"""CLI: python -m unibot --input input/domains.txt --run run1

Resuming the same --run continues from the SQLite queue. Ctrl-C flushes outputs.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import warnings

from .config import Config
from .pipeline import run


def _args() -> Config:
    p = argparse.ArgumentParser(prog="unibot", description="Unified lead + SEO + cleanup bot")
    p.add_argument("--input", default=None, help="domains .txt (1/line) or .csv with a domain column")
    p.add_argument("--run", default=None, help="run name (output subfolder + queue scope)")
    p.add_argument("--out", default=None, help="output directory")
    p.add_argument("--fresh", action="store_true", help="wipe and start over")
    p.add_argument("--resume", action="store_true", help="resume without asking")
    p.add_argument("--retry-failed", action="store_true", help="re-queue previously failed domains")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--no-progress", action="store_true")
    p.add_argument("--no-metrics", action="store_true", help="do not write metrics.jsonl")
    p.add_argument("--leads-only", action="store_true", help="export ONLY domains with a contact")
    p.add_argument("--rows-per-shard", type=int, default=None, help="max rows per CSV/TXT file (Excel limit ~1,048,576)")
    p.add_argument("--metrics-sample", type=int, default=None, help="write 1 of every N metric rows")
    a = p.parse_args()
    cfg = Config()
    if a.input: cfg.input_path = a.input
    if a.run: cfg.run_name = a.run
    if a.out: cfg.out_dir = a.out
    if a.fresh: cfg.fresh = True
    if a.resume: cfg.assume_resume = True
    if a.concurrency: cfg.max_concurrency = a.concurrency
    if a.no_progress: cfg.progress = False
    if a.no_metrics: cfg.metrics = False
    if a.leads_only: cfg.output_leads_only = True
    if a.rows_per_shard: cfg.rows_per_shard = a.rows_per_shard
    if a.metrics_sample: cfg.metrics_sample = a.metrics_sample
    cfg._retry_failed = a.retry_failed  # type: ignore[attr-defined]
    return cfg


def main():
    warnings.filterwarnings("ignore")
    # NOTE: keep the default Windows Proactor loop. The Selector loop is capped at
    # 512 file descriptors (select()), which crashes at scale. Proactor scales and
    # the harmless ConnectionResetError noise is silenced by a loop handler in run().
    cfg = _args()
    print(f"[unibot] python {sys.version.split()[0]} | input={cfg.input_path} | run={cfg.run_name}",
          flush=True)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
