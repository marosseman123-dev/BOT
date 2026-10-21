"""Unified export: one CSV + one TXT, lead-first column order."""
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
    "is_competitor", "agency_reason", "overall_score", "seo_score",
    "security_score", "performance_score", "modernity_score", "platform", "cms", "framework",
    "language_runtime", "php_version", "php_eol", "https", "responsive", "tls_version",
]
# audit trail near the end; issues_ranked is deliberately the VERY LAST column
_AUDIT_LAST = ["removed_competitor", "removed_dedup", "removed_blacklist",
               "email_details", "emails", "issues_ranked"]


def _order(rows: List[Dict]) -> List[str]:
    keys = []
    for r in rows:
        for k in r:
            if k not in keys and k not in _EXCLUDE:
                keys.append(k)
    mid = [k for k in keys if k not in _LEAD_FIRST and k not in _AUDIT_LAST]
    return [k for k in _LEAD_FIRST if k in keys] + mid + [k for k in _AUDIT_LAST if k in keys]


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


def to_row(rec: dict) -> dict:
    """Flatten a record for a CSV shard (drop internal/excluded fields, stringify)."""
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
