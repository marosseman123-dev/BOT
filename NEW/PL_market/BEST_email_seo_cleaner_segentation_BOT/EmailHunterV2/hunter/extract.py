"""Pure email extraction + scoring, plus the small crawl-helper functions the
fetch layer uses to find contact/about pages. No I/O in this module -- it only
parses HTML strings handed to it.

Extraction methods (roughly worst->best evidence order used in scoring):
    plain_text        -- bare address sitting in the rendered text
    obfuscated_text    -- "name (at) domain (dot) sk" style de-obfuscated text
    cf_decoded         -- Cloudflare's data-cfemail= obfuscation, decoded
    hcard              -- class="email" microformat
    jsonld             -- an "email" field inside a JSON-LD <script> block
    mailto             -- an actual mailto: link (strongest signal: a real CTA)
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple
from urllib.parse import unquote, urljoin, urlparse

_MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?&]+)', re.I)
# Local part includes Latin-1 Supplement + Latin Extended-A (À-ſ):
# covers every accented letter used in Slovak/Czech (a e i o u y c d l n r s t z
# with diacritics). Plain ASCII-only used to mean a genuine "objednavky@..."
# mailbox with the accented letter mid-word would fail to match as ONE email --
# the regex engine restarts right after the byte it can't include, producing a
# second, truncated, bogus "candidate" (e.g. "vky@domain" out of "objednavky@
# domain") that then got reported as if it were a real address. Confirmed on a
# live sample. This is a defense-in-depth alongside fixing the actual charset
# decoding (see fetch.py's _decode_html) -- it also protects plain_text/
# obfuscated_text extraction even if some other page arrives mis-decoded.
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-À-ſ]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}')
_CF_EMAIL_RE = re.compile(r'data-cfemail=["\']([a-f0-9]+)["\']', re.I)
_HCARD_RE = re.compile(
    r'class=["\'][^"\']*\bemail\b[^"\']*["\'][^>]*>\s*([A-Za-z0-9._%+\-À-ſ]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24})',
    re.I)
_TAG_ONLY_RE = re.compile(r"<[^>]+>")
# CAUTION: every quantifier below is BOUNDED ({0,2}/{1,3}). An earlier
# version used \s*[\[(]?\s*at\s*[\])]?\s* -- four separate \s* groups that
# can all draw from the same run of whitespace when the optional bracket is
# absent -- catastrophic backtracking, measured at 80+ SECONDS on one real
# 190KB page, because "at"/"dot" occur as bare substrings inside completely
# ordinary words (kontakt, chat, dotknut, ...) many thousands of times over a
# real page. That was "fixed" by bounding two of the three alternatives but
# leaving \s+at\s+ / \s+dot\s+ with a genuinely UNBOUNDED \s+ on each side --
# still safe for any ONE match attempt, but re.sub()/finditer() retries the
# pattern at EVERY position across a run of consecutive whitespace (e.g. many
# adjacent tags each collapsed to one space by strip_script_style -- common
# on modern icon-heavy sites), and each retry re-scans the remaining run
# looking for "at"/"dot" -- O(run_length) work at each of O(run_length)
# starting positions, i.e. the exact same O(whitespace_run^2) shape, just
# with the trigger moved from "at"/"dot"-as-substring to "long whitespace
# run" instead. Confirmed hanging a live crawl (o2.sk) for minutes at a time.
# {1,3} still matches any realistic human-typed "name at domain" (normally
# exactly one space, never more than a couple), so real obfuscated addresses
# are unaffected, but no alternative anywhere in this pattern can ever
# backtrack more than a small constant number of characters -- provably
# O(n) total regardless of input shape, not just "safe in every case tried".
_AT_RE = re.compile(r"\s{0,2}\[at\]\s{0,2}|\s{0,2}\(at\)\s{0,2}|\s{1,3}at\s{1,3}", re.I)
_DOT_RE = re.compile(r"\s{0,2}\[dot\]\s{0,2}|\s{0,2}\(dot\)\s{0,2}|\s{1,3}dot\s{1,3}", re.I)

_METHOD_BASE = {"mailto": 90, "jsonld": 85, "hcard": 82, "cf_decoded": 80,
                "obfuscated_text": 65, "plain_text": 55}

_FREEMAIL = {
    "gmail.com", "seznam.cz", "centrum.sk", "centrum.cz", "azet.sk", "post.sk",
    "atlas.sk", "zoznam.sk", "volny.cz", "email.cz", "post.cz", "outlook.com",
    "hotmail.com", "yahoo.com", "icloud.com", "live.com", "msn.com", "pobox.sk",
}

_BAD_LOCAL = {"noreply", "no-reply", "donotreply", "example", "test", "webmaster",
              "postmaster", "abuse", "mailer-daemon",
              # "server@domain" is a hosting/cPanel-generated catch-all/status address,
              # not a human contact -- confirmed on a live 400-domain sample: this exact
              # pattern appeared repeatedly (server@0.sk, server@1000koni.sk, ...), always
              # scoring as if it were a real lead. root/hostmaster are standard
              # sysadmin-only mailbox conventions, never a real business contact.
              "server", "root", "hostmaster"}
_BAD_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
# Website-template placeholder addresses left un-filled by whoever built the
# site -- these are not anyone's real address. Confirmed on the same sample:
# "info@companyname.com" was harvested and scored as a normal lead.
_BAD_HOST_TOKENS = ("companyname", "yourcompany", "yourdomain", "yoursite",
                    "example.com", "example.org", "example.net", "domain.com",
                    "testdomain", "sampledomain", "mycompany", "sitename")

_CONTACT_KEYWORDS = ("kontakt", "contact", "o-nas", "o-nás", "about", "tim", "team",
                     "impressum", "imprint", "sluzby", "o-firme", "o-spolocnosti")
_SEED_PATHS = ("kontakt", "kontakty", "contact", "contact-us", "o-nas", "about", "about-us")


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _clean(raw: str) -> str:
    e = unquote((raw or "").strip().strip("<>\"'.,;: \t\n"))
    e = e.split("?")[0]
    if "@" not in e:
        return ""
    local, _, host = e.partition("@")
    local, host = local.strip().lower(), host.strip().lower()
    if not local or not host or "." not in host:
        return ""
    if local in _BAD_LOCAL:
        return ""
    if any(host.endswith(ext) for ext in _BAD_EXT):
        return ""
    if any(tok in host for tok in _BAD_HOST_TOKENS):
        return ""
    if len(e) > 254:
        return ""
    return f"{local}@{host}"


def _cf_decode(hexstr: str) -> str:
    raw = bytes.fromhex(hexstr)
    if not raw:
        return ""
    key = raw[0]
    return "".join(chr(b ^ key) for b in raw[1:])


def _find_emails_in_json(node) -> List[str]:
    out: List[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out.extend(_find_emails_in_json(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_find_emails_in_json(v))
    elif isinstance(node, str):
        out.extend(_EMAIL_RE.findall(node))
    return out


def _jsonld_blocks(html: str) -> List[str]:
    """Extract the text content of every JSON-LD <script> block, linear time.

    First attempt here used a regex for the opening tag
    (r'<script[^>]+type=...["\'][^>]*>') on the assumption that `[^>]*` can't
    cross a tag boundary -- true ONLY when a '>' is actually nearby. On a
    malformed/truncated page with no '>' for a long stretch, `[^>]+` greedily
    consumes toward the next '>' wherever that is, then backtracks character
    by character when the required 'type=...' doesn't match there -- the
    exact same catastrophic-backtracking shape as the <script>...</script>
    bug this module already fixed once (see strip_script_style), just
    recreated in a new regex. Caught by this module's own adversarial
    perf-guard test (14.7s on one page) before it could repeat that hang in
    production. Fixed the same way: no regex crosses tag boundaries here at
    all, only bounded str.find scans."""
    low = html.lower()
    out: List[str] = []
    i, n = 0, len(html)
    while i < n:
        start = low.find("<script", i)
        if start == -1:
            break
        tag_end = low.find(">", start)
        if tag_end == -1:
            break   # unterminated opening tag -- nothing usable after it
        attrs = low[start:tag_end]
        i = tag_end + 1
        if "application/ld+json" not in attrs:
            continue
        close = low.find("</script", i)
        if close == -1:
            break
        out.append(html[tag_end + 1:close])
        i = close
    return out


def strip_script_style(html: str) -> str:
    """Remove <script>/<style> blocks and every remaining tag, in guaranteed
    linear time -- no backtracking regex. Public (no leading underscore):
    fetch.py's _is_spa() imports this directly -- it had its own inline copy
    of the exact same vulnerable pattern this replaced, confirmed independently
    exploitable. One shared, tested, linear implementation instead of N
    private near-copies that can each individually regress.

    The regex this replaced -- r"<(script|style)[^>]*>.*?</\\1>|<[^>]+>" with
    DOTALL -- looks bounded (a non-greedy .*?), but non-greedy + DOTALL still
    backtracks character-by-character searching for the closing tag: if
    '</script>' (or '</style>') is missing or rare relative to how often the
    literal substring '<script'/'<style' appears (a large minified/obfuscated
    JS bundle, Base64 blobs, or just malformed/truncated HTML), every such
    trigger point re-scans forward toward EOF looking for a close that may
    never come, turning O(n) into effectively O(n * k). Confirmed live: a
    py-spy dump of a real crawl caught the main thread pegged at 100% CPU for
    12+ minutes, stuck inside exactly this substitution via
    _deobfuscate_text() on one page -- functionally an infinite hang, taking
    the entire event loop (every other in-flight domain) down with it. A
    single manual left-to-right scan (str.find, never backtracks) is
    unconditionally O(n) regardless of what the page contains."""
    low = html.lower()
    out = []
    i, n = 0, len(html)
    while i < n:
        j = low.find("<script", i)
        k = low.find("<style", i)
        if j == -1 and k == -1:
            out.append(html[i:])
            break
        if j != -1 and (k == -1 or j < k):
            tag_start, tag_name = j, "script"
        else:
            tag_start, tag_name = k, "style"
        out.append(html[i:tag_start])
        close = low.find("</" + tag_name, tag_start)
        if close == -1:
            i = n   # unterminated block -- nothing usable after it anyway
            break
        end = low.find(">", close)
        i = (end + 1) if end != -1 else n
    return _TAG_ONLY_RE.sub(" ", "".join(out))


def _deobfuscate_text(html: str) -> str:
    text = strip_script_style(html)
    text = _AT_RE.sub("@", text)
    text = _DOT_RE.sub(".", text)
    return text


# Footer/credit-line phrases ("Built by X", "webdesign: X") -- the actual
# pattern that made an unrelated third party's address look like a lead for
# the crawled domain (see _score's is_credit note). Checked against a small
# text window around where the address was found, not the whole page: most
# real off-domain contacts (e.g. a group company's shared support mailbox)
# have no such phrase anywhere near them.
_CREDIT_KEYWORDS = (
    "webdesign", "web design", "tvorba web", "tvorbu web", "tvorba stranok",
    "tvorbu stranok", "vytvoril", "vytvorila", "dodavatel webu", "dodavatelom webu",
    "created by", "designed by", "design by", "made by", "powered by", "web by",
    "developed by", "grafika a web", "webstranky vytvoril",
)
_CONTEXT_WINDOW = 70


def _has_credit_context(text: str, pos: int) -> bool:
    lo, hi = max(0, pos - _CONTEXT_WINDOW), pos + _CONTEXT_WINDOW
    window = text[lo:hi].lower()
    return any(kw in window for kw in _CREDIT_KEYWORDS)


def harvest_all(html: str, domain: str, prefix: str = "") -> List[Dict]:
    """Return raw candidates: [{'email','method','source','credit'}, ...].
    'credit' flags an address found right next to a "built by <agency>"-style
    footer phrase -- finalize()/score() penalize those much harder, since
    that's someone else's contact, not the crawled site's. No other
    scoring/dedup here -- that happens in finalize() once all pages combine."""
    if not html:
        return []
    candidates: List[Dict] = []
    seen: set = set()

    def push(raw: str, method: str, credit: bool = False) -> None:
        e = _clean(raw)
        if not e:
            return
        key = (e, method)
        if key in seen:
            return
        seen.add(key)
        candidates.append({"email": e, "method": method, "source": f"{prefix}{method}", "credit": credit})

    for m in _MAILTO_RE.finditer(html):
        push(m.group(1), "mailto", _has_credit_context(html, m.start()))

    for hexstr in _CF_EMAIL_RE.findall(html):
        try:
            push(_cf_decode(hexstr), "cf_decoded")
        except Exception:
            pass

    for block in _jsonld_blocks(html):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for e in _find_emails_in_json(data):
            push(e, "jsonld")

    for m in _HCARD_RE.finditer(html):
        push(m.group(1), "hcard", _has_credit_context(html, m.start()))

    deobf = _deobfuscate_text(html)
    for m in _EMAIL_RE.finditer(deobf):
        push(m.group(0), "obfuscated_text", _has_credit_context(deobf, m.start()))

    text_only = strip_script_style(html)
    for m in _EMAIL_RE.finditer(text_only):
        push(m.group(0), "plain_text", _has_credit_context(text_only, m.start()))

    return candidates


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _registrable(host: str) -> str:
    h = (host or "").lower().strip()
    return h[4:] if h.startswith("www.") else h


def _domain_match(email_host: str, domain: str) -> bool:
    h, d = _registrable(email_host), _registrable(domain)
    if not h or not d:
        return False
    return h == d or h.endswith("." + d) or d.endswith("." + h)


def _score(email: str, method: str, domain: str, credit: bool = False) -> int:
    base = _METHOD_BASE.get(method, 50)
    host = email.rsplit("@", 1)[-1]
    if _domain_match(host, domain):
        base = min(99, base + 8)
    elif host in _FREEMAIL and not credit:
        pass
    elif credit:
        # Found right next to a "Built by <agency>" / "webdesign:" footer
        # phrase (see _has_credit_context) -- that is deliberately someone
        # ELSE's contact, printed on the page on purpose, so it must be
        # excluded regardless of method or freemail status. Confirmed this is
        # exactly what users flagged as "irrelevant": an email visibly present
        # on the page, but not actually belonging to the business being
        # crawled. Pushes every method below the default min_score=50.
        base = max(1, base - 60)
    else:
        # Off-domain, non-freemail, and NOT next to a credit-line phrase: most
        # often this is a real contact on a different registered domain --
        # a group/holding company's shared mailbox, a distributor's regional
        # address (confirmed on a live sample: a Slovak reseller's own site
        # listing "slovakia@<parent-company>.com" as ITS real, correct
        # contact) -- not noise. A moderate penalty still lets strong-evidence
        # methods (an actual mailto: link, a hcard, structured JSON-LD) pass
        # the default threshold, while weak-evidence bare mentions in running
        # text (plain_text/obfuscated_text) are still filtered out, since an
        # incidental unrelated address is far more likely to just be MENTIONED
        # in text than to be someone's actual clickable mailto: contact.
        base = max(1, base - 25)
    return base


def finalize(candidates: List[Dict], domain: str, cap: int = 10,
             min_score: int = 50) -> List[Tuple[str, int, str]]:
    """Score, dedupe (best-method wins per address), sort desc, filter by
    min_score, cap. Returns [(email, score, source), ...]."""
    best: Dict[str, Tuple[str, int, str]] = {}
    for c in candidates:
        email, method, source = c["email"], c["method"], c["source"]
        sc = _score(email, method, domain, c.get("credit", False))
        cur = best.get(email)
        if cur is None or sc > cur[1]:
            best[email] = (email, sc, source)
    ranked = sorted(best.values(), key=lambda t: t[1], reverse=True)
    ranked = [t for t in ranked if t[1] >= min_score]
    return ranked[:cap]


# ---------------------------------------------------------------------------
# contact/about page discovery (used by the crawler, not by enrich)
# ---------------------------------------------------------------------------

def discover_contact_links(html: str, base_url: str, max_links: int = 3) -> List[str]:
    if not html or not base_url:
        return []
    base_netloc = urlparse(base_url).netloc.lower()
    out: List[str] = []
    seen: set = set()
    for href, text in re.findall(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
                                  html, re.I | re.S):
        hay = (href + " " + re.sub(r"<[^>]+>", " ", text)).lower()
        if not any(k in hay for k in _CONTACT_KEYWORDS):
            continue
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href.strip())
        p = urlparse(abs_url)
        if p.netloc.lower() != base_netloc:
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
        if len(out) >= max_links:
            break
    return out


def generate_seed_urls(domain: str, scheme: str = "https") -> List[str]:
    d = domain.strip("/")
    return [f"{scheme}://{d}/{p}" for p in _SEED_PATHS]
