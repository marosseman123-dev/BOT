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
        return self.conn.execute(
            "SELECT domain,attempts,seed_emails FROM queue WHERE status='pending' AND next_eligible<=? "
            "ORDER BY attempts ASC, next_eligible ASC LIMIT ?", (now, limit)).fetchall()

    def count_pending_total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]

    def count_done(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='done'").fetchone()[0]

    def soonest_eligible(self) -> float:
        row = self.conn.execute(
            "SELECT MIN(next_eligible) FROM queue WHERE status='pending'").fetchone()
        return row[0] if row and row[0] is not None else 0.0

    def requeue(self, domain: str, attempts: int, cooldown: float, reason: str):
        now = time.time()
        self.conn.execute(
            "UPDATE queue SET attempts=?, next_eligible=?, last_reason=?, updated=? WHERE domain=?",
            (attempts + 1, now + cooldown, reason, now, domain))
        self.conn.commit()

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
        self.conn.commit()
        self._writes += 1
        if self._writes % self.checkpoint_every == 0:
            try:
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
