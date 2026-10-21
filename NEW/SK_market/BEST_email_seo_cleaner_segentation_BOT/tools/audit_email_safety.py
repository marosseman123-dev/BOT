"""Read-only safety audit for a marketing CSV."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from email_safety import clean_row, count_reasons, row_candidates


def audit(path: Path, examples_limit: int = 20, max_rows: int | None = None) -> dict[str, object]:
    stats: Counter[str] = Counter()
    examples: list[dict[str, object]] = []
    used: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        for physical_row, row in enumerate(reader, start=2):
            if max_rows is not None and stats["rows"] >= max_rows:
                break
            candidates = row_candidates(row)
            cleaned, decisions, has_primary = clean_row(row, used)
            stats["rows"] += 1
            stats["extracted_email_count"] += len(candidates)
            stats["rows_primary_replaced"] += bool(row.get("emails", "").strip() and row.get("emails", "").strip().lower() != cleaned.get("emails", ""))
            stats["rows_without_safe_contact"] += not has_primary
            stats.update(count_reasons(decisions))
            for decision in decisions:
                if not decision.allowed and len(examples) < examples_limit:
                    examples.append({"physical_csv_row": physical_row, "domain": row.get("domain", ""), "email": decision.email, "reason": decision.reason, "source_url": row.get("new_final_url", row.get("final_url", "")), "score": row.get("new_best_email_score", row.get("best_email_score", ""))})
    return {"input": str(path), "read_only": True, "max_rows": max_rows, "statistics": dict(stats), "examples": examples}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit marketing-email safety without modifying the input CSV.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--examples", type=int, default=20)
    parser.add_argument("--max-rows", type=int, help="audit only the first N data rows")
    args = parser.parse_args()
    report = audit(args.input, args.examples, args.max_rows)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
