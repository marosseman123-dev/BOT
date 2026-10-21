"""Agency / competitor classification (ported from Cleaner_BOT, made pure).

Given a homepage's HTML, decide whether the site is a web/marketing/SEO AGENCY
(a competitor we should not email) rather than a real business lead.

Logic (unchanged from Cleaner_BOT, which was sound):
  1. Strip footers/credits first -- webdesigner signatures live there and would
     create false positives.
  2. Count agency phrases in the MAIN text only.
  3. Rescue: if the site also shows strong e-commerce / industry signals well
     above the agency signal, it is a real business, not an agency.

Returns {is_competitor, agency_score, business_score, reason}. Pure, no I/O.
"""
from __future__ import annotations

from typing import Dict

from bs4 import BeautifulSoup

_MAX = 20000

AGENCY_CORE_PHRASES = [
    "tvorba webstránok", "tvorenie webstránok", "tvorba web stránok", "tvorba webu", "tvorba webov",
    "web na mieru", "firemný web", "firemné webstránky", "tvorba e-shopov", "tvorba eshopov",
    "eshop na mieru", "e-shop na mieru", "vytvorenie webstránky", "redizajn webu", "redesign webu",
    "seo optimalizacia", "seo optimalizace", "seo audit", "linkbuilding", "vývoj webu",
    "vývoj aplikácií", "vývoj aplikací", "marketingová agentúra", "marketingova agentura",
    "reklamná agentúra", "reklamní agentura", "digitálna agentúra", "digitální agentura",
    "správa kampaní", "ppc správa", "ppc kampane", "google ads", "facebook reklama",
    "social media marketing", "web development", "webové štúdio", "webove studio",
    "seo agentúra", "vývoj softvéru", "tvorba ux/ui", "grafické štúdio", "výkonnostný marketing",
]

BUSINESS_COMMERCE = [
    "do košíka", "do košíku", "vložiť do košíka", "skladom", "skladem", "cena s dph", "bez dph",
    "obchodné podmienky", "obchodní podmínky", "doprava a platba", "reklamácie", "reklamačný poriadok",
    "otváracie hodiny", "otevírací doba", "rezervácia", "rezervovať", "naša predajňa", "pobočka",
]

BUSINESS_INDUSTRIES = [
    "autoservis", "pneuservis", "náhradné diely", "stk", "emisná",
    "reality", "nehnuteľnosti", "nemovitosti", "byt na predaj", "prenájom", "realitná kancelária",
    "stavebniny", "stavebné práce", "stavební práce", "rekonštrukcie", "zemné práce", "zateplenie", "fasády",
    "reštaurácia", "restaurace", "jedálny lístok", "jídelní lístek", "denné menu", "denní menu",
    "ubytovanie", "ubytování", "penzión", "apartmány", "hotel",
    "ambulancia", "ordinace", "poliklinika", "lekáreň", "lekárna", "zubná", "stomatológia", "veterinár",
    "kaderníctvo", "kozmetika", "masáže", "wellness",
    "účtovníctvo", "účetnictví", "daňové poradenstvo", "znalec", "advokát", "právne služby", "notár",
    "autodoprava", "kuriér", "sťahovanie", "taxi", "čistenie", "tepovanie", "upratovanie",
    "výroba", "maloobchod", "veľkoobchod",
]

BUSINESS_PROTECTION_ALL = BUSINESS_COMMERCE + BUSINESS_INDUSTRIES

FOOTER_SELECTORS = [
    "footer", "div#footer", "div.footer", "div.site-footer", "div.paticka", "div#paticka",
    ".copyright", "#copyright", ".credits", ".design-by", ".created-by", ".developed-by",
    ".autor-webu", "a[href*='webdesign']", "a[href*='tvorba']",
]


def _main_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for sel in FOOTER_SELECTORS:          # strip footers/credits (agency signatures)
        for el in soup.select(sel):
            el.decompose()
    return soup.get_text(separator=" ").strip().lower()[:_MAX]


def classify_agency(html: str) -> Dict:
    info = {"is_competitor": False, "agency_score": 0, "business_score": 0, "reason": ""}
    if not html:
        info["reason"] = "no_html"
        return info
    low = html.lower()
    # fast pre-check: no agency wording at all -> not an agency (cheap)
    if not any(p in low for p in AGENCY_CORE_PHRASES):
        info["reason"] = "no_agency_words"
        return info
    text = _main_text(html)
    if not text:
        info["reason"] = "no_main_text"
        return info
    agency = sum(1 for p in AGENCY_CORE_PHRASES if p in text)
    business = sum(1 for p in BUSINESS_PROTECTION_ALL if p in text)
    info["agency_score"] = agency
    info["business_score"] = business
    if agency >= 2:
        if business > agency * 1.5:
            info["reason"] = f"protected_business (biz {business} > ag {agency})"
            info["is_competitor"] = False
        else:
            info["reason"] = f"agency (ag {agency} >= biz {business})"
            info["is_competitor"] = True
    else:
        info["reason"] = f"not_enough_agency_signals (ag {agency})"
    return info
