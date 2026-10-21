from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unibot.cleanup import _same_registrable, blacklisted_reason

EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
SPLIT_RE = re.compile(r"[,;\s]+")
SUSPICIOUS_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}
METRIC_FIELDS = ("score", "seo_score", "security_score", "performance_score", "overall_score")


def norm_domain(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/", 1)[0]
    return value.removeprefix("www.").rstrip(".")


def split_emails(value: str) -> list[str]:
    out, seen = [], set()
    for item in SPLIT_RE.split(value or ""):
        email = item.strip().strip("<>\"'").lower()
        if email and EMAIL_RE.match(email) and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def numeric(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def row_quality(row: dict, source: str) -> tuple:
    alive = row.get("alive", "").lower() in {"true", "1", "yes"} or numeric(row.get("http_status")) in range(200, 400)
    emails = split_emails(row.get("emails_final", "") if source == "new" else row.get("emails", ""))
    completeness = sum(bool(row.get(k, "")) for k in ("seo_issues", "technical_issues", "performance_issues", "security_issues", "issues"))
    score = numeric(row.get("overall_score" if source == "new" else "score"))
    return (int(alive), int(bool(emails)), completeness, score, len(emails))


def candidate_score(email: str, domain: str) -> tuple:
    local, host = email.rsplit("@", 1)
    exact_host = int(_same_registrable(host, domain))
    role = int(local in {"info", "kontakt", "contact", "office", "sales", "hello", "obchod", "marketing"})
    free = int(host in {"gmail.com", "outlook.com", "hotmail.com", "seznam.cz", "centrum.cz", "azet.sk"})
    return (exact_host, role, -free, -len(email), email)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", default="out/run1/audit_results_2026_marketing_v4_1.csv")
    parser.add_argument("--new", default="out/run1/unified_results_00001.csv")
    parser.add_argument("--out", default="out/run1/universal_marketing_leads_2026.csv")
    parser.add_argument("--audit", default="out/run1/universal_cleanup_report_2026.csv")
    parser.add_argument("--summary", default="out/run1/universal_merge_summary_2026.json")
    args = parser.parse_args()

    old_rows, old_fields = [], []
    with open(args.old, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        old_fields = reader.fieldnames or []
        for index, row in enumerate(reader):
            row["_row_index"] = index
            row["_source"] = "old"
            old_rows.append(row)

    new_by_domain: dict[str, dict] = {}
    new_order: list[str] = []
    with open(args.new, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        new_fields = reader.fieldnames or []
        for row in reader:
            domain = norm_domain(row.get("domain", ""))
            if not domain:
                continue
            if domain not in new_by_domain:
                new_order.append(domain)
                new_by_domain[domain] = row
            elif row_quality(row, "new") > row_quality(new_by_domain[domain], "new"):
                new_by_domain[domain] = row

    old_by_domain: dict[str, dict] = {}
    for row in old_rows:
        domain = norm_domain(row.get("domain", ""))
        if domain and domain not in old_by_domain:
            old_by_domain[domain] = row

    # Keep every old row, including duplicate domains, in its exact source
    # order. The sending bot's row cursor must never shift. New-only domains
    # are appended after the complete old file.
    output_entries = [(norm_domain(r.get("domain", "")), r) for r in old_rows]
    output_entries += [(d, None) for d in new_order if d not in old_by_domain]
    unique_domains = list(dict.fromkeys(domain for domain, _ in output_entries))
    domains = unique_domains

    audit = []
    combined: dict[str, list[str]] = {}
    selected_new: dict[str, dict] = {}
    stats = Counter()
    for domain in domains:
        old = old_by_domain.get(domain)
        new = new_by_domain.get(domain)
        if old and new:
            stats["matched_domains"] += 1
        elif old:
            stats["old_only_domains"] += 1
        else:
            stats["new_only_domains"] += 1
        if new:
            selected_new[domain] = new
        emails = []
        if new:
            emails += split_emails(new.get("emails_final", ""))
        if old:
            emails += split_emails(old.get("emails", ""))
        unique = list(dict.fromkeys(emails))
        kept, removed = [], []
        is_competitor = bool(new and new.get("is_competitor", "").lower() in {"true", "1", "yes"})
        for email in unique:
            reason = blacklisted_reason(email)
            local = email.split("@", 1)[0]
            if is_competitor:
                removed.append((domain, email, "competitor_domain"))
            elif reason:
                removed.append((domain, email, f"blacklist:{reason}"))
            elif local in SUSPICIOUS_LOCAL:
                removed.append((domain, email, "non_contact_mailbox"))
            else:
                kept.append(email)
        kept.sort(key=lambda email: candidate_score(email, domain), reverse=True)
        combined[domain] = kept
        audit.extend(removed)

    # Cross-domain misplaced-email pass, matching the bot's cleanup semantics.
    occurrences = Counter(email for emails in combined.values() for email in emails)
    for domain, emails in list(combined.items()):
        kept, removed = [], []
        for email in emails:
            if occurrences[email] >= 3 and not _same_registrable(email.rsplit("@", 1)[1], domain):
                removed.append((domain, email, "cross_domain_duplicate"))
            else:
                kept.append(email)
        combined[domain] = kept
        audit.extend(removed)

    output_fields = list(old_fields)
    extra = [
        "merge_source", "merge_old_row_index", "merge_metric_source", "merge_email_source",
        "merge_email_count", "merge_removed_emails", "merge_removed_reasons", "selected_overall_score",
        "selected_seo_score", "selected_technical_score", "selected_security_score",
        "selected_performance_score", "selected_modernity_score", "selected_issues",
    ]
    output_fields += [f"new_{field}" for field in new_fields if field not in old_fields and field != "domain"]
    output_fields += [field for field in extra if field not in output_fields]

    removed_by_domain = defaultdict(list)
    for domain, email, reason in audit:
        removed_by_domain[domain].append(f"{email} [{reason}]")

    with open(args.out, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for domain, old_entry in output_entries:
            old = old_entry or {}
            new = selected_new.get(domain, {})
            row = {field: "" for field in output_fields}
            row.update(old)
            row["domain"] = domain
            emails = combined.get(domain, []) if domain else []
            if domain:
                row["emails"] = ",".join(emails)
                row["merge_email_count"] = len(emails)
            else:
                row["merge_email_count"] = len(split_emails(old.get("emails", "")))
            row["merge_source"] = "old+new" if old and new else ("old" if old else "new")
            row["merge_old_row_index"] = old.get("_row_index", "")
            metric_source = "new" if new and row_quality(new, "new") >= row_quality(old, "old") else "old"
            row["merge_metric_source"] = metric_source
            row["merge_email_source"] = "new_cleaned+old" if old and new else ("new_cleaned" if new else "old")
            row["merge_removed_emails"] = len(removed_by_domain.get(domain, []))
            row["merge_removed_reasons"] = " | ".join(removed_by_domain.get(domain, []))
            if new:
                for field in new_fields:
                    if field != "domain" and field not in old_fields:
                        row[f"new_{field}"] = new.get(field, "")
                for selected, source_field in (("selected_overall_score", "overall_score"), ("selected_seo_score", "seo_score"),
                                               ("selected_technical_score", "technical_score"), ("selected_security_score", "security_score"),
                                               ("selected_performance_score", "performance_score"), ("selected_modernity_score", "modernity_score")):
                    row[selected] = new.get(source_field, "") if metric_source == "new" else old.get(source_field, old.get("score", ""))
                row["selected_issues"] = new.get("issues_ranked", "") or old.get("issues", "")
            else:
                row["selected_overall_score"] = old.get("score", "")
                row["selected_issues"] = old.get("issues", "")
            writer.writerow(row)

    audit_fields = ["domain", "email", "reason"]
    with open(args.audit, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=audit_fields)
        writer.writeheader()
        for domain, email, reason in audit:
            writer.writerow({"domain": domain, "email": email, "reason": reason})

    stats.update({
        "old_rows": len(old_rows), "new_rows": len(new_by_domain), "output_rows": len(output_entries),
        "output_unique_domains": len(domains),
        "output_rows_with_email": sum(bool(combined[domain]) for domain, _ in output_entries),
        "output_unique_domains_with_email": sum(bool(v) for v in combined.values()),
        "output_emails": sum(len(v) for v in combined.values()), "removed_email_events": len(audit),
        "removed_competitor": sum(reason == "competitor_domain" for _, _, reason in audit),
        "removed_blacklist": sum(reason.startswith("blacklist:") for _, _, reason in audit),
        "removed_non_contact": sum(reason == "non_contact_mailbox" for _, _, reason in audit),
        "removed_cross_domain_duplicate": sum(reason == "cross_domain_duplicate" for _, _, reason in audit),
        "added_new_only_domains": stats["new_only_domains"],
    })
    Path(args.summary).write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
