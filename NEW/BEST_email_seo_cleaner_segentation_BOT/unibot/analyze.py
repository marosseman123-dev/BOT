"""Bottleneck analyzer: read metrics.jsonl and show where time goes.

    python -m unibot.analyze                 # defaults to out/run1/metrics.jsonl
    python -m unibot.analyze out/run1/metrics.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter

_PHASES = ["t_dns", "t_homepage", "t_ancillary", "t_contacts", "t_enrich", "t_total"]


def _pct(a, p):
    if not a:
        return 0.0
    a = sorted(a)
    return a[min(len(a) - 1, int(len(a) * p))]


def _fmt(x):
    return f"{x:6.2f}s"


def analyze(path: str):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        print(f"metrics file not found: {path}")
        return
    if not rows:
        print("no metrics rows")
        return

    n = len(rows)
    print(f"\n=== Bottleneck report: {n:,} domain-attempts  ({path}) ===\n")

    # outcome mix
    states = Counter(r.get("state", "") for r in rows)
    dead = sum(1 for r in rows if r.get("dns_dead"))
    blocked = sum(1 for r in rows if r.get("blocked"))
    alive = sum(1 for r in rows if r.get("alive"))
    with_email = sum(1 for r in rows if (r.get("email_count", 0) or 0) > 0)
    emails = sum(r.get("email_count", 0) or 0 for r in rows)
    uniq = len(set(r.get("domain") for r in rows))
    reasons = Counter(r.get("dead_reason", "") for r in rows if r.get("dead_reason"))
    print(f"outcomes : alive={alive}  dead={dead}  blocked={blocked}  "
          f"with_contact={with_email}  emails={emails}")
    print(f"attempts : {n:,} for {uniq:,} unique domains "
          f"({n / max(uniq,1):.2f} attempts/domain -- lower is better)")
    print(f"dead why : {dict(reasons)}   (dns=no DNS, tcp=resolves but no server)")
    print(f"states   : {dict(states)}\n")

    # per-phase distribution
    print(f"{'phase':<12}{'mean':>8}{'p50':>8}{'p90':>8}{'p99':>8}{'max':>8}   share_of_total")
    tot_mean = (sum(r.get("t_total", 0) for r in rows) / n) or 1e-9
    for ph in _PHASES:
        vals = [r.get(ph, 0) or 0 for r in rows]
        mean = sum(vals) / n
        share = "" if ph == "t_total" else f"{mean / tot_mean * 100:5.0f}%"
        print(f"{ph[2:]:<12}{_fmt(mean)}{_fmt(_pct(vals,.5))}{_fmt(_pct(vals,.9))}"
              f"{_fmt(_pct(vals,.99))}{_fmt(max(vals) if vals else 0)}   {share}")

    # dominant phase (excluding total)
    means = {ph: sum(r.get(ph, 0) or 0 for r in rows) / n for ph in _PHASES if ph != "t_total"}
    worst = max(means, key=means.get)
    print(f"\n>> Biggest time sink: {worst[2:]}  "
          f"({means[worst] / tot_mean * 100:.0f}% of average total time)")

    # slowest domains
    slow = sorted(rows, key=lambda r: r.get("t_total", 0), reverse=True)[:15]
    print("\nSlowest 15 domains (t_total):")
    for r in slow:
        print(f"  {r.get('t_total',0):6.2f}s  {r.get('domain','')[:40]:<40} "
              f"dns={r.get('t_dns',0):.1f} home={r.get('t_homepage',0):.1f} "
              f"anc={r.get('t_ancillary',0):.1f} con={r.get('t_contacts',0):.1f} "
              f"enr={r.get('t_enrich',0):.1f}  {r.get('state','')}")
    print()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "out/run1/metrics.jsonl"
    analyze(path)


if __name__ == "__main__":
    main()
