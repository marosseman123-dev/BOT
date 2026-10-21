# UnifiedBot — one crawl, three bots' worth of results

Merges the three separate bots into a single, resumable run so each domain is
**fetched once** and analysed by all three engines on the same HTML snapshot:

```
ingest domains (.txt or .csv)
  → ONE crawl per domain (homepage + contact/about pages + robots/sitemap/TLS)   [aiohttp]
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
- `../seo_audit` — the SEO engine. A new pure entry `seo_audit.audit.analyze_fetch(fetch, domain, cfg)`
  runs the complete audit on an already-fetched page (no re-crawl).
- `../EmailHunterV2/hunter/extract.py` — the email extractor/scorer, used verbatim
  (`harvest_all` + `finalize`), plus its contact-link / seed-URL discovery for the crawl.
- Cleaner's agency classifier and blacklists are ported into `classify.py` / `cleanup.py`,
  with the blacklist matching tightened (domain-suffix + host-label token instead of
  loose `substring in email`, which could delete valid leads like `designova-lekaren.sk`).

`unibot/__init__.py` puts both sibling packages on `sys.path` automatically.

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

Each part: ~5M domains, DB ~7.5 GB, output = a handful of ≤1M-row shards. Runs are
independent and resumable. Use `--leads-only` so only domains with a real contact
land in the report shards (dead/no-contact domains are still counted and stored).

Tuning knobs (config.py / CLI): `--rows-per-shard`, `--leads-only`,
`--metrics-sample`, `--concurrency`, plus `ingest_chunk`, `wal_checkpoint_every`,
`dedup_max_inmemory` in config.py.
