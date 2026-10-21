from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path


def domain(value: str) -> str:
    value = (value or "").strip().lower()
    return re.sub(r"^https?://", "", value).split("/", 1)[0].removeprefix("www.").rstrip(".")


def emails(value: str) -> set[str]:
    return {x.strip().lower() for x in re.split(r"[,;\s]+", value or "") if "@" in x and "." in x.rsplit("@", 1)[-1]}


old_path = Path("out/run1/audit_results_2026_marketing_v4_1.csv")
source_path = Path("out/run1/universal_marketing_leads_2026.csv")
output_path = Path("out/run1/universal_marketing_leads_2026_relevant_only.csv")
summary_path = Path("out/run1/universal_relevant_only_summary_2026.json")

old_by_domain: dict[str, set[str]] = {}
old_rows = 0
with old_path.open(encoding="utf-8-sig", newline="") as source:
    for row in csv.DictReader(source):
        old_rows += 1
        d = domain(row.get("domain", ""))
        if d:
            old_by_domain.setdefault(d, set()).update(emails(row.get("emails", "")))

kept_rows = 0
kept_emails = 0
new_domain_rows = 0
new_domains: set[str] = set()
added_to_old: set[tuple[str, str]] = set()
source_rows = 0
with source_path.open(encoding="utf-8-sig", newline="") as source:
    reader = csv.DictReader(source)
    fields = reader.fieldnames or []
    with output_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            source_rows += 1
            d = domain(row.get("domain", ""))
            current = emails(row.get("emails", ""))
            if not d or not current:
                continue
            writer.writerow(row)
            kept_rows += 1
            kept_emails += len(current)
            if d not in old_by_domain:
                new_domain_rows += 1
                new_domains.add(d)
            else:
                for email in current - old_by_domain[d]:
                    added_to_old.add((d, email))

summary = {
    "source_universal_rows": source_rows,
    "source_old_rows": old_rows,
    "filtered_rows_with_relevant_email": kept_rows,
    "filtered_email_tokens": kept_emails,
    "removed_rows_without_domain_or_relevant_email": source_rows - kept_rows,
    "new_rows_with_domain_and_email_vs_old": new_domain_rows,
    "new_unique_domains_with_email_vs_old": len(new_domains),
    "emails_added_to_existing_old_domains": len(added_to_old),
    "old_domains_compared": len(old_by_domain),
    "output": str(output_path),
}
summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
