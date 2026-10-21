"""Unified export: one CSV + one TXT, lead-first column order.

Column set is a STATIC, complete schema -- NOT derived from whichever row
happens to be written first. That was a real, serious bug: ShardedWriter's
csv.DictWriter is built once with a fixed header and extrasaction="ignore"
(silently drops any key not in that header, for every row that follows). The
very first domain processed in a scan is very often dead or has no contact
(seo_skipped=True), whose record only has ~20 keys (see enrich._skipped_seo_row)
-- so every other row's 90+ SEO/security/performance columns, even for
domains that WERE fully analysed, were silently discarded from the CSV for
the rest of the run. The data was never lost (still in the SQLite results
table), just missing from the export. A static header -- present here as the
exact field set enrich()/audit.py/cleanup.py are known to ever produce --
fixes it: every row uses the same complete header, with blank cells for
whatever a given domain's record legitimately doesn't have (e.g. a dead
domain has no seo_score -- that's a correct blank, not a bug).
"""
from __future__ import annotations

from typing import Dict, List

# never exported (internal / dropped on request)
_EXCLUDE = {"emails_list", "outreach_comment"}

# lead-facing columns first, then SEO/tech, then cleanup audit trail.
# score + category are the headline decision columns (drive the personalized offer);
# visible_email documents which addresses were literally shown on the scanned site.
_LEAD_FIRST = [
    "domain", "state", "alive", "seeded", "score", "category", "seo_skipped",
    "emails_final", "email_count",
    "visible_email", "visible_email_count", "best_email", "best_email_visible",
    "best_email_score", "new_emails", "new_email_count", "seed_emails",
    "is_competitor", "agency_reason", "overall_score", "seo_score", "technical_score",
    "security_score", "performance_score", "modernity_score", "platform", "cms", "framework",
    "language_runtime", "php_version", "php_eol", "https", "responsive", "tls_version",
]
# audit trail near the end; issues_ranked is deliberately the VERY LAST column
_AUDIT_LAST = ["removed_competitor", "removed_dedup", "removed_blacklist",
               "email_details", "emails", "issues_ranked"]

# Everything analyze_fetch()/flatten_full() (seo_audit) can produce, plus what
# enrich.py and cleanup.py add on top. Kept in one place so the export schema
# can never silently narrow again -- if a NEW field is ever added upstream, it
# must be added here too, or it will be dropped with a clear, findable cause
# (missing from this list) instead of an invisible first-row accident.
_SEO_AUDIT_FIELDS = [
    "lang", "lang_declared", "evidence", "is_excellent", "completeness",
    "platform", "cms", "framework", "language_runtime", "server", "cdn", "waf",
    "analytics", "js_libraries", "https", "ssl_certificate", "tls_version", "http_version",
    "compression", "compression_type", "ttfb_s", "load_time_s", "html_kb",
    "hsts", "csp", "x_frame_options", "x_content_type_options", "referrer_policy",
    "permissions_policy", "security_headers_count",
    "responsive", "modern_layout", "structured_data", "og_tags", "favicon",
    "hreflang_count", "images_total", "images_with_alt_pct", "images_nextgen_pct",
    "images_compressed", "images_legacy", "title_ok", "meta_description_ok", "h1_count",
    "canonical", "noindex", "internal_links", "word_count",
    "img_jpg", "img_png", "img_gif", "img_svg", "img_webp", "img_avif", "img_other",
    "schema_valid", "schema_blocks", "schema_types",
    "wp_version", "wp_theme", "wp_theme_version", "wp_plugin_count", "wp_plugins",
    "many_plugins_review", "sitemap_url", "jquery_version", "bootstrap_version",
    "wordpress_version", "meta_generator", "robots_txt", "sitemap_xml",
    "final_url", "http_status", "blocked", "challenged", "spa_shell",
    "n_critical", "n_high", "seo_issues", "technical_issues", "performance_issues",
    "security_issues", "modernity_issues", "top_priorities",
    "dns_dead", "dead_reason", "agency_score", "contact_pages_crawled",
]

_FULL_SCHEMA = (_LEAD_FIRST
                + [k for k in _SEO_AUDIT_FIELDS if k not in _LEAD_FIRST]
                + _AUDIT_LAST)


def _order(rows: List[Dict] | None = None) -> List[str]:
    """Static schema (see module docstring). `rows` is accepted for backward
    compatibility with old call sites but no longer influences the result --
    the point of this fix is that the header must NOT depend on which rows
    happen to have been seen."""
    return list(_FULL_SCHEMA)


def _clean(rows: List[Dict]) -> List[Dict]:
    cols = _order(rows)
    out = []
    for r in rows:
        row = {}
        for k in cols:
            v = r.get(k, "")
            row[k] = ", ".join(map(str, v)) if isinstance(v, list) else v
        ef = r.get("emails_final", r.get("emails", ""))
        row["emails_final"] = ", ".join(ef) if isinstance(ef, list) else ef
        out.append(row)
    return out


def write_csv(rows: List[Dict], path: str):
    import pandas as pd
    pd.DataFrame(_clean(rows)).to_csv(path, index=False, encoding="utf-8-sig")


def write_txt(rows: List[Dict], path: str):
    rows = sorted(rows, key=lambda r: r.get("score", r.get("overall_score", 0)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"UNIFIED LEAD + SEO REPORT  |  {len(rows)} domains\n" + "=" * 80 + "\n\n")
        for r in rows:
            f.write(_txt_body(r))


def _fmt_emails(r: dict) -> str:
    ef = r.get("emails_final", r.get("emails", ""))
    return ", ".join(ef) if isinstance(ef, list) else ef


def _txt_body(r: dict) -> str:
    ef = _fmt_emails(r)
    tag = "  [COMPETITOR]" if r.get("is_competitor") else ""
    cat = r.get("category") or "-"
    out = f"{r.get('domain','')}   score={r.get('score', r.get('overall_score', 0))}/100  [{cat}]{tag}\n"
    out += f"  contacts : {ef or '-'}\n"
    out += f"  visible  : {r.get('visible_email') or '-'}\n"   # emails proven on the scanned site
    out += (f"  platform : {r.get('platform') or '-'}  php={r.get('php_version') or '-'}"
            f"{' (EOL)' if r.get('php_eol') else ''}  https={r.get('https')}\n")
    if r.get("issues_ranked"):
        out += f"  issues   : {r['issues_ranked']}\n"
    return out + "\n"


_SCHEMA_SET = set(_FULL_SCHEMA)
_unknown_fields_warned: set = set()


def _warn_unknown_fields(rec: dict) -> None:
    """One-time-per-field safety net: if a record ever carries a key that
    isn't in the static schema above, ShardedWriter's DictWriter(...,
    extrasaction='ignore') would silently drop it from the CSV -- exactly the
    bug this module was rewritten to fix, just reintroduced by a future field
    someone forgot to add to _SEO_AUDIT_FIELDS. Logging it the first time
    means that shows up immediately in unified.log instead of silently
    shipping an incomplete export again."""
    unknown = set(rec) - _SCHEMA_SET - _EXCLUDE - _unknown_fields_warned
    if unknown:
        import logging
        logging.getLogger("unibot").warning(
            "export: field(s) %s not in the static CSV schema -- will be silently "
            "dropped from every row unless added to export._SEO_AUDIT_FIELDS",
            sorted(unknown))
        _unknown_fields_warned.update(unknown)


def to_row(rec: dict) -> dict:
    """Flatten a record for a CSV shard (drop internal/excluded fields, stringify)."""
    _warn_unknown_fields(rec)
    row = {}
    for k, v in rec.items():
        if k in _EXCLUDE:
            continue
        row[k] = ", ".join(map(str, v)) if isinstance(v, list) else v
    ef = rec.get("emails_final", rec.get("emails", ""))
    row["emails_final"] = ", ".join(ef) if isinstance(ef, list) else ef
    return row


def column_order(row: dict) -> list:
    return _order([row])


def txt_block(rec: dict) -> str:
    return _txt_body(rec)
