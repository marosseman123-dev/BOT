"""
beton_classifier_cz.py  —  CZ/SK trh

Hľadá odbytových partnerov pre mikrocementy a stavebné materiály.
Klasifikuje domény do is_relevant=YES ak firma pôsobí v oblasti:

  Betón & cement: betonárky, cementárne, maltárne, prefabrikáty
  Stavebné firmy: pozemné, inžinierske, priemyselné, bytová výstavba
  Rekonštrukcie: bytov, domov, bytových domov, revitalizácie
  Podlahy: liate, priemyselné, epoxidové, stierky, pokládka
  Omietky & stierky: dekoratívne, fasádne, štukatéri
  Maliari: maliarské práce, fasádne nátěry, nátery
  Fasády & zateplenie: ETICS, fasádne práce
  Kúpeľne/kuchyne: štúdiá, obkladači, dlažby
  Interiéry: štúdiá, dizajnéri, architekti
  Sanácia betónu & hydroizolácia
  Komunikácie: dlažby, chodníky, parkoviská
  Novostavby & developer projekty

Výstupy (UTF-8 s BOM — Excel otvorí správne):
  output_beton_all_cz.csv      — všetky domény
  output_beton_relevant_cz.csv — len is_relevant=YES
"""
from __future__ import annotations

import asyncio
import aiohttp
import csv
import json
import logging
import os
import random
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from tqdm import tqdm

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent

def _res(env_key: str, default: str) -> str:
    v = os.environ.get(env_key, default)
    p = Path(v)
    return str((BASE_DIR / p if not p.is_absolute() else p).resolve())

INPUT_FILE      = _res("BETON_INPUT",    "domains.txt")
OUTPUT_ALL      = _res("BETON_OUTPUT",   "output_beton_all_cz.csv")
OUTPUT_RELEVANT = _res("BETON_RELEVANT", "output_beton_relevant_cz.csv")
PROGRESS_JSONL  = _res("BETON_PROGRESS", "progress_beton_cz.jsonl")
LOG_FILE        = _res("BETON_LOG",      "beton_classifier_cz.log")

# ── Sieť ──────────────────────────────────────────────────────────────────────

SSL_INSECURE       = os.environ.get("BETON_INSECURE_SSL", "").lower() in ("1", "true", "yes")
HOMEPAGE_TIMEOUT   = 12
FETCH_RETRIES      = 2
RETRY_DELAY        = 0.5
WORKER_COUNT       = min(80, max(30, (os.cpu_count() or 4) * 8))
CONNECTOR_LIMIT    = 256
CONNECTOR_PER_HOST = 2
PER_HOST_MIN_DELAY = 0.25
PER_HOST_MAX_DELAY = 0.8
WRITER_BATCH_SIZE  = 80
WRITER_FLUSH_SEC   = 1.0
STATUS_EVERY_SEC   = 6.0
_WRITER_TICK = object()

# Podstránky pre 2. prechod (homepage OK, ale skóre nízke)
SUBPAGES = ["/o-nas", "/sluzby", "/o-firme"]

OUTPUT_FIELDNAMES = [
    "input_line_number", "domain",
    "page_title", "meta_description", "schema_types",
    "keywords_matched", "is_relevant", "confidence",
    "error_note",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

_LOG = logging.getLogger("beton_classifier_cz")

# ── Scoring ────────────────────────────────────────────────────────────────────
# title_score : kw nájdené v title + h1 + h2  (plné skóre)
# meta_score  : kw v meta description         (* 0.5)
# body_score  : kw iba v tele stránky         (* 0.4)  ← zvýšené z 0.3
# Záporné     : penalizácia (nesúvisiaci odbor)

BODY_MULT = 0.4
META_MULT = 0.5

# Prahy — zamerané na aplikátorov povrchov a odberateľov profilov/systémov
# ponytail: HIGH=180 eliminuje hrubú stavbu, LOW=70 eliminuje single-word false positives
HIGH_THRESHOLD = 180
MED_THRESHOLD  = 110
LOW_THRESHOLD  =  50

# ── Keyword rules ──────────────────────────────────────────────────────────────
# (kľúčové_slovo_lowercase, title_score, label)

KEYWORD_RULES: List[Tuple[str, int, str]] = [

    # ══════════════════════════════════════════════════════════════════════════
    # 1. MIKROCEMENT — priamy signál (najsilnejší)
    # ══════════════════════════════════════════════════════════════════════════
    ("mikrocement",            250, "Mikrocement"),
    ("microcement",            250, "Mikrocement"),
    ("mikrobeton",             240, "Mikrobeton"),
    ("mikro beton",            220, "Mikrobeton"),
    ("dekorativní beton",      200, "Dekorativní beton"),
    ("dekorativni beton",      200, "Dekorativní beton"),
    ("dekoratívny betón",      200, "Dekoratívny betón"),
    ("dekorativny beton",      200, "Dekoratívny betón"),
    ("pohledový beton",        200, "Pohledový beton"),
    ("pohledovy beton",        200, "Pohledový beton"),
    ("pohľadový betón",        200, "Pohľadový betón"),
    ("pohladovy beton",        200, "Pohľadový betón"),
    ("dekorativní povrch",     160, "Dekorativní povrch"),
    ("dekorativni povrch",     160, "Dekorativní povrch"),
    ("dekoratívny povrch",     160, "Dekoratívny povrch"),

    # ══════════════════════════════════════════════════════════════════════════
    # 2. PODLAHY — primárna aplikačná oblasť mikrocement
    # ══════════════════════════════════════════════════════════════════════════
    ("podlahářství",           180, "Podlahářství"),
    ("podlaharství",           180, "Podlahářství"),
    ("podlahárstvo",           180, "Podlahárstvo"),
    ("podlaharstvo",           180, "Podlahárstvo"),
    ("podlahové studio",       160, "Podlahové studio"),
    ("podlahove studio",       160, "Podlahové studio"),
    ("podlahové štúdio",       160, "Podlahové štúdio"),
    ("pokládka podlah",        160, "Pokládka podlah"),
    ("pokladka podlah",        160, "Pokládka podlah"),
    ("pokládanie podláh",      160, "Pokládanie podláh"),
    ("pokladanie podlah",      160, "Pokládanie podláh"),
    ("lité podlahy",           190, "Lité podlahy"),
    ("lite podlahy",           190, "Lité podlahy"),
    ("liata podlaha",          190, "Liata podlaha"),
    ("průmyslové podlahy",     180, "Průmyslové podlahy"),
    ("prumyslove podlahy",     180, "Průmyslové podlahy"),
    ("priemyselné podlahy",    180, "Priemyselné podlahy"),
    ("priemyselne podlahy",    180, "Priemyselné podlahy"),
    ("betonové podlahy",       180, "Betonové podlahy"),
    ("betonove podlahy",       180, "Betonové podlahy"),
    ("epoxidové podlahy",      170, "Epoxidové podlahy"),
    ("epoxidove podlahy",      170, "Epoxidové podlahy"),
    ("stěrkové podlahy",       170, "Stěrkové podlahy"),
    ("sterkove podlahy",       170, "Stěrkové podlahy"),
    ("stierková podlaha",      170, "Stierková podlaha"),
    ("stierkova podlaha",      170, "Stierková podlaha"),
    ("renovace podlah",        140, "Renovace podlah"),
    ("renovacia podlah",       140, "Renovácia podláh"),
    ("broušení podlah",        130, "Broušení podlah"),
    ("brouseni podlah",        130, "Broušení podlah"),
    ("brúsenie podláh",        130, "Brúsenie podláh"),
    ("podlahoviny",            130, "Podlahoviny"),
    ("podlahové krytiny",      120, "Podlahové krytiny"),
    ("podlahove krytiny",      120, "Podlahové krytiny"),
    ("podlahy",                 80, "Podlahy"),

    # ══════════════════════════════════════════════════════════════════════════
    # 3. OMIETKY / STIERKY / ŠTUKATÉRSTVO
    # ══════════════════════════════════════════════════════════════════════════
    ("dekorativní omítky",     180, "Dekorativní omítky"),
    ("dekorativni omitky",     180, "Dekorativní omítky"),
    ("dekoratívne omietky",    180, "Dekoratívne omietky"),
    ("dekorativne omietky",    180, "Dekoratívne omietky"),
    ("stěrkové omítky",        170, "Stěrkové omítky"),
    ("sterkove omitky",        170, "Stěrkové omítky"),
    ("stierková omietka",      170, "Stierková omietka"),
    ("stierkova omietka",      170, "Stierková omietka"),
    ("štukatérství",           160, "Štukatérství"),
    ("stukatersvi",            160, "Štukatérství"),
    ("štukatérstvo",           160, "Štukatérstvo"),
    ("omítky a stěrky",        150, "Omítky a stěrky"),
    ("omitky a sterky",        150, "Omítky a stěrky"),
    ("omietky a stierky",      150, "Omietky a stierky"),
    ("omítkové směsi",         130, "Omítkové směsi"),
    ("omietkár",               100, "Omietkár"),
    ("omietkar",               100, "Omietkár"),
    ("omítkář",                100, "Omítkář"),
    ("omitkar",                100, "Omítkář"),
    ("stěrka",                  80, "Stěrka"),
    ("sterka",                  80, "Stěrka"),
    ("stierka",                 80, "Stierka"),
    ("omítky",                  75, "Omítky"),
    ("omitky",                  75, "Omítky"),
    ("omietky",                 75, "Omietky"),

    # ══════════════════════════════════════════════════════════════════════════
    # 4. KÚPEĽNE / KUCHYNE / OBKLADY
    # ══════════════════════════════════════════════════════════════════════════
    ("koupelnové studio",      160, "Koupelnové studio"),
    ("koupelnove studio",      160, "Koupelnové studio"),
    ("kúpeľňové štúdio",       160, "Kúpeľňové štúdio"),
    ("kupelnove studio",       160, "Kúpeľňové štúdio"),
    ("koupelna na klíč",       150, "Koupelna na klíč"),
    ("koupelna na klic",       150, "Koupelna na klíč"),
    ("rekonstrukce koupelny",  160, "Rekonstrukce koupelny"),
    ("rekonštrukcia kúpeľne",  160, "Rekonštrukcia kúpeľne"),
    ("kuchyňské studio",       150, "Kuchyňské studio"),
    ("kuchynske studio",       150, "Kuchyňské studio"),
    ("kuchynské štúdio",       150, "Kuchynské štúdio"),
    ("obkladačství",           150, "Obkladačství"),
    ("obkladacstvi",           150, "Obkladačství"),
    ("obkladačstvo",           150, "Obkladačstvo"),
    ("obkladacstvo",           150, "Obkladačstvo"),
    ("obkladač",               120, "Obkladač"),
    ("obkladac",               120, "Obkladač"),
    ("dlažby a obklady",       140, "Dlažby a obklady"),
    ("dlazby a obklady",       140, "Dlažby a obklady"),
    ("kuchyňské desky",        130, "Kuchyňské desky"),
    ("kuchynske desky",        130, "Kuchyňské desky"),
    ("pracovní desky",         120, "Pracovní desky"),
    ("pracovni desky",         120, "Pracovní desky"),
    ("dlažby",                  75, "Dlažby"),
    ("dlazby",                  75, "Dlažby"),

    # ══════════════════════════════════════════════════════════════════════════
    # 5. REKONŠTRUKCIE BYTOV / INTERIÉROV — aplikujú povrchové úpravy
    # ══════════════════════════════════════════════════════════════════════════
    ("rekonstrukce bytu",      150, "Rekonstrukce bytu"),
    ("rekonstrukce domu",      140, "Rekonstrukce domu"),
    ("rekonstrukce bytů",      150, "Rekonstrukce bytů"),
    ("rekonštrukcia bytu",     150, "Rekonštrukcia bytu"),
    ("rekonstrukcia bytu",     150, "Rekonštrukcia bytu"),
    ("bytové rekonstrukce",    140, "Bytové rekonstrukce"),
    ("bytove rekonstrukce",    140, "Bytové rekonstrukce"),
    ("dokončovací práce",      120, "Dokončovací práce"),
    ("dokoncovaci prace",      120, "Dokončovací práce"),
    ("dokončovacie práce",     120, "Dokončovacie práce"),
    ("dokoncovanie prace",     120, "Dokončovacie práce"),
    ("rekonstrukce koupelny",  160, "Rekonstrukce koupelny"),
    ("rekonstrukce koupelen",  160, "Rekonstrukce koupelen"),

    # ══════════════════════════════════════════════════════════════════════════
    # 6. MALIARI / FASÁDNÍ NÁTERY
    # ══════════════════════════════════════════════════════════════════════════
    ("malířské práce",         120, "Malířské práce"),
    ("maliřske prace",         120, "Malířské práce"),
    ("maliarské práce",        120, "Maliarské práce"),
    ("maliarske prace",        120, "Maliarské práce"),
    ("malíř pokojů",           110, "Malíř pokojů"),
    ("malir pokoju",           110, "Malíř pokojů"),
    ("fasádní nátěry",         130, "Fasádní nátěry"),
    ("fasadni natery",         130, "Fasádní nátěry"),
    ("fasádne nátery",         130, "Fasádne nátery"),
    ("fasadne natery",         130, "Fasádne nátery"),
    ("malování interiéru",     110, "Malování interiéru"),
    ("malovanie interieru",    110, "Maľovanie interiéru"),
    ("malíř",                   75, "Malíř"),
    ("maliar",                  75, "Maliar"),
    ("náterové systémy",       110, "Náterové systémy"),
    ("naterove systemy",       110, "Náterové systémy"),
    ("fasádnik",                85, "Fasádnik"),
    ("fasadnik",                85, "Fasádnik"),

    # ══════════════════════════════════════════════════════════════════════════
    # 7. FASÁDY / ZATEPLENIE (aplikátori, nie predajcovia)
    # ══════════════════════════════════════════════════════════════════════════
    ("fasádní práce",          130, "Fasádní práce"),
    ("fasadni prace",          130, "Fasádní práce"),
    ("fasádne práce",          130, "Fasádne práce"),
    ("fasadne prace",          130, "Fasádne práce"),
    ("zateplení fasád",        140, "Zateplení fasád"),
    ("zatepleni fasad",        140, "Zateplení fasád"),
    ("zateplenie fasád",       140, "Zateplenie fasád"),
    ("zateplenie fasad",       140, "Zateplenie fasád"),
    ("etics",                  100, "ETICS"),
    ("fasádní systém",         110, "Fasádní systém"),
    ("fasadni system",         110, "Fasádní systém"),
    ("zateplení",               85, "Zateplení"),
    ("zateplenie",              85, "Zateplenie"),
    ("omítání",                 85, "Omítání"),
    ("omitani",                 85, "Omítání"),

    # ══════════════════════════════════════════════════════════════════════════
    # 8. ZEDNICKÉ / MURÁRSKE PRÁCE (aplikujú omietky a stierky)
    # ══════════════════════════════════════════════════════════════════════════
    ("zednické práce",         110, "Zednické práce"),
    ("zednicke prace",         110, "Zednické práce"),
    ("zednická firma",         110, "Zednická firma"),
    ("zednicka firma",         110, "Zednická firma"),
    ("murárske práce",         110, "Murárske práce"),
    ("murarske prace",         110, "Murárske práce"),
    ("murárska firma",         110, "Murárska firma"),
    ("murarska firma",         110, "Murárska firma"),
    ("zedník",                  75, "Zedník"),
    ("zednik",                  75, "Zedník"),
    ("murár",                   75, "Murár"),
    ("murar",                   75, "Murár"),

    # ══════════════════════════════════════════════════════════════════════════
    # 9. SANÁCIA BETÓNU & HYDROIZOLÁCIA (špecializovaná povrchová práca)
    # ══════════════════════════════════════════════════════════════════════════
    ("sanace betonu",          180, "Sanace betonu"),
    ("sanácia betónu",         180, "Sanácia betónu"),
    ("sanacia betonu",         180, "Sanácia betónu"),
    ("oprava betonu",          160, "Oprava betonu"),
    ("oprava betónu",          160, "Oprava betónu"),
    ("torkret",                150, "Torkret"),
    ("stříkaný beton",         150, "Stříkaný beton"),
    ("strikany beton",         150, "Stříkaný beton"),
    ("injektáž betonu",        140, "Injektáž betonu"),
    ("injektaz betonu",        140, "Injektáž betonu"),
    ("hydroizolace",           120, "Hydroizolace"),
    ("hydroizolácia",          120, "Hydroizolácia"),
    ("hydroizolacia",          120, "Hydroizolácia"),
    ("střešní izolace",        100, "Střešní izolace"),
    ("stresni izolace",        100, "Střešní izolace"),
    ("strešná izolácia",       100, "Strešná izolácia"),

    # ══════════════════════════════════════════════════════════════════════════
    # 10. INTERIÉROVÉ ŠTÚDIÁ / DIZAJN (konzultanti + aplikátori)
    # ══════════════════════════════════════════════════════════════════════════
    ("interiérové studio",     150, "Interiérové studio"),
    ("interierove studio",     150, "Interiérové studio"),
    ("interiérové štúdio",     150, "Interiérové štúdio"),
    ("interiérový design",     130, "Interiérový design"),
    ("interierovy design",     130, "Interiérový design"),
    ("interiérový dizajn",     130, "Interiérový dizajn"),
    ("interierovy dizajn",     130, "Interiérový dizajn"),
    ("interiérové práce",      120, "Interiérové práce"),
    ("interierove prace",      120, "Interiérové práce"),

    # ══════════════════════════════════════════════════════════════════════════
    # 11. BAZÉNY / TERASOVÉ PLOCHY
    # ══════════════════════════════════════════════════════════════════════════
    ("stavba bazénů",          140, "Stavba bazénů"),
    ("stavba bazenu",          140, "Stavba bazénů"),
    ("stavba bazénov",         140, "Stavba bazénov"),
    ("bazénová technologie",   120, "Bazénová technologie"),
    ("bazenova technologie",   120, "Bazénová technologie"),
    ("terasové plochy",        110, "Terasové plochy"),
    ("terasove plochy",        110, "Terasové plochy"),
    ("terasové dlažby",        110, "Terasové dlažby"),
    ("terasove dlazby",        110, "Terasové dlažby"),

    # ══════════════════════════════════════════════════════════════════════════
    # 12. STAVEBNINY — distribučný kanál pre produkty
    # ══════════════════════════════════════════════════════════════════════════
    ("stavebniny",             110, "Stavebniny"),
    ("stavební materiály",      90, "Stavební materiály"),
    ("stavebni materialy",      90, "Stavební materiály"),
    ("stavebné materiály",      90, "Stavebné materiály"),
    ("stavebne materialy",      90, "Stavebné materiály"),

    # ══════════════════════════════════════════════════════════════════════════
    # 13. STRECHY / KLEMPÍRSTVO — odkvapové systémy a profily
    # ══════════════════════════════════════════════════════════════════════════
    ("střešní práce",          130, "Střešní práce"),
    ("stresni prace",          130, "Střešní práce"),
    ("strešné práce",          130, "Strešné práce"),
    ("stresne prace",          130, "Strešné práce"),
    ("pokrývač",               120, "Pokrývač"),
    ("pokryvac",               120, "Pokrývač"),
    ("krytinář",               120, "Krytinář"),
    ("krytinar",               120, "Krytinář"),
    ("krytinárstvo",           120, "Krytinárstvo"),
    ("klempíř",                120, "Klempíř"),
    ("klempir",                120, "Klempíř"),
    ("klempiar",               120, "Klempiar"),
    ("klempířství",            130, "Klempířství"),
    ("klempirstvi",            130, "Klempířství"),
    ("klempiarstvo",           130, "Klempiarstvo"),
    ("střešní krytina",        110, "Střešní krytina"),
    ("stresni krytina",        110, "Střešní krytina"),
    ("strešná krytina",        110, "Strešná krytina"),
    ("plechová střecha",       110, "Plechová střecha"),
    ("plechova strecha",       110, "Plechová střecha"),
    ("montáž střechy",         120, "Montáž střechy"),
    ("montaz strechy",         120, "Montáž střechy"),
    ("montáž strechy",         120, "Montáž strechy"),
    ("střechy",                 80, "Střechy"),
    ("strecha",                 80, "Strecha"),

    # ══════════════════════════════════════════════════════════════════════════
    # 14. ODKVAPOVÉ / OKAPOVÉ SYSTÉMY — priamy produkt
    # ══════════════════════════════════════════════════════════════════════════
    ("odkvap",                 130, "Odkvap"),
    ("odkvapový žľab",         160, "Odkvapový žľab"),
    ("odkvapovy zlab",         160, "Odkvapový žľab"),
    ("odkvapový systém",       160, "Odkvapový systém"),
    ("odkvapovy system",       160, "Odkvapový systém"),
    ("okapový systém",         160, "Okapový systém"),
    ("okapovy system",         160, "Okapový systém"),
    ("okapy",                  120, "Okapy"),
    ("okap",                    85, "Okap"),
    ("okapový žlab",           160, "Okapový žlab"),
    ("okapovy zlab",           160, "Okapový žlab"),
    ("okapový svod",           140, "Okapový svod"),
    ("okapovy svod",           140, "Okapový svod"),
    ("odkvapový svod",         140, "Odkvapový svod"),

    # ══════════════════════════════════════════════════════════════════════════
    # 15. PROFILY — ukončovacie, rohové, balkónové, terasové, okapové
    # ══════════════════════════════════════════════════════════════════════════
    ("ukončovací profil",      160, "Ukončovací profil"),
    ("ukoncovaci profil",      160, "Ukončovací profil"),
    ("ukoncovacie profily",    160, "Ukončovací profily"),
    ("ukončovací lišta",       150, "Ukončovací lišta"),
    ("ukoncovaci lista",       150, "Ukončovací lišta"),
    ("ukončovacie lišty",      150, "Ukončovacie lišty"),
    ("rohový profil",          150, "Rohový profil"),
    ("rohovy profil",          150, "Rohový profil"),
    ("rohove profily",         150, "Rohové profily"),
    ("rohová lišta",           140, "Rohová lišta"),
    ("rohova lista",           140, "Rohová lišta"),
    ("dilatačný profil",       150, "Dilatačný profil"),
    ("dilatacny profil",       150, "Dilatačný profil"),
    ("dilatační profil",       150, "Dilatační profil"),
    ("dilatacni profil",       150, "Dilatační profil"),
    ("terasový profil",        160, "Terasový profil"),
    ("terasovy profil",        160, "Terasový profil"),
    ("terasove profily",       160, "Terasové profily"),
    ("okapový profil",         160, "Okapový profil"),
    ("okapovy profil",         160, "Okapový profil"),
    ("okapove profily",        160, "Okapové profily"),
    ("balkónový profil",       160, "Balkónový profil"),
    ("balkonovy profil",       160, "Balkónový profil"),
    ("balkónové profily",      160, "Balkónové profily"),
    ("balkonove profily",      160, "Balkónové profily"),
    ("okapnica",               130, "Okapnica"),
    ("okapový nos",            140, "Okapový nos"),
    ("okapovy nos",            140, "Okapový nos"),
    ("začišťovací profil",     150, "Začišťovací profil"),
    ("zacisovacie profily",    150, "Začišťovací profily"),
    ("spodní lišta",           110, "Spodní lišta"),
    ("spodna lista",           110, "Spodná lišta"),
    ("hliníkový profil",       110, "Hliníkový profil"),
    ("hlinikovy profil",       110, "Hliníkový profil"),
    ("hliníkový žlab",         120, "Hliníkový žlab"),
    ("hlinikovy zlab",         120, "Hliníkový žlab"),
    ("lišty a profily",        120, "Lišty a profily"),
    ("listy a profily",        120, "Lišty a profily"),

    # ══════════════════════════════════════════════════════════════════════════
    # 16. BALKÓNY / TERASY — práce a sanácia
    # ══════════════════════════════════════════════════════════════════════════
    ("balkónové práce",        120, "Balkónové práce"),
    ("balkonove prace",        120, "Balkónové práce"),
    ("oprava balkóna",         120, "Oprava balkóna"),
    ("oprava balkona",         120, "Oprava balkóna"),
    ("sanace balkonu",         120, "Sanace balkónu"),
    ("sanácia balkóna",        120, "Sanácia balkóna"),
    ("sanacia balkona",        120, "Sanácia balkóna"),
    ("terasy a balkóny",       110, "Terasy a balkóny"),
    ("terasy a balkony",       110, "Terasy a balkóny"),
    ("balkóny a terasy",       110, "Balkóny a terasy"),
    ("balkony a terasy",       110, "Balkóny a terasy"),
    ("terasové systémy",       120, "Terasové systémy"),
    ("terasove systemy",       120, "Terasové systémy"),
    ("zatekanie balkóna",      110, "Zatekanie balkóna"),
    ("oprava terasy",          110, "Oprava terasy"),

    # ══════════════════════════════════════════════════════════════════════════
    # ZÁPORNÉ — nesúvisiace odvetvia + false-positive pasce
    # ══════════════════════════════════════════════════════════════════════════
    ("fotovoltaika",           -200, ""),
    ("fotovoltaický",          -180, ""),
    ("solární panely",         -160, ""),
    ("solarni panely",         -160, ""),
    ("solárne panely",         -160, ""),
    ("větrná energie",         -120, ""),
    ("veterna energia",        -120, ""),
    ("pojišťovna",             -100, ""),
    ("pojistovna",             -100, ""),
    ("poisťovňa",              -100, ""),
    ("poistovna",              -100, ""),
    ("advokát",                -100, ""),
    ("advokat",                -100, ""),
    ("daňový poradce",          -90, ""),
    ("danovy poradce",          -90, ""),
    ("daňový poradca",          -90, ""),
    ("zubní",                   -90, ""),
    ("zubár",                   -90, ""),
    ("nemocnice",               -90, ""),
    ("lékárna",                 -80, ""),
    ("lekarna",                 -80, ""),
    ("lekáreň",                 -80, ""),
    ("lekaren",                 -80, ""),
    ("software",                -90, ""),
    ("it služby",               -80, ""),
    ("optické sítě",            -80, ""),
    ("připojení k internetu",   -80, ""),
    ("restaurace",              -70, ""),
    ("reštaurácia",             -70, ""),
    ("restauracia",             -70, ""),
    ("cestovní kancelář",       -70, ""),
    ("cestovni kancelar",       -70, ""),
    ("cestovná kancelária",     -70, ""),
    ("kadeřnictví",             -70, ""),
    ("kadernictvi",             -70, ""),
    ("fitness",                -200, ""),
    ("realitní makléř",         -60, ""),
    ("realitni makler",         -60, ""),
    ("realitný maklér",         -60, ""),
    ("realitny makler",         -60, ""),
    ("stěhování",               -80, ""),
    ("stehovani",               -80, ""),
    ("stěhovací",               -80, ""),
    ("stehovaci",               -80, ""),
    ("sťahovanie",              -80, ""),
    ("úklidové",               -200, ""),
    ("uklidove",               -200, ""),
    ("úklidová",               -200, ""),
    ("uklidova",               -200, ""),
    ("půjčovna",                -80, ""),
    ("pujcovna",                -80, ""),
    ("požičovňa",               -80, ""),
    ("pozicovna",               -80, ""),
    ("autoservis",              -70, ""),
    ("pneuservis",              -70, ""),
    ("parkoviště",              -60, ""),
    ("parkoviste",              -60, ""),
    ("parkovisko",              -60, ""),
    ("zahradnictví",           -120, ""),
    ("zahradnictvi",           -120, ""),
    ("záhradníctvo",           -120, ""),
    ("zahradnictvo",           -120, ""),
    ("účetnictví",              -60, ""),
    ("uctnictvi",               -60, ""),
    ("účtovníctvo",             -60, ""),
    ("uctovnictvo",             -60, ""),
    ("magazín",                 -50, ""),
    ("magazin",                 -50, ""),
    ("zpravodaj",               -50, ""),
    # ── Nové negatívy — false positive kategórie ────────────────────────────────
    # Šport / fitness — aerobik klub, telocvičňa
    ("aerobic",                -300, ""),
    ("aerobik",                -300, ""),
    ("cvičení",                -150, ""),
    ("cvičenie",               -150, ""),
    ("chovatelství",           -120, ""),
    ("sportovní klub",         -200, ""),
    # Nábytok
    ("kancelářský nábytek",    -200, ""),
    ("kancelársky nábytok",    -200, ""),
    ("nábytek",                -150, ""),
    ("nábytok",                -150, ""),
    # Developer / predaj domov
    ("prodej domů",            -150, ""),
    ("prodej domu",            -150, ""),
    ("rodinné domy na prodej", -200, ""),
    ("rodinne domy na prodej", -200, ""),
    ("predaj domov",           -150, ""),
    ("nové byty",              -120, ""),
    ("nove byty",              -120, ""),
    # Záhradná technika / e-shop s náriadím
    ("štěpkovač",              -200, ""),
    ("stepkovac",              -200, ""),
    ("štiepacie",              -200, ""),
    ("drtič",                  -200, ""),
    ("drtic",                  -200, ""),
    ("motorová pila",          -200, ""),
    ("motorova pila",          -200, ""),
    ("kultivátory",            -150, ""),
    ("kultivatory",            -150, ""),
    ("zahradní nářadí",        -200, ""),
    ("zahradni naradi",        -200, ""),
    ("záhradné náradie",       -200, ""),
    ("zahradne naradie",       -200, ""),
    # Potravinárstvo / pohostinstvo
    ("kavárna",                -120, ""),
    ("kavarna",                -120, ""),
    ("kaviareň",               -120, ""),
    ("kaviaen",                -120, ""),
    ("pekárna",                -100, ""),
    ("pekarna",                -100, ""),
    ("pekáreň",                -100, ""),
]

# schema.org @type → (skóre, label)
SCHEMA_SCORES: Dict[str, Tuple[int, str]] = {
    "ConstructionBusiness":        (220, "Stavební firma"),
    "HomeAndConstructionBusiness": (220, "Stavební firma"),
    "GeneralContractor":           (220, "Generální dodavatel"),
    "RoofingContractor":           (160, "Střechař"),
    "LandscapingOrLawnService":    ( 70, "Zahradnictví"),
    "HomeImprovement":             (150, "Rekonstrukce"),
    "HardwareStore":               ( 90, "Stavebniny"),
    "InteriorDesigner":            (150, "Interiérový dizajn"),
    "FloorCovering":               (180, "Podlahy"),
    "Painter":                     (110, "Maliar"),
    "Plumber":                     ( 80, "Inštalatér"),
    "Electrician":                 ( 40, "Elektrikár"),
    "Store":                       ( 50, "Obchod"),
}

# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    if _LOG.handlers:
        return
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    _LOG.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.ERROR)
    ch.setFormatter(fmt)
    _LOG.addHandler(fh)
    _LOG.addHandler(ch)
    _LOG.propagate = False

def _flush_logs() -> None:
    for h in _LOG.handlers:
        try:
            h.flush()
        except Exception:
            pass

# ── Vstup ─────────────────────────────────────────────────────────────────────

def _normalize_domain(raw: str) -> str:
    v = raw.strip().strip('"\'')
    if not v:
        return ""
    target = v if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", v) else f"http://{v}"
    p = urlparse(target)
    host = (p.netloc or p.path).strip().lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    host = host.rstrip(".")
    if not host or "." not in host or re.search(r"\s", host):
        return ""
    return host


def load_input_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(f"Vstupny subor nenajdeny: {INPUT_FILE}")
    rows: List[Dict[str, Any]] = []
    ext = os.path.splitext(INPUT_FILE)[1].lower()
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        first = f.readline().strip()
        f.seek(0)
        is_csv = ext == ".csv" or "," in first or ";" in first
        if is_csv:
            delim = ";" if ";" in first else ","
            reader = csv.DictReader(f, delimiter=delim)
            lowered = [k.lower() for k in (reader.fieldnames or []) if k]
            if "domain" in lowered:
                for ln, row in enumerate(reader, 2):
                    for k in row:
                        if k and k.lower() == "domain":
                            d = _normalize_domain(row[k] or "")
                            if d:
                                rows.append({"input_line_number": ln, "domain": d})
                            break
            else:
                f.seek(0)
                for ln, row in enumerate(csv.reader(f, delimiter=delim), 1):
                    if row:
                        d = _normalize_domain(row[0])
                        if d:
                            rows.append({"input_line_number": ln, "domain": d})
        else:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith("#"):
                    d = _normalize_domain(line)
                    if d:
                        rows.append({"input_line_number": ln, "domain": d})
    print(f"Nacitanych domen: {len(rows)}", flush=True)
    return rows


def _row_key(item: Dict[str, Any]) -> str:
    return f"{item['domain']}\t{item.get('input_line_number', '')}"

# ── Progress ──────────────────────────────────────────────────────────────────

def load_progress_map() -> Dict[str, Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(PROGRESS_JSONL):
        return m
    with open(PROGRESS_JSONL, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = rec.get("row_key")
            if k:
                m[k] = rec
    return m


def append_progress(records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    Path(PROGRESS_JSONL).parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_JSONL, "a", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
        fp.flush()
        os.fsync(fp.fileno())

# ── CSV výstup ────────────────────────────────────────────────────────────────

def write_output_csvs(rows: List[Dict[str, Any]]) -> None:
    state = load_progress_map()
    Path(OUTPUT_ALL).parent.mkdir(parents=True, exist_ok=True)
    relevant_rows: List[Dict[str, Any]] = []

    def _build_row(item: Dict[str, Any]) -> Dict[str, Any]:
        rec = state.get(_row_key(item), {})
        return {
            "input_line_number": item.get("input_line_number", ""),
            "domain":            item["domain"],
            "page_title":        rec.get("page_title", ""),
            "meta_description":  rec.get("meta_description", ""),
            "schema_types":      rec.get("schema_types", ""),
            "keywords_matched":  rec.get("keywords_matched", ""),
            "is_relevant":       rec.get("is_relevant", "UNKNOWN" if not rec else "NO"),
            "confidence":        rec.get("confidence", ""),
            "error_note":        rec.get("error_note", ""),
        }

    tmp = OUTPUT_ALL + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=OUTPUT_FIELDNAMES)
        w.writeheader()
        for item in rows:
            row = _build_row(item)
            w.writerow(row)
            if row["is_relevant"] == "YES":
                relevant_rows.append(row)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, OUTPUT_ALL)

    tmp = OUTPUT_RELEVANT + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=OUTPUT_FIELDNAMES)
        w.writeheader()
        for row in relevant_rows:
            w.writerow(row)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, OUTPUT_RELEVANT)

    print(
        f"Vsetky domeny:  {OUTPUT_ALL}\n"
        f"Pre klienta:    {OUTPUT_RELEVANT}  [{len(relevant_rows)} domen]",
        flush=True,
    )
    _flush_logs()

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _build_headers() -> Dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "cs-CZ,cs;q=0.9,sk;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": random.choice(USER_AGENTS),
    }


def _err_type(e: Exception) -> str:
    if isinstance(e, asyncio.TimeoutError): return "timeout"
    if isinstance(e, aiohttp.ClientConnectorDNSError): return "dns"
    if isinstance(e, aiohttp.ClientSSLError): return "ssl"
    if isinstance(e, aiohttp.ClientConnectorError): return "connect"
    return "request"


class HostThrottle:
    def __init__(self, lo: float, hi: float):
        self._lo, self._hi = lo, hi
        self._last: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, url: str) -> None:
        # ponytail: sleep musí byť mimo zámku — inak blokuje všetky workery
        host = urlparse(url).netloc.lower()
        async with self._lock:
            now = time.monotonic()
            gap = random.uniform(self._lo, self._hi)
            wait_for = self._last.get(host, 0.0) + gap - now
            self._last[host] = now + max(0.0, wait_for) + gap
        if wait_for > 0:
            await asyncio.sleep(wait_for)


async def _fetch(session: aiohttp.ClientSession, throttle: HostThrottle,
                 url: str, timeout_sec: int) -> Tuple[str, str]:
    ssl_arg: Any = False if SSL_INSECURE else None
    last_err = ""
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            await throttle.wait(url)
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ssl=ssl_arg,
                allow_redirects=True,
                headers=_build_headers(),
            ) as resp:
                ct = (resp.headers.get("Content-Type") or "").lower()
                if 200 <= resp.status < 400 and (
                    "text/html" in ct or "application/xhtml+xml" in ct or ct == ""
                ):
                    return await resp.text(errors="ignore"), ""
                return "", f"HTTP {resp.status}"
        except Exception as e:
            last_err = f"{_err_type(e)}: {type(e).__name__}"
            if attempt < FETCH_RETRIES and _err_type(e) in {"timeout", "connect"}:
                await asyncio.sleep(RETRY_DELAY * attempt)
    return "", last_err


async def fetch_homepage(session: aiohttp.ClientSession, throttle: HostThrottle,
                         domain: str) -> Tuple[str, str, str]:
    """Vráti (html, base_url, err). base_url = funkčná URL varianta."""
    last_err = ""
    for base in (f"https://{domain}", f"http://{domain}",
                 f"https://www.{domain}", f"http://www.{domain}"):
        html, err = await _fetch(session, throttle, base, HOMEPAGE_TIMEOUT)
        if html:
            return html, base, ""
        last_err = err
    return "", "", last_err or "all variants failed"


async def fetch_subpages(session: aiohttp.ClientSession, throttle: HostThrottle,
                          base_url: str) -> Optional[Dict[str, str]]:
    """Stiahne bežné podstránky a vráti zlúčený obsah, alebo None ak nič nenašiel."""
    prominent = ""
    body      = ""
    meta_desc = ""
    schema_types = ""
    found = 0
    for path in SUBPAGES:
        url = base_url.rstrip("/") + path
        html, err = await _fetch(session, throttle, url, HOMEPAGE_TIMEOUT)
        if not html:
            continue
        c = extract_content(html)
        prominent    += " " + c["prominent"]
        body         += " " + c["body_text"]
        if not meta_desc:    meta_desc    = c["meta_desc"]
        if not schema_types: schema_types = c["schema_types"]
        found += 1
        if len(body) > 10000:
            break
    if not found:
        return None
    return {
        "title":        "",
        "meta_desc":    meta_desc,
        "schema_types": schema_types,
        "prominent":    prominent.strip()[:6000],
        "body_text":    body.strip()[:10000],
    }

# ── Extrakcia obsahu ──────────────────────────────────────────────────────────

def _schema_types(soup: BeautifulSoup) -> List[str]:
    types: List[str] = []
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        def _collect(node: Any) -> None:
            if isinstance(node, dict):
                t = node.get("@type")
                if isinstance(t, str): types.append(t)
                elif isinstance(t, list): types.extend(str(x) for x in t)
                for val in node.values():
                    _collect(val)
            elif isinstance(node, list):
                for item in node:
                    _collect(item)

        _collect(data)
    return types


def extract_content(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_desc = (meta_tag.get("content") or "").strip()[:300] if meta_tag else ""
    schema_str = ", ".join(dict.fromkeys(_schema_types(soup)))
    headings = " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:6])
    prominent = f"{title} {headings}".lower()
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    body_text = soup.get_text(" ", strip=True).lower()[:5000]
    return {
        "title":        title[:250],
        "meta_desc":    meta_desc,
        "schema_types": schema_str,
        "prominent":    prominent,
        "body_text":    body_text,
    }

# ── Klasifikátor ──────────────────────────────────────────────────────────────

def classify(content: Dict[str, str]) -> Tuple[str, str, str]:
    prominent = content["prominent"]
    body      = content["body_text"]
    meta      = content["meta_desc"].lower()
    score = 0.0
    max_single = 0.0   # najvyssie skore jedineho keyword hitu (bez negativov)
    matched: List[str] = []

    for stype in content["schema_types"].split(", "):
        stype = stype.strip()
        if stype in SCHEMA_SCORES:
            sc, label = SCHEMA_SCORES[stype]
            score += sc
            if sc > max_single: max_single = float(sc)
            matched.append(label)

    for kw, kw_score, label in KEYWORD_RULES:
        in_prominent = kw in prominent
        in_meta      = kw in meta
        in_body      = kw in body
        if kw_score < 0:
            if in_prominent or in_meta or body.count(kw) >= 2:
                score += kw_score
        else:
            if in_prominent:
                contrib = float(kw_score)
                score += contrib
                if contrib > max_single: max_single = contrib
                if label: matched.append(label)
            elif in_meta:
                contrib = kw_score * META_MULT
                score += contrib
                if contrib > max_single: max_single = contrib
                if label: matched.append(label)
            elif in_body:
                contrib = kw_score * BODY_MULT
                score += contrib
                if contrib > max_single: max_single = contrib
                if label: matched.append(label)

    # ponytail: ak najsilnejsi hit < 48 bodov, odmietni cisto akumulativne signaly
    # (napr. malir+zednik+pokryvac v body texte aerobickeho klubu -> false HIGH)
    # Ceiling: legitima strecha/maliar stranka s max ~48 neprejde — upgrade: znizit na 40
    if max_single < 48.0:
        score = min(score, LOW_THRESHOLD - 1)

    seen: set = set()
    unique = [x for x in matched if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]
    kw_str = ", ".join(unique[:6])

    if score >= HIGH_THRESHOLD: return kw_str, "YES", "HIGH"
    if score >= MED_THRESHOLD:  return kw_str, "YES", "MED"
    if score >= LOW_THRESHOLD:  return kw_str, "YES", "LOW"
    return kw_str, "NO", ""

# ── Jedna doména ──────────────────────────────────────────────────────────────

_EMPTY: Dict[str, str] = {
    "title": "", "meta_desc": "", "schema_types": "", "prominent": "", "body_text": "",
}


def _make_result(item: Dict[str, Any], content: Dict[str, str],
                 kw: str, relevant: str, conf: str, err: str) -> Dict[str, Any]:
    return {
        "row_key":           _row_key(item),
        "input_line_number": item.get("input_line_number", ""),
        "domain":            item["domain"],
        "page_title":        content.get("title", ""),
        "meta_description":  content.get("meta_desc", ""),
        "schema_types":      content.get("schema_types", ""),
        "keywords_matched":  kw,
        "is_relevant":       relevant,
        "confidence":        conf,
        "error_note":        err,
    }


async def crawl_one(item: Dict[str, Any], session: aiohttp.ClientSession,
                    throttle: HostThrottle) -> Dict[str, Any]:
    await asyncio.sleep(random.uniform(0, 0.12))
    html, base_url, err = await fetch_homepage(session, throttle, item["domain"])
    if not html:
        return _make_result(item, _EMPTY, "", "UNKNOWN", "", err)
    content = extract_content(html)
    kw, relevant, conf = classify(content)

    # 2. prechod: homepage sa načítala ale skóre nízke → skús podstránky
    if relevant == "NO" and base_url:
        sub = await fetch_subpages(session, throttle, base_url)
        if sub:
            merged: Dict[str, str] = {
                "title":        content["title"],
                "meta_desc":    content["meta_desc"] or sub["meta_desc"],
                "schema_types": ", ".join(filter(None, [content["schema_types"], sub["schema_types"]])),
                "prominent":    (content["prominent"] + " " + sub["prominent"])[:8000],
                "body_text":    (content["body_text"] + " " + sub["body_text"])[:12000],
            }
            kw, relevant, conf = classify(merged)

    return _make_result(item, content, kw, relevant, conf, "")

# ── Writer / workers ──────────────────────────────────────────────────────────

async def writer_loop(q: asyncio.Queue) -> None:
    # ponytail: asyncio.wait_for(q.get(), timeout=0.0) v CPython 3.10 okamzite timeoutuje
    # aj ked fronta nie je prazdna — writer by nikdy neziskal polozky.
    # Pouzivame drain-sleep pattern: get_nowait() v cykle, yield cez sleep.
    # CancelledError pocas sleep -> finally blok flushne zvysok.
    buf: List[Dict[str, Any]] = []
    try:
        while True:
            # Vyprazdni vsetko co je teraz vo fronte (neblokujuce)
            sentinel = False
            while True:
                try:
                    item = q.get_nowait()
                    q.task_done()
                    if item is None:
                        sentinel = True
                        break
                    buf.append(item)
                except asyncio.QueueEmpty:
                    break
            if buf:
                append_progress(buf)
                buf.clear()
            if sentinel:
                break
            # Yield event loopu; CancelledError tu ukonci try blok -> finally flushne
            await asyncio.sleep(0.4)
    finally:
        # Zachrante polozky v buf aj nezkonzumovane z fronty (napr. po Ctrl+C)
        while True:
            try:
                item = q.get_nowait()
                q.task_done()
                if item is None or not isinstance(item, dict):
                    break
                buf.append(item)
            except asyncio.QueueEmpty:
                break
        if buf:
            append_progress(buf)


async def run_workers(rows: List[Dict[str, Any]]) -> None:
    done_map = load_progress_map()
    todo     = [r for r in rows if _row_key(r) not in done_map]
    print(f"\nTotal: {len(rows)} | Hotove: {len(rows)-len(todo)} | Zostava: {len(todo)}", flush=True)
    if not todo:
        print("Vsetko spracovane.", flush=True)
        return

    nq: asyncio.Queue = asyncio.Queue()
    wq: asyncio.Queue = asyncio.Queue()
    for r in todo:
        nq.put_nowait(r)

    conn = aiohttp.TCPConnector(
        limit=CONNECTOR_LIMIT, limit_per_host=CONNECTOR_PER_HOST,
        resolver=aiohttp.ThreadedResolver(), family=socket.AF_INET,
        ttl_dns_cache=600, enable_cleanup_closed=True,
    )
    throttle = HostThrottle(PER_HOST_MIN_DELAY, PER_HOST_MAX_DELAY)
    stop = asyncio.Event()
    counter = {"done": 0}
    started = time.monotonic()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=HOMEPAGE_TIMEOUT, sock_read=HOMEPAGE_TIMEOUT),
        connector=conn,
    ) as session:
        bar = tqdm(total=len(todo), desc="Klasifikacia domen", unit="site",
                   dynamic_ncols=True, mininterval=0.3, leave=True, file=sys.stdout)
        wr = asyncio.create_task(writer_loop(wq))

        async def status_reporter() -> None:
            while not stop.is_set():
                await asyncio.sleep(STATUS_EVERY_SEC)
                d, t = counter["done"], len(todo)
                el = max(time.monotonic() - started, 0.001)
                print(f"[STATUS] {d}/{t} ({d/t*100:.1f}%) | {d/el:.2f} site/s | {el:.0f}s", flush=True)
                bar.refresh()

        status_task = asyncio.create_task(status_reporter())

        async def worker() -> None:
            while not stop.is_set():
                try:
                    it = await asyncio.wait_for(nq.get(), timeout=0.4)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    if stop.is_set(): break
                    continue
                try:
                    rec = await crawl_one(it, session, throttle)
                except Exception as e:
                    _LOG.error("crawl_one %s: %s", it.get("domain"), e)
                    rec = _make_result(it, _EMPTY, "", "UNKNOWN", "", str(e)[:120])
                await wq.put(rec)
                counter["done"] += 1
                bar.update(1)
                nq.task_done()

        pool = [asyncio.create_task(worker()) for _ in range(WORKER_COUNT)]
        try:
            await nq.join()
            await wq.put(None)
            await wq.join()
            await wr
        finally:
            stop.set()
            status_task.cancel()
            try:
                await status_task
            except asyncio.CancelledError:
                pass
            for t in pool:
                t.cancel()
            await asyncio.gather(*pool, return_exceptions=True)
            bar.refresh()
            bar.close()


def prompt_startup() -> None:
    if os.path.exists(PROGRESS_JSONL):
        print(f"\nProgress najdeny: {PROGRESS_JSONL}")
        a = input("Pokracovat [p] alebo zmazat a zacat odznova [z]? [p/z]: ").strip().lower()
        if a == "z":
            for p in (PROGRESS_JSONL, OUTPUT_ALL, OUTPUT_RELEVANT):
                if os.path.exists(p):
                    try: os.remove(p)
                    except OSError as e:
                        print(f"Chyba: {p}: {e}")
                        sys.exit(1)
            print("Progress zmazany.")
        else:
            print("Pokracujem z progress suboru.")
    else:
        for p in (OUTPUT_ALL, OUTPUT_RELEVANT):
            if os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
        print("Novy beh.")


_cached_rows: Optional[List[Dict[str, Any]]] = None


def main() -> None:
    global _cached_rows
    _setup_logging()
    _LOG.info("==== beton_classifier_cz START | input=%s ====", INPUT_FILE)
    prompt_startup()
    rows = load_input_rows()
    _cached_rows = rows
    _cached_rows = rows

    async def _main_async() -> None:
        loop = asyncio.get_running_loop()
        def _silence_conn_reset(loop, ctx):
            if isinstance(ctx.get("exception"), ConnectionResetError):
                return
            loop.default_exception_handler(ctx)
        loop.set_exception_handler(_silence_conn_reset)
        await run_workers(rows)

    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        print("\nPrerusene. Ukladam vysledky ...", flush=True)
    finally:
        try:
            write_output_csvs(rows)
        except Exception as e:
            _LOG.exception("Zapis CSV zlyhal: %s", e)
    print("\nHotovo.", flush=True)


if __name__ == "__main__":
    main()
