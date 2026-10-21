"""Unified config. One dataclass; sub-engine configs are derived from it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # ---- I/O ----
    input_path: str = "input/domains.txt"     # .txt (one domain/line) or .csv (domain[,emails])
    domain_column: str = "domain"
    emails_column: str = "emails"
    out_dir: str = "out"
    run_name: str = "run1"
    fresh: bool = False
    assume_resume: bool = False
    auto_profile: bool = True                 # safe per-machine CPU/RAM/TCP sizing at startup

    # ---- crawl (single fetch feeds all analyzers) ----
    # max_concurrency is a CEILING, not a fixed level: pipeline.py's Throttle
    # (see throttle.py) starts near it and adapts the live target down/up off
    # the rolling block-rate (403/429/challenge), so a single home IP with no
    # upstream proxy pool speeds up when the network is clean and backs off
    # automatically the moment sites start actively blocking it.
    #
    # Was 72. Measured live (netstat during a real run): at conc=72, ~765
    # sockets sat in Windows' TIME_WAIT state against a dynamic port range of
    # only 16,384 (netsh int ipv4 show dynamicport tcp) with a 120s
    # TIME_WAIT lifetime (registry TcpTimedWaitDelay, default when unset).
    # Each domain opens up to ~6 simultaneous connections (homepage + robots +
    # sitemap + up to 3 contact pages); without a request-level cap, conc=72
    # ~=123 new connections/sec, which over a 120s TIME_WAIT window steady-
    # states at ~14,800 ports held -- 90% of the ENTIRE dynamic range, on a
    # machine also running its OS/browser/other apps. Correlated directly
    # against the run's own logged rate: ~20-27 conc sustained 3.3-4.85/s;
    # once the throttle climbed to 57-72, rate collapsed to 1.97-2.60/s and
    # ConnectError/ConnectTimeout grew 10-18x faster than domains processed --
    # port exhaustion, not a code bug, and NOT something the throttle's
    # block-rate/latency signals can see coming (blocked sockets read as slow
    # connects, which the throttle tries to fix by... adding MORE
    # concurrency, the opposite of what helps). The request-level gate below
    # is the primary socket protection; the 32-domain cap still limits parser,
    # DNS and queue pressure.
    max_concurrency: int = 32
    # throttle floor -- never gates down below this. Was 16: with the real
    # measured median per-domain time (~3.7s, see metrics.jsonl), floor
    # throughput is target/latency -- 16/3.7 =~ 4.3/s, i.e. BELOW 5/s by
    # construction any time congestion legitimately pushes the throttle down
    # to its floor, regardless of any other fix. 20/3.7 =~ 5.4/s clears it.
    min_concurrency: int = 20
    # A domain can fan out into contact, robots and sitemap requests.  This is
    # therefore the real network budget, independent of domain concurrency.
    # Keeping it below the domain ceiling prevents a 32-domain run from
    # silently opening 60-100 sockets and congesting the local connection.
    max_http_concurrency: int = 28
    connect_timeout: float = 4.0               # dead-but-resolving hosts give up faster (was 6.0)
    tcp_probe_timeout: float = 6.0             # hard bound on DNS resolution, incl. its own
                                                # transient-error retry+backoff (see fetch._liveness)
    read_timeout: float = 12.0
    domain_hard_timeout: float = 30.0
    max_attempts: int = 2              # 1 retry; a transient slow connect gets a second chance
    requeue_cooldown: float = 15.0
    ban_cooldown: float = 25.0
    per_host_min_interval: float = 0.2         # stagger gap between a domain's OWN concurrent
                                                # sub-requests (contact pages) -- looks less like
                                                # a scanner hitting one host all at once
    batch_size: int = 2000
    max_contact_pages: int = 3                 # extra contact/about pages per domain
    max_html_bytes: int = 3_000_000
    probe_timeout: float = 5.0                 # timeout for robots.txt / sitemap.xml probes
    # ancillary per-site checks (fetch_domain reads these) -- required or every alive
    # domain raises AttributeError after the homepage loads and gets marked dead.
    check_robots: bool = True
    check_sitemap: bool = True

    # ---- graceful shutdown ----
    shutdown_grace_s: float = 12.0             # let in-flight workers finish before force-cancel

    # ---- CPU-bound work (enrich: BeautifulSoup/lxml parsing + regex audit) ----
    # enrich() is pure CPU, not I/O, and Python's GIL means it does NOT benefit
    # from asyncio concurrency -- 72 "concurrent" workers all doing enrich() on
    # one event-loop thread just serializes them onto a single CPU core.
    # Measured on this exact machine: the crawler process pegged ~100% of ONE
    # core (256s CPU time over ~250s wall clock) while 15 of 16 logical cores
    # sat idle. Offloading enrich() to a process pool (see pipeline._run_workers)
    # lets multiple domains' parsing run truly in parallel across real cores.
    # 0 = auto: cpu_count()-1 threads reserved for the event loop + DNS pool.
    process_workers: int = 0

    # ---- email relevance ----
    email_min_score: int = 50                  # EmailHunter finalize() threshold
    early_email_score: int = 80                # only this confidence may stop contact-page crawling
    email_cap: int = 10
    dedup_threshold: int = 3                   # email seen on >= N rows -> misplaced-check

    # ---- competitor / cleanup ----
    drop_competitor_emails: bool = True        # wipe emails of detected agency domains
    analyze_only_alive_with_email: bool = True # SEO audit + agency ONLY for alive domains that have an email
    apply_blacklist: bool = True               # remove institution/corporate/hosting emails

    # ---- output / UX ----
    progress: bool = True
    metrics: bool = True                        # write per-domain phase timings to metrics.jsonl
    log_level: str = "INFO"
    db_file: str = "unified.db"
    log_file: str = "unified.log"

    # ---- scale (100M-domain inputs, multi-GB outputs) ----
    rows_per_shard: int = 1_000_000            # split CSV/TXT so each file opens in Excel (<=1,048,576)
    output_leads_only: bool = False            # write ONLY domains with a contact (set True for huge runs)
    ingest_chunk: int = 50_000                 # stream input in chunks; never load the whole file in RAM
    metrics_sample: int = 1                    # write 1 of every N metric lines (raise for huge runs)
    dedup_max_inmemory: int = 100_000          # above this many results, use bounded-memory two-pass dedup
    wal_checkpoint_every: int = 200_000        # truncate the SQLite WAL periodically so it can't balloon

    def uas(self) -> List[str]:
        return [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]
