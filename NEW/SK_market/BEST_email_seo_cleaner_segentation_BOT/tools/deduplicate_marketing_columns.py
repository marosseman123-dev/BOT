from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


MIN_MATCHING_DOMAINS = 100
PREFIXES = ("new_", "selected_", "merge_")
ALIASES = {
    "overall_score": "score",
    "technical_score": "tech_score",
    "security_score": "security_score",
    "performance_score": "performance_score",
    "modernity_score": "modernity_score",
}


def canonical_value(value: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            pass
    return text


def semantic_name(field: str) -> str:
    name = field
    changed = True
    while changed:
        changed = False
        for prefix in PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
                break
    return ALIASES.get(name, name)


def update_digest(digest: "hashlib._Hash", row_number: int, value: str) -> None:
    payload = f"{row_number}\0{value}\0".encode("utf-8")
    digest.update(payload)


def first_pass(input_path: Path) -> tuple[list[str], int, int, list[int], list[hashlib._Hash], set[str]]:
    counts: list[int] = []
    digests: list[hashlib._Hash] = []
    domains: set[str] = set()
    total_rows = 0
    with input_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader)
        counts = [0] * len(header)
        digests = [hashlib.blake2b(digest_size=16) for _ in header]
        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            if len(row) != len(header):
                raise ValueError(f"Row {row_number} has {len(row)} fields, expected {len(header)}")
            domain = canonical_value(row[0])
            if domain:
                domains.add(domain)
            for index, raw_value in enumerate(row):
                value = canonical_value(raw_value)
                if value:
                    counts[index] += 1
                    update_digest(digests[index], row_number, value)
    return header, total_rows, len(domains), counts, digests, domains


def candidate_pairs(fields: list[str], counts: list[int], digests: list[hashlib._Hash]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    by_signature: dict[tuple[int, str], list[int]] = defaultdict(list)
    for index, digest in enumerate(digests):
        by_signature[(counts[index], digest.hexdigest())].append(index)
    for indexes in by_signature.values():
        for left_pos, left in enumerate(indexes):
            for right in indexes[left_pos + 1:]:
                pairs.add((left, right))

    by_semantic_name: dict[str, list[int]] = defaultdict(list)
    for index, field in enumerate(fields):
        by_semantic_name[semantic_name(field)].append(index)
    for indexes in by_semantic_name.values():
        for left_pos, left in enumerate(indexes):
            for right in indexes[left_pos + 1:]:
                pairs.add((left, right))
    return pairs


def compare_candidates(input_path: Path, fields: list[str], pairs: set[tuple[int, int]]) -> list[dict[str, object]]:
    if not pairs:
        return []
    pair_list = sorted(pairs)
    overlap = [0] * len(pair_list)
    equal = [0] * len(pair_list)
    conflicts = [0] * len(pair_list)
    matched_values: list[set[str]] = [set() for _ in pair_list]
    with input_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        next(reader)
        for row in reader:
            for pair_index, (left, right) in enumerate(pair_list):
                left_value = canonical_value(row[left])
                right_value = canonical_value(row[right])
                if not left_value or not right_value:
                    continue
                overlap[pair_index] += 1
                if left_value == right_value:
                    equal[pair_index] += 1
                    if len(matched_values[pair_index]) < 10:
                        matched_values[pair_index].add(left_value)
                else:
                    conflicts[pair_index] += 1
    results: list[dict[str, object]] = []
    for pair_index, (left, right) in enumerate(pair_list):
        left_count = 0
        right_count = 0
        # Counts are obtained from the source in the caller and added later.
        results.append({
            "left_index": left,
            "right_index": right,
            "left": fields[left],
            "right": fields[right],
            "overlap_rows": overlap[pair_index],
            "equal_rows": equal[pair_index],
            "conflicting_rows": conflicts[pair_index],
            "matched_value_examples": sorted(matched_values[pair_index])[:5],
            "matched_distinct_values_capped": len(matched_values[pair_index]),
            "left_nonempty": left_count,
            "right_nonempty": right_count,
        })
    return results


def choose_removals(fields: list[str], counts: list[int], comparisons: list[dict[str, object]], min_domains: int = MIN_MATCHING_DOMAINS) -> tuple[list[str], list[dict[str, object]]]:
    proven: list[dict[str, object]] = []
    graph: dict[int, set[int]] = defaultdict(set)
    for comparison in comparisons:
        left = int(comparison["left_index"])
        right = int(comparison["right_index"])
        overlap = int(comparison["overlap_rows"])
        equal = int(comparison["equal_rows"])
        conflict = int(comparison["conflicting_rows"])
        smaller_count = min(counts[left], counts[right])
        distinct = int(comparison["matched_distinct_values_capped"])
        is_proven = (
            overlap >= min_domains
            and equal == overlap
            and conflict == 0
            and overlap == smaller_count
            # A constant FALSE/TRUE flag can be equal by accident while still
            # carrying a different business meaning. Require observable value
            # variety before treating a partially populated pair as duplicate.
            and distinct >= 2
        )
        comparison["left_nonempty"] = counts[left]
        comparison["right_nonempty"] = counts[right]
        comparison["proven_duplicate"] = is_proven
        if is_proven:
            graph[left].add(right)
            graph[right].add(left)
            proven.append(comparison)

    def keep_score(index: int) -> tuple[int, int, int]:
        field = fields[index]
        prefixed = int(field.startswith(PREFIXES))
        selected_or_merge = int(field.startswith(("selected_", "merge_")))
        return prefixed, selected_or_merge, index

    remove: set[int] = set()
    visited: set[int] = set()
    for start in sorted(graph):
        if start in visited:
            continue
        component: set[int] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(graph[current] - visited)
        keep = min(component, key=keep_score)
        remove.update(index for index in component if index != keep)
        for comparison in proven:
            if int(comparison["left_index"]) in component and int(comparison["right_index"]) in component:
                comparison["kept_column"] = fields[keep]
                comparison["removed_column"] = fields[int(comparison["right_index"] if int(comparison["left_index"]) == keep else comparison["left_index"])]
    return [fields[index] for index in sorted(remove)], proven


def deduplicate_file(input_path: Path, output_path: Path, report_path: Path, min_domains: int = MIN_MATCHING_DOMAINS) -> dict[str, object]:
    fields, total_rows, unique_domains, counts, digests, _ = first_pass(input_path)
    pairs = candidate_pairs(fields, counts, digests)
    comparisons = compare_candidates(input_path, fields, pairs)
    remove, proven = choose_removals(fields, counts, comparisons, min_domains)
    remove_set = set(remove)
    kept_fields = [field for field in fields if field not in remove_set]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open("r", encoding="utf-8-sig", newline="") as source, output_path.open("w", encoding="utf-8-sig", newline="") as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=kept_fields, extrasaction="ignore")
        writer.writeheader()
        written_rows = 0
        for row in reader:
            writer.writerow({field: row.get(field, "") for field in kept_fields})
            written_rows += 1
    report = {
        "input": str(input_path),
        "output": str(output_path),
        "total_rows": total_rows,
        "unique_domains": unique_domains,
        "input_columns": len(fields),
        "output_columns": len(kept_fields),
        "written_rows": written_rows,
        "minimum_matching_domains": min_domains,
        "removed_columns": remove,
        "proven_duplicate_pairs": proven,
        "candidate_pairs_checked": len(comparisons),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove only data-proven duplicate CSV columns.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--min-domains", type=int, default=MIN_MATCHING_DOMAINS)
    args = parser.parse_args()
    report = deduplicate_file(args.input, args.output, args.report, args.min_domains)
    print(json.dumps({key: value for key, value in report.items() if key != "proven_duplicate_pairs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
