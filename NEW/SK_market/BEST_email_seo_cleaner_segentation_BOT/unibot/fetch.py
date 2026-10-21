"""HTTP fetch layer on **httpx** (pure-Python, proven stable on Python 3.14).

Rewritten off aiohttp because aiohttp's compiled C extensions (multidict/yarl/
parser) crash with an access violation (0xC0000005) on bleeding-edge CPython 3.14.
httpx uses httpcore/h11 (no fragile C extensions) and is the same client the
seo_audit engine ran 143k domains on without a single native crash.

Liveness is DNS-only (getaddrinfo), bounded by a hard timeout so a single
slow-to-fail lookup can't stall a worker beyond that budget. We deliberately do
NOT open our own TCP socket to the target before the real fetch: measured on
this exact network (see project notes), a separate raw-socket TLS probe used
to run here purely to read the negotiated TLS version added a MEDIAN 3.0s /
P90 4.9s / max 8.4s per domain and shared the same thread pool as DNS -- by
far the single biggest throughput cost in the whole pipeline, for information
httpx already has. The TLS version is now read off httpx's own connection
(response.extensions["network_stream"]) at zero extra cost, zero extra
sockets, and zero extra load on the DNS pool.

One domain -> homepage (https then http) + contact pages + robots/sitemap,
fetched once. Fail-soft: a dead domain returns homepage {"ok": False}.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import socket
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import List
from urllib.parse import urlparse

import httpx

from hunter import extract as EH
from .config import Config
from .request_gate import RequestGate

# Dedicated thread pool for the BLOCKING DNS lookups (socket.getaddrinfo has no
# native timeout/cancel -- it can only be bounded from the outside via
# asyncio.wait_for on the wrapping future, see _liveness()). Kept off the event
# loop's default executor because httpx resolves DNS on that default executor
# on Windows; sharing it would let these lookups starve httpx's own connects.
# Sized well above max practical concurrency: a handful of lookups that ignore
# our wait_for timeout and keep running in the background (the OS thread itself
# cannot be killed, only abandoned) must never be able to exhaust this pool.
_PROBE_POOL = ThreadPoolExecutor(max_workers=256, thread_name_prefix="livecheck")

# How many DNS lookups may actually be IN FLIGHT at once, independent of the
# thread pool's size (256, above). The pool is sized big for hang-safety, but
# actually firing that many simultaneous getaddrinfo() calls floods the local
# resolver: confirmed in production -- 13 of 15 domains the bot marked
# "DNS dead" resolved instantly and correctly when queried again a moment
# later, in isolation, on the exact same machine/network. The failing calls
# carried WSATRY_AGAIN (Windows errno 11002 / POSIX EAI_AGAIN) -- literally
# "the DNS server didn't answer in time", a resolver-overload signal, not
# NXDOMAIN. Capping concurrent lookups keeps the resolver from being flooded
# in the first place, on top of the retry in _blocking_liveness below.
_DNS_CONCURRENCY = 40
_DNS_SEM = asyncio.Semaphore(_DNS_CONCURRENCY)

# charset_normalizer's from_bytes() is a synchronous, CPU-bound scan with no
# built-in bound -- measured fast (<1s) on every shape tried locally, but it
# runs on the shared asyncio event loop thread, so ANY domain unlucky enough
# to hit a slow case (a huge or adversarially-shaped page) would stall EVERY
# other in-flight domain for however long it takes, with zero visibility (no
# exception, no log line -- the loop is simply busy). That is indistinguishable
# from a full process freeze from the outside, which is exactly what a run was
# observed to do (17 minutes of total silence -- no HTTP requests, no throttle
# heartbeat, nothing). Rather than trust "it was fast in every test", bound it
# the same way _liveness() already bounds getaddrinfo(): off the loop, in a
# dedicated thread, with a hard wait_for timeout. A timeout just means "decode
# with whatever httpx already guessed" -- never a stall, never a crash.
_CHARSET_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="charsetdet")
_CHARSET_DETECT_TIMEOUT = 2.0
_CHARSET_SAMPLE_BYTES = 200_000   # encoding is uniform across a doc; a sample is enough

# Structural fix, not another one-off regex patch: harvest_all() is regex-only
# and was assumed "fast enough to run inline on the event loop" (see Phase 2
# below) -- true for every page tried in testing, false on at least one real
# page in production (py-spy caught the event loop pegged at 100% CPU for
# 12+ minutes inside it, TWICE, on two different regressions in the same
# regex). asyncio.wait_for's domain_hard_timeout CANNOT save this: it can
# only deliver a cancellation at an await point, and a plain synchronous call
# has none -- the whole event loop, every other in-flight domain, stays
# frozen until that one synchronous call finishes on its own, no matter what
# timeout is configured around it. Moving the call to a thread doesn't make
# the regex itself faster, but it DOES let the event loop keep getting its
# periodic GIL time-slice to service every other domain's I/O while a slow
# one runs in the background -- and wait_for around the thread's future
# actually works this time, because a Future -> thread hop IS a real await
# point. Whatever HTML shape trips up harvest_all next (there is no way to
# prove no such shape exists -- regex safety isn't provable by testing a
# finite set of adversarial inputs), it can no longer take the rest of the
# run down with it.
_HARVEST_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="harvest")
_HARVEST_TIMEOUT = 5.0


async def _harvest_bounded(html: str, domain: str, prefix: str) -> list:
    if not html:
        return []
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_HARVEST_POOL, EH.harvest_all, html, domain, prefix),
            timeout=_HARVEST_TIMEOUT)
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []


async def _is_spa_bounded(html: str) -> bool:
    if not html:
        return False
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_HARVEST_POOL, _is_spa, html),
            timeout=_HARVEST_TIMEOUT)
    except Exception:
        return False

_EAI_AGAIN_CODES = {11002}   # Windows WSATRY_AGAIN
if hasattr(errno, "EAI_AGAIN"):
    _EAI_AGAIN_CODES.add(errno.EAI_AGAIN)   # POSIX (Linux/macOS), usually -3


def _is_transient_dns_error(e: Exception) -> bool:
    """True for 'the resolver didn't answer in time / try again' -- NOT for
    a genuine NXDOMAIN (WSAHOST_NOT_FOUND / EAI_NONAME), which really does
    mean the domain doesn't exist and shouldn't be retried forever."""
    if isinstance(e, socket.gaierror) and e.args:
        return e.args[0] in _EAI_AGAIN_CODES
    return False

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
    # was its own inline r"<script[\s\S]*?</script>" (DOTALL-equivalent,
    # non-greedy) -- the exact same catastrophic-backtracking shape confirmed
    # (via a live py-spy dump) to freeze the entire event loop for 12+ minutes
    # in hunter/extract.py's near-identical pattern. Shares that module's
    # linear-time fix instead of carrying its own vulnerable near-copy.
    txt = EH.strip_script_style(low)
    return len(txt.split()) < 120 and low.count("<script") >= 3


# ---------------------------------------------------------------------------
# Liveness: DNS resolve ONLY (no target socket). httpx does TCP + TLS.
# ---------------------------------------------------------------------------
def _blocking_liveness(domain: str):
    """Returns (alive, host, port, reason). port=443 => homepage tries https then http.
    DNS-only on purpose (see module docstring): the raw TCP probe caused ConnectError
    storms under concurrency. A DNS-resolving host that refuses TCP surfaces as an
    httpx ConnectError during the homepage fetch and is retried/settled normally.

    Retries a transient resolver failure (WSATRY_AGAIN/EAI_AGAIN -- "didn't
    answer in time", not "doesn't exist") a couple of times with a short
    backoff before giving up on THIS host and trying the www. variant. A
    genuine NXDOMAIN is not retried -- that would just slow the whole run
    down for no benefit."""
    bare = domain[4:] if domain.startswith("www.") else domain
    saw_transient = False
    for host in (bare, "www." + bare):
        for attempt in range(3):
            try:
                socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                return True, host, 443, ""
            except Exception as e:
                if _is_transient_dns_error(e):
                    saw_transient = True
                    if attempt < 2:
                        time.sleep(0.2 * (attempt + 1))
                        continue
                break   # permanent failure (or transient retries exhausted) -- try next host
    # "dns_transient" IS eligible for a real requeue+retry at the pipeline level
    # (see pipeline._worker); a plain "dns" NXDOMAIN is treated as settled-dead.
    return False, "", 0, ("dns_transient" if saw_transient else "dns")


async def _liveness(domain: str, cfg: Config):
    """DNS-resolve with a hard wall-clock bound. socket.getaddrinfo() has no
    timeout/cancel of its own -- once started in the thread pool it runs to
    completion no matter what we do here. asyncio.wait_for() only bounds how
    long THIS coroutine waits for it; on timeout we treat the domain as
    unresolved-for-now and move on immediately instead of stalling the whole
    worker on one slow resolver response. The pool is sized (256 workers, see
    module docstring) so that any lookups which do run long in the background
    can't starve the domains queued behind them. _DNS_SEM caps how many are
    actually fired at once (see its own docstring) -- the thread pool's size
    is just hang-safety headroom, not the intended steady-state concurrency."""
    loop = asyncio.get_event_loop()
    async with _DNS_SEM:
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_PROBE_POOL, _blocking_liveness, domain),
                timeout=cfg.tcp_probe_timeout)
        except asyncio.TimeoutError:
            return False, "", 0, "dns_timeout"


def _extract_tls_version(r: httpx.Response) -> str:
    """Read the TLS version straight off httpx's own connection -- no extra
    socket, no extra handshake, no extra load on anything. Falls back to
    'unknown' for plain-http responses or transports that don't expose it."""
    try:
        ns = r.extensions.get("network_stream")
        if ns is None:
            return "unknown"
        ssl_obj = ns.get_extra_info("ssl_object")
        if ssl_obj is None:
            return "unknown"
        return ssl_obj.version() or "unknown"
    except Exception:
        return "unknown"


def _dead_homepage(dns_dead: bool = False, dead_reason: str = "") -> dict:
    return {"ok": False, "url": "", "scheme": "", "status": 0, "headers": {}, "html": "",
            "blocked": False, "challenged": False, "spa_shell": False, "html_size_bytes": 0,
            "robots_status": "unknown", "sitemap_status": "unknown", "sitemap_url": "",
            "tls_version": "unknown", "http_version": "", "ttfb_s": 0.0, "load_time_s": 0.0,
            "dns_dead": dns_dead, "dead_reason": dead_reason}


def _detect_encoding_blocking(sample: bytes) -> "str | None":
    from charset_normalizer import from_bytes
    best = from_bytes(sample).best()
    return best.encoding if best is not None else None


async def _decode_html(r: httpx.Response) -> str:
    """httpx (since dropping chardet/charset_normalizer as a hard dependency)
    force-decodes as UTF-8 whenever a response doesn't declare a charset in its
    Content-Type header -- it does NOT sniff the body even if a detection
    library is installed; that has to be wired in by the caller. Plenty of
    older/smaller SK/CZ sites (exactly this bot's target list) serve
    cp1250/iso-8859-2 without declaring it, so every accented byte became a
    replacement character -- confirmed on a live sample, corrupting real
    emails (e.g. an 'objednavky@' mailbox) and occasionally splitting one
    address into a bogus extra "email" at the corruption point (the email
    regex's local-part charset doesn't include the mangled byte, so it starts
    a fresh match right after it). Only pay for charset_normalizer's detection
    when httpx's decode actually produced replacement characters -- the common
    case (a page that correctly declares/uses UTF-8) is unaffected.

    Detection runs off the event loop with a hard timeout (see _CHARSET_POOL's
    docstring) on a bounded sample, not the whole page -- a stall here would
    freeze every other in-flight domain, which is exactly the failure shape a
    real run exhibited (17 minutes of total silence)."""
    html = r.text or ""
    if "�" not in html:
        return html
    try:
        loop = asyncio.get_event_loop()
        sample = r.content[:_CHARSET_SAMPLE_BYTES]
        encoding = await asyncio.wait_for(
            loop.run_in_executor(_CHARSET_POOL, _detect_encoding_blocking, sample),
            timeout=_CHARSET_DETECT_TIMEOUT)
        if encoding:
            return r.content.decode(encoding, errors="replace")
    except Exception:
        pass
    return html


async def _get(client: httpx.AsyncClient, url: str, cfg: Config, domain: str, timeout: float,
               request_gate: RequestGate | None = None, priority: str = "homepage"):
    try:
        start = time.monotonic()
        if request_gate is None:
            gate_wait_s = 0.0
            wire_started = time.monotonic()
            r = await client.get(url, headers=_headers(cfg, domain),
                                 timeout=httpx.Timeout(timeout, connect=cfg.connect_timeout))
        else:
            # The domain gate limits domains, not their contact/SEO subrequests.
            # Hold this shared slot only for actual network I/O so every request
            # type contributes to one hard, machine-safe socket budget.
            async with request_gate.slot(priority) as gate_wait_s:
                wire_started = time.monotonic()
                r = await client.get(url, headers=_headers(cfg, domain),
                                     timeout=httpx.Timeout(timeout, connect=cfg.connect_timeout))
        wire_s = time.monotonic() - wire_started
        html = await _decode_html(r)
        el = time.monotonic() - start
        scheme = urlparse(str(r.url)).scheme
        return {"ok": True, "url": str(r.url), "status": r.status_code,
                "headers": {k.lower(): v for k, v in r.headers.items()},
                "html": html[: cfg.max_html_bytes], "load_time_s": el, "ttfb_s": el,
                "gate_wait_s": gate_wait_s, "wire_time_s": wire_s,
                "http_version": r.http_version or "",
                "tls_version": _extract_tls_version(r) if scheme == "https" else "unknown",
                "scheme": scheme}
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


async def _probe(client, url, cfg, domain, request_gate=None, priority="audit"):
    r = await _get(client, url, cfg, domain, cfg.probe_timeout, request_gate, priority)
    if r is None:
        return "unknown", ""
    if r["status"] == 200:
        return "present", r
    if r["status"] in (404, 410):
        return "absent", ""
    return "unknown", ""


async def _robots(client, root, cfg, domain, request_gate=None):
    st, r = await _probe(client, root + "/robots.txt", cfg, domain, request_gate, "audit")
    sms = []
    if isinstance(r, dict):
        for line in (r["html"] or "")[:20000].splitlines():
            if line.lower().startswith("sitemap:"):
                u = line.split(":", 1)[1].strip()
                if u.startswith("http"):
                    sms.append(u)
    return st, sms


async def _sitemap_at(client, url, cfg, domain, request_gate=None):
    r = await _get(client, url, cfg, domain, cfg.probe_timeout, request_gate, "audit")
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


async def fetch_domain(client: httpx.AsyncClient, domain: str, cfg: Config,
                       request_gate: RequestGate | None = None):
    """Return (homepage_dict, [contact_page_dicts]). homepage['ok']==False if dead."""
    _t = {"dns": 0.0, "homepage": 0.0, "ancillary": 0.0, "contacts": 0.0,
          "homepage_gate_wait": 0.0, "homepage_wire": 0.0,
          "contacts_gate_wait": 0.0, "contacts_wire": 0.0}
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
        r = await _get(client, url, cfg, domain, cfg.read_timeout, request_gate, "homepage")
        if r is None:
            if time.monotonic() - _cs >= cfg.connect_timeout:
                break            # hung -> the other scheme will hang too; stop
            continue
        body = r["html"]
        r["challenged"] = _is_challenge(r["status"], body)
        r["blocked"] = r["status"] in (403, 429) or r["challenged"]
        r["spa_shell"] = (not r["blocked"]) and await _is_spa_bounded(body)
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
    _t["homepage_gate_wait"] = hp.get("gate_wait_s", 0.0)
    _t["homepage_wire"] = hp.get("wire_time_s", 0.0)

    parsed = urlparse(hp["url"])
    root = f"{parsed.scheme}://{parsed.netloc}"
    hp["robots_status"] = "unknown"; hp["sitemap_status"] = "unknown"; hp["sitemap_url"] = ""
    hp.setdefault("tls_version", "unknown")   # already set by _get() off the real connection

    # --- Phase 1: confidence-first contact crawl ------------------------------
    # A strong mailbox already visible on the homepage is sufficient evidence;
    # downloading /contact, /kontakt and /kontakty after that only adds latency
    # and traffic. Weak/off-domain/plain-text candidates deliberately do NOT
    # stop discovery: they still get the same fallback coverage as before.
    #
    # Real contact links are tried before generated seed paths. Seed paths are
    # a useful fallback for sites with no contact link, not three requests that
    # should race a known-good /kontakt URL.
    _tc = time.monotonic()
    contact_pages: List[dict] = []
    has_lead = False

    async def _email_evidence(html: str, prefix: str) -> tuple[bool, bool]:
        """Return (relevant, strong) without letting extraction stall the loop."""
        if not html:
            return False, False
        raw = await _harvest_bounded(html, domain, prefix)
        if raw is None:
            return True, False       # fail open: never skip SEO on probe failure
        try:
            relevant = bool(EH.finalize(raw, domain, cap=1, min_score=cfg.email_min_score))
            strong = bool(EH.finalize(raw, domain, cap=1, min_score=cfg.early_email_score))
            return relevant, strong
        except Exception:
            return True, False

    discovered: List[str] = []
    seeds: List[str] = []
    if not hp["blocked"] and hp["html"]:
        try:
            relevant, strong = await _email_evidence(hp["html"], "homepage_")
            has_lead = relevant
            if not strong:
                seen = {hp["url"]}
                for u in EH.discover_contact_links(hp["html"], hp["url"], max_links=cfg.max_contact_pages):
                    if u not in seen:
                        seen.add(u); discovered.append(u)
                for u in EH.generate_seed_urls(domain, scheme=parsed.scheme or "https"):
                    if u not in seen:
                        seen.add(u); seeds.append(u)
        except Exception:
            has_lead = True           # fail open: retain prior coverage on an unexpected parser error

    # Small stagger instead of firing every sub-request at this one host in the
    # same instant -- a handful of pages a fraction of a second apart reads as
    # normal browsing to a WAF, a dead-simultaneous burst reads as a scanner.
    async def _delayed_get(u: str, delay: float, priority: str):
        if delay:
            await asyncio.sleep(delay)
        return await _get(client, u, cfg, domain, cfg.read_timeout, request_gate, priority)

    async def _fetch_contact(u: str, delay: float = 0.0,
                             priority: str = "fallback") -> tuple[dict | None, bool, bool]:
        r = await _delayed_get(u, delay, priority)
        if not isinstance(r, dict) or not r.get("html"):
            return None, False, False
        page = {"url": r["url"], "html": r["html"], "source": "contact_page",
                "gate_wait_s": r.get("gate_wait_s", 0.0),
                "wire_time_s": r.get("wire_time_s", 0.0)}
        relevant, strong = await _email_evidence(page["html"], "contact_page_")
        return page, relevant, strong

    # One real link is the highest-value request.  A strong result ends here.
    if discovered:
        page, relevant, strong = await _fetch_contact(discovered.pop(0), priority="primary")
        if page:
            contact_pages.append(page)
        has_lead = has_lead or relevant
        if strong:
            discovered.clear(); seeds.clear()

    # If the primary page was inconclusive, retain coverage with a bounded
    # parallel fallback wave. Generated paths only enter this wave after every
    # discovered link has had priority.
    remaining = (discovered + seeds)[:max(0, cfg.max_contact_pages - len(contact_pages))]
    if remaining:
        results = await asyncio.gather(
            *[_fetch_contact(u, i * cfg.per_host_min_interval) for i, u in enumerate(remaining)],
            return_exceptions=True)
        for result in results:
            if isinstance(result, tuple):
                page, relevant, _strong = result
                if page:
                    contact_pages.append(page)
                has_lead = has_lead or relevant
    _t["contacts_gate_wait"] = sum(p.get("gate_wait_s", 0.0) for p in contact_pages)
    _t["contacts_wire"] = sum(p.get("wire_time_s", 0.0) for p in contact_pages)
    _t["contacts"] = time.monotonic() - _tc

    # --- Phase 2: robots/sitemap -- ONLY for a relevant lead ------------------
    _ta = time.monotonic()
    want_audit = has_lead or not cfg.analyze_only_alive_with_email
    robots_sms: List[str] = []
    coros = []
    if want_audit and not hp["blocked"] and cfg.check_robots:
        coros.append(_tagged("robots", _robots(client, root, cfg, domain, request_gate)))
    if want_audit and not hp["blocked"] and cfg.check_sitemap:
        coros.append(_tagged("sitemap", _sitemap_at(client, root + "/sitemap.xml", cfg, domain, request_gate)))
    if coros:
        for name, val in await asyncio.gather(*coros):
            if val is None:
                continue
            if name == "robots":
                hp["robots_status"], robots_sms = val
            elif name == "sitemap":
                hp["sitemap_status"], hp["sitemap_url"] = val
        if hp["sitemap_status"] != "present" and robots_sms:
            st2, uu = await _sitemap_at(client, robots_sms[0], cfg, domain, request_gate)
            if st2 == "present":
                hp["sitemap_status"], hp["sitemap_url"] = st2, uu
    _t["ancillary"] = time.monotonic() - _ta
    hp["_t"] = _t
    return hp, contact_pages
