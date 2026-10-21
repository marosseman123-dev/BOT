"""Offline self-check for the hunter package (no network).

    python -m hunter.selfcheck

Covers extraction correctness for every method plus a hard performance
regression guard: _AT_RE/_DOT_RE previously used multiple adjacent \\s*
groups (\\s*[\\[(]?\\s*at\\s*[\\])]?\\s*) which is catastrophic-backtracking
regex -- measured at 80+ seconds on a single real 190KB page, because "at"
and "dot" occur as bare substrings inside completely ordinary words
(kontakt, chat, dotknut, ...) many thousands of times over real text. The
perf_guard test below reproduces that shape of input (many word-like tokens
containing "at"/"dot" as substrings, plus real whitespace runs) at a larger
size and asserts it finishes in well under a second, so a regression is
caught immediately instead of silently reintroducing a multi-minute stall
per domain in production.
"""
from __future__ import annotations

import time

from . import extract as EH


def _check_mailto():
    html = '<a href="mailto:info@firma.sk">napiste</a>'
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "mailto" for c in cands), cands


def _check_cf_decode():
    # "info@firma.sk" Cloudflare-obfuscated with key 0x1a
    key = 0x1A
    raw = "info@firma.sk".encode()
    hexstr = bytes([key]).hex() + bytes(b ^ key for b in raw).hex()
    html = f'<a data-cfemail="{hexstr}">[email protected]</a>'
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "cf_decoded" for c in cands), cands


def _check_jsonld():
    html = ('<script type="application/ld+json">'
            '{"@type":"Organization","email":"info@firma.sk"}</script>')
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "jsonld" for c in cands), cands


def _check_hcard():
    html = '<span class="vcard email">info@firma.sk</span>'
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "hcard" for c in cands), cands


def _check_obfuscated_text():
    html = "<p>Kontaktujte nas: info (at) firma (dot) sk</p>"
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "obfuscated_text" for c in cands), cands
    html2 = "<p>info[at]firma[dot]sk</p>"
    cands2 = EH.harvest_all(html2, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" for c in cands2), cands2


def _check_accented_local_part_not_split():
    """Regression for a live-sample bug: an ASCII-only local-part charset made
    'objednavky@firma.sk' (accented letter mid-word) fail to match as ONE
    email -- the regex restarted right after the accented byte, producing a
    bogus truncated second "email" like 'vky@firma.sk'. Must extract exactly
    one, correct, full address."""
    html = "<p>Napiste na objednávky@firma.sk, tesime sa.</p>"
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    emails = {c["email"] for c in cands}
    assert "objednávky@firma.sk" in emails, emails
    assert "vky@firma.sk" not in emails, emails


def _check_plain_text():
    html = "<p>Napiste nam na info@firma.sk kedykolvek.</p>"
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    assert any(c["email"] == "info@firma.sk" and c["method"] == "plain_text" for c in cands), cands


def _check_junk_filtered():
    """Regression for a live-sample finding: hosting-generated role mailboxes
    and website-template placeholder addresses were being harvested and
    scored as if they were real leads (server@0.sk, info@companyname.com)."""
    html = ('<a href="mailto:server@firma.sk">srv</a>'
            '<a href="mailto:info@companyname.com">tpl</a>'
            '<a href="mailto:root@firma.sk">r</a>'
            '<a href="mailto:info@firma.sk">real</a>')
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    emails = {c["email"] for c in cands}
    assert "server@firma.sk" not in emails, emails
    assert "info@companyname.com" not in emails, emails
    assert "root@firma.sk" not in emails, emails
    assert "info@firma.sk" in emails, emails


def _check_finalize_scoring_and_domain_match():
    cands = [{"email": "info@firma.sk", "method": "mailto", "source": "homepage_mailto"},
             {"email": "random@unrelated.com", "method": "plain_text", "source": "homepage_plain_text"}]
    out = EH.finalize(cands, "firma.sk", cap=10, min_score=50)
    emails = [e for e, _s, _src in out]
    assert "info@firma.sk" in emails, out
    # own-domain mailto must outrank an unrelated-domain plain-text hit
    assert out[0][0] == "info@firma.sk", out


def _check_creditline_email_filtered():
    """Regression for the actual user-reported complaint: a 'Vytvorila
    webdesign agentura: x@agentura.sk' footer credit line was harvested and
    scored as if it were a lead for the crawled domain -- that's deliberately
    someone ELSE's contact, printed on the page on purpose. Must be excluded
    regardless of method, while the real mailto: contact for THIS domain
    (and a plain gmail.com owner address) still comes through."""
    html = ('<a href="mailto:info@firma.sk">Kontakt</a>'
            '<footer>Vytvorila webdesign agentura '
            '<a href="mailto:jana@agentura.sk">jana@agentura.sk</a></footer>')
    cands = EH.harvest_all(html, "firma.sk", "homepage_")
    out = EH.finalize(cands, "firma.sk", cap=10, min_score=50)
    emails = {e for e, _s, _src in out}
    assert "jana@agentura.sk" not in emails, out
    assert "info@firma.sk" in emails, out


def _check_offdomain_legit_contact_not_overpenalized():
    """Regression: a genuinely off-domain but REAL contact (a group/holding
    company's shared mailbox, a distributor's own regional address) must NOT
    be wholesale excluded just for living on a different registered domain --
    confirmed on a live sample where a Slovak reseller's own site listed
    'slovakia@<parent-company>.com' as its actual, correct, clickable
    mailto: contact, with no 'built by/webdesign' phrase anywhere nearby. A
    strong-evidence method (mailto) for that case must still clear the
    default threshold; a bare incidental MENTION of some unrelated address in
    running text (weak evidence) should not."""
    html = ('<footer><p>Elbacom GmbH</p>'
            '<a href="mailto:slovakia@elbacom.com">slovakia@elbacom.com</a></footer>')
    cands = EH.harvest_all(html, "11iot.sk", "homepage_")
    out = EH.finalize(cands, "11iot.sk", cap=10, min_score=50)
    emails = {e for e, _s, _src in out}
    assert "slovakia@elbacom.com" in emails, out

    html2 = "<p>Viac informacii najdete aj na inywebe.sk alebo napiste info@inywebe.sk.</p>"
    cands2 = EH.harvest_all(html2, "firma.sk", "homepage_")
    out2 = EH.finalize(cands2, "firma.sk", cap=10, min_score=50)
    assert not any(e == "info@inywebe.sk" for e, _s, _src in out2), out2


def _check_contact_discovery():
    html = '<a href="/kontakt">Kontakt</a><a href="https://other.sk/x">iny web</a>'
    links = EH.discover_contact_links(html, "https://firma.sk/", max_links=3)
    assert any("firma.sk/kontakt" in u for u in links), links
    assert all("other.sk" not in u for u in links), links   # cross-domain link must be excluded

    seeds = EH.generate_seed_urls("firma.sk", scheme="https")
    assert all(u.startswith("https://firma.sk/") for u in seeds), seeds


def _perf_guard():
    """Reproduces the SHAPE of input that made the old regex take 80s+: lots
    of ordinary words containing 'at'/'dot' as substrings, interspersed with
    real whitespace runs, at a size larger than the real page that triggered
    the original bug (190KB) -- must stay well under a second."""
    word = "kontaktujte chat dotknut statny matka natalia formatovanie "
    html = "<p>" + word * 4000 + "</p>"   # ~250KB, ~24000 "at"/"dot" substring occurrences
    t0 = time.perf_counter()
    EH.harvest_all(html, "firma.sk", "homepage_")
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"harvest_all took {dt:.2f}s on adversarial-shaped input -- regex regression?"


def _perf_guard_script_tags():
    """Regression for a LIVE hang, not a hypothetical one: py-spy dumped a
    real stuck crawl process and found the main thread pegged at 100% CPU for
    12+ minutes inside _deobfuscate_text -> the old
    r"<(script|style)[^>]*>.*?</\1>|<[^>]+>" (DOTALL, non-greedy) regex. That
    pattern looks bounded but still backtracks per trigger point when the
    closing tag is missing/rare relative to how often the opening substring
    appears -- reproduced here with many literal '<script' occurrences and
    NO closing tag at all (a truncated/malformed page, or one where 'script'
    just appears many times as ordinary text/JSON). Must stay well under a
    second; the old code would not have finished this within any reasonable
    test timeout."""
    html = "<p>" + ("some text mentioning <script inline config here, not a real tag " * 6000) + "</p>"
    t0 = time.perf_counter()
    EH.harvest_all(html, "firma.sk", "homepage_")
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"harvest_all took {dt:.2f}s on unterminated-<script>-shaped input -- regex regression?"


def _perf_guard_long_whitespace_run():
    """Regression for a SECOND live hang on the same regex, found while fixing
    the first one: _AT_RE/_DOT_RE's third alternative (\\s+at\\s+) has a
    genuinely unbounded \\s+ on each side. A single match attempt is fine, but
    a global re.sub() retries the pattern at every position within a long run
    of consecutive whitespace (many adjacent tags each collapsed to one space
    -- confirmed on a real page, o2.sk, with 9 <script> tags among plenty of
    other markup), and each retry re-scans the rest of the run -- the same
    O(run_length^2) shape the module's own docstring already describes,
    just triggered by whitespace-run length instead of at/dot-substring
    count. A few thousand collapsed-space characters with no 'at'/'dot'
    anywhere nearby reproduces it directly."""
    html = "<p>" + ("<i></i>" * 3000) + " nothing obfuscated here at all </p>"
    t0 = time.perf_counter()
    EH.harvest_all(html, "firma.sk", "homepage_")
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"harvest_all took {dt:.2f}s on a long collapsed-whitespace run -- regex regression?"


def main():
    _check_mailto()
    _check_cf_decode()
    _check_jsonld()
    _check_hcard()
    _check_obfuscated_text()
    _check_accented_local_part_not_split()
    _check_plain_text()
    _check_junk_filtered()
    _check_finalize_scoring_and_domain_match()
    _check_creditline_email_filtered()
    _check_offdomain_legit_contact_not_overpenalized()
    _check_contact_discovery()
    _perf_guard()
    _perf_guard_script_tags()
    _perf_guard_long_whitespace_run()
    print("hunter selfcheck OK")


if __name__ == "__main__":
    main()
