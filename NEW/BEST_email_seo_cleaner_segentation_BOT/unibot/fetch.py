"""HTTP fetch layer on **httpx** (pure-Python, proven stable on Python 3.14).

Rewritten off aiohttp because aiohttp's compiled C extensions (multidict/yarl/
parser) crash with an access violation (0xC0000005) on bleeding-edge CPython 3.14.
httpx uses httpcore/h11 (no fragile C extensions) and is the same client the
seo_audit engine ran 143k domains on without a single native crash.

Liveness is DNS-only (getaddrinfo). We deliberately do NOT open a TCP socket to the
target: doing that and then letting httpx open a second connection to the same
host:443 under high concurrency triggered mass ConnectErrors on Windows. seo_audit
(proven at 64 concurrency) has no such probe -- httpx does TCP/TLS -- so neither do we.

One domain -> homepage (https then http) + contact pages + robots/sitemap/TLS,
fetched once. Fail-soft: a dead domain returns homepage {"ok": False}.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx

from hunter import extract as EH
from .config import Config

# Dedicated thread pool for the BLOCKING DNS/TLS lookups. Kept off the event loop's
# default executor because httpx resolves DNS on that default executor on Windows;
# sharing it would let these lookups starve httpx's own connects.
_PROBE_POOL = ThreadPoolExecutor(max_workers=64, thread_name_prefix="livecheck")

# Why did a GET fail? _get swallows exceptions (fail-soft), but we still tally the
# error TYPES so a systemic failure (e.g. every page a ConnectError) is visible in
# the log/dashboard instead of silently marking the whole run dead.
FETCH_ERRORS: "Counter[str]" = Counter()
_log = logging.getLogger("unibot")


def error_summary(top: int = 8) -> str:
    if not FETCH_ERRORS:
        return "no fetch errors recorded"
    return ", ".join(f"{name}={n}" for name, n in FETCH_ERRORS.most_common(top))


_CHALLENGE = ("just a moment", "cf-chl", "attention required", "checking your browser",
              "please enable javascript and cookies", "__cf_chl")
_SPA_ROOTS = ('id="root"', 'id="app"', 'id="__next"', "data-reactroot", 'id="__nuxt"', "ng-app")


def _headers(cfg: Config, domain: str) -> dict:
    uas = cfg.uas()
    h = int(hashlib.md5(domain.encode()).hexdigest(), 16)
    return {"User-Agent": uas[h % len(uas)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "cs-CZ,cs;q=0.9,sk;q=0.7,en;q=0.5",
            # ponytail: advertise only codecs httpx decodes natively. Advertising
            # 'br'/'zstd' without the brotli/zstandard package makes r.text raise a
            # DecodingError on every compressed page -> every domain looks dead.
            "Accept-Encoding": "gzip, deflate", "Connection": "keep-alive"}


def _is_challenge(status: int, body: str) -> bool:
    b = body[:4000].lower()
    return (status in (429, 503) and any(m in b for m in _CHALLENGE)) or any(m in b for m in _CHALLENGE)


def _is_spa(html: str) -> bool:
    low = html.lower()
    if not any(r in low for r in _SPA_ROOTS):
        return False
    import re
    txt = re.sub(r"<[^>]+>", " ", re.sub(r"<script[\s\S]*?</script>", " ", low))
    return len(txt.split()) < 120 and low.count("<script") >= 3


# ---------------------------------------------------------------------------
# Liveness: DNS resolve ONLY (no target socket). httpx does TCP + TLS.
# ---------------------------------------------------------------------------
def _blocking_liveness(domain: str, tcp_to: float):
    """Returns (alive, host, port, reason). port=443 => homepage tries https then http.
    DNS-only on purpose (see module docstring): the raw TCP probe caused ConnectError
    storms under concurrency. A DNS-resolving host that refuses TCP surfaces as an
    httpx ConnectError during the homepage fetch and is retried/settled normally."""
    bare = domain[4:] if domain.startswith("www.") else domain
    for host in (bare, "www." + bare):
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return True, host, 443, ""
        except Exception:
            continue
    return False, "", 0, "dns"


async def _liveness(domain: str, cfg: Config):
    return await asyncio.get_event_loop().run_in_executor(
        _PROBE_POOL, _blocking_liveness, domain, cfg.tcp_probe_timeout)


def _blocking_tls(host: str, timeout: float) -> str:
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, 443), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                return ss.version() or "unknown"
    except Exception:
        return "unknown"


async def _tls_version(host: str, timeout: float) -> str:
    return await asyncio.get_event_loop().run_in_executor(_PROBE_POOL, _blocking_tls, host, timeout)


def _dead_homepage(dns_dead: bool = False, dead_reason: str = "") -> dict:
    return {"ok": False, "url": "", "scheme": "", "status": 0, "headers": {}, "html": "",
            "blocked": False, "challenged": False, "spa_shell": False, "html_size_bytes": 0,
            "robots_status": "unknown", "sitemap_status": "unknown", "sitemap_url": "",
            "tls_version": "unknown", "http_version": "", "ttfb_s": 0.0, "load_time_s": 0.0,
            "dns_dead": dns_dead, "dead_reason": dead_reason}


async def _get(client: httpx.AsyncClient, url: str, cfg: Config, domain: str, timeout: float):
    try:
        start = time.monotonic()
        r = await client.get(url, headers=_headers(cfg, domain),
                             timeout=httpx.Timeout(timeout, connect=cfg.connect_timeout))
        html = r.text or ""
        el = time.monotonic() - start
        return {"ok": True, "url": str(r.url), "status": r.status_code,
                "headers": {k.lower(): v for k, v in r.headers.items()},
                "html": html[: cfg.max_html_bytes], "load_time_s": el, "ttfb_s": el,
                "http_version": r.http_version or "",
                "scheme": urlparse(str(r.url)).scheme}
    except Exception as e:
        et = type(e).__name__
        if FETCH_ERRORS[et] == 0:      # first of its kind -> surface it immediately
            _log.warning("fetch error (%s) on %s: %s", et, url, e)
        FETCH_ERRORS[et] += 1
        return None


async def _tagged(name, coro):
    try:
        return name, await coro
    except Exception:
        return name, None


async def _probe(client, url, cfg, domain):
    r = await _get(client, url, cfg, domain, cfg.probe_timeout)
    if r is None:
        return "unknown", ""
    if r["status"] == 200:
        return "present", r
    if r["status"] in (404, 410):
        return "absent", ""
    return "unknown", ""


async def _robots(client, root, cfg, domain):
    st, r = await _probe(client, root + "/robots.txt", cfg, domain)
    sms = []
    if isinstance(r, dict):
        for line in (r["html"] or "")[:20000].splitlines():
            if line.lower().startswith("sitemap:"):
                u = line.split(":", 1)[1].strip()
                if u.startswith("http"):
                    sms.append(u)
    return st, sms


async def _sitemap_at(client, url, cfg, domain):
    r = await _get(client, url, cfg, domain, cfg.probe_timeout)
    if r is None:
        return "unknown", ""
    if r["status"] == 200:
        ct = r["headers"].get("content-type", "").lower()
        head = (r["html"][:300].lower() if r["html"] else "")
        if "xml" in ct or url.endswith(".xml") or "<urlset" in head or "<sitemapindex" in head:
            return "present", url
        return "absent", ""
    if r["status"] in (404, 410):
        return "absent", ""
    return "unknown", ""


async def fetch_domain(client: httpx.AsyncClient, domain: str, cfg: Config):
    """Return (homepage_dict, [contact_page_dicts]). homepage['ok']==False if dead."""
    _t = {"dns": 0.0, "homepage": 0.0, "ancillary": 0.0, "contacts": 0.0}
    _t0 = time.monotonic()
    alive, host, port, reason = await _liveness(domain, cfg)
    _t["dns"] = time.monotonic() - _t0
    if not alive:
        hp = _dead_homepage(dns_dead=True, dead_reason=reason)
        hp["_t"] = _t
        return hp, []

    order = ((f"https://{host}", f"http://{host}") if port == 443
             else (f"http://{host}", f"https://{host}"))
    _th = time.monotonic()
    hp = None
    for url in order:
        _cs = time.monotonic()
        r = await _get(client, url, cfg, domain, cfg.read_timeout)
        if r is None:
            if time.monotonic() - _cs >= cfg.connect_timeout:
                break            # hung -> the other scheme will hang too; stop
            continue
        body = r["html"]
        r["challenged"] = _is_challenge(r["status"], body)
        r["blocked"] = r["status"] in (403, 429) or r["challenged"]
        r["spa_shell"] = (not r["blocked"]) and _is_spa(body)
        r["html_size_bytes"] = len(body.encode("utf-8", "ignore"))
        if 200 <= r["status"] < 400 and not r["blocked"]:
            hp = r
            break
        if hp is None:
            hp = r

    _t["homepage"] = time.monotonic() - _th
    if hp is None:
        hp = _dead_homepage(dns_dead=False, dead_reason="http")
        hp["_t"] = _t
        return hp, []

    parsed = urlparse(hp["url"])
    root = f"{parsed.scheme}://{parsed.netloc}"
    tls_host = parsed.netloc.split(":")[0]
    _ta = time.monotonic()
    coros = []
    if not hp["blocked"] and cfg.check_robots:
        coros.append(_tagged("robots", _robots(client, root, cfg, domain)))
    if not hp["blocked"] and cfg.check_sitemap:
        coros.append(_tagged("sitemap", _sitemap_at(client, root + "/sitemap.xml", cfg, domain)))
    if cfg.check_tls and parsed.scheme == "https" and tls_host:
        coros.append(_tagged("tls", _tls_version(tls_host, cfg.connect_timeout)))
    robots_sms = []
    hp["robots_status"] = "unknown"; hp["sitemap_status"] = "unknown"; hp["sitemap_url"] = ""
    hp["tls_version"] = "unknown"
    if coros:
        for name, val in await asyncio.gather(*coros):
            if val is None:
                continue
            if name == "robots":
                hp["robots_status"], robots_sms = val
            elif name == "sitemap":
                hp["sitemap_status"], hp["sitemap_url"] = val
            elif name == "tls":
                hp["tls_version"] = val
    if hp["sitemap_status"] != "present" and robots_sms:
        st2, uu = await _sitemap_at(client, robots_sms[0], cfg, domain)
        if st2 == "present":
            hp["sitemap_status"], hp["sitemap_url"] = st2, uu
    _t["ancillary"] = time.monotonic() - _ta

    _tc = time.monotonic()
    contact_pages = []
    if not hp["blocked"] and hp["html"]:
        try:
            urls = EH.discover_contact_links(hp["html"], hp["url"], max_links=cfg.max_contact_pages)
            seeds = EH.generate_seed_urls(domain, scheme=parsed.scheme or "https")
            seen, todo = {hp["url"]}, []
            for u in list(urls) + list(seeds):
                if u not in seen:
                    seen.add(u); todo.append(u)
                if len(todo) >= cfg.max_contact_pages:
                    break
            results = await asyncio.gather(
                *[_get(client, u, cfg, domain, cfg.read_timeout) for u in todo],
                return_exceptions=True)
            for r in results:
                if isinstance(r, dict) and r.get("html"):
                    contact_pages.append({"url": r["url"], "html": r["html"], "source": "contact_page"})
        except Exception:
            pass
    _t["contacts"] = time.monotonic() - _tc
    hp["_t"] = _t
    return hp, contact_pages
