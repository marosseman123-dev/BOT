"""Global cleanup phase -- runs AFTER all domains are crawled (cross-row logic).

Three passes, precision-first (must never delete a valid lead by accident):

  1. Competitor drop  -- wipe emails of domains the HTML classifier flagged as an
     agency (see classify.py). Evidence-based (the site's own text), not a loose
     domain-substring guess.
  2. Misplaced dedup  -- an email appearing on >= N different domains is kept only
     on the row whose web-domain matches the email's host (kills a webdesigner's
     address copied into many client footers).
  3. Institution blacklist -- remove emails whose HOST is a gov / bank / telco /
     retail-chain / media / edu / hosting-platform domain. Matched by domain
     SUFFIX or host LABEL token -- never by raw substring, so `designova-lekaren`
     is safe.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List

# ============================================================================
#  Institution / corporate / hosting blacklists -- CZ market (+ SK kept).
#  An email whose host (or role mailbox) matches these is NOT a business lead a
#  web developer should contact: gov, ministries, courts, inspectorates, police,
#  municipalities/cities/regions, banks, insurers, utilities, media, edu, hosting.
# ============================================================================

# Role mailboxes that only public administration uses (matched on the LOCAL part).
# 'podatelna@' is the definitive CZ/SK official-mailbox marker.
_LOCALPART_BLOCK = {
    "podatelna", "epodatelna", "e-podatelna", "elektronickapodatelna", "posta.podatelna",
    "starosta", "mistostarosta", "starostka", "mistostarostka", "tajemnik", "tajemnice",
    "urad", "obecniurad", "mestskyurad", "matrika",
}

_RAW = [
    # ---- CZ central government / ministries ----
    "vlada.cz", "gov.cz", "psp.cz", "senat.cz", "hrad.cz", "mzv.cz", "mfcr.cz", "mvcr.cz",
    "mpo.cz", "mzp.cz", "msmt.cz", "mpsv.cz", "mdcr.cz", "mzcr.cz", "mze.cz", "mkcr.cz",
    "mmr.cz", "army.cz", "mocr.cz", "komora.cz", "statnisprava.cz", "portal.gov.cz",
    # ---- CZ courts / legal / oversight ----
    "justice.cz", "nsoud.cz", "nssoud.cz", "usoud.cz", "concourt.cz", "uohs.cz", "ochrance.cz",
    "uoou.cz", "nku.cz", "exekutorskakomora.cz", "nkcr.cz", "cak.cz", "notarskakomora.cz",
    # ---- CZ agencies / inspectorates / registries ----
    "coi.cz", "szpi.cz", "cizp.cz", "sujb.cz", "suip.cz", "ctu.cz", "cnb.cz", "cuzk.cz",
    "czso.cz", "sukl.cz", "ukzuz.cz", "svscr.cz", "cbucr.cz", "szu.cz", "cssz.cz",
    "financnisprava.cz", "celnisprava.cz", "uzsvm.cz", "drozd.cz", "sfzp.cz", "sfdi.cz",
    # ---- CZ police / security / emergency ----
    "policie.cz", "pcr.cz", "hzscr.cz", "bis.cz", "uzsi.cz", "gibs.cz",
    # ---- CZ regions (kraje) ----
    "praha.eu", "praha.cz", "kr-stredocesky.cz", "kr-jihocesky.cz", "kraj-jihocesky.cz",
    "kraj-jihomoravsky.cz", "kr-vysocina.cz", "kr-karlovarsky.cz", "kr-kralovehradecky.cz",
    "kr-olomoucky.cz", "kr-moravskoslezsky.cz", "msk.cz", "kr-pardubicky.cz", "kr-plzensky.cz",
    "plzensky-kraj.cz", "kr-ustecky.cz", "kr-zlinsky.cz", "kr-liberecky.cz", "kraj-lbc.cz",
    # ---- CZ larger cities ----
    "brno.cz", "ostrava.cz", "plzen.eu", "liberec.cz", "olomouc.eu", "c-budejovice.cz",
    "hradeckralove.cz", "pardubice.eu", "zlin.eu", "kladno.cz", "most.cz", "opava.cz",
    "frydekmistek.cz", "karvina.cz", "jihlava.cz", "teplice.cz", "decin.cz", "chomutov.cz",
    "prostejov.eu", "usti-nad-labem.cz", "mesto-kolin.cz", "trebic.cz",
    # ---- CZ banks ----
    "csob.cz", "csas.cz", "kb.cz", "moneta.cz", "airbank.cz", "rb.cz", "raiffeisen.cz",
    "unicreditbank.cz", "ppfbanka.cz", "creditas.cz", "trinitybank.cz", "maxbanka.cz",
    "fio.cz", "mbank.cz", "cnb.cz",
    # ---- CZ insurers / health funds ----
    "kooperativa.cz", "allianz.cz", "generaliceska.cz", "csobpoj.cz", "uniqa.cz", "cpp.cz",
    "pvzp.cz", "vzp.cz", "ozp.cz", "cpzp.cz", "zpmvcr.cz", "rbp.cz", "vozp.cz", "csobpoj.cz",
    # ---- CZ utilities / energy / post / state enterprises ----
    "cez.cz", "eon.cz", "egd.cz", "pre.cz", "innogy.cz", "gasnet.cz", "ceps.cz", "ppas.cz",
    "ceskaposta.cz", "cpost.cz", "rwe.cz",
    # ---- CZ media ----
    "ceskatelevize.cz", "rozhlas.cz", "nova.cz", "prima.cz", "idnes.cz", "novinky.cz",
    "seznam.cz", "aktualne.cz", "blesk.cz", "ringier", "mafra", "denik.cz", "irozhlas.cz",
    # ---- CZ education ----
    "edu.cz", "cuni.cz", "muni.cz", "cvut.cz", "vutbr.cz", "vscht.cz", "czu.cz", "upol.cz",
    "osu.cz", "zcu.cz", "tul.cz", "vsb.cz", "mendelu.cz", "uhk.cz", "utb.cz",
    # ---- hosting / website platforms (an email @provider is not a lead) ----
    "forpsi.com", "forpsi.cz", "wedos.cz", "wedos.com", "active24.cz", "webglobe.cz",
    "vshosting.cz", "gransy.cz", "savana.cz", "ignum.cz", "banan.cz", "nic.cz",
    "shoptet.cz", "upgates.cz", "webnode.cz", "webnode.com", "wix.com", "shopify.com",
    "wordpress.com", "squarespace.com", "ionos.com", "cloudflare.com", "godaddy.com",
    "estranky.cz", "mioweb.cz", "byznysweb.cz", "eshop-rychlo.cz", "fastcentrik.cz",
    "webareal.cz", "solidpixels.cz", "websnadno.cz",
    # ---- SK (kept: SK institutions/banks/etc. -- harmless on CZ domains) ----
    "gov.sk", "vlada.sk", "nrsr.sk", "prezident.sk", "slovensko.sk", "mzv.sk", "justice.sk",
    "prokuratura.sk", "policia.sk", "financnasprava.sk", "upsvar.sk", "soi.sk", "sukl.sk",
    "uvo.gov.sk", "statistics.sk", "vop.gov.sk", "nbs.sk", "vub.sk", "tatrabanka.sk", "slsp.sk",
    "csob.sk", "365.bank", "mbank.sk", "fio.sk", "primabanka.sk", "postovabanka.sk", "dovera.sk",
    "vszp.sk", "union.sk", "telekom.sk", "orange.sk", "o2.sk", "4ka.sk", "sse.sk", "zse.sk",
    "vse.sk", "spp.sk", "slovnaft.sk", "kia.sk", "volkswagen.sk", "tesco.sk", "kaufland.sk",
    "lidl.sk", "billa.sk", "coop.sk", "alza.sk", "mall.sk", "ikea.sk", "obi.sk", "markiza.sk",
    "joj.sk", "ta3.com", "rtvs.sk", "aktuality.sk", "sme.sk", "dennikn.sk", "pravda.sk",
    "topky.sk", "pluska.sk", "hnonline.sk", "edu.sk", "uniba.sk", "stuba.sk", "tuke.sk",
    "upjs.sk", "euba.sk", "websupport.sk", "webglobe.sk", "sk-nic.sk", "shoptet.sk",
    "upgates.sk", "webnode.sk", "biznisweb.sk", "eshop-rychlo.sk", "fastcentrik.sk",
    # ---- generic institutional / EU tokens ----
    "europa.eu", "ec.europa.eu", "europarl.europa.eu", "eu.int",
    "gdpr", "exekutor", "naka", "univerzit", "nemocnic", "poliklinik", "ministerstvo",
]

_DOTTED = sorted({e for e in _RAW if "." in e})
_TOKENS = sorted({e for e in _RAW if "." not in e})
_SPLIT = re.compile(r"[.\-_]")
_LOCAL_SPLIT = re.compile(r"[.\-_+]")


def email_host(email: str) -> str:
    e = (email or "").lower().strip()
    return e.rsplit("@", 1)[1] if "@" in e else ""


def _same_registrable(host: str, domain: str) -> bool:
    def core(x: str) -> str:
        x = (x or "").lower().strip()
        x = re.sub(r"^https?://", "", x).split("/")[0]
        return x[4:] if x.startswith("www.") else x
    h, d = core(host), core(domain)
    if not h or not d:
        return False
    return h == d or h.endswith("." + d) or d.endswith("." + h)


def _is_municipal(host: str) -> bool:
    """CZ/SK municipality / city / region host (obce, mesta, kraje, urady)."""
    for lab in host.split("."):
        if lab in ("mesto", "obec", "mestys", "magistrat", "radnice"):
            return True
        if lab.startswith(("mu-", "ou-", "meu-", "kr-", "kraj-", "mesto-", "obec-",
                           "mestys-", "mikroregion", "obecniurad")):
            return True
        if lab.endswith(("-mesto", "-obec", "-kraj")):
            return True
        if any(t in lab for t in ("magistrat", "radnice", "mikroregion", "svazekobci",
                                  "mestskacast", "mestys")):
            return True
    return False


def blacklisted_reason(email: str) -> str:
    """Return WHY the email is blacklisted (matched entry), else ''. Checks the
    role mailbox (podatelna@/starosta@...), then the host domain, then municipality
    patterns. Precise: domain SUFFIX / host LABEL token, never a raw substring."""
    e = (email or "").lower().strip()
    if "@" not in e:
        return ""
    local, host = e.rsplit("@", 1)
    for tok in _LOCAL_SPLIT.split(local):
        if tok in _LOCALPART_BLOCK:
            return f"role:{tok}"
    if not host:
        return ""
    for ent in _DOTTED:
        if host == ent or host.endswith("." + ent):
            return ent
    labels = _SPLIT.split(host)
    for ent in _TOKENS:
        for lab in labels:
            if lab == ent or (len(ent) >= 5 and lab.startswith(ent)):
                return ent
    if _is_municipal(host):
        return "municipality"
    return ""


def apply(records: List[Dict], cfg) -> Dict:
    """Mutate each record's 'emails_final' from 'emails_list'. Returns stats.

    Each record must have: 'domain', 'emails_list' (List[str]),
    'is_competitor' (bool). Adds: 'emails_final', 'removed_competitor',
    'removed_dedup', 'removed_blacklist'.
    """
    stats = {"competitor": 0, "dedup": 0, "blacklist": 0, "kept": 0}

    # pass 2 pre-count (across ALL rows, before any removal)
    counts: Counter = Counter()
    for r in records:
        for em in r.get("emails_list", []):
            counts[em] += 1
    dups = {e for e, c in counts.items() if c >= cfg.dedup_threshold}

    for r in records:
        emails = list(r.get("emails_list", []))
        removed_comp, removed_dedup, removed_bl = [], [], []
        # pass 1: competitor domain -> wipe all
        if cfg.drop_competitor_emails and r.get("is_competitor"):
            removed_comp = emails
            emails = []
            stats["competitor"] += len(removed_comp)
        # pass 2: misplaced dedup
        if emails:
            kept = []
            for em in emails:
                if em in dups and not _same_registrable(email_host(em), r["domain"]):
                    removed_dedup.append(em)
                else:
                    kept.append(em)
            stats["dedup"] += len(removed_dedup)
            emails = kept
        # pass 3: institution/corporate/hosting blacklist
        if cfg.apply_blacklist and emails:
            kept = []
            for em in emails:
                why = blacklisted_reason(em)
                if why:
                    removed_bl.append(f"{em} [{why}]")
                else:
                    kept.append(em)
            stats["blacklist"] += len(removed_bl)
            emails = kept

        r["emails_final"] = emails
        r["removed_competitor"] = ", ".join(removed_comp)
        r["removed_dedup"] = ", ".join(removed_dedup)
        r["removed_blacklist"] = ", ".join(removed_bl)
        stats["kept"] += len(emails)
    return stats


def apply_row(record: Dict, cfg) -> Dict:
    """Per-row cleanup for streaming/huge runs: competitor-drop + institution
    blacklist only (cross-row misplaced-dedup needs all rows in RAM, so it is
    skipped above cfg.dedup_max_inmemory). Sets 'emails_final'."""
    emails = list(record.get("emails_list", []))
    removed_comp, removed_bl = [], []
    if cfg.drop_competitor_emails and record.get("is_competitor"):
        removed_comp, emails = emails, []
    if cfg.apply_blacklist and emails:
        kept = []
        for em in emails:
            why = blacklisted_reason(em)
            (removed_bl if why else kept).append(f"{em} [{why}]" if why else em)
        emails = kept
    record["emails_final"] = emails
    record["removed_competitor"] = ", ".join(removed_comp)
    record["removed_dedup"] = ""
    record["removed_blacklist"] = ", ".join(removed_bl)
    return record
