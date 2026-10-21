from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.email_safety import clean_row as safety_clean_row
from tools.email_safety import split_emails as safety_split_emails
from tools.email_safety import used_emails


CAMPAIGNS = {"C1", "C2", "C3", "C4", "C5", "C6"}
DEFAULT_CUTOFF_LINE = 115_498
EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
EMAIL_SPLIT_RE = re.compile(r"[,;\s]+")
NON_CONTACT_LOCALPARTS = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}
CONTACT_LOCALPARTS = {
    "info", "kontakt", "contact", "office", "sales", "obchod", "marketing",
    "hello", "ahoj", "predaj", "rezervacia", "objednavky", "recepcia",
}
DEVELOPER_LOCALPARTS = {
    "webmaster", "webadmin", "webdeveloper", "developer", "webdesigner",
    "webdesign", "agentura", "agency", "dev",
}
DEVELOPER_HOST_LABELS = {
    "agency", "agentura", "webdesign", "webdesigner", "webdev", "developer",
    "webstudio",
}


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def bool_text(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def split_emails(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in EMAIL_SPLIT_RE.split(value or ""):
        email = item.strip().strip("<>\"'").lower()
        if email and EMAIL_RE.match(email) and email not in seen:
            result.append(email)
            seen.add(email)
    return result


def same_registrable(host: str, domain: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    domain = (domain or "").lower().removeprefix("www.")
    return bool(host and domain and (host == domain or host.endswith("." + domain) or domain.endswith("." + host)))


def developer_email_reason(email: str) -> str:
    local, _, host = email.partition("@")
    local_tokens = set(re.split(r"[._+\-]", local.lower()))
    if local.lower() in DEVELOPER_LOCALPARTS or local_tokens & DEVELOPER_LOCALPARTS:
        return "developer_mailbox"
    labels = set(re.split(r"[._\-]", host.lower()))
    if labels & DEVELOPER_HOST_LABELS:
        return "developer_domain"
    return ""


def email_relevance(email: str, domain: str) -> tuple[int, int, int, int, str]:
    local, _, host = email.partition("@")
    exact_domain = int(same_registrable(host, domain))
    role_mailbox = int(local in CONTACT_LOCALPARTS)
    free_provider = int(host in {"gmail.com", "outlook.com", "hotmail.com", "seznam.cz", "centrum.cz", "azet.sk"})
    return exact_domain, role_mailbox, -free_provider, -len(email), email


def clean_emails(row: dict[str, str], domain: str) -> tuple[list[str], list[str]]:
    probe = dict(row)
    probe["domain"] = domain
    cleaned, decisions, _ = safety_clean_row(probe)
    kept = safety_split_emails(cleaned.get("emails", "")) + safety_split_emails(cleaned.get("other_emails", ""))
    removed = [f"{d.email} [{d.reason}]" for d in decisions if not d.allowed]
    return kept, removed


def selected_score(row: dict[str, str], selected_name: str, fallback_name: str = "") -> float:
    return parse_float(row.get(selected_name, ""), parse_float(row.get(fallback_name, "")))


def is_c6_candidate(row: dict[str, str]) -> bool:
    overall = selected_score(row, "selected_overall_score", "overall_score")
    seo = selected_score(row, "selected_seo_score", "seo_score")
    technical = selected_score(row, "selected_technical_score", "tech_score")
    security = selected_score(row, "selected_security_score", "security_score")
    performance = selected_score(row, "selected_performance_score", "performance_score")
    disqualifying = any(truthy(row.get(field, "")) for field in ("new_dns_dead", "new_blocked", "new_challenged", "new_is_competitor"))
    return (
        not disqualifying
        and overall >= 85
        and seo >= 80
        and technical >= 82
        and security >= 75
        and performance >= 80
    )


def campaign_for(row: dict[str, str]) -> str:
    existing = (row.get("campaign") or "").strip().upper()
    if existing in CAMPAIGNS:
        return existing
    segment = (row.get("segment") or "").strip()
    if segment == "S5_top" or is_c6_candidate(row):
        return "C6"
    return "C1"


def segment_for(row: dict[str, str], campaign: str) -> str:
    existing = (row.get("segment") or "").strip()
    if existing:
        return existing
    if campaign == "C6":
        return "S5_top"
    if any(truthy(row.get(field, "")) for field in ("new_dns_dead", "new_blocked", "new_challenged")):
        return "S4_critical"
    technical = selected_score(row, "selected_technical_score", "tech_score")
    modernity = parse_float(row.get("new_modernity_score", ""), technical)
    if technical < 76 or modernity < 40 or row.get("new_ssl_certificate", "").strip().lower() in {"false", "0"}:
        return "S2_legacy_redesign"
    commerce_text = " ".join(row.get(field, "") for field in ("new_cms", "new_framework", "new_wp_plugins", "details")).lower()
    if any(token in commerce_text for token in ("shoptet", "woocommerce", "prestashop", "eshop", "e-shop", "cart", "checkout", "product")):
        return "S1_eshop_marketing"
    return "S3_tech_ok_seo_weak"


def issue_text(row: dict[str, str]) -> str:
    return " | ".join(value for value in (row.get("new_issues_ranked", ""), row.get("selected_issues", ""), row.get("new_top_priorities", "")) if value).strip()


def title_ok_from_issues(issues: str) -> bool:
    lowered = issues.lower()
    return "title tag missing" not in lowered and "title tag" not in lowered


def issue_subset(issues: str, tokens: Iterable[str]) -> str:
    chunks = [chunk.strip() for chunk in re.split(r"\s*\|\s*", issues) if chunk.strip()]
    wanted = tuple(token.lower() for token in tokens)
    return " | ".join(chunk for chunk in chunks if any(token in chunk.lower() for token in wanted))


def campaign_copy(domain: str, campaign: str, score: float, seo: float, technical: float, issues: str) -> dict[str, str]:
    if campaign == "C6":
        return {
            "campaign_title": f"Váš web {domain} je technicky pripravený – poďme zvýšiť konverzie a marketing",
            "campaign_short_desc": "Špeciálna konzultácia a spolupráca zameraná na zvyšovanie konverzií a marketingový rast webu, ktorý je už technicky vo veľmi dobrom stave.",
            "personalized_summary": f"Pre {domain} sme v audite videli silný celkový stav – overall {score:.0f}, tech {technical:.0f}, SEO {seo:.0f}. Technicky a z pohľadu SEO je web pripravený na marketing, obsah a zvyšovanie konverzií.",
            "personalized_details_html": "<ul><li>spoločne prejdeme kľúčové landing pages a objednávkový proces</li><li>navrhneme A/B testy nad titulnými a produktovými stránkami</li><li>nastavíme alebo vylepšíme meranie konverzií a mikrokonverzií</li><li>pripravíme jednoduchý growth plán na najbližších 90 dní</li></ul>",
        }
    priority_items = [chunk.strip() for chunk in re.split(r"\s*\|\s*", issues) if chunk.strip()][:6]
    details = "".join(f"<li>{html.escape(item)}</li>" for item in priority_items)
    summary_issue = priority_items[0] if priority_items else "doplniť hlavné technické a SEO nastavenia"
    if campaign == "C1":
        short_desc = "Optimalizácia SEO, obsah a marketing, aby technicky pripravený web prinášal viac dopytov alebo objednávok."
    else:
        short_desc = "Modernizácia webu, SEO a technických základov podľa výsledkov auditu."
    return {
        "campaign_title": f"Krátky audit vášho webu {domain} – konkrétne návrhy zlepšení",
        "campaign_short_desc": short_desc,
        "personalized_summary": f"Pre {domain} sme v audite videli overall {score:.0f}, tech {technical:.0f}, SEO {seo:.0f}. Ako prvé odporúčame: {summary_issue}.",
        "personalized_details_html": f"<ul>{details}</ul>" if details else "<ul><li>doplniť technické a SEO nastavenia podľa auditu</li></ul>",
    }


def populate_legacy_fields(row: dict[str, str], campaign: str) -> None:
    domain = row.get("domain", "").strip()
    issues = issue_text(row)
    if not issues:
        issues = row.get("new_dead_reason", "").strip()
    if not issues:
        issues = "Web sa nepodarilo spoľahlivo načítať" if not truthy(row.get("new_alive", "")) else "Audit neidentifikoval detailné chyby"
    overall = selected_score(row, "selected_overall_score", "overall_score")
    seo = selected_score(row, "selected_seo_score", "seo_score")
    technical = selected_score(row, "selected_technical_score", "tech_score")
    security = selected_score(row, "selected_security_score", "security_score")
    performance = selected_score(row, "selected_performance_score", "performance_score")
    modernity = parse_float(row.get("selected_modernity_score", ""), parse_float(row.get("new_modernity_score", ""), technical))
    for field, value in {
        "score": overall,
        "tech_score": technical,
        "seo_score": seo,
        "security_score": security,
        "performance_score": performance,
        "overall_score": overall,
        "selected_overall_score": row.get("selected_overall_score", "") or str(int(overall)),
        "selected_seo_score": row.get("selected_seo_score", "") or str(int(seo)),
        "selected_technical_score": row.get("selected_technical_score", "") or str(int(technical)),
        "selected_security_score": row.get("selected_security_score", "") or str(int(security)),
        "selected_performance_score": row.get("selected_performance_score", "") or str(int(performance)),
        "selected_modernity_score": row.get("selected_modernity_score", "") or str(int(modernity)),
        "issues": row.get("selected_issues", "") or issues,
        "selected_issues": row.get("selected_issues", "") or issues,
        "segment": segment_for(row, campaign),
        "segment_confidence": "1.0",
        "outbound_priority": "P1" if campaign == "C6" else "P2",
        "outbound_bucket": campaign,
        "campaign": campaign,
        "status_audit": "OK" if truthy(row.get("new_alive", "")) else (row.get("new_state", "") or "UNKNOWN").upper(),
    }.items():
        row[field] = str(value)

    if not row.get("status"):
        state = row.get("new_state", "")
        row["status"] = "OK" if state.lower() == "ok" else state.upper()
    if not row.get("platform"):
        row["platform"] = row.get("new_cms", "") or row.get("new_framework", "") or "unknown"
    if not row.get("site_type"):
        commerce_text = " ".join(row.get(field, "") for field in ("new_cms", "new_framework", "new_wp_plugins", "details")).lower()
        row["site_type"] = "eshop" if any(token in commerce_text for token in ("shoptet", "woocommerce", "prestashop", "eshop", "cart", "checkout", "product")) else ("cms" if row.get("new_cms") else "static_html")
    if not row.get("details"):
        row["details"] = "; ".join(value for value in (row.get("new_meta_generator", ""), row.get("new_cms", ""), row.get("new_framework", "")) if value)

    bool_mappings = {
        "https": "new_ssl_certificate",
        "https_ok": "new_ssl_certificate",
        "responsive": "new_modernity_score",
        "modern_layout": "new_modernity_score",
        "has_favicon": "new_favicon",
        "structured_data": "new_schema_valid",
        "canonical_ok": "new_canonical",
        "robots_noindex": "new_noindex",
        "meta_desc_ok": "new_meta_description_ok",
        "robots_status": "new_robots_txt",
        "sitemap_status": "new_sitemap_xml",
        "social_og": "new_og_tags",
        "compression": "new_compression_type",
    }
    for target, source in bool_mappings.items():
        if target in {"robots_status", "sitemap_status"}:
            row[target] = row.get(source, "") or "absent"
        elif target in {"responsive", "modern_layout"}:
            row[target] = bool_text(modernity >= 50)
        else:
            value = row.get(source, "")
            row[target] = bool_text(truthy(value) if value.lower() in {"true", "false", "1", "0", "yes", "no"} else bool(value))
    if row.get("new_alive"):
        row["http_status"] = "200" if truthy(row.get("new_alive", "")) else "0"
    if row.get("new_h1_count"):
        row["h1_ok"] = bool_text(parse_float(row.get("new_h1_count")) >= 1)
    if row.get("new_lang"):
        row["lang_declared"] = "TRUE"
    if row.get("new_html_kb"):
        row["html_size_bytes"] = str(int(parse_float(row.get("new_html_kb")) * 1024))
    if row.get("new_images_total"):
        row["img_count"] = row.get("new_images_total", "")
        row["img_alt_ratio"] = str(parse_float(row.get("new_images_with_alt_pct")) / 100)
        row["img_nextgen_ratio"] = str(parse_float(row.get("new_images_nextgen_pct")) / 100)
        row["img_nextgen_count"] = str(int(parse_float(row.get("new_img_webp")) + parse_float(row.get("new_img_avif"))))
        row["img_legacy_count"] = row.get("new_images_legacy", "")
    if row.get("new_internal_links"):
        row["internal_links_count"] = row.get("new_internal_links", "")
    if row.get("new_security_headers_count"):
        row["security_headers"] = bool_text(parse_float(row.get("new_security_headers_count")) > 0)
    row["tech_issues"] = row.get("new_technical_issues", "")
    row["seo_issues"] = issue_subset(issues, ("title", "description", "canonical", "h1", "schema", "sitemap", "robots", "image", "alt", "noindex"))
    row["security_issues"] = issue_subset(issues, ("security", "hsts", "frame", "content-security", "php", "tls"))
    row["performance_issues"] = issue_subset(issues, ("load", "compression", "slow", "ttfb"))
    if issues:
        row["title_ok"] = bool_text(title_ok_from_issues(issues))
        for target, needle in (("hsts", "hsts"), ("x_frame_options", "x-frame-options"), ("x_content_type_options", "x-content-type-options"), ("csp", "content-security-policy")):
            row[target] = bool_text(needle not in issues.lower())
    row["category"] = "Moderný" if overall >= 85 else ("OK" if overall >= 67 else "Zlý")
    row.update(campaign_copy(domain, campaign, overall, seo, technical, issues))


def normalize_row(row: dict[str, str], line_no: int, cutoff_line: int, used: set[str] | None = None) -> tuple[dict[str, str], list[str]]:
    domain = (row.get("domain") or "").strip().lower().removeprefix("www.")
    row["domain"] = domain
    removed: list[str] = []
    campaign = campaign_for(row)
    # The old cutoff left unsafe addresses in historical rows.  Safety is now
    # applied to every physical CSV row while preserving its order and domain.
    cleaned, decisions, _ = safety_clean_row(row, used)
    row.update(cleaned)
    removed = [f"{d.email} [{d.reason}]" for d in decisions if not d.allowed]
    if line_no >= cutoff_line:
        value = row["emails"]
        row["new_new_emails"] = value
        row["new_new_email_count"] = row["new_email_count"]
        row["new_visible_email_count"] = "1" if value else "0"
        row["merge_email_count"] = row["new_email_count"]
        populate_legacy_fields(row, campaign)
    elif not (row.get("campaign") or "").strip():
        row["campaign"] = campaign
    return row, removed


def reservoir_add(bucket: list[dict[str, str]], item: dict[str, str], seen: int, limit: int, rng: random.Random) -> None:
    if len(bucket) < limit:
        bucket.append(item)
        return
    position = rng.randrange(seen)
    if position < limit:
        bucket[position] = item


def run(input_path: Path, output_path: Path, cutoff_line: int = DEFAULT_CUTOFF_LINE, used_csv: Path | None = None) -> dict[str, object]:
    rng = random.Random(20260929)
    source_campaign_counts: Counter[str] = Counter()
    output_campaign_counts: Counter[str] = Counter()
    sample_c6: list[dict[str, str]] = []
    sample_other: list[dict[str, str]] = []
    sample_seen = {"C6": 0, "other": 0}
    removed_email_count = 0
    rows_safety_processed = 0
    rows_with_safe_primary = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    if used_csv:
        with used_csv.open("r", encoding="utf-8-sig", newline="") as historical:
            used = used_emails(csv.DictReader(historical))
    with input_path.open("r", encoding="utf-8-sig", newline="") as source, output_path.open("w", encoding="utf-8-sig", newline="") as target:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        for field in ("other_emails", "safety_reasons", "email_safety_status"):
            if field not in fields:
                fields.append(field)
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for line_no, original in enumerate(reader, start=2):
            source_campaign = (original.get("campaign") or "").strip().upper()
            source_campaign_counts[source_campaign] += 1
            if source_campaign in CAMPAIGNS:
                bucket = sample_c6 if source_campaign == "C6" else sample_other
                bucket_key = "C6" if source_campaign == "C6" else "other"
                sample_seen[bucket_key] += 1
                reservoir_add(bucket, {
                    "line": line_no,
                    "domain": original.get("domain", ""),
                    "campaign": source_campaign,
                    "segment": original.get("segment", ""),
                    "overall_score": original.get("overall_score", "") or original.get("selected_overall_score", ""),
                    "seo_score": original.get("seo_score", "") or original.get("selected_seo_score", ""),
                    "tech_score": original.get("tech_score", "") or original.get("selected_technical_score", ""),
                    "structured_data": original.get("structured_data", "") or original.get("new_schema_valid", ""),
                    "social_og": original.get("social_og", "") or original.get("new_og_tags", ""),
                    "issues": original.get("issues", "") or original.get("selected_issues", ""),
                }, sample_seen[bucket_key], 15 if source_campaign == "C6" else 35, rng)
            normalized, removed = normalize_row(original, line_no, cutoff_line, used)
            rows_safety_processed += 1
            removed_email_count += len(removed)
            if normalized.get("emails"):
                rows_with_safe_primary += 1
            output_campaign_counts[(normalized.get("campaign") or "").strip().upper()] += 1
            writer.writerow(normalized)
    sample = sorted(sample_c6, key=lambda item: item["line"]) + sorted(sample_other, key=lambda item: item["line"])
    report = {
        "input": str(input_path),
        "output": str(output_path),
        "cutoff_line": cutoff_line,
        "used_csv": str(used_csv) if used_csv else "",
        "source_campaign_counts": dict(source_campaign_counts),
        "output_campaign_counts": dict(output_campaign_counts),
        "rows_processed_for_email_safety": rows_safety_processed,
        "rows_with_safe_primary_email": rows_with_safe_primary,
        "emails_removed_by_safety_rules": removed_email_count,
        "sample_size": len(sample),
        "sample_c6_count": sum(item["campaign"] == "C6" for item in sample),
        "sample": sample,
        "decision_rule": "C6 only when overall>=85, seo>=80, technical>=82, security>=75, performance>=80 and no dns_dead/blocked/challenged/competitor signal; blank campaigns otherwise become C1.",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Unify marketing lead columns, assign blank campaigns and limit new-row emails.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cutoff-line", type=int, default=DEFAULT_CUTOFF_LINE)
    parser.add_argument("--used-csv", type=Path, help="older sending log; its emails are never reused")
    args = parser.parse_args()
    report = run(args.input, args.output, args.cutoff_line, args.used_csv)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"sample"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
