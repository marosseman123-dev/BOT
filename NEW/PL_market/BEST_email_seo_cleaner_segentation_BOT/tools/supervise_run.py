"""Windows supervisor for a resumable UnifiedBot run.

The crawler's own asyncio timeouts handle normal request failures.  This
separate process handles the stronger failure mode: a wedged event loop or a
broken ProcessPoolExecutor that no longer advances SQLite and cannot receive
Ctrl+C because the crawler was launched in the background.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv_unified" / "Scripts" / "python.exe"


def progress(db_path: Path) -> tuple[int, float]:
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            done = int(conn.execute("SELECT COUNT(*) FROM queue WHERE status='done'").fetchone()[0])
            updated = float(conn.execute("SELECT COALESCE(MAX(updated), 0) FROM queue").fetchone()[0])
        return done, updated
    except sqlite3.Error:
        return -1, 0.0


def snapshot(run: str, out: str) -> None:
    stamp = datetime.now().strftime("recovery_auto_%Y%m%d_%H%M%S")
    subprocess.run([str(PYTHON), "tools\\export_saved_run.py", "--run", run,
                    "--out", out, "--prefix", stamp, "--raw"],
                   cwd=ROOT, check=False)


def stop_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def launch(input_path: str, run: str, out: str) -> subprocess.Popen:
    return subprocess.Popen([str(PYTHON), "-B", "-m", "unibot", "--input", input_path,
                             "--run", run, "--out", out, "--resume"], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restart UnifiedBot only after verified progress stalls")
    parser.add_argument("--input", default="input\\domains.txt")
    parser.add_argument("--run", default="run1")
    parser.add_argument("--out", default="out")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--stall-seconds", type=int, default=180,
                        help="no SQLite progress for this long triggers snapshot + restart")
    args = parser.parse_args()
    if args.stall_seconds < args.poll_seconds * 2:
        raise SystemExit("--stall-seconds must be at least twice --poll-seconds")

    db_path = ROOT / args.out / args.run / "unified.db"
    child = launch(args.input, args.run, args.out)
    last = progress(db_path)
    last_change = time.monotonic()
    print(f"[supervisor] watching PID {child.pid}; stall limit={args.stall_seconds}s", flush=True)
    try:
        while True:
            code = child.poll()
            if code is not None:
                if code == 0:
                    print("[supervisor] crawler finished", flush=True)
                    return
                print(f"[supervisor] crawler exited {code}; snapshot + resume", flush=True)
                snapshot(args.run, args.out)
                child = launch(args.input, args.run, args.out)
                last, last_change = progress(db_path), time.monotonic()
                continue
            time.sleep(args.poll_seconds)
            current = progress(db_path)
            if current != last and current[0] >= 0:
                last, last_change = current, time.monotonic()
                continue
            if time.monotonic() - last_change >= args.stall_seconds:
                print("[supervisor] SQLite progress stalled; snapshotting and restarting", flush=True)
                snapshot(args.run, args.out)
                stop_tree(child.pid)
                child = launch(args.input, args.run, args.out)
                last, last_change = progress(db_path), time.monotonic()
    except KeyboardInterrupt:
        print("[supervisor] stop requested; snapshotting then stopping crawler", flush=True)
        snapshot(args.run, args.out)
        stop_tree(child.pid)


if __name__ == "__main__":
    main()
