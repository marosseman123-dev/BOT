"""Deterministic, offline email safety rules for marketing CSV exports.

The module deliberately uses evidence already present in a row.  It does not
invent contacts, perform network lookups, or reject a mailbox merely because it
is called ``info`` or ``office``.
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unibot.cleanup import blacklisted_reason

EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.-])")
SPLIT_RE = re.compile(r"[,;\s]+")
SYSTEM_LOCALPARTS = {
    "noreply", "no-reply", "do-not-reply", "donotreply", "mailer-daemon",
    "postmaster", "root", "abuse", "hostmaster", "webmaster", "webadmin",
    "server", "system", "daemon", "bounce",
}
DEVELOPER_LOCALPARTS = {"developer", "webdeveloper", "webdesigner", "webdesign", "webdev"}
AGENCY_LABELS = {"agentura", "agency", "marketing", "digital", "webdesign", "webdesigner", "webdev", "webstudio", "seo"}
FINANCE_MARKERS = {"bank", "banka", "pois", "insurance", "finance", "financ"}
HOSTING_MARKERS = {"hosting", "host", "registrar", "domain", "server"}
FREE_PROVIDERS = {"gmail.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me", "protonmail.com", "seznam.cz", "centrum.cz", "azet.sk"}
CONTACT_PRIORITY = {"info": 5, "kontakt": 5, "contact": 5, "sales": 4, "obchod": 4, "office": 3, "marketing": 3, "hello": 2}


@dataclass(frozen=True)
class Decision:
    email: str
    allowed: bool
    reason: str = ""


def split_emails(value: str) -> list[str]:
    """Return syntactically valid, lower-cased addresses in source order."""
    found: list[str] = []
    seen: set[str] = set()
    for item in SPLIT_RE.split(value or ""):
        email = item.strip(" <>\"'").lower()
        if EMAIL_RE.fullmatch(email) and email not in seen:
            seen.add(email)
            found.append(email)
    return found


def emails_in_text(value: str) -> list[str]:
    return list(dict.fromkeys(match.lower() for match in EMAIL_RE.findall(value or "")))


def normalized_domain(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/", 1)[0].removeprefix("www.")
    return value.rstrip(".")


def same_registrable(host: str, domain: str) -> bool:
    host, domain = normalized_domain(host), normalized_domain(domain)
    return bool(host and domain and (host == domain or host.endswith("." + domain) or domain.endswith("." + host)))


def _blacklist_category(email: str) -> str:
    """Map the existing precise blacklist to stable report categories."""
    matched = blacklisted_reason(email)
    if not matched:
        return ""
    low = matched.lower()
    if low.startswith("role:") or low == "municipality":
        return "public_authority"
    if any(token in low for token in ("gov", "vlada", "minister", "justice", "policie", "inspek", "coi", "soi", "urad", "sud")):
        return "government" if "gov" in low or "vlada" in low or "minister" in low else "inspection_or_regulator"
    if any(token in low for token in FINANCE_MARKERS) or low in {"csob.cz", "csob.sk", "vub.sk", "nbs.sk", "cnb.cz", "vzp.cz"}:
        return "bank_or_finance"
    if any(token in low for token in HOSTING_MARKERS) or low in {"webnode.cz", "webnode.sk", "websupport.sk", "shoptet.cz", "shoptet.sk", "wix.com", "shopify.com", "cloudflare.com"}:
        return "hosting_provider"
    return "dataset_blacklist"


def classify_email(email: str, row: dict[str, str] | None = None) -> Decision:
    row = row or {}
    email = email.lower().strip()
    if not EMAIL_RE.fullmatch(email):
        return Decision(email, False, "low_confidence")
    local, host = email.rsplit("@", 1)
    if str(row.get("new_is_competitor", row.get("is_competitor", ""))).strip().lower() in {"true", "1", "yes"}:
        return Decision(email, False, "agency_or_marketing_competitor")
    if local in SYSTEM_LOCALPARTS or local in DEVELOPER_LOCALPARTS:
        return Decision(email, False, "technical_or_system_address")
    category = _blacklist_category(email)
    if category:
        return Decision(email, False, category)
    labels = set(re.split(r"[._-]", host))
    # A matching business domain can legitimately contain words such as
    # "marketing"; only reject a clearly third-party supplier address.
    domain = normalized_domain(str(row.get("domain", "")))
    if not same_registrable(host, domain) and labels & AGENCY_LABELS:
        return Decision(email, False, "agency_or_marketing_competitor")
    return Decision(email, True)


def relevance(email: str, domain: str, score: float = 0.0) -> tuple[float, int, int, int, str]:
    local, host = email.rsplit("@", 1)
    known_contact = CONTACT_PRIORITY.get(local, 0)
    return (score, int(same_registrable(host, domain)), known_contact, -int(host in FREE_PROVIDERS), email)


def detail_scores(row: dict[str, str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in re.split(r"[,;\n]+", row.get("new_email_details", "") or row.get("email_details", "")):
        parts = item.strip().split("|")
        if len(parts) >= 2 and EMAIL_RE.fullmatch(parts[0].strip().lower()):
            try:
                scores[parts[0].strip().lower()] = float(parts[1])
            except ValueError:
                pass
    return scores


def row_candidates(row: dict[str, str]) -> list[str]:
    """Collect addresses from every email-bearing source column in source order."""
    candidates: list[str] = []
    for field, value in row.items():
        lowered = field.lower()
        if "email" in lowered and not any(token in lowered for token in ("count", "score", "visible", "removed", "reason")):
            candidates.extend(emails_in_text(value))
    return list(dict.fromkeys(candidates))


def clean_row(row: dict[str, str], used: set[str] | None = None) -> tuple[dict[str, str], list[Decision], bool]:
    """Return a copy with one primary email and safe alternates.

    ``used`` is a cross-file/cross-row set.  It is updated only with the chosen
    primary address, so alternatives remain available for a later row.
    """
    result = dict(row)
    used = used if used is not None else set()
    scores = detail_scores(row)
    decisions: list[Decision] = []
    allowed: list[str] = []
    for email in row_candidates(row):
        decision = classify_email(email, row)
        if decision.allowed and email in used:
            decision = Decision(email, False, "duplicate_already_used")
        decisions.append(decision)
        if decision.allowed:
            allowed.append(email)
    domain = normalized_domain(row.get("domain", ""))
    allowed.sort(key=lambda email: relevance(email, domain, scores.get(email, 0.0)), reverse=True)
    primary = allowed[0] if allowed else ""
    others = allowed[1:]
    if primary:
        used.add(primary)

    # All lead-facing email columns must agree; invalid values must not survive
    # in details/audit columns that downstream tooling might accidentally read.
    for field in ("emails", "emails_final", "new_emails_final", "new_visible_email", "new_best_email", "best_email"):
        result[field] = primary
    result["other_emails"] = ", ".join(others)
    result["new_email_count"] = str(int(bool(primary)) + len(others))
    result["email_count"] = result["new_email_count"]
    result["new_best_email_visible"] = "TRUE" if primary else "FALSE"
    result["new_best_email_score"] = str(int(scores[primary])) if primary and primary in scores else ""
    result["safety_reasons"] = " | ".join(sorted({d.reason for d in decisions if not d.allowed}))
    result["email_safety_status"] = "safe" if primary else "no_safe_contact"
    # These columns are informative only.  Keeping rejected addresses here
    # violates the export contract, so preserve no address in them.
    for field in ("new_email_details", "email_details", "new_removed_blacklist", "new_removed_dedup", "new_removed_competitor", "merge_removed_reasons"):
        if field in result:
            result[field] = ""
    for field in list(result):
        lowered = field.lower()
        if "email" in lowered and field not in {"emails", "emails_final", "new_emails_final", "new_visible_email", "new_best_email", "best_email", "other_emails", "new_email_count", "email_count", "new_best_email_visible", "new_best_email_score", "email_safety_status"}:
            result[field] = EMAIL_RE.sub("", str(result[field])).strip(" ,;|")
    used.update(allowed)
    return result, decisions, bool(primary)


def used_emails(rows: Iterable[dict[str, str]], columns: tuple[str, ...] = ("emails",)) -> set[str]:
    result: set[str] = set()
    for row in rows:
        for column in columns:
            result.update(emails_in_text(row.get(column, "")))
    return result


def delivery_key(physical_csv_row: int, domain: str, email: str, campaign: str) -> str:
    payload = f"{physical_csv_row}|{normalized_domain(domain)}|{email.lower()}|{campaign.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def count_reasons(decisions: Iterable[Decision]) -> Counter[str]:
    return Counter(d.reason for d in decisions if not d.allowed)


def verify_resume_domain(actual_domain: str, expected_domain: str) -> None:
    if normalized_domain(actual_domain) != normalized_domain(expected_domain):
        raise ValueError("resume domain does not match the source CSV row")


def verify_resume_row(input_path: str, physical_csv_row: int, expected_domain: str) -> None:
    """Fail closed unless the 1-based physical CSV row still has that domain.

    CSV row 1 is the header; DataFrame index 0 is physical CSV row 2.
    """
    import csv
    if physical_csv_row < 2:
        raise ValueError("resume row must be a physical CSV data row (>= 2)")
    with open(input_path, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        for row_number, row in enumerate(reader, start=2):
            if row_number == physical_csv_row:
                verify_resume_domain(row.get("domain", ""), expected_domain)
                return
    raise ValueError("resume row is outside the source CSV")
