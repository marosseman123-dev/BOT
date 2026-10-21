"""SQLite store: durable queue + unified results. WAL, resumable, crash-safe.

The queue also carries seed_emails: in CSV (leads) mode a domain arrives with an
already-known email. That email is stored on the queue row so it survives resume and
is merged back in enrich -- and so a known-alive lead is never lost even if the live
re-fetch fails.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Dict, List, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    domain TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, next_eligible REAL NOT NULL DEFAULT 0,
    last_reason TEXT DEFAULT '', updated REAL NOT NULL DEFAULT 0,
    seed_emails TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_q ON queue(status, next_eligible);
CREATE TABLE IF NOT EXISTS results (
    domain TEXT PRIMARY KEY, ts REAL, state TEXT, overall_score INTEGER,
    seo_score INTEGER, email_count INTEGER, is_competitor INTEGER,
    platform TEXT, json_full TEXT
);
"""


class Storage:
    def __init__(self, path: str, checkpoint_every: int = 200_000):
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        # migrate older DBs that predate seed_emails
        try:
            self.conn.execute("ALTER TABLE queue ADD COLUMN seed_emails TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()
        self.checkpoint_every = max(10_000, int(checkpoint_every))
        self._writes = 0
        # save_result()/requeue() used to call conn.commit() on EVERY single
        # domain -- one WAL sync per row, on the same thread as the asyncio
        # event loop, blocking all in-flight fetches for its duration. Cheap
        # early on, but SQLite's per-commit cost grows with DB/WAL size, so as
        # a run's 'done' table climbed into the hundreds of thousands of rows
        # this was a second, independent reason throughput decayed over time
        # (alongside the dashboard COUNT(*) polling fixed separately) --100k+
        # domains means 100k+ individual fsync-class commits blocking the loop.
        # Batching commits (every N writes or T seconds, whichever first) cuts
        # that by ~1-2 orders of magnitude. Worst case on an unclean crash: the
        # last <_commit_batch writes/_commit_interval seconds of results are
        # not yet durable -- but those domains are still 'pending' or their
        # prior state in the queue (never partially written, since it's still
        # one transaction per commit), so a resume just reprocesses them. No
        # domain is ever lost or duplicated.
        self._uncommitted = 0
        self._commit_batch = 50
        self._commit_interval = 2.0
        self._last_commit = time.monotonic()

    def seed(self, domains, fresh: bool):
        self.seed_stream(iter(domains), fresh=fresh)

    def seed_stream(self, row_iter, fresh: bool, chunk: int = 50_000) -> int:
        """Chunked ingest. Each item is either a plain 'domain' string or a
        (domain, seed_emails) tuple. Never holds the whole input in RAM; duplicates
        collapse on the queue PRIMARY KEY."""
        if fresh:
            self.conn.execute("DELETE FROM queue")
            self.conn.execute("DELETE FROM results")
            self.conn.commit()
        now = time.time()
        sql = ("INSERT OR IGNORE INTO queue(domain,status,attempts,next_eligible,updated,seed_emails)"
               " VALUES(?,'pending',0,0,?,?)")
        buf, total = [], 0
        for item in row_iter:
            if isinstance(item, (tuple, list)):
                d, se = item[0], (item[1] or "")
            else:
                d, se = item, ""
            buf.append((d, now, se))
            if len(buf) >= chunk:
                self.conn.executemany(sql, buf); self.conn.commit()
                total += len(buf); buf = []
        if buf:
            self.conn.executemany(sql, buf); self.conn.commit(); total += len(buf)
        return total

    def fetch_pending(self, limit: int, now: float) -> List[Tuple[str, int, str]]:
        """Selects eligible rows AND atomically claims them (status -> 'inflight')
        in the same call. Claiming matters: _feeder (pipeline.py) polls this in a
        tight loop whenever its in-memory queue has room, with NO wait between
        calls that return rows. Before this claim, a still-'pending' row stayed
        'pending' until a worker eventually finished it (seconds later, bounded
        by real network I/O) -- so every feeder poll in between re-selected and
        re-enqueued the SAME rows. Each duplicate got independently fetched,
        enriched and tallied by a worker: confirmed in testing, 5 seeded domains
        produced 87 tallied outcomes. That's not just wrong stats -- it's the
        dominant reason throughput decayed over a run (9/s -> 2/s): the fewer
        genuinely-fresh eligible rows remained (e.g. most of the queue sitting
        in retry cooldown), the more of the feeder's CPU-bound polling loop was
        spent re-selecting and re-enqueueing the same handful of rows instead of
        making progress, and the more duplicate network fetches piled up behind
        real ones. Claiming means a row can only ever be in ONE queue slot at a
        time; requeue() puts it back to 'pending' for the next real attempt."""
        rows = self.conn.execute(
            "SELECT domain,attempts,seed_emails FROM queue WHERE status='pending' AND next_eligible<=? "
            "ORDER BY attempts ASC, next_eligible ASC LIMIT ?", (now, limit)).fetchall()
        if rows:
            doms = [(r[0],) for r in rows]
            self.conn.executemany("UPDATE queue SET status='inflight' WHERE domain=?", doms)
            self.conn.commit()
        return rows

    def count_pending_total(self) -> int:
        # 'inflight' (claimed by fetch_pending, not yet settled) still counts as
        # outstanding work -- otherwise _feeder's "nothing left, ever" check
        # (pipeline.py) could fire while workers are still processing claimed
        # rows, and the feeder would stop topping up the queue too early.
        return self.conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status IN ('pending','inflight')").fetchone()[0]

    def reclaim_inflight(self) -> int:
        """Rows left 'inflight' by a crash/kill mid-run (claimed by fetch_pending,
        never settled or requeued because the process died first) would otherwise
        sit invisible to fetch_pending forever -- they're not 'pending' anymore,
        but nothing will ever finish them either. Called once at startup/resume
        so a killed run never silently loses whatever it had claimed."""
        n = self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='inflight'").fetchone()[0]
        if n:
            self.conn.execute("UPDATE queue SET status='pending' WHERE status='inflight'")
            self.conn.commit()
        return n

    def count_done(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='done'").fetchone()[0]

    def soonest_eligible(self) -> float:
        row = self.conn.execute(
            "SELECT MIN(next_eligible) FROM queue WHERE status='pending'").fetchone()
        return row[0] if row and row[0] is not None else 0.0

    def _maybe_commit(self, force: bool = False) -> None:
        self._uncommitted += 1
        now = time.monotonic()
        if (force or self._uncommitted >= self._commit_batch
                or (now - self._last_commit) >= self._commit_interval):
            self.conn.commit()
            self._uncommitted = 0
            self._last_commit = now

    def requeue(self, domain: str, attempts: int, cooldown: float, reason: str):
        now = time.time()
        self.conn.execute(
            "UPDATE queue SET status='pending', attempts=?, next_eligible=?, last_reason=?, updated=? "
            "WHERE domain=?",
            (attempts + 1, now + cooldown, reason, now, domain))
        self._maybe_commit()

    def requeue_failed(self) -> int:
        rows = self.conn.execute("SELECT domain FROM results WHERE state IN "
                                 "('unreachable','timed_out','error')").fetchall()
        doms = [r[0] for r in rows]
        if doms:
            self.conn.executemany("UPDATE queue SET status='pending', attempts=0, next_eligible=0, "
                                  "last_reason='retry_failed' WHERE domain=?", [(d,) for d in doms])
            self.conn.executemany("DELETE FROM results WHERE domain=?", [(d,) for d in doms])
            self.conn.commit()
        return len(doms)

    def save_result(self, domain: str, record: Dict):
        rec = dict(record)   # keep full record (incl. emails_list) for the cleanup phase
        self.conn.execute(
            "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?)",
            (domain, time.time(), record.get("state", ""), int(record.get("overall_score", 0) or 0),
             int(record.get("seo_score", 0) or 0), int(record.get("email_count", 0) or 0),
             int(bool(record.get("is_competitor"))), record.get("platform", "") or "",
             json.dumps(rec, ensure_ascii=False, default=str)))
        self.conn.execute("UPDATE queue SET status='done', last_reason=?, updated=? WHERE domain=?",
                          (record.get("state", "done"), time.time(), domain))
        self._maybe_commit()
        self._writes += 1
        if self._writes % self.checkpoint_every == 0:
            try:
                self._maybe_commit(force=True)   # checkpoint must see everything, not just committed-so-far
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass

    def iter_records(self):
        for (blob,) in self.conn.execute("SELECT json_full FROM results"):
            try:
                yield json.loads(blob)
            except Exception:
                continue

    def close(self):
        try:
            self.conn.commit()
        finally:
            self.conn.close()
