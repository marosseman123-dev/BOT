"""The audit engine itself: analyze_fetch(fetch, domain, cfg) -> AuditResult.

Pure function over an already-fetched page (see models.FetchResult) -- no I/O.
Parses the HTML/headers once and derives ~90 feature columns covering platform
detection, SEO basics, security headers, performance, images, and WordPress
specifics, then rolls those up into five 0-100 category scores plus an overall
score and a ranked list of findings.

There is no live CVE feed here (no internet access from this engine) -- version
staleness (PHP EOL, old jQuery, outdated WP plugin counts, ...) is judged with
documented heuristics in config.py rather than a real vulnerability database.
That is a deliberate, disclosed simplification, not a bug.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import Config
from .models import AuditResult, Finding, FetchResult
from .severity import Severity
from .states import EvidenceStatus

_IMG_EXT = re.compile(r"\.(jpe?g|png|gif|svg|webp|avif)(?:[?#]|$)", re.I)
_SCRIPT_SRC = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)
_LINK_HREF = re.compile(r'<link[^>]+href=["\']([^"\']+)["\']', re.I)
_A_HREF = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.I)
_PHP_VER = re.compile(r"PHP/(\d+\.\d+)", re.I)
_JQUERY_VER = re.compile(r"jquery[.\-]?(\d+\.\d+(?:\.\d+)?)", re.I)
_BOOTSTRAP_VER = re.compile(r"bootstrap[.\-]?(\d+\.\d+(?:\.\d+)?)", re.I)
_WP_GENERATOR_VER = re.compile(r"wordpress\s+(\d+\.\d+(?:\.\d+)?)", re.I)
_WP_PLUGIN = re.compile(r"wp-content/plugins/([a-z0-9\-_]+)", re.I)
_WP_THEME = re.compile(r"wp-content/themes/([a-z0-9\-_]+)", re.I)
_WP_THEME_VER = re.compile(r"wp-content/themes/[a-z0-9\-_]+/style\.css\?ver=([\d.]+)", re.I)

_CDN_HEADER_HINTS = (
    ("cf-ray", "Cloudflare"), ("cf-cache-status", "Cloudflare"),
    ("x-served-by", "Fastly"), ("x-fastly-request-id", "Fastly"),
    ("x-amz-cf-id", "CloudFront"), ("x-cache", "CDN"),
    ("x-vercel-id", "Vercel"), ("x-azure-ref", "Azure CDN"),
)
_WAF_HINTS = ("cloudflare", "sucuri", "incapsula", "akamai", "imperva")

_PLATFORM_SIGNALS: List[Tuple[str, str]] = [
    ("wp-content", "WordPress"), ("wp-includes", "WordPress"),
    ("cdn.shopify.com", "Shopify"), ("shopify", "Shopify"),
    ("static.wixstatic.com", "Wix"), ("wix.com", "Wix"),
    ("squarespace", "Squarespace"),
    ("/media/jui/", "Joomla"), ("joomla", "Joomla"),
    ("sites/default/files", "Drupal"), ("drupal", "Drupal"),
    ("prestashop", "PrestaShop"),
    ("shoptet", "Shoptet"), ("static.upgates", "Upgates"),
    ("cdn11.bigcommerce", "BigCommerce"),
    ("webnode", "Webnode"), ("static.webnode", "Webnode"),
    ("cdn.webflow.com", "Webflow"), ("webflow", "Webflow"),
    ("mgz-images", "Magento"), ("magento", "Magento"),
]

_ANALYTICS_SIGNALS = [
    ("googletagmanager.com", "Google Tag Manager"),
    ("google-analytics.com", "Google Analytics"),
    ("gtag(", "Google Analytics (gtag)"),
    ("connect.facebook.net", "Meta Pixel"),
    ("hotjar.com", "Hotjar"),
    ("seznam.cz/analytics", "Seznam Sklik"),
    ("matomo", "Matomo"),
]

_JS_LIB_SIGNALS = [
    ("jquery", "jQuery"), ("react", "React"), ("vue.js", "Vue"), ("vue.min", "Vue"),
    ("angular", "Angular"), ("bootstrap", "Bootstrap"), ("tailwind", "Tailwind"),
    ("alpine.js", "Alpine.js"), ("htmx", "htmx"),
]

_AGENCY_CONTACT_PATHS = ("kontakt", "contact", "o-nas", "o-nás", "about")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _soup(html: str):
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return BeautifulSoup("", "html.parser")


def _meta(soup, **attrs) -> str:
    tag = soup.find("meta", attrs=attrs)
    return (tag.get("content") or "").strip() if tag else ""


def _header(headers: Dict[str, str], name: str) -> str:
    return (headers.get(name.lower()) or "").strip()


def _php_eol(version: str, cfg: Config) -> bool:
    if not version:
        return False
    try:
        maj, minr = (int(x) for x in version.split(".")[:2])
    except Exception:
        return False
    return (maj, minr) < cfg.php_supported_floor


def _jquery_eol(version: str, cfg: Config) -> bool:
    if not version:
        return False
    try:
        maj = int(version.split(".")[0])
    except Exception:
        return False
    return maj in cfg.jquery_eol_majors


def _detect_platform(html_low: str, headers: Dict[str, str]) -> Tuple[str, str, str]:
    """Return (platform, cms, framework)."""
    for token, name in _PLATFORM_SIGNALS:
        if token in html_low:
            cms = name if name not in ("Webflow",) else ""
            return name, (name if name != "Webflow" else ""), ("Webflow" if name == "Webflow" else "")
    xpb = _header(headers, "x-powered-by").lower()
    if "express" in xpb:
        return "Node.js/Express", "", "Express"
    if "next.js" in html_low or "__next" in html_low:
        return "Next.js", "", "Next.js"
    if "nuxt" in html_low:
        return "Nuxt", "", "Nuxt"
    if "asp.net" in xpb:
        return "ASP.NET", "", "ASP.NET"
    if "django" in xpb:
        return "Django", "", "Django"
    return "custom/static", "", ""


def _image_stats(soup) -> Dict:
    imgs = soup.find_all("img")
    total = len(imgs)
    counts = {"jpg": 0, "png": 0, "gif": 0, "svg": 0, "webp": 0, "avif": 0, "other": 0}
    with_alt = 0
    for im in imgs:
        alt = (im.get("alt") or "").strip()
        if alt:
            with_alt += 1
        src = (im.get("src") or im.get("data-src") or "").lower()
        m = _IMG_EXT.search(src)
        ext = (m.group(1) if m else "").lower()
        if ext in ("jpg", "jpeg"):
            counts["jpg"] += 1
        elif ext == "png":
            counts["png"] += 1
        elif ext == "gif":
            counts["gif"] += 1
        elif ext == "svg":
            counts["svg"] += 1
        elif ext == "webp":
            counts["webp"] += 1
        elif ext == "avif":
            counts["avif"] += 1
        else:
            counts["other"] += 1
    nextgen = counts["webp"] + counts["avif"]
    legacy = counts["jpg"] + counts["png"] + counts["gif"]
    alt_pct = int(round(100 * with_alt / total)) if total else 0
    nextgen_pct = int(round(100 * nextgen / total)) if total else 0
    return {
        "images_total": total, "images_with_alt_pct": alt_pct,
        "images_nextgen_pct": nextgen_pct, "images_compressed": nextgen > 0,
        "images_legacy": legacy,
        "img_jpg": counts["jpg"], "img_png": counts["png"], "img_gif": counts["gif"],
        "img_svg": counts["svg"], "img_webp": counts["webp"], "img_avif": counts["avif"],
        "img_other": counts["other"],
    }


def _schema_stats(html: str, soup) -> Dict:
    blocks, types, valid = 0, [], False
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        blocks += 1
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type"):
                t = it["@type"]
                types += t if isinstance(t, list) else [t]
                valid = True
    # microdata fallback
    if soup.find(attrs={"itemtype": True}):
        valid = True
        for tag in soup.find_all(attrs={"itemtype": True}):
            it = tag.get("itemtype", "")
            types.append(it.rsplit("/", 1)[-1])
    return {"schema_valid": valid, "schema_blocks": blocks,
            "schema_types": ", ".join(sorted(set(types)))}


def _wp_stats(html: str, html_low: str, soup, cfg: Config) -> Dict:
    is_wp = "wp-content" in html_low or "wp-includes" in html_low
    version = ""
    m = _WP_GENERATOR_VER.search(_meta(soup, name="generator"))
    if m:
        version = m.group(1)
    plugins = sorted(set(_WP_PLUGIN.findall(html)))
    themes = sorted(set(_WP_THEME.findall(html)))
    theme = themes[0] if themes else ""
    tm = _WP_THEME_VER.search(html)
    theme_ver = tm.group(1) if tm else ""
    return {
        "wp_version": version, "wp_theme": theme, "wp_theme_version": theme_ver,
        "wp_plugin_count": len(plugins), "wp_plugins": ", ".join(plugins[:40]),
        "many_plugins_review": len(plugins) >= cfg.many_plugins_threshold,
        "wordpress_version": version, "_is_wp": is_wp,
    }


def _security_headers(headers: Dict[str, str]) -> Dict:
    hsts = bool(_header(headers, "strict-transport-security"))
    csp = bool(_header(headers, "content-security-policy"))
    xfo = bool(_header(headers, "x-frame-options"))
    xcto = bool(_header(headers, "x-content-type-options"))
    rp = bool(_header(headers, "referrer-policy"))
    pp = bool(_header(headers, "permissions-policy"))
    return {"hsts": hsts, "csp": csp, "x_frame_options": xfo,
            "x_content_type_options": xcto, "referrer_policy": rp,
            "permissions_policy": pp,
            "security_headers_count": sum((hsts, csp, xfo, xcto, rp, pp))}


def _extract_features(fetch: FetchResult, domain: str, cfg: Config) -> Dict:
    html = fetch.html or ""
    html_low = html.lower()
    headers = {k.lower(): v for k, v in (fetch.headers or {}).items()}
    soup = _soup(html)

    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else ""
    desc = _meta(soup, name="description")
    h1_count = len(soup.find_all("h1"))
    viewport = _meta(soup, name="viewport")
    responsive = "width=device-width" in viewport
    canonical_tag = soup.find("link", rel="canonical")
    canonical = bool(canonical_tag and canonical_tag.get("href"))
    robots_meta = _meta(soup, name="robots").lower()
    noindex = "noindex" in robots_meta
    og_tags = bool(soup.find("meta", property=re.compile(r"^og:", re.I)))
    favicon = bool(soup.find("link", rel=re.compile(r"icon", re.I)))
    hreflang_count = len(soup.find_all("link", rel="alternate", hreflang=True))
    html_tag = soup.find("html")
    lang = (html_tag.get("lang") or "").strip() if html_tag else ""

    text = soup.get_text(separator=" ")
    word_count = len(text.split())

    netloc = urlparse(fetch.final_url or "").netloc.lower()
    base_host = netloc.split(":")[0] or domain
    internal_links = 0
    for href in _A_HREF.findall(html)[:500]:
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        h = urlparse(href).netloc.lower()
        if not h or h == base_host or h.endswith("." + base_host.lstrip("www.")):
            internal_links += 1

    modern_layout = responsive and (
        "display:flex" in html_low or "display: flex" in html_low
        or "display:grid" in html_low or "display: grid" in html_low
        or "grid-template" in html_low or "flexbox" in html_low
        or any(fw in html_low for fw in ("bootstrap", "tailwind", "bulma", "foundation"))
    )

    server = _header(headers, "server")
    xpb = _header(headers, "x-powered-by")
    php_m = _PHP_VER.search(xpb) or _PHP_VER.search(server)
    php_version = php_m.group(1) if php_m else ""
    php_eol = _php_eol(php_version, cfg)

    jq_m = _JQUERY_VER.search(html_low)
    jquery_version = jq_m.group(1) if jq_m else ""
    bs_m = _BOOTSTRAP_VER.search(html_low)
    bootstrap_version = bs_m.group(1) if bs_m else ""
    meta_generator = _meta(soup, name="generator")

    cdn = ""
    for hname, cname in _CDN_HEADER_HINTS:
        if _header(headers, hname):
            cdn = cname
            break
    waf = ""
    server_low = server.lower()
    for hint in _WAF_HINTS:
        if hint in server_low or hint in html_low[:2000]:
            waf = hint.capitalize()
            break

    platform, cms, framework = _detect_platform(html_low, headers)
    language_runtime = ("PHP " + php_version) if php_version else (
        "Node.js" if "express" in xpb.lower() else "")

    analytics = ", ".join(sorted({n for tok, n in _ANALYTICS_SIGNALS if tok in html_low}))
    js_libraries = ", ".join(sorted({n for tok, n in _JS_LIB_SIGNALS if tok in html_low}))

    content_encoding = _header(headers, "content-encoding").lower()
    compression = bool(content_encoding)

    tls = fetch.tls_version or "unknown"
    https = (fetch.scheme == "https") or tls != "unknown"
    ssl_certificate = tls != "unknown"

    feats = {
        "lang": lang, "lang_declared": bool(lang),
        "platform": platform, "cms": cms, "framework": framework,
        "language_runtime": language_runtime, "server": server, "cdn": cdn, "waf": waf,
        "analytics": analytics, "js_libraries": js_libraries,
        "https": https, "ssl_certificate": ssl_certificate, "tls_version": tls,
        "http_version": fetch.http_version or "",
        "compression": compression, "compression_type": content_encoding,
        "ttfb_s": round(fetch.ttfb_s or 0.0, 3), "load_time_s": round(fetch.load_time_s or 0.0, 3),
        "html_kb": round((fetch.html_size_bytes or len(html.encode("utf-8", "ignore"))) / 1024, 1),
        "responsive": responsive, "modern_layout": modern_layout,
        "title_ok": bool(title) and cfg.title_len_range[0] <= len(title) <= cfg.title_len_range[1] * 3,
        "meta_description_ok": bool(desc) and cfg.meta_desc_len_range[0] <= len(desc) <= cfg.meta_desc_len_range[1],
        "h1_count": h1_count, "canonical": canonical, "noindex": noindex,
        "internal_links": internal_links, "word_count": word_count,
        "og_tags": og_tags, "favicon": favicon, "hreflang_count": hreflang_count,
        "php_version": php_version, "php_eol": php_eol,
        "jquery_version": jquery_version, "bootstrap_version": bootstrap_version,
        "meta_generator": meta_generator,
        "robots_txt": fetch.robots_status, "sitemap_xml": fetch.sitemap_status,
        "sitemap_url": fetch.sitemap_url,
        "final_url": fetch.final_url, "http_status": fetch.http_status,
        "blocked": bool(fetch.blocked), "challenged": bool(fetch.challenged),
        "spa_shell": bool(fetch.spa_shell),
    }
    feats.update(_security_headers(headers))
    feats.update(_image_stats(soup))
    feats.update(_schema_stats(html, soup))
    feats.update(_wp_stats(html, html_low, soup, cfg))
    return feats


# ---------------------------------------------------------------------------
# scoring / findings
# ---------------------------------------------------------------------------

def _cat_score(checks: List[Tuple[bool, float]]) -> int:
    possible = sum(w for _c, w in checks)
    earned = sum(w for c, w in checks if c)
    if possible <= 0:
        return 0
    return max(0, min(100, int(round(100 * earned / possible))))


def _build_findings(f: Dict, cfg: Config) -> List[Finding]:
    F: List[Finding] = []

    def add(cond_bad: bool, fid: str, title: str, sev: Severity, category: str, impact: str):
        if cond_bad:
            F.append(Finding(fid, title, sev, category, impact))

    # --- security ---
    add(not f["https"], "no_https", "Site not served over HTTPS", Severity.CRITICAL,
        "security", "no TLS on any page")
    add(f["php_eol"], "php_eol", "PHP version is end-of-life", Severity.HIGH,
        "security", f"PHP {f['php_version'] or '?'} no longer receives security patches")
    add(not f["hsts"], "no_hsts", "Missing HSTS header", Severity.LOW, "security", "no Strict-Transport-Security")
    add(not f["csp"], "no_csp", "Missing Content-Security-Policy", Severity.MEDIUM,
        "security", "no CSP header, higher XSS exposure")
    add(not f["x_frame_options"], "no_xfo", "Missing X-Frame-Options", Severity.LOW,
        "security", "clickjacking exposure")
    add(f["many_plugins_review"], "many_plugins", "Large WordPress plugin surface", Severity.MEDIUM,
        "security", f"{f['wp_plugin_count']} plugins detected, wider attack surface")

    # --- seo ---
    add(not f["title_ok"], "bad_title", "Title tag missing or poorly sized", Severity.MEDIUM,
        "seo", "hurts click-through from search results")
    add(not f["meta_description_ok"], "bad_meta_desc", "Meta description missing or poorly sized",
        Severity.MEDIUM, "seo", "search snippets will be auto-generated")
    add(f["h1_count"] == 0, "no_h1", "No H1 heading", Severity.LOW, "seo", "weak on-page structure")
    add(f["h1_count"] > 1, "multi_h1", "Multiple H1 headings", Severity.LOW, "seo", "diluted heading signal")
    add(not f["schema_valid"], "no_schema", "No structured data (schema.org)", Severity.LOW,
        "seo", "missing rich-result eligibility")
    add(f["sitemap_xml"] != "present", "no_sitemap", "No sitemap.xml found", Severity.LOW,
        "seo", "slower/incomplete indexing")
    add(f["robots_txt"] != "present", "no_robots", "No robots.txt found", Severity.INFO,
        "seo", "no explicit crawl guidance")
    add(f["word_count"] < cfg.min_word_count, "thin_content", "Very little text content", Severity.MEDIUM,
        "seo", f"only {f['word_count']} words on the page")
    add(f["noindex"], "noindex", "Page is marked noindex", Severity.HIGH, "seo", "excluded from search results")

    # --- technical ---
    add(not f["compression"], "no_compression", "No response compression", Severity.LOW,
        "technical", "larger transfer size than necessary")
    add(f["_is_wp"] and not f["wp_version"], "wp_version_hidden", "WordPress version not exposed",
        Severity.GOOD, "technical", "")
    add(bool(f.get("jquery_version")) and _jquery_eol(f.get("jquery_version", ""), cfg),
        "old_jquery", "Outdated jQuery version", Severity.MEDIUM, "technical",
        f"jQuery {f.get('jquery_version')} is old and unsupported")
    add(f["blocked"], "blocked", "Site blocked/challenged the crawler", Severity.HIGH,
        "technical", "bot-protection prevented a full page read")
    add(f["spa_shell"], "spa_shell", "Page is a near-empty JS shell", Severity.MEDIUM,
        "technical", "content likely not visible to simple crawlers/SEO bots")

    # --- performance ---
    lt = f["load_time_s"]
    if lt > cfg.slow_load_s:
        F.append(Finding("slow_load", "Page loads slowly", Severity.HIGH, "performance",
                          f"{lt:.1f}s full load"))
    elif lt > cfg.ok_load_s:
        F.append(Finding("slow_load", "Page load time could be faster", Severity.LOW, "performance",
                          f"{lt:.1f}s full load"))
    add(f["html_kb"] > cfg.large_page_kb, "large_page", "HTML document is large", Severity.LOW,
        "performance", f"{f['html_kb']:.0f} KB of markup")
    if f["images_total"] > 0:
        add(f["images_nextgen_pct"] < 30 and not f["images_compressed"], "old_image_formats",
            "Images not using modern formats", Severity.LOW, "performance",
            "no WebP/AVIF detected among page images")
        add(f["images_with_alt_pct"] < 60, "missing_alt", "Many images missing alt text",
            Severity.LOW, "seo", f"only {f['images_with_alt_pct']}% of images have alt text")

    # --- modernity ---
    add(not f["responsive"], "not_responsive", "No mobile viewport tag", Severity.HIGH,
        "modernity", "site likely not mobile-friendly")
    add(not f["modern_layout"], "old_layout", "No modern CSS layout detected", Severity.LOW,
        "modernity", "no flex/grid or modern framework signals")
    add(not f["og_tags"], "no_og", "No Open Graph tags", Severity.INFO, "modernity",
        "social-share previews will be generic")

    return F


def _category_scores(f: Dict, cfg: Config) -> Dict[str, int]:
    seo = _cat_score([
        (f["title_ok"], 15), (f["meta_description_ok"], 15), (f["h1_count"] == 1, 10),
        (f["schema_valid"], 10), (f["sitemap_xml"] == "present", 10),
        (f["robots_txt"] == "present", 5), (f["word_count"] >= cfg.good_word_count, 15),
        (f["canonical"], 5), (f["internal_links"] >= 3, 10), (f["favicon"], 5),
    ])
    technical = _cat_score([
        (f["https"], 25), (not f["php_eol"], 20), (f["compression"], 15),
        (f["sitemap_xml"] == "present", 10), (f["robots_txt"] == "present", 10),
        (not f["blocked"], 10), (not f["spa_shell"], 10),
    ])
    performance = _cat_score([
        (f["load_time_s"] <= cfg.fast_load_s, 35),
        (f["load_time_s"] <= cfg.ok_load_s, 15),
        (f["html_kb"] <= cfg.large_page_kb, 20),
        (f["compression"], 15),
        (f["images_total"] == 0 or f["images_nextgen_pct"] >= 30 or f["images_compressed"], 15),
    ])
    security = _cat_score([
        (f["https"], 20), ("1.3" in f["tls_version"] or "1.2" in f["tls_version"], 10),
        (f["hsts"], 12), (f["csp"], 15), (f["x_frame_options"], 10),
        (f["x_content_type_options"], 10), (f["referrer_policy"], 8),
        (f["permissions_policy"], 5), (not f["php_eol"], 10),
    ])
    modernity = _cat_score([
        (f["responsive"], 35), (f["modern_layout"], 25), (f["schema_valid"], 15),
        (f["og_tags"], 10),
        (not f.get("jquery_version") or not _jquery_eol(f.get("jquery_version", ""), cfg), 15),
    ])
    return {"seo_score": seo, "technical_score": technical, "performance_score": performance,
            "security_score": security, "modernity_score": modernity}


def _outreach_comment(f: Dict, cat: Dict[str, int], overall: int) -> str:
    weakest = min(cat, key=cat.get)
    label = {"seo_score": "SEO basics", "technical_score": "technical setup",
             "performance_score": "page speed", "security_score": "security headers",
             "modernity_score": "mobile/modern design"}[weakest]
    if overall >= 90:
        return "Strong site overall -- minor polish only."
    if overall >= 67:
        return f"Solid site; biggest opportunity is {label}."
    if overall >= 34:
        return f"Site works but is dated -- lead with {label} as the pitch."
    return f"Site is in poor shape ({label} especially) -- good candidate for a full rebuild."


def analyze_fetch(fetch: FetchResult, domain: str, cfg: Config) -> AuditResult:
    """Run the full audit against an already-fetched page. Never raises for a
    normal (even malformed) page -- callers still wrap this in try/except for
    truly unexpected input."""
    if not fetch.ok:
        evidence = EvidenceStatus.UNAVAILABLE
        feats: Dict = {}
        findings: List[Finding] = []
        cat = {"seo_score": 0, "technical_score": 0, "performance_score": 0,
               "security_score": 0, "modernity_score": 0}
        overall = 0
    else:
        feats = _extract_features(fetch, domain, cfg)
        findings = _build_findings(feats, cfg)
        cat = _category_scores(feats, cfg)
        overall = int(round(sum(cat.values()) / len(cat)))
        evidence = fetch.evidence if isinstance(fetch.evidence, EvidenceStatus) else EvidenceStatus.CONFIRMED

    n_critical = sum(1 for x in findings if x.severity == Severity.CRITICAL)
    n_high = sum(1 for x in findings if x.severity == Severity.HIGH)

    def issues_for(category: str) -> str:
        return "; ".join(x.title for x in findings
                          if x.category == category and x.severity >= Severity.LOW)

    feats["n_critical"] = n_critical
    feats["n_high"] = n_high
    feats["seo_issues"] = issues_for("seo")
    feats["technical_issues"] = issues_for("technical")
    feats["performance_issues"] = issues_for("performance")
    feats["security_issues"] = issues_for("security")
    feats["modernity_issues"] = issues_for("modernity")
    feats["is_excellent"] = overall >= cfg.excellent_score
    feats["completeness"] = 1.0 if fetch.ok and not fetch.blocked else (0.3 if fetch.ok else 0.0)
    feats.pop("_is_wp", None)

    ranked = sorted(findings, key=lambda x: x.order_key, reverse=True)
    top_priorities = [x.title for x in ranked if x.severity >= Severity.MEDIUM][:3]

    state = "ok" if fetch.ok and not fetch.blocked else ("blocked" if fetch.blocked else "unreachable")

    return AuditResult(
        domain=domain, state=state, evidence=str(evidence), overall_score=overall,
        features=feats, detections=[], findings=findings, category_scores=cat,
        top_priorities=top_priorities,
        outreach_comment=_outreach_comment(feats, cat, overall) if fetch.ok else "",
        fetch={"blocked": bool(fetch.blocked), "challenged": bool(fetch.challenged),
               "http_status": fetch.http_status, "final_url": fetch.final_url},
    )
