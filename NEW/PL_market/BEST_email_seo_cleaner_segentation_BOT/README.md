# UnifiedBot — one crawl, three bots' worth of results

Merges the three separate bots into a single, resumable run so each domain is
**fetched once** and analysed by all three engines on the same HTML snapshot:

```
ingest domains (.txt or .csv)
  → ONE crawl per domain (homepage + contact/about pages + robots/sitemap/TLS)   [httpx]
       ├─ emails      : EmailHunter extractor (mailto/JSON-LD/hCard/CF/obfuscated) + scoring
       ├─ SEO/tech    : seo_audit full audit (95 cols: platform, PHP EOL, schema,
       │               image formats, WP plugins, CVEs, speed, security …)
       └─ agency check: Cleaner classifier (is this a competitor web/SEO agency?)
  → GLOBAL cleanup (after all rows)
       ├─ drop competitor-domain emails (evidence: the site's own text)
       ├─ misplaced-email dedup (same address on ≥N domains → keep only its own)
       └─ institution/corporate/hosting blacklist (domain-suffix / label match)
  → ONE unified export: contacts + SEO score + platform + competitor flag + audit trail
```

Previously the same homepage was crawled up to **3×** (EmailHunter, SEO, Cleaner).
Now it's crawled once → ~3× less traffic, lower ban risk, faster, and every
analyzer sees the identical page.

## How it reuses the existing bots (no functionality lost)

It does **not** copy their code — it imports them in place:
- `./seo_audit` — the SEO engine. A new pure entry `seo_audit.audit.analyze_fetch(fetch, domain, cfg)`
  runs the complete audit on an already-fetched page (no re-crawl).
- `./EmailHunterV2/hunter/extract.py` — the email extractor/scorer, used verbatim
  (`harvest_all` + `finalize`), plus its contact-link / seed-URL discovery for the crawl.
- Cleaner's agency classifier and blacklists are ported into `classify.py` / `cleanup.py`,
  with the blacklist matching tightened (domain-suffix + host-label token instead of
  loose `substring in email`, which could delete valid leads like `designova-lekaren.sk`).

`unibot/__init__.py` puts these local packages on `sys.path` automatically.

## Run

```bat
run.bat --input input\domains.txt --run run1
run.bat --input input\domains.txt --run run1 --fresh          REM start over
run.bat --input input\domains.txt --run run1 --retry-failed   REM re-crawl failed
```

`run.bat` builds an isolated `.venv_unified`, installs deps, and launches. Re-running
the same `--run` resumes from the SQLite queue; Ctrl-C flushes outputs. Output lands in
`out\<run>\`: `unified_results.csv`, `unified_report.txt`, `unified.db`, `unified.log`.

Offline logic check (no network): `python -m unibot.selfcheck`.

## Operational rules and throughput

Only one bot process may run from this installation at a time, regardless of
`--run` name or `--out` folder. The bot creates `.unibot.global.lock` in the
installation folder before it opens SQLite
or starts ingesting input, and refuses every second start. This prevents double
work and competition for network sockets, disk/log and SQLite.

New lock records also include the Python process birth time. Starting a new
instance replaces only a confirmed older process from this same installation
(including its parser child tree); a legacy, foreign, reused or otherwise
unverified PID is never terminated automatically.

At startup the bot detects CPU, RAM and (on Windows) the dynamic TCP-port range,
then selects a safe profile. `--concurrency N` keeps explicit operator control of
the maximum number of active domains; `--http-concurrency N` controls the real
global network ceiling across homepage, contact, robots and sitemap requests.
On the Ryzen 7 / Wi-Fi profile this is 28 HTTP requests, even though up to 32
domains may be active. `--no-auto-profile` keeps the static `config.py` values.
Raising either value does not guarantee a higher rate and can cause congestion
and timeouts. Measure a completed run with:

Each persistent domain worker owns a small HTTP connection pool and cookie jar.
Cookies remain available throughout one domain (including redirects and contact
pages), then are cleared before that worker accepts another domain. This avoids
httpx's linear cookie-matching cost growing with the lifetime domain count while
the shared request gate still enforces one global network ceiling.

```bat
.venv_unified\Scripts\python -m unibot.analyze out\run1\metrics.jsonl
```

The sustainable rate is bounded by the real domain latency: a hard universal
`>=5 domains/s` guarantee is impossible while preserving retries and complete
contact-page checks when the target sites themselves are slow. Treat 5/s as an
operational SLO: keep one process per run, alert when the rolling settled rate
falls below it, and investigate the throttler, timeout/error mix and active
processes before increasing concurrency.

Every completed metric includes UTC Unix start/end timestamps. Rolling settled
rate and real homepage wire time remain operational diagnostics, but neither
reduces concurrency: adjacent groups of domains can have inherently different
response times. Only confirmed blocking/challenge responses trigger automatic
backoff and cooldown; the machine-sized global HTTP gate is the socket and
network safety ceiling.

Contact crawling is confidence-first: a homepage email with score at least 80
ends contact-page discovery; weaker candidates keep full fallback coverage.
The first real contact link is tried before generated `/kontakt`/`/contact`
paths, which are used only if that direct evidence is inconclusive. The global
HTTP scheduler always gives the next released slot to homepage or the first real
contact link. Homepage and primary contact requests are scheduled fairly, so a
continuous homepage stream cannot starve contact discovery. Fallback and SEO
may borrow every otherwise-idle slot, avoiding a permanently unused reserve.

The throttler logs a rolling 60-second settled rate and latency for operations,
but does not compare them with an older part of the input. A confirmed block-
rate backoff starts a 90-second cooldown before concurrency may rise again. It
does not reduce contact-page coverage, retries, e-mail scoring or SEO analysis
to gain speed.
Each raised concurrency level is held for 20 seconds before another increase,
so its real throughput can be measured rather than guessed from a brief burst.

## Output (lead-first columns)

`domain · emails_final · email_count · best_email · best_email_score · is_competitor ·
agency_reason · overall_score · seo/security/performance/modernity scores · platform ·
cms · framework · php_version · php_eol · https · tls · …all 95 SEO columns… ·
removed_competitor · removed_dedup · removed_blacklist · email_details · outreach_comment`

## Robustness (inherited)

Per-domain hard-timeout watchdog, requeue-with-cooldown on block/timeout/error,
never-unrated finalisation, SQLite WAL resume, live dashboard, crash/Ctrl-C-safe
export (timestamped fallback if a CSV is locked in Excel).

## Status: skeleton + core (verified), then iterate

Working and self-checked end-to-end on the pure path. Planned iterations:
1. Fold in EmailHunter's DNS **prefilter** (drop dead domains before crawling) and its
   circuit-breaker / adaptive-throttle for even safer high-volume runs.
2. Optional: read known emails from an input CSV and merge with freshly found ones.
3. Per-provider summary file (leads by platform / score band) at end of run.

## Running at massive scale (100M+ domains)

The bot is hardened so its own size can't crash it:

- **Streaming ingest** — the input `.txt`/`.csv` is read line-by-line in chunks and
  inserted straight into SQLite (dedup via PRIMARY KEY). A 100M-line file is never
  loaded into RAM. Verified: 250k ingest at ~43 MB peak RSS.
- **Sharded, streaming output** — results are written to numbered files
  `unified_results_00001.csv/.txt`, `_00002...`, rolling every `rows_per_shard`
  rows (default **1,000,000** — under Excel's 1,048,576-row limit). The exporter
  streams from a DB cursor row-by-row, so it never builds a DataFrame or a single
  multi-GB file.
- **Bounded SQLite** — WAL is checkpoint-truncated every `wal_checkpoint_every`
  writes so the `-wal` file can't balloon.
- **Bounded metrics** — `--metrics-sample N` writes 1 of every N metric rows (use
  e.g. 100 for a 100M run so `metrics.jsonl` stays small).
- **Cleanup at scale** — competitor-drop + institution blacklist run per-row inline;
  the RAM-heavy cross-row misplaced-email dedup auto-disables above
  `dedup_max_inmemory` (default 500k results) since it needs all rows in memory.

### File-size limits to respect
| Format | Hard limit | What we do |
|---|---|---|
| CSV in Excel | 1,048,576 rows | shard at `rows_per_shard` (default 1M) |
| CSV/TXT on disk (NTFS) | 16 TB | irrelevant — we shard long before that |
| SQLite DB | 281 TB | fine, but keep it reasonable via input splitting |

### Recommended workflow for 100M
Split the list into ~5M-domain parts so each run has its own bounded DB + output
set (a single 100M DB would be ~150 GB):

```bat
.venv_unified\Scripts\python -m unibot.split input\domains.txt 5000000
REM -> input\domains_part_0001.txt ... _0020.txt

for %%f in (input\domains_part_*.txt) do run.bat --input "%%f" --run %%~nf --leads-only --metrics-sample 100
```

Each part: ~5M domains, DB ~7.5 GB, output = a handful of ≤1M-row shards. Process
parts sequentially, one bot process at a time. Use `--leads-only` so only domains
with a real contact land in the report shards (dead/no-contact domains are still
counted and stored).

Tuning knobs (config.py / CLI): `--rows-per-shard`, `--leads-only`,
`--metrics-sample`, `--concurrency`, `--http-concurrency`, plus `ingest_chunk`,
`wal_checkpoint_every`, `dedup_max_inmemory` in config.py.
