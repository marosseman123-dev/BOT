"""Transparent 1-100 web-quality score + C1/C2/C3 category.

Built purely from the concrete metrics the crawler already gathered (HTTPS, TLS,
mobile-responsiveness, modern layout, PHP EOL, image optimization, meta/title,
structured data, security headers, compression, sitemap/robots, load time...).

100 = perfect, 1 = worst (needs an urgent fix or a brand-new site). The category
groups the score into three actionable bands so a personalized offer can be sent:
    C1 (67-100): web in good shape        -> offer optimization / SEO tune-up
    C2 (34-66):  web mediocre             -> offer modernization / redesign
    C3 (1-33):   web poor / broken        -> offer a brand-new website
"""
from __future__ import annotations

from typing import Dict, Tuple

C1_MIN = 67          # >= C1_MIN -> C1
C2_MIN = 34          # >= C2_MIN -> C2, else C3


def _present(v) -> bool:
    return str(v).strip().lower() == "present"


def category_for(score: int) -> str:
    if score >= C1_MIN:
        return "C1"
    if score >= C2_MIN:
        return "C2"
    return "C3"


def web_score(rec: Dict) -> Tuple[int, str]:
    """Return (score 1-100, category). Category is '' when the site was never really
    analysed (no successful page load) -- we don't certify quality we couldn't see."""
    status = int(rec.get("http_status") or 0)
    analysed = (200 <= status < 400) and bool(rec.get("alive")) and not rec.get("blocked")
    if not analysed:
        return 0, ""     # couldn't assess the live site this run

    earned = 0.0
    possible = 0.0

    def add(cond: bool, pts: float):
        nonlocal earned, possible
        possible += pts
        if cond:
            earned += pts

    # --- security / transport ---
    add(bool(rec.get("https") or rec.get("ssl_certificate")), 12)
    tls = str(rec.get("tls_version") or "")
    add("1.3" in tls or "1.2" in tls, 5)
    ver = str(rec.get("http_version") or "")
    add("2" in ver or "3" in ver, 3)
    add(not rec.get("php_eol"), 8)
    jq = str(rec.get("jquery_version") or "")
    add(jq[:1] not in ("1", "2"), 4)               # old jQuery 1.x/2.x = bad
    sec = int(rec.get("security_headers_count") or 0)
    possible += 6
    earned += min(sec, 6)

    # --- mobile / modernity (heaviest weight for a web-dev pitch) ---
    add(bool(rec.get("responsive")), 15)
    add(bool(rec.get("modern_layout")), 10)

    # --- SEO basics ---
    add(bool(rec.get("meta_description_ok")), 6)
    add(bool(rec.get("title_ok")), 5)
    add(int(rec.get("h1_count") or 0) >= 1, 3)
    add(bool(rec.get("structured_data") or rec.get("schema_valid")), 4)
    add(_present(rec.get("sitemap_xml")), 3)
    add(_present(rec.get("robots_txt")), 2)
    add(int(rec.get("word_count") or 0) >= 60, 2)

    # --- performance ---
    add(bool(rec.get("compression")), 3)
    lt = float(rec.get("load_time_s") or 0)
    possible += 6
    if 0 < lt <= 1.5:
        earned += 6
    elif lt <= 3:
        earned += 4
    elif lt <= 6:
        earned += 2

    # --- images (only judged if the page actually has images) ---
    if int(rec.get("images_total") or 0) > 0:
        add(int(rec.get("images_nextgen_pct") or 0) >= 50 or bool(rec.get("images_compressed")), 6)
        add(int(rec.get("images_with_alt_pct") or 0) >= 60, 4)

    # --- penalty: mixed content on an https page ---
    if rec.get("mixed_content"):
        earned -= 5

    score = 1 if possible <= 0 else int(round(earned / possible * 100))
    score = max(1, min(100, score))
    return score, category_for(score)
