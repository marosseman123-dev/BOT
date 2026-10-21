"""Flatten an AuditResult.to_dict()-shaped nested dict into one flat row.

Consumed by unibot.enrich, which merges the flat dict straight into its wide
per-domain record. Nested containers (features/detections/findings/
category_scores/fetch) are promoted to top-level keys and then dropped --
callers never see the nesting.
"""
from __future__ import annotations

from typing import Dict


def flatten_full(d: Dict) -> Dict:
    out: Dict = {}
    out["domain"] = d.get("domain", "")
    out["state"] = d.get("state", "")
    out["evidence"] = d.get("evidence", "")
    out["overall_score"] = d.get("overall_score", 0)

    feats = d.get("features") or {}
    for k, v in feats.items():
        out[k] = v

    cat = d.get("category_scores") or {}
    for k, v in cat.items():
        out[k] = v

    fetch = d.get("fetch") or {}
    for k, v in fetch.items():
        out[k] = v

    out["top_priorities"] = d.get("top_priorities") or []
    out["outreach_comment"] = d.get("outreach_comment", "")
    return out
