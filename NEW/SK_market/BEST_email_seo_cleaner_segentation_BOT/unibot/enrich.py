"""Per-domain enrichment: ONE fetched page set -> emails + (conditional) SEO audit
+ agency check.

Pipeline order (as requested):
    1. dead vs alive is decided by the crawler (DNS + HTTP).
    2. ALIVE domains are crawled for emails (homepage + contact pages).
    3. ONLY alive domains that actually have an email get the SEO audit + agency
       (cleaner) analysis -- dead domains and alive-but-no-contact domains are not
       worth auditing, which saves the bulk of the compute.

CSV (leads) mode: a domain can arrive with seed_emails already known. Such a domain
is ALIVE BY FIXTURE -- it is audited and always produces a result (the seed email is
never lost), and any newly discovered emails are merged in.
"""
from __future__ import annotations

import re
from typing import Dict, List

from hunter import extract as EH               # EmailHunter (pure extractor)
from seo_audit.audit import analyze_fetch
from seo_audit.config import Config as SeoConfig
from seo_audit.export import flatten_full
from seo_audit.models import FetchResult
from seo_audit.severity import Severity
from seo_audit.states import EvidenceStatus

from . import classify
from .score import web_score

_SEO_CFG = SeoConfig()
_NEG = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)
_EMAIL_SPLIT = re.compile(r"[,;\s]+")


def _parse_seed(seed_emails) -> List[str]:
    """Accept a comma/semicolon-separated string or a list; return clean lowercased
    email addresses, de-duplicated, order preserved."""
    if not seed_emails:
        return []
    if isinstance(seed_emails, str):
        parts = _EMAIL_SPLIT.split(seed_emails)
    else:
        parts = list(seed_emails)
    out, seen = [], set()
    for p in parts:
        e = (p or "").strip().strip("<>\"'").lower()
        if "@" in e and "." in e.split("@")[-1] and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _merge_emails(seed: List[str], harvested: List[str], cap: int) -> List[str]:
    """Seed emails first (known-good), then any newly harvested ones, deduped."""
    out, seen = [], set()
    for e in list(seed) + list(harvested):
        el = e.lower()
        if el not in seen:
            seen.add(el)
            out.append(e)
        if len(out) >= cap:
            break
    return out


def _evidence(hp: Dict) -> EvidenceStatus:
    if not hp.get("ok"):
        return EvidenceStatus.UNAVAILABLE
    if hp.get("blocked") or hp.get("challenged"):
        return EvidenceStatus.BLOCKED
    if hp.get("spa_shell"):
        return EvidenceStatus.PARTIAL
    return EvidenceStatus.CONFIRMED


def _fetchresult(domain: str, hp: Dict) -> FetchResult:
    return FetchResult(
        requested_domain=domain, ok=bool(hp.get("ok")), final_url=hp.get("url", ""),
        scheme=hp.get("scheme", ""), http_status=hp.get("status", 0),
        http_version=hp.get("http_version", ""), ttfb_s=hp.get("ttfb_s", 0.0),
        load_time_s=hp.get("load_time_s", 0.0), headers=hp.get("headers", {}) or {},
        html=hp.get("html", "") or "",
        html_size_bytes=hp.get("html_size_bytes", len((hp.get("html", "") or "").encode("utf-8", "ignore"))),
        tls_version=hp.get("tls_version", "unknown"), blocked=bool(hp.get("blocked")),
        challenged=bool(hp.get("challenged")), spa_shell=bool(hp.get("spa_shell")),
        robots_status=hp.get("robots_status", "unknown"),
        sitemap_status=hp.get("sitemap_status", "unknown"),
        sitemap_url=hp.get("sitemap_url", ""), evidence=_evidence(hp))


def _skipped_seo_row(domain: str, hp: Dict) -> Dict:
    state = "unreachable" if not hp.get("ok") else ("blocked" if hp.get("blocked") else "no_lead")
    return flatten_full({
        "domain": domain, "state": state, "evidence": "unavailable", "overall_score": 0,
        "features": {}, "detections": [], "findings": [], "category_scores": {},
        "top_priorities": [], "outreach_comment": "",
        "fetch": {"blocked": bool(hp.get("blocked")), "challenged": bool(hp.get("challenged")),
                  "http_status": hp.get("status", 0), "final_url": hp.get("url", "")}})


def _rank_issues(audit) -> str:
    """All negative findings, worst-first, each with a short cause in parentheses."""
    negs = [f for f in audit.findings if f.severity in _NEG]
    negs.sort(key=lambda f: f.order_key, reverse=True)
    return " | ".join(f"{f.title} ({f.impact})" for f in negs)


def enrich(domain: str, homepage: Dict, contact_pages: List[Dict],
           analyze_leads_only: bool = True, seed_emails=None) -> Dict:
    """Return one unified record (flat dict) for a domain. Fail-soft.

    seed_emails (CSV leads mode): emails already known for this domain. The domain is
    then treated as alive-by-fixture and always produces a result with the email."""
    seed = _parse_seed(seed_emails)

    # --- 1. emails (EmailHunter extractor across all fetched pages) ---
    candidates = []
    try:
        if homepage.get("html"):
            candidates += EH.harvest_all(homepage["html"], domain, "homepage_")
        for pg in contact_pages:
            if pg.get("html"):
                candidates += EH.harvest_all(pg["html"], domain, "contact_page_")
        scored = EH.finalize(candidates, domain, cap=10, min_score=50)
    except Exception:
        scored = []
    harvested = [e for e, _s, _src in scored]
    emails_list = _merge_emails(seed, harvested, cap=10)

    ok = bool(homepage.get("ok"))
    html = homepage.get("html") or ""
    blocked = bool(homepage.get("blocked") or homepage.get("challenged"))
    # a domain that arrived WITH an email is known-alive even if this fetch failed
    alive = (ok and bool(html) and not blocked) or bool(seed)
    has_emails = bool(emails_list)
    # SEO audit + agency classification for reachable leads that have a contact
    run_analysis = (alive and has_emails) if analyze_leads_only else alive

    if run_analysis:
        try:
            audit = analyze_fetch(_fetchresult(domain, homepage), domain, _SEO_CFG)
            seo_row = flatten_full(audit.to_dict())
            issues_ranked = _rank_issues(audit)
        except Exception as e:
            seo_row = {"domain": domain, "state": "error", "overall_score": 0}
            issues_ranked = f"audit_error:{type(e).__name__}"
        try:
            ag = classify.classify_agency(html)
        except Exception:
            ag = {"is_competitor": False, "agency_score": 0, "reason": "classify_error"}
        seo_skipped = False
    else:
        seo_row = _skipped_seo_row(domain, homepage)
        issues_ranked = ""
        ag = {"is_competitor": False, "agency_score": 0, "reason": "skipped_no_lead"}
        seo_skipped = True

    # newly discovered emails = harvested that were NOT already in the seed
    seed_set = {e.lower() for e in seed}
    new_emails = [e for e in harvested if e.lower() not in seed_set]

    # --- VISIBILITY (GDPR/defensibility) -------------------------------------
    # An email is "visible" if it was literally present on the page(s) we fetched:
    # either EmailHunter pulled it straight from the scanned HTML (handles mailto:
    # and light obfuscation), or its exact text appears in the downloaded HTML.
    # This is the proof that the address was publicly shown on the site itself.
    # Seed emails NOT re-found on the page are (correctly) NOT marked visible.
    scanned = homepage.get("html") or ""
    for pg in contact_pages:
        scanned += "\n" + (pg.get("html") or "")
    scanned_low = scanned.lower()
    harvested_set = {e.lower() for e in harvested}
    visible = [e for e in emails_list
               if e.lower() in harvested_set or e.lower() in scanned_low]
    visible_set = {e.lower() for e in visible}

    # --- merge into one wide record ---
    rec = dict(seo_row)
    rec["domain"] = domain
    rec["dns_dead"] = bool(homepage.get("dns_dead")) and not seed  # seeded => never dns_dead
    rec["dead_reason"] = homepage.get("dead_reason", "")
    rec["alive"] = alive
    rec["seeded"] = bool(seed)
    rec["seed_emails"] = ", ".join(seed)
    rec["new_emails"] = ", ".join(new_emails)
    rec["new_email_count"] = len(new_emails)
    rec["emails_list"] = emails_list
    rec["emails"] = ", ".join(emails_list)
    rec["email_count"] = len(emails_list)
    rec["best_email"] = emails_list[0] if emails_list else ""
    rec["best_email_visible"] = bool(rec["best_email"]) and rec["best_email"].lower() in visible_set
    rec["visible_email"] = ", ".join(visible)
    rec["visible_email_count"] = len(visible)
    rec["best_email_score"] = scored[0][1] if scored else (99 if seed else 0)
    details = [f"{e}|seed|csv" for e in seed] + [f"{e}|{s}|{src}" for e, s, src in scored]
    rec["email_details"] = "; ".join(details)
    rec["is_competitor"] = bool(ag.get("is_competitor"))
    rec["agency_score"] = ag.get("agency_score", 0)
    rec["agency_reason"] = ag.get("reason", "")
    rec["contact_pages_crawled"] = len(contact_pages)
    rec["seo_skipped"] = seo_skipped
    rec["issues_ranked"] = issues_ranked
    # headline web-quality score (1-100) + actionable category (C1/C2/C3)
    rec["score"], rec["category"] = web_score(rec)
    return rec
