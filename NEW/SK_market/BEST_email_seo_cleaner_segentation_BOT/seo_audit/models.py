"""Data shapes passed between the crawler and the audit engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .severity import Severity
from .states import EvidenceStatus


@dataclass
class FetchResult:
    """One already-fetched page, as handed to analyze_fetch(). The audit engine
    never fetches anything itself -- this is its only window on the world."""
    requested_domain: str
    ok: bool
    final_url: str = ""
    scheme: str = ""
    http_status: int = 0
    http_version: str = ""
    ttfb_s: float = 0.0
    load_time_s: float = 0.0
    headers: Dict[str, str] = field(default_factory=dict)
    html: str = ""
    html_size_bytes: int = 0
    tls_version: str = "unknown"
    blocked: bool = False
    challenged: bool = False
    spa_shell: bool = False
    robots_status: str = "unknown"
    sitemap_status: str = "unknown"
    sitemap_url: str = ""
    evidence: EvidenceStatus = EvidenceStatus.UNAVAILABLE


@dataclass
class Finding:
    """One audit observation. Negative findings (LOW..CRITICAL) are what
    outreach copy and issue-ranking are built from."""
    id: str
    title: str
    severity: Severity
    category: str            # "seo" | "technical" | "performance" | "security" | "modernity"
    impact: str = ""         # short cause/explanation shown next to the title
    detail: str = ""

    @property
    def order_key(self) -> int:
        return int(self.severity)

    def to_dict(self) -> Dict:
        return {"id": self.id, "title": self.title, "severity": self.severity.name,
                "category": self.category, "impact": self.impact, "detail": self.detail}


@dataclass
class AuditResult:
    domain: str
    state: str
    evidence: str
    overall_score: int
    features: Dict
    detections: List[str]
    findings: List[Finding]
    category_scores: Dict[str, int]
    top_priorities: List[str]
    outreach_comment: str
    fetch: Dict

    def to_dict(self) -> Dict:
        return {
            "domain": self.domain,
            "state": self.state,
            "evidence": self.evidence,
            "overall_score": self.overall_score,
            "features": self.features,
            "detections": self.detections,
            "findings": [f.to_dict() for f in self.findings],
            "category_scores": self.category_scores,
            "top_priorities": self.top_priorities,
            "outreach_comment": self.outreach_comment,
            "fetch": self.fetch,
        }
