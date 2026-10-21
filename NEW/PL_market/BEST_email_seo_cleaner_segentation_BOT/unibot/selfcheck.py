"""Offline self-check for the unified bot (no network / no aiohttp).

Exercises the pure pipeline: enrich (emails + conditional SEO + agency) -> storage
roundtrip -> global cleanup -> export.

    python -m unibot.selfcheck
"""
from __future__ import annotations

import os
import tempfile

from . import cleanup, export
from .config import Config
from .enrich import enrich
from .storage import Storage
from .runtime import RuntimeProfile
from .runlock import RunLock
from .throttle import Throttle

CFG = Config()

BIZ_HTML = ('<html lang="sk"><head><title>Autoservis a pneuservis pre vase auto</title>'
            '<meta name="description" content="Autoservis, pneuservis a STK sluzby dostatocne dlhy popis pre SEO kontrolu.">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'
            '<h1>Autoservis</h1><a href="mailto:info@autoservis.sk">napiste</a>'
            ' skladom, do kosika, otvaracie hodiny, autoservis, pneuservis</body></html>')

AGENCY_HTML = ('<html lang="sk"><head><title>Tvorba webstranok a SEO na mieru</title></head><body>'
               '<h1>Agentura</h1> Tvorba webu, SEO audit, vyvoj webu, marketingova agentura, linkbuilding.'
               '<a href="mailto:hello@agentura.sk">kontakt</a></body></html>')

NOMAIL_HTML = ('<html lang="sk"><head><title>Firma bez emailu ale so sluzbami</title>'
               '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
               '<body><h1>Sluzby</h1> skladom, do kosika</body></html>')


def _hp(html, url, headers=None):
    return {"ok": True, "url": url, "scheme": url.split("://")[0], "status": 200,
            "http_version": "HTTP/2", "ttfb_s": 0.3, "load_time_s": 0.7,
            "headers": headers or {"server": "nginx"}, "html": html,
            "html_size_bytes": len(html.encode()), "robots_status": "present",
            "sitemap_status": "present", "sitemap_url": url + "sitemap.xml",
            "tls_version": "TLSv1.3", "blocked": False, "challenged": False, "spa_shell": False}


def main():
    # 0. Runtime sizing and run ownership remain dependency-free and safe.
    profile = RuntimeProfile(16, 48 * 1024**3, 55_000, 8, 24, 32, 28)
    assert profile.max_concurrency > profile.min_concurrency >= 1
    lock_dir = tempfile.mkdtemp()
    first = RunLock(lock_dir); first.acquire()
    try:
        try:
            RunLock(lock_dir).acquire()
            raise AssertionError("second run lock unexpectedly acquired")
        except RuntimeError:
            pass
    finally:
        first.release()
    throttle = Throttle(24, 40, check_every=0)
    for _ in range(60):
        throttle.record_latency(1.0)
    throttle._maybe_adjust()
    assert throttle._best_latency == 1.0 and throttle.target == 27
    for _ in range(3):
        throttle.record_settled()
    assert throttle.settled_rate() >= 0
    # 1. alive business WITH email -> email + SEO + not competitor + ranked issues
    biz = enrich("autoservis.sk", _hp(BIZ_HTML, "https://autoservis.sk/",
                 {"server": "Apache", "x-powered-by": "PHP/7.4"}), [])
    assert "info@autoservis.sk" in biz["emails"], biz["emails"]
    assert biz["is_competitor"] is False and biz["seo_skipped"] is False
    assert biz["overall_score"] > 0 and biz["php_eol"] is True and biz["email_count"] == 1
    assert biz["issues_ranked"] and "(" in biz["issues_ranked"]     # issue (cause)
    assert biz["alive"] is True and biz["dns_dead"] is False

    # 2. agency (alive + email) -> classified competitor, SEO runs
    ag = enrich("agentura.sk", _hp(AGENCY_HTML, "https://agentura.sk/"), [])
    assert ag["is_competitor"] is True, ag["agency_reason"]
    assert "hello@agentura.sk" in ag["emails"] and ag["seo_skipped"] is False

    # 3. DEAD (no fetch) -> SEO SKIPPED, minimal record, no emails
    dead = enrich("dead.sk", {"ok": False, "dns_dead": True}, [])
    assert dead["email_count"] == 0 and dead["seo_skipped"] is True
    assert dead["state"] in ("unreachable", "timed_out") and dead["dns_dead"] is True

    # 3b. ALIVE but NO email -> SEO also SKIPPED (only alive-with-email get audited)
    nomail = enrich("nomail.sk", _hp(NOMAIL_HTML, "https://nomail.sk/"), [])
    assert nomail["email_count"] == 0 and nomail["seo_skipped"] is True and nomail["alive"] is True

    # 4. storage roundtrip
    d = tempfile.mkdtemp()
    st = Storage(os.path.join(d, "u.db"))
    st.seed(["autoservis.sk", "agentura.sk", "dead.sk"], fresh=False)
    for r in (biz, ag, dead):
        st.save_result(r["domain"], r)
    assert st.count_done() == 3
    records = list(st.iter_records())
    assert len(records) == 3 and all("emails_list" in r for r in records)

    # 5. global cleanup -> agency emails wiped, business kept
    stats = cleanup.apply(records, CFG)
    by = {r["domain"]: r for r in records}
    assert by["agentura.sk"]["emails_final"] == []
    assert "info@autoservis.sk" in by["autoservis.sk"]["emails_final"]
    assert stats["competitor"] >= 1

    # 5b. SK city/municipality blacklist (live-sample regression: bratislava.sk
    # was being kept as if it were a business lead)
    assert cleanup.blacklisted_reason("info@bratislava.sk") == "bratislava.sk"
    assert cleanup.blacklisted_reason("starosta@kosice.sk").startswith("role:")

    # 6. export (issues_ranked is the last column)
    csvp = os.path.join(d, "u.csv")
    export.write_csv(records, csvp)
    export.write_txt(records, os.path.join(d, "u.txt"))
    header = open(csvp, encoding="utf-8-sig").readline().strip().split(",")
    assert header[-1] == "issues_ranked", header[-3:]
    st.close()

    print("unibot selfcheck OK")


if __name__ == "__main__":
    main()
