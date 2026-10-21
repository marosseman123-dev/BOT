"""Orchestrator: ingest -> DNS/HTTP crawl -> conditional enrich -> store ->
global cleanup -> unified export. One resumable run, one dashboard, one output.

Two input modes (chosen at startup):
  * TXT  -> a plain domain list; every domain gets the full scan.
  * CSV  -> domain[,emails,...]; only rows that ALREADY have an email are processed.
            Those domains are known-alive: the bot re-fetches to add any missing
            emails, runs the SEO audit + cleaner, and always delivers a result
            (the seed email is never lost, even if the live fetch fails).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time

import httpx

from . import cleanup, export
from .config import Config
from .dashboard import build_panel, _enable_vt
from .enrich import enrich
from .fetch import fetch_domain
from .metrics import MetricsWriter
from .storage import Storage
from .shardwriter import ShardedWriter

log = logging.getLogger("unibot")


def _normalize(d: str) -> str:
    d = str(d).strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return d.strip(".")


def _is_csv(path: str) -> bool:
    return path.lower().endswith(".csv")


def _pick_input(cfg: Config) -> None:
    """Interactive startup picker: list *.txt / *.csv in the input folder and let the
    user choose. TXT => full scan; CSV => only domains that already have an email."""
    if not (sys.stdin and sys.stdin.isatty()):
        return
    base = os.path.dirname(cfg.input_path) or "input"
    if not os.path.isdir(base):
        base = "input" if os.path.isdir("input") else "."
    files = sorted(f for f in os.listdir(base) if f.lower().endswith((".txt", ".csv")))
    if not files:
        return
    print("\n[unibot] Vyber vstupny subor:")
    for i, f in enumerate(files, 1):
        kind = ("CSV  -> len domeny ktore UZ maju email (doplni emaily + SEO + cleaner)"
                if _is_csv(f) else "TXT  -> kompletny sken vsetkych domen")
        print(f"   [{i}] {f}    {kind}")
    try:
        ans = input(f"   zadaj cislo (Enter = {os.path.basename(cfg.input_path)}): ").strip()
    except EOFError:
        ans = ""
    if ans.isdigit() and 1 <= int(ans) <= len(files):
        cfg.input_path = os.path.join(base, files[int(ans) - 1])
    mode = "CSV (len domeny s emailom)" if _is_csv(cfg.input_path) else "TXT (vsetky domeny)"
    print(f"[unibot] vstup: {cfg.input_path}  |  rezim: {mode}\n", flush=True)


def _iter_rows(cfg: Config):
    """Yield ingest rows, streaming (never loads the whole file into RAM).
      * TXT -> yields 'domain' (str). Full scan.
      * CSV -> yields (domain, seed_emails) ONLY for rows whose emails column is
               non-empty. Rows without an email are skipped (leads mode)."""
    path = cfg.input_path
    if not os.path.exists(path):
        raise SystemExit(f"[unibot] input not found: {path}")
    if _is_csv(path):
        import pandas as pd
        for chunk in pd.read_csv(path, encoding="utf-8-sig", low_memory=False,
                                 chunksize=100_000, dtype=str, keep_default_na=False):
            cols = {c.strip().lower(): c for c in chunk.columns}
            dcol = cols.get(cfg.domain_column.lower(), chunk.columns[0])
            ecol = cols.get(cfg.emails_column.lower())
            if ecol is None:      # no emails column -> treat CSV like a plain domain list
                for v in chunk[dcol].astype(str):
                    d = _normalize(v)
                    if d and "." in d and d not in ("nan", "none", "null"):
                        yield d
                continue
            sub = chunk[chunk[ecol].astype(str).str.strip().ne("")]
            for dv, ev in zip(sub[dcol].astype(str), sub[ecol].astype(str)):
                d = _normalize(dv)
                e = ev.strip()
                if d and "." in d and d not in ("nan", "none", "null") and e.lower() not in ("nan", "none", "null"):
                    yield (d, e)
    else:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                d = _normalize(line)
                if d and "." in d and d not in ("nan", "none", "null"):
                    yield d


def _http2_available() -> bool:
    try:
        import h2  # noqa: F401
        return True
    except Exception:
        return False


_HTTP2 = _http2_available()


def _session(cfg: Config):
    # verify=False: a lead crawler must reach hosts with broken/mismatched/expired
    # certs (very common on small CZ domains). verify=True would reject them with
    # CERTIFICATE_VERIFY_FAILED. Pool must cover the per-domain fan-out (homepage +
    # up to 3 contact pages + robots + sitemap) or workers throttle to ~half.
    limits = httpx.Limits(max_keepalive_connections=cfg.max_concurrency * 3,
                          max_connections=cfg.max_concurrency * 6)
    timeout = httpx.Timeout(cfg.read_timeout, connect=cfg.connect_timeout)
    return httpx.AsyncClient(http2=_HTTP2, follow_redirects=True, timeout=timeout,
                             limits=limits, verify=False, trust_env=False)


async def _process(domain: str, session, cfg: Config, seed_emails: str = "") -> dict:
    t0 = time.monotonic()
    hp, contacts = await fetch_domain(session, domain, cfg)
    tf = time.monotonic()
    rec = enrich(domain, hp, contacts,
                 analyze_leads_only=cfg.analyze_only_alive_with_email, seed_emails=seed_emails)
    te = time.monotonic()
    t = hp.get("_t", {}) if isinstance(hp, dict) else {}
    rec["_metrics"] = {
        "domain": domain, "state": rec.get("state", ""), "alive": rec.get("alive"),
        "dns_dead": rec.get("dns_dead"), "dead_reason": rec.get("dead_reason", ""), "blocked": rec.get("blocked"),
        "email_count": rec.get("email_count", 0), "new_email_count": rec.get("new_email_count", 0),
        "seeded": rec.get("seeded", False), "http_status": hp.get("status", 0),
        "contact_pages": rec.get("contact_pages_crawled", 0),
        "t_dns": round(t.get("dns", 0), 3), "t_homepage": round(t.get("homepage", 0), 3),
        "t_ancillary": round(t.get("ancillary", 0), 3), "t_contacts": round(t.get("contacts", 0), 3),
        "t_enrich": round(te - tf, 3), "t_total": round(te - t0, 3),
    }
    return rec


def _tally_saved(rec: dict, counter: dict):
    if rec.get("alive"):
        counter["alive"] += 1
        counter["ok"] += 1
        ec = int(rec.get("email_count", 0) or 0)
        if ec > 0:
            counter["with_contact"] += 1
            counter["emails"] += ec
    elif rec.get("state") in ("unreachable", "timed_out") or rec.get("dns_dead"):
        counter["dead"] += 1
    else:
        counter["failed"] += 1


async def _worker(queue, storage, session, cfg, counter, metrics=None):
    while True:
        try:
            domain, attempts, seed_emails = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        seeded = bool((seed_emails or "").strip())
        try:
            rec = await asyncio.wait_for(_process(domain, session, cfg, seed_emails),
                                         timeout=cfg.domain_hard_timeout)
            m = rec.pop("_metrics", None)
            if metrics and m:
                metrics.write(m)
            dns_dead = bool(rec.get("dns_dead"))
            state = rec.get("state", "")
            blocked = bool(rec.get("blocked")) or bool(rec.get("challenged"))
            transient = (not dns_dead) and (state in ("unreachable", "timed_out") or blocked)
            if transient and attempts + 1 < cfg.max_attempts:
                cd = cfg.ban_cooldown if blocked else cfg.requeue_cooldown
                storage.requeue(domain, attempts, cd, state or "retry")
                counter["retries"] += 1
            else:
                storage.save_result(domain, rec)   # dead settles here immediately
                _tally_saved(rec, counter)
        except asyncio.TimeoutError:
            if attempts + 1 < cfg.max_attempts:
                storage.requeue(domain, attempts, cfg.requeue_cooldown, "hard_timeout")
                counter["retries"] += 1
            else:
                # keep the seed email even when the live fetch never succeeded
                rec = enrich(domain, {"ok": False}, [], seed_emails=seed_emails,
                             analyze_leads_only=cfg.analyze_only_alive_with_email)
                storage.save_result(domain, rec)
                _tally_saved(rec, counter) if seeded else counter.__setitem__("dead", counter["dead"] + 1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("worker error %s: %r", domain, e)
            if attempts + 1 < cfg.max_attempts:
                storage.requeue(domain, attempts, cfg.requeue_cooldown, f"err:{type(e).__name__}")
                counter["retries"] += 1
            else:
                rec = enrich(domain, {"ok": False}, [], seed_emails=seed_emails,
                             analyze_leads_only=cfg.analyze_only_alive_with_email)
                storage.save_result(domain, rec)
                _tally_saved(rec, counter) if seeded else counter.__setitem__("failed", counter["failed"] + 1)
        finally:
            counter["done"] += 1
            queue.task_done()


async def _dashboard(storage, cfg, counter, total, stop):
    tty = sys.stdout.isatty()
    if tty:
        _enable_vt()
    start = time.time()
    drawn = 0
    while not stop.is_set():
        try:
            done = storage.count_done()
            pending = storage.count_pending_total()
            el = time.time() - start
            rate = counter["done"] / el if el else 0.0
            eta = pending / rate if rate else 0.0
            from . import fetch as _F
            note = _F.error_summary(4) if _F.FETCH_ERRORS else ""
            lines = build_panel(run=cfg.run_name, total=total, done=done, pending=pending,
                                rate=rate, eta=eta, elapsed=el, conc=cfg.max_concurrency,
                                alive=counter["alive"], dead=counter["dead"],
                                with_contact=counter["with_contact"], emails=counter["emails"],
                                ok=counter["ok"], retrying=counter["retries"],
                                failed=counter["failed"], note=note, color=tty)
            if tty:
                if drawn:
                    sys.stdout.write(f"\033[{drawn}A")
                for ln in lines:
                    sys.stdout.write("\033[2K" + ln + "\n")
                sys.stdout.flush()
                drawn = len(lines)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5 if tty else 15)
        except asyncio.TimeoutError:
            pass


def _cleaned_records(storage, cfg):
    """Yield records with 'emails_final' set. Small runs get full cross-row dedup in
    RAM; huge runs stream per-row (competitor + blacklist only) to stay bounded."""
    total = storage.count_done()
    if total <= cfg.dedup_max_inmemory:
        records = list(storage.iter_records())
        stats = cleanup.apply(records, cfg)
        log.info("cleanup: competitor=%d dedup=%d blacklist=%d kept=%d",
                 stats["competitor"], stats["dedup"], stats["blacklist"], stats["kept"])
        for r in records:
            yield r
    else:
        log.warning("large run (%d results) -> streaming export; cross-row dedup skipped "
                    "(competitor + blacklist still applied)", total)
        for r in storage.iter_records():
            cleanup.apply_row(r, cfg)
            yield r


def _export_all(storage, out_root, cfg):
    """Stream to numbered CSV+TXT shards (<= rows_per_shard each). Constant memory."""
    writer = None
    written = 0
    try:
        for rec in _cleaned_records(storage, cfg):
            if cfg.output_leads_only and not rec.get("emails_final"):
                continue
            row = export.to_row(rec)
            if writer is None:
                writer = ShardedWriter(out_root, "unified_results", cfg.rows_per_shard,
                                       export.column_order(row))
            writer.write(row, export.txt_block(rec))
            written += 1
    except Exception as e:
        log.error("export error: %s", e)
    finally:
        shards = writer.idx if writer else 0
        if writer:
            writer.close()
        log.info("exported %d rows across %d shard(s) -> %s", written, shards, out_root)
        print(f"[unibot] exported {written:,} rows across {shards} shard(s)", flush=True)


def _quiet_handler(loop, context):
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return  # remote forcibly closed the socket -- harmless, don't spam the log
    loop.default_exception_handler(context)


async def run(cfg: Config):
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_quiet_handler)
        # NOTE: do NOT override the default executor. httpx resolves DNS on it; the
        # blocking DNS/TLS lookups run on fetch._PROBE_POOL so they can't starve it.
    except Exception:
        pass

    _pick_input(cfg)                     # startup file/mode chooser
    leads_mode = _is_csv(cfg.input_path)

    out_root = os.path.join(cfg.out_dir, cfg.run_name)
    os.makedirs(out_root, exist_ok=True)
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        filename=os.path.join(out_root, cfg.log_file),
                        format="%(asctime)s | %(levelname)s | %(message)s")
    storage = Storage(os.path.join(out_root, cfg.db_file), checkpoint_every=cfg.wal_checkpoint_every)
    if not cfg.fresh and not cfg.assume_resume and sys.stdin and sys.stdin.isatty():
        d0, p0 = storage.count_done(), storage.count_pending_total()
        if d0 + p0 > 0:
            print(f"\n[unibot] Existing run '{cfg.run_name}': {d0:,} done, {p0:,} pending.")
            try:
                ans = input("           [R] resume   [F] fresh   (default R): ").strip().lower()
            except EOFError:
                ans = ""
            if ans.startswith("f"):
                cfg.fresh = True
    print("[unibot] ingesting input (streaming) ...", flush=True)
    seeded = storage.seed_stream(_iter_rows(cfg), fresh=cfg.fresh, chunk=cfg.ingest_chunk)
    if getattr(cfg, "_retry_failed", False) and not cfg.fresh:
        print(f"[unibot] re-queued {storage.requeue_failed():,} previously failed domains", flush=True)
    total = storage.count_pending_total() + storage.count_done()
    tag = "leads (CSV: len domeny s emailom)" if leads_mode else "scan (TXT: vsetky domeny)"
    print(f"[unibot] mode={tag}  ingested={seeded:,} pending={storage.count_pending_total():,} "
          f"done={storage.count_done():,}", flush=True)

    counter = {"done": 0, "ok": 0, "retries": 0, "failed": 0,
               "alive": 0, "dead": 0, "with_contact": 0, "emails": 0}
    metrics = MetricsWriter(os.path.join(out_root, "metrics.jsonl"), fresh=cfg.fresh, sample=cfg.metrics_sample) if cfg.metrics else None
    stop = asyncio.Event()
    dash = asyncio.ensure_future(_dashboard(storage, cfg, counter, total, stop)) if cfg.progress else None
    try:
        while True:
            now = time.time()
            batch = storage.fetch_pending(cfg.batch_size, now)
            if not batch:
                if storage.count_pending_total() == 0:
                    break
                await asyncio.sleep(max(0.5, min(10.0, storage.soonest_eligible() - time.time())))
                continue
            async with _session(cfg) as session:
                q: asyncio.Queue = asyncio.Queue()
                for row in batch:
                    q.put_nowait(row)
                await asyncio.gather(*[_worker(q, storage, session, cfg, counter, metrics)
                                       for _ in range(cfg.max_concurrency)],
                                     return_exceptions=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if dash:
            await asyncio.gather(dash, return_exceptions=True)
            if sys.stdout.isatty():
                sys.stdout.write("\n")
        if metrics:
            metrics.close()
        _export_all(storage, out_root, cfg)
        storage.close()
        from . import fetch as _F
        log.info("fetch errors: %s", _F.error_summary())
        print(f"[unibot] done: alive={counter['alive']} dead={counter['dead']} "
              f"with_contact={counter['with_contact']} emails={counter['emails']} "
              f"failed={counter['failed']}", flush=True)
        print(f"[unibot] fetch errors: {_F.error_summary()}", flush=True)
