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
import atexit
import logging
import os
import re
import signal
import sys
import threading
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import AsyncExitStack

import httpx

from . import cleanup, export
from .config import Config
from .dashboard import build_panel, _enable_vt
from .enrich import enrich
from .fetch import fetch_domain
from .metrics import MetricsWriter
from .runlock import RunLock
from .runtime import apply_runtime_profile
from .request_gate import RequestGate
from .storage import Storage
from .shardwriter import ShardedWriter
from .throttle import Gate, Throttle

log = logging.getLogger("unibot")


def _ignore_sigint_in_worker() -> None:
    """ProcessPoolExecutor initializer: on Windows, Ctrl+C (CTRL_C_EVENT) is
    delivered to the WHOLE console process group by default, which includes
    every spawned worker process, not just this one. Without this, each
    worker independently raises KeyboardInterrupt out of its own task-fetch
    loop and prints its own unhandled traceback -- cosmetically alarming (and
    observed in production: 5+ separate 'Process SpawnProcess-N: Traceback...'
    blocks on a single Ctrl+C) even though it's functionally harmless, since
    the parent's own shutdown path (signal handler -> shutdown event -> stop
    sentinels -> pool.shutdown()) already tears worker processes down cleanly
    on its own. Workers ignoring SIGINT just leaves that one path in charge."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


class PoolBox:
    """Wraps a ProcessPoolExecutor and transparently rebuilds it if a worker
    process dies unexpectedly.

    By design, once ANY worker in a ProcessPoolExecutor terminates abnormally
    (a crash, not a normal Python exception -- those are caught and returned
    as a result, not a pool break), the ENTIRE pool becomes permanently
    unusable: every subsequent submission immediately raises
    BrokenProcessPool. Confirmed in production on this exact machine/Python
    build: one crashed worker (Python 3.14 on Windows -- this project already
    hit a comparable native-stability issue with aiohttp's C extensions on
    this same interpreter, see fetch.py's docstring) turned into a full
    cascade where EVERY remaining domain failed immediately, and the whole
    process went silent and died within seconds. Rebuilding the pool on the
    first break, instead of letting the cascade run, keeps one bad page from
    taking down the rest of a 478k-domain run.

    The old (broken) pool's own shutdown() is NOT awaited inline: on this same
    fragile combination, a broken pool's internal manager thread could itself
    be wedged, and blocking recovery on it would just move the hang here
    instead of preventing it. It's torn down best-effort on a daemon thread
    instead, so a stuck cleanup can never stop the run.
    """

    def __init__(self, factory):
        self._factory = factory
        self.pool = factory()
        self._lock = asyncio.Lock()

    async def run(self, fn, *args):
        loop = asyncio.get_running_loop()
        current = self.pool
        try:
            return await loop.run_in_executor(current, fn, *args)
        except BrokenProcessPool:
            async with self._lock:
                if self.pool is current:    # first to see this break -- rebuild once
                    log.warning("process pool broken (a worker crashed) -- rebuilding "
                               "(this domain and any others mid-flight on the old pool "
                               "will be retried)")

                    def _bg_shutdown():
                        try:
                            current.shutdown(wait=True, cancel_futures=True)
                        except Exception:
                            pass
                    threading.Thread(target=_bg_shutdown, daemon=True).start()
                    self.pool = self._factory()
            return await loop.run_in_executor(self.pool, fn, *args)

    def shutdown(self, wait: bool, cancel_futures: bool = True) -> None:
        self.pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    def terminate_workers(self, join_timeout: float = 3.0) -> int:
        """Directly kill every live worker OS process right now, with no
        cooperation required from them. Used only on the second Ctrl+C.

        Why this exists: pool.shutdown(wait=False) (used elsewhere) just puts
        a stop sentinel on the workers' call queue and returns immediately --
        it does NOT confirm they actually exit. Confirmed in production: after
        a double-Ctrl+C and 'forcing immediate exit', 7 worker processes were
        still alive and consuming CPU minutes later, orphaned from their
        now-dead parent, while a SEPARATE later run of the bot sat stalled
        (contending with them for the machine's resources). os._exit() in the
        parent tears down the PARENT only -- it does nothing at all for
        already-spawned children. Reaching into the executor's own
        `_processes` dict (pid -> multiprocessing.Process, the same map
        ProcessPoolExecutor itself uses internally) and calling .terminate()
        sends an unconditional OS-level kill to each one directly, with no
        dependency on IPC, queues, or the worker's own cooperation."""
        n = 0
        try:
            procs = list(getattr(self.pool, "_processes", {}).values())
        except Exception:
            procs = []
        for proc in procs:
            try:
                if proc.is_alive():
                    proc.terminate()
                    n += 1
            except Exception:
                pass
        # Do not return control to a new run while old parser children are
        # still alive. A bounded join keeps shutdown responsive even when one
        # child was already in a native-library failure.
        for proc in procs:
            try:
                proc.join(timeout=join_timeout)
            except Exception:
                pass
        return n


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
    # CERTIFICATE_VERIFY_FAILED.
    #
    # This session belongs to one persistent domain worker. Sharing one client
    # across the entire crawl also shares httpx's CookieJar across unrelated
    # sites. Cookie matching is linear in jar size; after thousands of domains
    # it consumed one full event-loop CPU core and progressively slowed every
    # request. RequestGate remains the authoritative global HTTP cap.
    per_worker_connections = max(4, cfg.max_contact_pages + 2)
    limits = httpx.Limits(max_keepalive_connections=2,
                          max_connections=per_worker_connections)
    timeout = httpx.Timeout(cfg.read_timeout, connect=cfg.connect_timeout)
    return httpx.AsyncClient(http2=_HTTP2, follow_redirects=True, timeout=timeout,
                             limits=limits, verify=False, trust_env=False)


async def _process(domain: str, session, cfg: Config, pool, request_gate,
                   seed_emails: str = "") -> dict:
    t0 = time.monotonic()
    wall_t0 = time.time()
    hp, contacts = await fetch_domain(session, domain, cfg, request_gate)
    tf = time.monotonic()
    # enrich() is pure CPU (BeautifulSoup/lxml parsing + regex audit), not I/O --
    # running it inline here would serialize every domain's parsing onto this
    # one event-loop thread regardless of max_concurrency (see config.py's
    # process_workers comment for the measured 1-core-pegged evidence). Off it
    # goes to a real OS process (via PoolBox, which survives a crashed worker)
    # so it runs on whichever core is free.
    rec = await pool.run(enrich, domain, hp, contacts,
                         cfg.analyze_only_alive_with_email, seed_emails)
    te = time.monotonic()
    t = hp.get("_t", {}) if isinstance(hp, dict) else {}
    rec["_metrics"] = {
        "domain": domain, "state": rec.get("state", ""), "alive": rec.get("alive"),
        "started_at_unix": round(wall_t0, 3), "finished_at_unix": round(time.time(), 3),
        "dns_dead": rec.get("dns_dead"), "dead_reason": rec.get("dead_reason", ""), "blocked": rec.get("blocked"),
        "email_count": rec.get("email_count", 0), "new_email_count": rec.get("new_email_count", 0),
        "seeded": rec.get("seeded", False), "http_status": hp.get("status", 0),
        "contact_pages": rec.get("contact_pages_crawled", 0),
        "t_dns": round(t.get("dns", 0), 3), "t_homepage": round(t.get("homepage", 0), 3),
        "t_homepage_gate_wait": round(t.get("homepage_gate_wait", 0), 3),
        "t_homepage_wire": round(t.get("homepage_wire", 0), 3),
        "t_ancillary": round(t.get("ancillary", 0), 3), "t_contacts": round(t.get("contacts", 0), 3),
        "t_contacts_gate_wait": round(t.get("contacts_gate_wait", 0), 3),
        "t_contacts_wire": round(t.get("contacts_wire", 0), 3),
        "t_enrich": round(te - tf, 3), "t_total": round(te - t0, 3),
    }
    return rec


def _tally_saved(rec: dict, counter: dict):
    counter["settled"] += 1   # one domain reached a final (non-retryable) state
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


async def _worker(queue, storage, session, cfg, counter, gate, stop, pool, request_gate, metrics=None):
    """Persistent worker: blocks on the shared queue and keeps consuming until
    it receives the None stop-sentinel (see _feeder/run()). There is no more
    per-batch 'drain this list of 2000 then wait for everyone before starting
    the next 2000' -- that lockstep design meant every batch's wall-clock time
    was gated by its single slowest straggler (up to domain_hard_timeout,
    measured up to ~17s idle gaps affecting the ENTIRE pool on a real run),
    even though most workers had long since finished. A worker that finishes
    fast now immediately picks up whatever the feeder has queued next."""
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            return
        domain, attempts, seed_emails = item
        # The prior domain is fully finished here. Preserve cookies throughout
        # redirects/homepage/contact requests for one site, then discard them
        # before this worker moves to an unrelated domain. Jar lookup therefore
        # remains constant-time over a multi-million-domain run.
        session.cookies.clear()
        if stop.is_set():
            # Already dequeued but not yet started: its DB row is still
            # 'pending' and untouched, so skipping it here loses nothing --
            # the next resume (or this same run, if it wasn't a real shutdown)
            # picks it up fresh. Drain quickly rather than process during
            # shutdown wind-down.
            queue.task_done()
            continue
        # Throttle gate: caps how many domains are ACTUALLY in flight at once to
        # the live adaptive target, independent of how many worker coroutines
        # exist.
        if not await gate.acquire(stop):
            queue.task_done()
            continue
        try:
            seeded = bool((seed_emails or "").strip())
            try:
                rec = await asyncio.wait_for(_process(domain, session, cfg, pool, request_gate, seed_emails),
                                             timeout=cfg.domain_hard_timeout)
                m = rec.pop("_metrics", None)
                if metrics and m:
                    metrics.write(m)
                dns_dead = bool(rec.get("dns_dead"))
                dead_reason = rec.get("dead_reason", "")
                # A genuine NXDOMAIN ("dns") is settled dead immediately -- retrying it
                # wastes time for no benefit. A TRANSIENT resolver hiccup ("dns_transient" =
                # WSATRY_AGAIN/EAI_AGAIN, "the DNS server didn't answer in time", NOT "this
                # domain doesn't exist"; or "dns_timeout" = our own wait_for gave up early)
                # is a completely different thing and deserves the same retry any other
                # transient failure gets. Confirmed in production: 13 of 15 domains marked
                # "dns_dead" this way resolved correctly moments later in isolation -- the
                # bug was treating every DNS failure as permanent with zero retries.
                permanent_dns_dead = dns_dead and dead_reason == "dns"
                transient_dns = dns_dead and dead_reason in ("dns_transient", "dns_timeout")
                state = rec.get("state", "")
                blocked = bool(rec.get("blocked")) or bool(rec.get("challenged"))
                gate.throttle.record(blocked)   # only an explicit block/challenge counts as a ban signal
                if m and m.get("http_status"):   # only real responses -- a fast dead-domain
                    # Queue wait is our own scheduler, not internet latency.
                    gate.throttle.record_latency(m.get("t_homepage_wire", 0))
                transient = (not permanent_dns_dead) and (
                    transient_dns or state in ("unreachable", "timed_out") or blocked)
                if transient and attempts + 1 < cfg.max_attempts:
                    cd = cfg.ban_cooldown if blocked else cfg.requeue_cooldown
                    storage.requeue(domain, attempts, cd, state or "retry")
                    counter["retries"] += 1
                else:
                    storage.save_result(domain, rec)   # dead settles here immediately
                    _tally_saved(rec, counter)
                    gate.throttle.record_settled()
            except asyncio.TimeoutError:
                # Ambiguous signal (could be a slow dead domain, could be a silent
                # drop by a WAF) -- deliberately NOT fed to the throttle as a block;
                # this broad historical .sk list is known (measured) to contain a
                # large share of genuinely dead/parked domains that always time out
                # regardless of our behaviour, and conflating that with "we're being
                # banned" would throttle the whole run down for no real reason.
                if attempts + 1 < cfg.max_attempts:
                    storage.requeue(domain, attempts, cfg.requeue_cooldown, "hard_timeout")
                    counter["retries"] += 1
                else:
                    # keep the seed email even when the live fetch never succeeded
                    rec = enrich(domain, {"ok": False}, [], seed_emails=seed_emails,
                                 analyze_leads_only=cfg.analyze_only_alive_with_email)
                    storage.save_result(domain, rec)
                    if seeded:
                        _tally_saved(rec, counter)
                    else:
                        counter["dead"] += 1
                        counter["settled"] += 1
                    gate.throttle.record_settled()
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
                    if seeded:
                        _tally_saved(rec, counter)
                    else:
                        counter["failed"] += 1
                        counter["settled"] += 1
                    gate.throttle.record_settled()
            finally:
                counter["done"] += 1
                queue.task_done()
        finally:
            await gate.release()


async def _dashboard(storage, cfg, counter, total, stop, throttle=None):
    """Renders every 0.5s off in-memory counters only -- NOT storage.count_done()/
    count_pending_total(). Those are 'SELECT COUNT(*) FROM queue WHERE status=...'
    scans: cheap early on, but their cost grows with the number of matching rows,
    and this loop was calling BOTH of them twice a second on the same thread as
    the event loop. On a run climbing into the hundreds of thousands of 'done'
    rows this was measurably why throughput decayed over time (2.9/s and falling,
    never stabilizing) even though nothing about the actual crawl got slower --
    the dashboard itself was eating a growing, self-inflicted share of every
    second. counter['settled'] is updated in-process the instant a domain
    reaches its final state, so it's just as accurate with zero DB cost."""
    tty = sys.stdout.isatty()
    if tty:
        _enable_vt()
    start = time.time()
    drawn = 0
    # rate/ETA come from a rolling ~60s window of (time, settled) samples, not
    # counter["done"]/elapsed-since-process-start. That lifetime average has two
    # problems: (1) counter["done"] counts every worker iteration INCLUDING
    # retries, not net domains finished, and (2) a cumulative average can only
    # ever drift toward the steady-state rate -- if the run opened with a
    # faster couple of minutes (a warm queue, throttle still probing how high
    # it can go before real congestion), the lifetime average keeps sliding
    # down toward "normal" forever even after throughput has long since
    # stabilized, which reads as "still getting slower" when it isn't.
    # Measured on this exact run: first 2 min = 5.98/s, most recent 2 min =
    # 3.41/s, but the per-30s rate over the last 10 min was flat/noisy in the
    # 2.4-5.2/s band -- stable, just never visible through a lifetime average.
    rate_samples: "deque[tuple[float, int]]" = deque(maxlen=240)
    rate_window_s = 60.0
    while not stop.is_set():
        try:
            done = counter["settled"]
            pending = max(0, total - done)
            el = time.time() - start
            now_t = time.time()
            rate_samples.append((now_t, done))
            while len(rate_samples) > 1 and now_t - rate_samples[0][0] > rate_window_s:
                rate_samples.popleft()
            t0, d0 = rate_samples[0]
            span = now_t - t0
            rate = (done - d0) / span if span >= 3 else (done / el if el else 0.0)
            eta = pending / rate if rate else 0.0
            from . import fetch as _F
            note = _F.error_summary(4) if _F.FETCH_ERRORS else ""
            conc = throttle.target if throttle else cfg.max_concurrency
            lines = build_panel(run=cfg.run_name, total=total, done=done, pending=pending,
                                rate=rate, eta=eta, elapsed=el, conc=conc,
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
    """Yield records with 'emails_final' set using bounded-memory cleanup."""
    total = storage.count_done()
    if total <= cfg.dedup_max_inmemory:
        records = list(storage.iter_records())
        stats = cleanup.apply(records, cfg)
        log.info("cleanup: competitor=%d dedup=%d blacklist=%d kept=%d",
                 stats["competitor"], stats["dedup"], stats["blacklist"], stats["kept"])
        for r in records:
            yield r
    else:
        log.info("large run (%d results) -> two-pass streaming cleanup; "
                 "cross-row dedup preserved without loading full records", total)
        yield from cleanup.iter_apply_streaming(lambda: storage.iter_records(), cfg)


def _export_all(storage, out_root, cfg) -> dict:
    """Stream to numbered CSV+TXT shards (<= rows_per_shard each). Constant memory.
    Returns a success-rate/quality summary dict, also logged and printed --
    this is the "how good were the results" answer, not just "how many rows"."""
    writer = None
    written = 0
    stats = {"total": 0, "alive": 0, "blocked": 0, "with_email": 0, "competitor": 0,
             "cat": {"C1": 0, "C2": 0, "C3": 0, "": 0}, "score_sum": 0, "score_n": 0}
    try:
        for rec in _cleaned_records(storage, cfg):
            stats["total"] += 1
            if rec.get("alive"):
                stats["alive"] += 1
            if rec.get("blocked") or rec.get("challenged"):
                stats["blocked"] += 1
            if rec.get("emails_final"):
                stats["with_email"] += 1
            if rec.get("is_competitor"):
                stats["competitor"] += 1
            cat = rec.get("category") or ""
            stats["cat"][cat] = stats["cat"].get(cat, 0) + 1
            if cat:
                stats["score_sum"] += int(rec.get("score") or 0)
                stats["score_n"] += 1
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

    t = max(1, stats["total"])
    avg_score = round(stats["score_sum"] / stats["score_n"], 1) if stats["score_n"] else 0.0
    summary = (
        f"quality: total={stats['total']} alive={stats['alive']} ({100*stats['alive']/t:.1f}%) "
        f"blocked={stats['blocked']} ({100*stats['blocked']/t:.1f}%) "
        f"with_email={stats['with_email']} ({100*stats['with_email']/t:.1f}%) "
        f"competitor_dropped={stats['competitor']} "
        f"avg_score={avg_score} "
        f"category C1={stats['cat']['C1']} C2={stats['cat']['C2']} C3={stats['cat']['C3']} "
        f"not_analysed={stats['cat']['']}"
    )
    log.info(summary)
    print(f"[unibot] {summary}", flush=True)
    return stats


def _quiet_handler(loop, context):
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return  # remote forcibly closed the socket -- harmless, don't spam the log
    loop.default_exception_handler(context)


def _install_sigint(shutdown: asyncio.Event, pool_ref: dict) -> None:
    """Real signal.signal()-based Ctrl+C handling instead of relying on
    KeyboardInterrupt propagation through asyncio.

    Why: loop.add_signal_handler() raises NotImplementedError on Windows'
    ProactorEventLoop (required here for scale past 512 sockets -- see
    __main__.py), and letting Python's default handler raise KeyboardInterrupt
    at whatever await happens to be suspended when the signal lands does NOT
    cancel the in-flight worker Tasks it interrupts -- they keep running
    orphaned on the loop while the `finally` block below closes the database
    and exports out from under them, which is exactly the "doesn't stop
    properly / doesn't save everything" bug this replaces. signal.signal()
    runs synchronously on the main thread between bytecodes (same thread as
    the loop -- no call_soon_threadsafe needed), just like the loop itself, so
    setting the Event directly here is safe.

    First Ctrl+C: stop picking up NEW domains, let in-flight ones finish
    (bounded by cfg.shutdown_grace_s), save+export everything settled so far.
    Second Ctrl+C: the user has already asked once and it isn't responding
    fast enough -- exit immediately. os._exit() skips all further Python
    cleanup (by design -- this is the "get me out NOW" escape hatch), which
    would otherwise orphan the enrich() process-pool workers (observed while
    testing this exact path), so this makes one fast, non-blocking attempt to
    tear the pool down first; pool_ref is a dict filled in by run() once the
    pool exists (it doesn't exist yet when this handler is installed).
    """
    state = {"n": 0}

    def _handler(signum, frame):
        state["n"] += 1
        if state["n"] == 1:
            print("\n[unibot] Ctrl+C: stopping -- finishing in-flight domains and saving "
                  "progress (press Ctrl+C again to force-quit immediately) ...", flush=True)
            shutdown.set()
        else:
            pool = pool_ref.get("pool")
            n_killed = 0
            if pool is not None:
                try:
                    n_killed = pool.terminate_workers()   # unconditional OS-level kill, no cooperation needed
                except Exception:
                    pass
            print(f"\n[unibot] second Ctrl+C: forcing immediate exit "
                 f"({n_killed} worker process(es) terminated; SQLite data already "
                 f"saved so far is intact -- re-run to resume).", flush=True)
            os._exit(130)

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        pass   # not the main thread / platform doesn't support it -- best effort


async def _feeder(storage, cfg, q: "asyncio.Queue", shutdown: asyncio.Event,
                  counter: dict, total: int) -> None:
    """Keeps the shared queue topped up from the persistent SQLite queue.
    Replaces the old 'pull batch_size, wait for the whole batch, pull the next
    batch_size' loop, which forced every one of the 72 workers to sit idle
    between batches until the single slowest straggler in the PREVIOUS batch
    hit its timeout. Here a worker that finishes early just gets whatever the
    feeder has already staged next -- no synchronization point between them.

    Returns when there is truly nothing left to do (queue drained forever) or
    when shutdown is requested; either way the caller pushes stop sentinels."""
    low_water = max(cfg.max_concurrency * 2, 40)
    while not shutdown.is_set():
        if q.qsize() >= low_water:
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=0.3)
            except asyncio.TimeoutError:
                pass
            continue
        rows = storage.fetch_pending(cfg.batch_size, time.time())
        if rows:
            for row in rows:
                await q.put(row)
            continue
        # 'nothing eligible RIGHT NOW' (e.g. a lull where most of the queue is
        # sitting in retry cooldown) used to call storage.count_pending_total()
        # here -- a 'SELECT COUNT(*) FROM queue WHERE status IN (...)' scan
        # over every not-yet-done row. Same anti-pattern already fixed on the
        # dashboard: cheap on a small table, but this branch can fire every
        # 0.2-5s during a cooldown lull, and its cost grows with the table, so
        # on a large run it became a second, independent way the crawl fed
        # itself less and less CPU over time. counter['settled'] is updated
        # in-process the instant a domain reaches ANY final state, so "is
        # there truly nothing left, ever" is just an in-memory comparison.
        if counter["settled"] >= total:
            return                      # nothing left, ever
        delay = max(0.2, min(5.0, storage.soonest_eligible() - time.time()))
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


async def _run_workers(storage, cfg, counter, gate, shutdown, metrics, pool, total: int,
                       request_gate: RequestGate) -> None:
    """One long-lived httpx session + a fixed pool of persistent workers fed
    continuously by _feeder, for the whole run (not recreated every batch --
    that also discarded the connection pool every ~2000 domains for no
    benefit, since a fresh AsyncClient is not free to build and tear down)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=cfg.batch_size * 2)
    async with AsyncExitStack() as sessions:
        # A worker-private jar preserves same-domain sessions without mixing
        # state from unrelated sites. Every client still shares RequestGate,
        # so aggregate outbound concurrency cannot exceed the configured cap.
        clients = [await sessions.enter_async_context(_session(cfg))
                   for _ in range(cfg.max_concurrency)]
        workers = [asyncio.ensure_future(
                       _worker(q, storage, client, cfg, counter, gate, shutdown,
                               pool, request_gate, metrics))
                   for client in clients]
        feeder = asyncio.ensure_future(_feeder(storage, cfg, q, shutdown, counter, total))
        await feeder
        for _ in workers:
            await q.put(None)           # stop sentinels: one per worker
        gather_task = asyncio.ensure_future(asyncio.gather(*workers, return_exceptions=True))
        if not shutdown.is_set():
            await gather_task            # natural end of work -- no deadline needed
            return
        try:
            await asyncio.wait_for(gather_task, timeout=cfg.shutdown_grace_s)
        except asyncio.TimeoutError:
            still = [w for w in workers if not w.done()]
            log.warning("shutdown: %d worker(s) still busy after %.0fs grace period -- "
                       "cancelling (their current domain stays queued for the next resume)",
                       len(still), cfg.shutdown_grace_s)
            for w in still:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)


async def run(cfg: Config):
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_quiet_handler)
        # NOTE: do NOT override the default executor. httpx resolves DNS on it; the
        # blocking DNS lookups run on fetch._PROBE_POOL so they can't starve it.
    except Exception:
        pass

    _pick_input(cfg)                     # startup file/mode chooser
    leads_mode = _is_csv(cfg.input_path)

    out_root = os.path.join(cfg.out_dir, cfg.run_name)
    os.makedirs(out_root, exist_ok=True)
    # Deliberately independent of --out: this installation must never run two
    # crawlers at once, even when their output folders differ.
    install_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_lock = RunLock(install_root)
    run_lock.acquire()
    # Covers exceptions before the normal worker-cleanup block is entered.
    # atexit also runs when the console is closed normally.
    atexit.register(run_lock.release)
    profile = apply_runtime_profile(cfg)
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        filename=os.path.join(out_root, cfg.log_file),
                        format="%(asctime)s | %(levelname)s | %(message)s")
    # httpx logs every successful request at INFO.  At crawler scale that turns
    # the event loop into a synchronous disk logger and creates huge run logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log.info("runtime profile: cpu=%d ram_gib=%.1f tcp_ports=%s workers=%d domains=%d..%d http_requests=%d",
             profile.logical_cpus, profile.memory_bytes / 1024**3,
             profile.tcp_dynamic_ports or "unknown", cfg.process_workers,
             cfg.min_concurrency, cfg.max_concurrency, cfg.max_http_concurrency)
    storage = Storage(os.path.join(out_root, cfg.db_file), checkpoint_every=cfg.wal_checkpoint_every)
    if not cfg.fresh:
        n_reclaimed = storage.reclaim_inflight()
        if n_reclaimed:
            log.info("resumed: reclaimed %d domain(s) left 'inflight' by a previous crash/stop", n_reclaimed)
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
    pending0, done0 = storage.count_pending_total(), storage.count_done()
    total = pending0 + done0
    tag = "leads (CSV: len domeny s emailom)" if leads_mode else "scan (TXT: vsetky domeny)"
    print(f"[unibot] mode={tag}  ingested={seeded:,} pending={pending0:,} "
          f"done={done0:,}", flush=True)

    # 'settled' seeds from done0 (rows already final from a previous session on
    # resume) and from then on is updated only in-process -- see _dashboard()'s
    # docstring for why this replaces polling storage.count_done() every tick.
    counter = {"done": 0, "ok": 0, "retries": 0, "failed": 0, "settled": done0,
               "alive": 0, "dead": 0, "with_contact": 0, "emails": 0}
    metrics = MetricsWriter(os.path.join(out_root, "metrics.jsonl"), fresh=cfg.fresh, sample=cfg.metrics_sample) if cfg.metrics else None

    # `shutdown` gates worker pickups (set on Ctrl+C); `dash_stop` only ends the
    # live dashboard once the run has fully wound down, so progress/the
    # "finishing in-flight domains" state stays visible during the grace period.
    shutdown = asyncio.Event()
    dash_stop = asyncio.Event()
    pool_ref: dict = {}
    _install_sigint(shutdown, pool_ref)
    throttle = Throttle(min_conc=cfg.min_concurrency, max_conc=cfg.max_concurrency)
    gate = Gate(throttle)
    request_gate = RequestGate(cfg.max_http_concurrency)
    n_proc = cfg.process_workers or max(2, (os.cpu_count() or 4) - 1)
    pool = PoolBox(lambda: ProcessPoolExecutor(max_workers=n_proc, initializer=_ignore_sigint_in_worker))
    pool_ref["pool"] = pool
    log.info("CPU-bound work (enrich) offloaded to %d process(es)", n_proc)

    dash = asyncio.ensure_future(
        _dashboard(storage, cfg, counter, total, dash_stop, throttle)) if cfg.progress else None
    try:
        if storage.count_pending_total() > 0:
            await _run_workers(storage, cfg, counter, gate, shutdown, metrics, pool, total, request_gate)
    except KeyboardInterrupt:
        shutdown.set()
    finally:
        interrupted = shutdown.is_set()
        shutdown.set()
        dash_stop.set()
        if dash:
            await asyncio.gather(dash, return_exceptions=True)
            if sys.stdout.isatty():
                sys.stdout.write("\n")
        if interrupted:
            terminated = pool.terminate_workers()
            if terminated:
                log.info("shutdown: terminated %d parser worker(s)", terminated)
            # Workers have received OS-level termination; do not let an
            # executor-manager edge case hold the console open indefinitely.
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=True, cancel_futures=True)
        if metrics:
            metrics.close()
        _export_all(storage, out_root, cfg)
        storage.close()
        run_lock.release()
        from . import fetch as _F
        log.info("fetch errors: %s", _F.error_summary())
        print(f"[unibot] done: alive={counter['alive']} dead={counter['dead']} "
              f"with_contact={counter['with_contact']} emails={counter['emails']} "
              f"failed={counter['failed']}", flush=True)
        print(f"[unibot] fetch errors: {_F.error_summary()}", flush=True)
