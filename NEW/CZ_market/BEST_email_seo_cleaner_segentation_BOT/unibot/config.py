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

    # ---- crawl (single fetch feeds all analyzers) ----
    max_concurrency: int = 72                  # I/O-bound; a single PC handles this fine (seo_audit runs 64)
    min_concurrency: int = 16
    connect_timeout: float = 4.0               # dead-but-resolving hosts give up faster (was 6.0)
    tcp_probe_timeout: float = 3.0             # fast liveness probe; host refusing TCP on 443/80 = dead
    read_timeout: float = 12.0
    domain_hard_timeout: float = 30.0
    max_attempts: int = 2              # 1 retry; a transient slow connect gets a second chance
    requeue_cooldown: float = 15.0
    ban_cooldown: float = 25.0
    per_host_min_interval: float = 1.0
    batch_size: int = 2000
    max_contact_pages: int = 3                 # extra contact/about pages per domain
    max_html_bytes: int = 3_000_000
    probe_timeout: float = 5.0                 # timeout for robots.txt / sitemap.xml probes
    # ancillary per-site checks (fetch_domain reads these) -- required or every alive
    # domain raises AttributeError after the homepage loads and gets marked dead.
    check_robots: bool = True
    check_sitemap: bool = True
    check_tls: bool = True

    # ---- email relevance ----
    email_min_score: int = 50                  # EmailHunter finalize() threshold
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
    dedup_max_inmemory: int = 500_000          # above this many results, skip the RAM-heavy cross-row dedup
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
