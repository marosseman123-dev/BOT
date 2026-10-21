from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unibot.cleanup import blacklisted_reason


def emails(value):
    out = []
    for item in (value or "").replace(";", ",").split(","):
        item = item.strip().lower()
        if item and item not in out:
            out.append(item)
    return out


old = "out/run1/audit_results_2026_marketing_v4_1.csv"
merged = "out/run1/universal_marketing_leads_2026.csv"
old_count = old_emails = 0
with open(old, encoding="utf-8-sig", newline="") as source:
    for row in csv.DictReader(source):
        old_count += 1
        old_emails += len(emails(row.get("emails")))
merged_count = merged_emails = 0
left_blacklist = []
metric_sources = Counter()
with open(merged, encoding="utf-8-sig", newline="") as source:
    merged_reader = csv.DictReader(source)
    for row in merged_reader:
        merged_count += 1
        merged_emails += len(emails(row.get("emails")))
        metric_sources[row.get("merge_metric_source", "")] += 1
        for email in emails(row.get("emails")):
            reason = blacklisted_reason(email)
            if reason:
                left_blacklist.append((row.get("domain", ""), email, reason))
same_order = True
with open(old, encoding="utf-8-sig", newline="") as left, open(merged, encoding="utf-8-sig", newline="") as right:
    old_reader, merged_reader = csv.DictReader(left), csv.DictReader(right)
    for index, (a, b) in enumerate(zip(old_reader, merged_reader)):
        if (a.get("domain", "").strip().lower() != b.get("domain", "").strip().lower()):
            same_order = False
            print("order_mismatch", index, a.get("domain"), b.get("domain"))
            break
summary = json.loads(Path("out/run1/universal_merge_summary_2026.json").read_text(encoding="utf-8"))
result = {
    "old_rows": old_count, "merged_rows": merged_count,
    "old_prefix_order_exact": same_order,
    "old_email_tokens": old_emails, "merged_email_tokens": merged_emails,
    "emails_net_change": merged_emails - old_emails,
    "left_blacklisted_email_tokens": len(left_blacklist),
    "metric_sources": metric_sources,
    "summary": summary,
}
Path("out/run1/universal_verification_2026.json").write_text(
    json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
if not same_order or merged_count < old_count or left_blacklist:
    raise SystemExit(2)
