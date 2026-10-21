# sending_bot.py

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
from jinja2 import Template
import smtplib
import imaplib
import socket
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imaplib import Time2Internaldate
import json
import csv
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import email.utils
import re
import html
from bs4 import BeautifulSoup  # pip install beautifulsoup4

# ─── ODOSIELATELIA (SMTP / IMAP / podpis) ────────────────────────────────────
# Skutočné prihlasovacie údaje a podpisy sa načítavajú zo súboru SENDERS_CONFIG_FILE
# (senders_config.json), nie z tohto skriptu. Tam sa dopĺňajú heslá a podpisy.
SENDERS_CONFIG_FILE = "senders_config.json"

# Doménový zámok – zabraňuje tomu, aby domény s viacerými adresami dostali
# emaily od dvoch rôznych odosielateľov naraz. Prvý odosielateľ, ktorý danú
# doménu osloví, je jej "vlastníkom" natrvalo (kým sa súbor ručne nevymaže).
DOMAIN_OWNER_FILE = "domain_owner.json"
DOMAIN_OWNER_LOCK_FILE = "domain_owner.lock"

# ─── SÚBORY ──────────────────────────────────────────────────────────────────
CSV_FILE = "universal_marketing_leads_2026_relevant_only_unified_deduplicated.csv"
LEGACY_CSV_FILE = "audit_results_2026_marketing_v4_1.csv"
OTHER_EMAILS_COLUMN = "other_emails"

# Kampane C1–C6
STATE_FILE = "bot_state.json"
LOG_CSV_FILE = "send_log.csv"
LOG_HTML_FILE = "send_log.html"

# Web na prenájom
WNP_TEMPLATE_FILE = "web-na-prenajom-email.html"
WNP_SUBJECT = "Návrh nového webu pre Vašu firmu"
WNP_MAX_ISSUES = 3
C6_MAX_ISSUES = 3
WNP_STATE_FILE = "bot_state_wnp.json"
WNP_LOG_CSV_FILE = "send_log_wnp.csv"
WNP_LOG_HTML_FILE = "send_log_wnp.html"

# C6 samostatný email
C6_COLLAB_TEMPLATE_FILE = "template_c6_collab.html"
C6_COLLAB_SUBJECT = "Námet na spoluprácu v marketingu a podpore predaja"

# Matúš – AI webové riešenie: fixná šablóna bez rozlišovania kampaní C1–C6.
# Šablóna je nezávislá od zvoleného režimu; riadky prideľuje spoločná fronta.
# Individuálny stav a log zostávajú oddelené.
MATUS_AI_TEMPLATE_FILE = "template_matus_ai.html"
MATUS_AI_SUBJECT = "Webové AI riešenie pre Vašu firmu"
MATUS_AI_STATE_FILE = "bot_state_matus_ai.json"
MATUS_AI_LOG_CSV_FILE = "send_log_matus_ai.csv"
MATUS_AI_LOG_HTML_FILE = "send_log_matus_ai.html"

WNP_TEXT_TEMPLATE = """Dobrý deň,

pri návšteve Vášho webu {{ DOMAIN }} som si všimol niekoľko bodov, ktoré môžu zbytočne znižovať jeho viditeľnosť vo vyhľadávaní a celkovo narušiť dobrý dojem pre návštevníkov.

Vybral som tri najsilnejšie:

{{ TOP_ISSUES_TEXT }}

Namiesto zložitého upravovania a prerábania pôvodného webu Vám viem ponúknuť elegantnejšiu cestu.

Môj koncept webu na prenájom je dostupný už od 35 € mesačne, čo predstavuje zlomok ceny klasického vývoja webu na mieru.

V praxi to znamená, že Vám viem v krátkom čase pripraviť nový web alebo redizajn staršej stránky, ktorá bude moderná, rýchla, pripravená pre mobilné zariadenia, SEO optimalizovaná a zároveň Vás nezaťaží vysokou jednorazovou investíciou.

V cene máte zároveň zahrnutý hosting, správu webu, pravidelné zálohovanie a kompletnú starostlivosť o Vás a Váš web, takže sa nemusíte zaoberať technickými vecami a môžete sa sústrediť na svoje podnikanie.

Ak by Vám takýto model dával zmysel, rád Vám pripravím nezáväznú ukážku riešenia priamo pre Vašu firmu.

Ak by Vás to nezaujalo, pokojne mi stačí odpísať, že si ďalšiu správu neželáte.

S úctou,

{{ SENDER_NAME }}
{{ SENDER_TITLE }}
in salelogics.sk | {{ SENDER_PHONE }}
Webstránky s AI webnaprenajom.sk

Salelogics Group s. r. o.
Čingovská 1380/10
Košice 040 01 | IČO: 57506191

Tento e-mail bol zaslaný na verejne dostupný firemný kontakt v súvislosti s Vašou podnikateľskou činnosťou. Ak si neželáte dostávať ďalšie obchodné ponuky, odpovedzte na tento e-mail textom „STOP“. Váš kontakt bezodkladne vyradíme z ďalšej komunikácie.
"""

C6_TEXT_TEMPLATE = """Dobrý deň,

pri prezeraní webu {{ DOMAIN }} som si všimol niekoľko konkrétnych vecí, ktoré dnes zbytočne brzdia jeho technickú kvalitu, viditeľnosť a schopnosť získavať dopyty.

Vybral som tri najsilnejšie:

{{ TOP_ISSUES_TEXT }}

Tieto úpravy viem riešiť priebežne tak, aby web nebol len vizitka, ale reálne pomáhal získavať viac zákazníkov. Rád Vám pripravím nezáväzný návrh konkrétnych krokov priamo pre Váš web.

Ak by Vás to nezaujalo, pokojne mi stačí odpísať, že si ďalšiu správu neželáte.

S úctou,

{{ SENDER_NAME }}
{{ SENDER_TITLE }}
in salelogics.sk | {{ SENDER_PHONE }}
salelogics.sk

Salelogics Group s. r. o.
Čingovská 1380/10
Košice 040 01 | IČO: 57506191

Tento e-mail bol zaslaný na verejne dostupný firemný kontakt v súvislosti s Vašou podnikateľskou činnosťou. Ak si neželáte dostávať ďalšie obchodné ponuky, odpovedzte na tento e-mail textom „STOP“. Váš kontakt bezodkladne vyradíme z ďalšej komunikácie.
"""

# ─── KONFIGURÁCIA ────────────────────────────────────────────────────────────
LOCAL_TZ = ZoneInfo("Europe/Bratislava")

# Prázdna množina = všetky kampane C1–C6; inak napr. {"C1", "C3"}
ACTIVE_CAMPAIGNS = set()

GLOBAL_BLUE_URL = "https://boost.salelogics.sk/"
GLOBAL_BLUE_LABEL = "Zistiť potenciál stránky"

TEST_RECIPIENT = os.environ.get("TEST_RECIPIENT", "")
TEST_DOMAIN = "www.salelogics.sk"

# ─── RATE LIMIT ──────────────────────────────────────────────────────────────
MAX_EMAILS_PER_HOUR = 190       # tvrdý strop – nikdy neprekročiť
DEFAULT_EMAILS_PER_HOUR = 180
WINDOW_LIMIT = 190              # max emailov v 59-min. okne (pôvodne 199)
WINDOW_MINUTES = 59

# Sieťové operácie nesmú zablokovať odosielaciu slučku na neurčito.
NETWORK_TIMEOUT_SECONDS = 30
MIN_GLOBAL_SEND_SECONDS = 8.0
MIN_GAP_AFTER_SEND_SECONDS = 2.0  # poistka: najmenej toľko od skončenia predchádzajúceho pokusu

LOCK_FILE = "bot_running.lock"


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERAKTÍVNE MENU
# ═══════════════════════════════════════════════════════════════════════════════

def choose_mode() -> str:
    """
    Vráti:
    - 'wnp_plain' / 'wnp_html' / 'campaigns' – POSIELANIE (najprv fronta nedoručených, potom nové riadky)
    - 'overview' – report bez odosielania
    - 'bounces'  – spracovanie vrátených správ (nič sa neodosiela)
    """
    width = 62
    row = lambda text="": "║" + text.ljust(width) + "║"
    lines = [
        "",
        "╔" + "═" * width + "╗",
        row("            EMAIL SENDING BOT – výber režimu"),
        "╠" + "═" * width + "╣",
        row("  POSIELANIE (1, 2, 3):"),
        row("   1  →  Web na prenájom – PLAIN TEXT"),
        row("   2  →  Web na prenájom – HTML"),
        row("   3  →  Kampane C1–C6"),
        row("      Najprv sa pošle fronta nedoručených (BOUNCED), potom"),
        row("      pokračuje od uloženej pozície v JSON. Žiadne duplicity."),
        row(),
        row("  4  →  Prehľad / Report (bez odosielania)"),
        row(),
        row("  5  →  Spracovať bounce-y (NIČ SA NEODOSIELA)"),
        row("      Zistí, na ktoré adresy odoslané botom sa nedoručilo,"),
        row("      a zaradí ich do fronty. Cudzia pošta sa ignoruje."),
        "╚" + "═" * width + "╝",
    ]
    print("\n".join(lines))

    while True:
        choice = input("\nZadaj číslo (1, 2, 3, 4 alebo 5): ").strip()

        if choice == "1":
            print("\n✔  Zvolený režim: Web na prenájom – PLAIN TEXT\n")
            return "wnp_plain"
        elif choice == "2":
            print("\n✔  Zvolený režim: Web na prenájom – HTML\n")
            return "wnp_html"
        elif choice == "3":
            print("\n✔  Zvolený režim: Kampane C1–C6\n")
            return "campaigns"
        elif choice == "4":
            print("\n✔  Zvolený režim: Prehľad / Report\n")
            return "overview"
        elif choice == "5":
            print("\n✔  Zvolený režim: Spracovanie bounce-ov\n")
            return "bounces"
        else:
            print("  ⚠  Neplatná voľba. Zadaj 1, 2, 3, 4 alebo 5.")


def choose_test_campaign() -> str:
    """Spýta sa, ktorú kampaň použiť pri testovacom emaili."""
    print("\n  Vyber kampaň pre testovací email:")
    for i, c in enumerate(["C1", "C2", "C3", "C4", "C5", "C6"], 1):
        print(f"    {i}  →  {c}")
    mapping = {"1": "C1", "2": "C2", "3": "C3", "4": "C4", "5": "C5", "6": "C6"}
    while True:
        choice = input("  Číslo kampane (1–6): ").strip()
        if choice in mapping:
            selected = mapping[choice]
            print(f"  ✔  Kampaň: {selected}\n")
            return selected
        print("  ⚠  Neplatná voľba.")


def ask_yes_no(prompt: str) -> bool:
    """Interaktívna otázka a/n. Vráti True = áno."""
    while True:
        ans = input(prompt + " (a/n): ").strip().lower()
        if ans in ("a", "y", "ano", "yes"):
            return True
        if ans in ("n", "nie", "no"):
            return False
        print("  ⚠  Zadaj 'a' (áno) alebo 'n' (nie).")


def ask_emails_per_hour(label: str, default_rate: int, max_rate: int):
    """Spýta sa na tempo odosielania pre daného odosielateľa. Vráti (rate, delay_seconds)."""
    max_rate = min(max_rate, MAX_EMAILS_PER_HOUR)
    raw = input(
        f"\n[{label}] Koľko emailov za hodinu chceš odoslať? "
        f"(Enter = {default_rate}, max {max_rate}): "
    ).strip()

    if not raw:
        rate = default_rate
    else:
        try:
            rate = int(raw)
        except ValueError:
            print(f"  ⚠  Neplatné číslo – používam predvolené {default_rate}/hod.")
            rate = default_rate

    if rate < 1:
        rate = 1
    if rate > max_rate:
        print(f"  ⚠  Limit je {max_rate}/hod – nastavujem na {max_rate}.")
        rate = max_rate

    delay = 3600.0 / rate
    print(f"  ✔  [{label}] Rozostup medzi emailmi: {delay:.1f} s (~{rate} emailov/hod).")
    return rate, delay


def ask_session_limit(label: str):
    """Spýta sa na max. počet emailov v tomto behu pre daného odosielateľa. None = bez limitu."""
    raw = input(
        f"  [{label}] Koľko emailov max poslať v tomto behu? (Enter = bez limitu): "
    ).strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        print("  ⚠  Neplatné číslo – beriem ako bez limitu.")
        return None
    return n if n > 0 else None


def load_sender_profiles() -> dict:
    """Načíta odosielateľov (SMTP/IMAP prihlásenie, podpis) zo súboru SENDERS_CONFIG_FILE."""
    if not os.path.exists(SENDERS_CONFIG_FILE):
        raise FileNotFoundError(
            f"\nSúbor '{SENDERS_CONFIG_FILE}' nebol nájdený!\n"
            "Vytvor ho v adresári bota s prihlasovacími údajmi a podpisom pre každého odosielateľa."
        )
    with open(SENDERS_CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for key, profile in data.items():
        profile["key"] = key
        profile.setdefault("emails_per_hour_default", DEFAULT_EMAILS_PER_HOUR)
        profile.setdefault("emails_per_hour_max", MAX_EMAILS_PER_HOUR)
        profile.setdefault("imap_sent_folder", "Sent")
    return data


def choose_senders(profiles: dict) -> list:
    """Spýta sa, z ktorých adries (jednej, viacerých, alebo všetkých naraz) sa má posielať."""
    keys = list(profiles.keys())
    print("\nOdosielatelia:")
    for i, k in enumerate(keys, 1):
        p = profiles[k]
        print(f"  {i}  →  {p['from_name']} <{p['from_addr']}>")
    all_choice = len(keys) + 1
    print(f"  {all_choice}  →  Všetci naraz (paralelne)")

    while True:
        raw = input(
            f"\nVyber odosielateľa/ov (napr. '1', alebo '1,2', alebo '{all_choice}' pre všetkých): "
        ).strip()

        if raw == str(all_choice):
            return keys

        picks = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            idxs = [int(p) for p in picks]
        except ValueError:
            idxs = []

        if idxs and all(1 <= i <= len(keys) for i in idxs):
            seen, result = set(), []
            for i in idxs:
                k = keys[i - 1]
                if k not in seen:
                    seen.add(k)
                    result.append(k)
            return result

        print("  ⚠  Neplatná voľba.")


def show_database_overview(df: pd.DataFrame, state: dict, log_csv: str,
                           emails_per_hour: int, label: str = "", queue: int = 0) -> None:
    """Prehľad pred ostrým odosielaním: čo je doručené, čo čaká vo fronte nedoručených a odkiaľ sa pokračuje."""
    total_rows = len(df)
    start_index = state.get("start_index", 0)

    delivered = invalid = 0
    if os.path.exists(log_csv):
        try:
            latest = scan_log_latest(log_csv)
            delivered = sum(1 for v in latest.values() if v[0] == "OK")
            invalid = sum(1 for v in latest.values() if v[0] == "INVALID")
        except Exception:
            pass

    remaining_rows = max(0, total_rows - start_index)
    per_mail = 3600.0 / emails_per_hour
    queue_seconds = queue * per_mail
    est_seconds = remaining_rows * per_mail
    fmt = lambda sec: f"~{int(sec // 3600)} h {int((sec % 3600) // 60)} min"

    title = f"PREHĽAD – {label}" if label else "PREHĽAD"
    print(f"""
============================================================
  {title}
============================================================
  Doručené (posledný stav OK):              {delivered}
  Neexistujúce adresy (INVALID):            {invalid}
  1) FRONTA NEDORUČENÝCH (pošle sa ako prvá): {queue} domén, {fmt(queue_seconds)}
  2) Potom nové riadky CSV od pozície:      {start_index} z {total_rows} (zostáva ~{remaining_rows})
  Tempo: {emails_per_hour} emailov/hod (limit {MAX_EMAILS_PER_HOUR}/hod); celé CSV by trvalo {fmt(est_seconds)}
  Duplicity: kontakt ani doména so stavom OK/INVALID/FAILED/REJECTED sa nikdy nepošle znova.
============================================================""")


def acquire_lock() -> bool:
    """Zabráni dvojitému spusteniu bota (napr. omylom 2x dvojklik na .bat)."""
    try:
        with open(LOCK_FILE, "x", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        print(f"\n  ⚠  Bot je zamknutý súborom '{LOCK_FILE}'.")
        print("     Po páde ho odstráň až po overení, že pôvodný proces už nebeží.")
        return False
    except OSError as error:
        print(f"  ✘  Nedá sa bezpečne zamknúť bot: {error}")
        return False
    return True


def release_lock() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  DOMÉNOVÝ ZÁMOK (viac odosielateľov naraz bez kolízií)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Pravidlo: ak doména má viac emailových adries a jednu z nich už oslovil
# odosielateľ A, všetky ďalšie adresy tej istej domény musia ísť tiež od A –
# nikdy od B. Vlastníctvo domény sa zapisuje do zdieľaného DOMAIN_OWNER_FILE,
# ku ktorému sa pristupuje cez exkluzívny súborový zámok, takže aj keď bežia
# dvaja odosielatelia paralelne (vlákna alebo procesy), nikdy sa "nepobijú"
# pri zápise a nikdy neprepíšu rozhodnutie toho druhého.

def _acquire_file_lock(lock_path: str, timeout: float = 15.0):
    start = time.time()
    while True:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock_path) > 30:
                    os.remove(lock_path)  # zámok "zabudnutý" po páde procesu
                    continue
            except OSError:
                pass
            if time.time() - start > timeout:
                raise TimeoutError(f"Nepodarilo sa získať zámok '{lock_path}'.")
            time.sleep(0.05)


def _release_file_lock(fd, lock_path: str) -> None:
    try:
        os.close(fd)
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _load_domain_owners(owner_path: str) -> dict:
    if not os.path.exists(owner_path):
        return {}
    with open(owner_path, "r", encoding="utf-8") as f:
        owners = json.load(f)
    if not isinstance(owners, dict):
        raise ValueError(f"Neplatný register domén {owner_path}")
    return owners


def _save_domain_owners(owners: dict, owner_path: str) -> None:
    tmp_path = owner_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(owners, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, owner_path)


# In-process cache pre domain_owner.json. Pri veľkej CSV databáze (státisíce
# riadkov) by čítanie a parsovanie celého JSON súboru pri KAŽDOM riadku bolo
# neúnosne pomalé. Cache sa invaliduje podľa mtime súboru (lacný os.stat),
# takže sa reálne znova načíta len keď ho zmenil iný odosielateľ/vlákno.
_domain_owner_cache_guard = threading.Lock()
_domain_owner_cache = {"path": None, "mtime": None, "data": {}}


def _cached_domain_owners(owner_path: str) -> dict:
    with _domain_owner_cache_guard:
        try:
            mtime = os.path.getmtime(owner_path)
        except OSError:
            mtime = None
        if not (_domain_owner_cache["path"] == owner_path and _domain_owner_cache["mtime"] == mtime):
            _domain_owner_cache["data"] = _load_domain_owners(owner_path)
            _domain_owner_cache["path"] = owner_path
            _domain_owner_cache["mtime"] = mtime
        return _domain_owner_cache["data"]


def _update_domain_owner_cache(owner_path: str, owners: dict) -> None:
    with _domain_owner_cache_guard:
        _domain_owner_cache["data"] = owners
        _domain_owner_cache["path"] = owner_path
        try:
            _domain_owner_cache["mtime"] = os.path.getmtime(owner_path)
        except OSError:
            _domain_owner_cache["mtime"] = None


def claim_domain(domain: str, sender_email: str, owner_path: str, lock_path: str,
                 previous_owner: str | None = None) -> bool:
    """
    Skúsi 'zabrať' doménu pre sender_email. Vráti True, ak doménu už vlastní
    tento istý odosielateľ (alebo ju práve teraz zabral), False ak ju vlastní iný.
    previous_owner povoľuje atomický prevod pre overenú neodoslanú rezerváciu.
    """
    # Rýchla cesta: doména je už v cache (bez čítania súboru zo zámkom).
    cached = _cached_domain_owners(owner_path)
    existing = cached.get(domain)
    if existing is not None and (existing == sender_email or existing != previous_owner):
        return existing == sender_email

    # Nové alebo prevoditeľné vlastníctvo overíme/zapíšeme pod zámkom.
    fd = _acquire_file_lock(lock_path)
    try:
        owners = _load_domain_owners(owner_path)
        current_owner = owners.get(domain)
        if current_owner is None or (previous_owner is not None and current_owner == previous_owner):
            owners[domain] = sender_email
            _save_domain_owners(owners, owner_path)
            _update_domain_owner_cache(owner_path, owners)
            return True
        _update_domain_owner_cache(owner_path, owners)
        return current_owner == sender_email
    finally:
        _release_file_lock(fd, lock_path)


def bootstrap_domain_owners_from_legacy_logs(owner_path: str, lock_path: str,
                                             legacy_log_files: list, default_owner_email: str) -> None:
    """
    Pri prvom spustení po zavedení viacerých odosielateľov: domény, ktoré už
    boli v minulosti úspešne oslovené (staré logy pred zavedením tejto funkcie,
    kde posielal iba jeden účet), sa priradia tomuto pôvodnému odosielateľovi,
    aby ich druhý odosielateľ nikdy neoslovil znova.
    """
    fd = _acquire_file_lock(lock_path)
    try:
        owners = _load_domain_owners(owner_path)
        changed = False
        for log_file in legacy_log_files:
            if not os.path.exists(log_file):
                continue
            try:
                log_df = pd.read_csv(log_file, encoding="utf-8-sig")
            except Exception:
                continue
            if "domain" not in log_df.columns or "status" not in log_df.columns:
                continue
            ok_domains = log_df.loc[log_df["status"] == "OK", "domain"].dropna().unique()
            for d in ok_domains:
                d = str(d).strip()
                if d and d not in owners:
                    owners[d] = default_owner_email
                    changed = True
        if changed:
            _save_domain_owners(owners, owner_path)
    finally:
        _release_file_lock(fd, lock_path)


def sender_paths(sender_key: str, base_state: str, base_log_csv: str, base_log_html: str):
    """Súbory stavu/logu pre daného odosielateľa. Pôvodný odosielateľ 'maros' si
    ponecháva pôvodné názvy súborov (nadväzuje na doterajšiu históriu posielania)."""
    if sender_key == "maros":
        return base_state, base_log_csv, base_log_html
    stem_state, ext_state = os.path.splitext(base_state)
    stem_csv, ext_csv = os.path.splitext(base_log_csv)
    stem_html, ext_html = os.path.splitext(base_log_html)
    return (
        f"{stem_state}_{sender_key}{ext_state}",
        f"{stem_csv}_{sender_key}{ext_csv}",
        f"{stem_html}_{sender_key}{ext_html}",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  OVEROVANIE FUNKČNOSTI EMAILOVEJ ADRESY (pred odoslaním obsahu)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Zámerne sa NEPRIPÁJAME priamo z tohto PC na cudzie mailservery (MX príjemcu) –
# vyskúšali sme to a takéto priame sondovanie rýchlo dostalo verejnú IP tohto
# stroja na spamové blacklisty (Barracuda BRBL a i.) a spôsobovalo falošné
# "adresa neexistuje" (SPF/reputation zamietnutia nesúvisiace s existenciou
# schránky). Namiesto toho sa neexistujúca adresa zachytáva PRIAMO v reálnom
# pokuse o odoslanie cez náš vlastný, autorizovaný relay (smtp.hostcreators.sk):
# smtplib.sendmail() interne robí RCPT TO a ak PRÍJEMCA server odmietne, vyhodí
# SMTPRecipientsRefused ešte PRED odoslaním obsahu (DATA) – pozri _smtp_send_one.
# Táto cesta je bezpečná (ide cez legitímnu, už autorizovanú infraštruktúru) aj
# keď o niečo menej "agresívna" (zachytí len to, čo aj reálny pokus o odoslanie).


# ═══════════════════════════════════════════════════════════════════════════════
#  NAČÍTANIE ŠABLÓN
# ═══════════════════════════════════════════════════════════════════════════════

def load_campaign_templates() -> dict:
    templates = {}
    for code in ["C1", "C2", "C3", "C4", "C5", "C6"]:
        filename = f"template_{code.lower()}.html"
        try:
            with open(filename, "r", encoding="utf-8") as f:
                templates[code] = f.read()
        except FileNotFoundError:
            pass
    return templates


def load_wnp_template() -> str:
    if not os.path.exists(WNP_TEMPLATE_FILE):
        raise FileNotFoundError(
            f"\nŠablóna '{WNP_TEMPLATE_FILE}' nebola nájdená!\n"
            "Uisti sa, že súbor existuje v rovnakom adresári ako bot."
        )
    with open(WNP_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_c6_collab_template() -> str:
    if not os.path.exists(C6_COLLAB_TEMPLATE_FILE):
        raise FileNotFoundError(
            f"\nŠablóna '{C6_COLLAB_TEMPLATE_FILE}' nebola nájdená!\n"
            "Uisti sa, že súbor existuje v rovnakom adresári ako bot."
        )
    with open(C6_COLLAB_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_matus_ai_template() -> str:
    if not os.path.exists(MATUS_AI_TEMPLATE_FILE):
        raise FileNotFoundError(
            f"\nŠablóna '{MATUS_AI_TEMPLATE_FILE}' nebola nájdená!\n"
            "Uisti sa, že súbor existuje v rovnakom adresári ako bot."
        )
    with open(MATUS_AI_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════════════════
#  SPRÁVA STAVU
# ═══════════════════════════════════════════════════════════════════════════════

def load_state(state_file: str) -> dict:
    if not os.path.exists(state_file):
        return {
            "last_index": -1,
            "start_index": 0,
            "sent_count_window": 0,
            "window_start": None
        }
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return {
            "last_index": -1,
            "start_index": 0,
            "sent_count_window": 0,
            "window_start": None
        }
    state.setdefault("start_index", 0)
    state.setdefault("sent_count_window", 0)
    state.setdefault("window_start", None)
    return state


def save_state(state: dict, state_file: str) -> None:
    tmp_path = state_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, state_file)


def migrate_state_to_csv(state: dict, df: pd.DataFrame, csv_file: str = CSV_FILE) -> tuple[dict, bool]:
    """Prepne stav na nový CSV bez zmeny číselnej pozície.

    ``start_index`` je fyzický index v súbore, ktorý používateľ vložil do
    pracovného adresára. Nový súbor sa nesmie triediť ani mapovať cez pomocné
    merge stĺpce; pri prechode zachováme presne číslo z existujúceho stavu.
    """
    current_file = os.path.basename(csv_file)
    if state.get("csv_file") == current_file:
        return state, False

    old_start = state.get("start_index", 0)
    try:
        old_start = int(old_start)
    except (TypeError, ValueError):
        old_start = 0

    state["previous_csv_file"] = state.get("csv_file") or LEGACY_CSV_FILE
    state["previous_start_index"] = old_start
    state["csv_file"] = current_file
    state["start_index"] = old_start
    state["last_index"] = old_start - 1
    return state, True


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGOVANIE
# ═══════════════════════════════════════════════════════════════════════════════

def append_logs(new_records: list, log_csv: str, log_html: str) -> None:
    """
    Rýchly O(1) zápis – dopisuje len nové riadky na koniec CSV, nečíta ani
    neprepisuje celý súbor. Predtým sa pri KAŽDOM zázname (aj INVALID) celý
    log CSV znova načítal, prepísal a k tomu sa pri každom zápise nanovo
    vyrenderovalo aj celé HTML (pri desiatkach tisíc riadkov to trvalo
    rádovo sekundy na jeden email a spomaľovalo to reálnu priepustnosť
    odosielania). HTML náhľad sa teraz generuje len na požiadanie
    (pozri regenerate_log_html), nie pri každom emaili.
    """
    if not new_records:
        return
    new_df = pd.DataFrame(new_records)
    file_exists = os.path.exists(log_csv)
    encoding = "utf-8" if file_exists else "utf-8-sig"
    new_df.to_csv(log_csv, mode="a", header=not file_exists, index=False, encoding=encoding)


def regenerate_log_html(log_csv: str, log_html: str) -> bool:
    """Na požiadanie prerenderuje HTML náhľad z aktuálneho CSV logu (nepoužíva sa automaticky)."""
    if not os.path.exists(log_csv):
        return False
    try:
        df = pd.read_csv(log_csv, encoding="utf-8-sig")
    except Exception:
        return False
    with open(log_html, "w", encoding="utf-8") as f:
        f.write(df.to_html(index=False, escape=False))
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#  POMOCNÉ FUNKCIE
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = (
        df.columns
        .map(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", "_", regex=True)
    )

    rename_map = {
        "personalizeddetailshtml": "personalized_details_html",
        "personalized_detailshtml": "personalized_details_html",
        "personalizeddetails_html": "personalized_details_html",
        "personalizedsummary": "personalized_summary",
        "blueurl": "blue_url",
        "email": "emails",
        "email_address": "emails",
    }
    df = df.rename(columns=rename_map)
    return df


def clean_csv_value(value) -> str:
    """Vráti prázdny text aj pre pandas NaN/NA hodnoty."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def row_campaign(row) -> str:
    """Načíta kampaň z nového alebo kompatibilného názvu stĺpca."""
    for column in ("campaign", "campaign_code", "new_campaign"):
        value = clean_csv_value(row.get(column, ""))
        if not value:
            continue
        match = re.search(r"\bC([1-6])\b", value.upper())
        if match:
            return f"C{match.group(1)}"
    return ""


def _split_issue_values(value: str) -> list[str]:
    parts = re.split(r"\s*\|\s*|\s*;\s*|\r?\n+", value)
    result = []
    seen = set()
    for part in parts:
        item = " ".join(html.unescape(part).split()).strip(" ,.;:-")
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def build_personalized_details_html(row) -> str:
    """Zjednotí starý slovenský a nový metrický formát chýb do HTML zoznamu."""
    existing = clean_csv_value(row.get("personalized_details_html", ""))
    merge_source = clean_csv_value(row.get("merge_source", "")).lower()
    metric_source = clean_csv_value(row.get("merge_metric_source", "")).lower()
    prefer_new_issues = merge_source == "new" or metric_source == "new"

    issue_columns = (
        "selected_issues",
        "new_issues_ranked",
        "new_top_priorities",
        "new_technical_issues",
        "new_modernity_issues",
        "tech_issues",
        "seo_issues",
        "security_issues",
        "performance_issues",
        "issues",
    )

    if prefer_new_issues:
        preferred = issue_columns
    else:
        preferred = ("personalized_details_html",) + issue_columns

    for column in preferred:
        value = clean_csv_value(row.get(column, ""))
        if not value:
            continue
        if column == "personalized_details_html":
            soup = BeautifulSoup(html.unescape(value), "html.parser")
            if soup.find("li"):
                return value
            issues = _split_issue_values(soup.get_text(" ", strip=True))
        else:
            issues = _split_issue_values(value)
        if issues:
            items = "".join(f"<li>{html.escape(item)}</li>" for item in issues)
            return f"<ul>{items}</ul>"

    return "<ul><li>Na Vašej doméne sme identifikovali viacero bodov na zlepšenie.</li></ul>"


def extract_all_emails(emails_field: str) -> list:
    if not isinstance(emails_field, str):
        return []
    text = emails_field.strip()
    text = text.replace("[", " ").replace("]", " ")
    text = text.replace("mailto:", "").replace("MAILTO:", "")
    found = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    seen, cleaned = set(), []
    for e in found:
        e = e.strip()
        if e and e not in seen:
            seen.add(e)
            cleaned.append(e)
    return cleaned


_SUPPRESSION_EMAIL_COLUMNS = (
    "new_removed_blacklist",
    "new_removed_competitor",
    "merge_removed_emails",
)

_PUBLIC_EMAIL_HOSTS = {
    "akussr.sk",
    "financnasprava.sk",
    "minv.sk",
    "mpsvr.sk",
    "nbs.sk",
    "pamiatky.sk",
    "slovensko.sk",
    "soi.sk",
    "statistics.sk",
    "upsvr.sk",
}

_BANK_EMAIL_HOSTS = {
    "365.bank",
    "csob.sk",
    "fio.sk",
    "mbank.sk",
    "nbs.sk",
    "slsp.sk",
    "tatrabanka.sk",
    "unicreditbank.sk",
    "vub.sk",
}

_HOSTING_EMAIL_HOST_TOKENS = (
    "active24",
    "coolhosting",
    "exohost",
    "forpsi",
    "hostcreators",
    "hosting",
    "liohosting",
    "serverhosting",
    "vas-hosting",
    "web4u",
    "webglobe",
    "webhost",
    "websupport",
)

_PUBLIC_LOCALPART_RE = re.compile(
    r"^(?:podatelna|podatelňa|urad|úrad|obec|obecny|obecný|starosta|"
    r"primator|primátor|minister|ministry|policia|polícia|inspekcia|"
    r"inšpekcia|registratura|registratúra)(?:[+._-]|$)",
    re.IGNORECASE,
)

_BANK_HOST_RE = re.compile(r"(?:^|[.-])(bank|banka)(?:[.-]|$)", re.IGNORECASE)

_AGENCY_MARKER_RE = re.compile(
    r"(?:agency|agentura|agentúra|digitalagency|marketing|seo|webdesign|"
    r"webstudio|web[-_ ]?dev|webdeveloper|tvorbaweb|reklam|creative|"
    r"socialmedia|online[-_ ]?marketing)",
    re.IGNORECASE,
)


def suppressed_emails_from_row(row) -> set[str]:
    """Načíta adresy, ktoré dataset už označil ako vyradené."""
    suppressed = set()
    for column in _SUPPRESSION_EMAIL_COLUMNS:
        for email_addr in extract_all_emails(clean_csv_value(row.get(column, ""))):
            suppressed.add(email_addr.lower())
    return suppressed


def email_suppression_reason(email_addr: str, row, dataset_suppressed: set[str] | None = None) -> str | None:
    """Vráti dôvod vyradenia adresy alebo None, ak je bezpečná pre sending."""
    normalized = clean_csv_value(email_addr).lower()
    if not normalized or "@" not in normalized:
        return "invalid_email"

    if dataset_suppressed is None:
        dataset_suppressed = suppressed_emails_from_row(row)
    if normalized in dataset_suppressed:
        return "dataset_suppression"

    localpart, host = normalized.rsplit("@", 1)
    host = host.rstrip(".")

    competitor_flag = clean_csv_value(row.get("new_is_competitor", "")).lower()
    if competitor_flag in {"true", "yes", "y", "1", "competitor"}:
        return "competitor_flag"

    if (
        host in _PUBLIC_EMAIL_HOSTS
        or host.endswith((".gov.sk", ".gov.cz", ".gov.hu", ".gov.pl", ".mil.sk"))
        or host.startswith(("gov.", "edu.", "mil."))
        or _PUBLIC_LOCALPART_RE.search(localpart)
    ):
        return "public_authority"

    if host in _BANK_EMAIL_HOSTS or _BANK_HOST_RE.search(host):
        return "bank"

    if any(token in host for token in _HOSTING_EMAIL_HOST_TOKENS):
        return "hosting_provider"

    if _AGENCY_MARKER_RE.search(f"{localpart}@{host}"):
        return "agency_or_marketing_competitor"

    return None


def email_candidates_for_row(row) -> list:
    """Zostaví unikátny zoznam emailov v poradí relevance pre daný riadok."""
    candidates = []
    seen = set()
    dataset_suppressed = suppressed_emails_from_row(row)
    for column in (
        "new_best_email",
        "new_email_details",
        "new_emails_final",
        "new_visible_email",
        "emails",
        OTHER_EMAILS_COLUMN,
    ):
        for email_addr in extract_all_emails(clean_csv_value(row.get(column, ""))):
            normalized = email_addr.lower()
            if (
                normalized not in seen
                and email_suppression_reason(email_addr, row, dataset_suppressed) is None
            ):
                seen.add(normalized)
                candidates.append(email_addr)
    return candidates


def select_primary_email(row) -> tuple[str, list[str]]:
    """Vráti jednu najrelevantnejšiu adresu a zvyšné adresy zvlášť."""
    candidates = email_candidates_for_row(row)
    if not candidates:
        return "", []
    return candidates[0], candidates[1:]


def html_to_plain_text(html_body: str) -> str:
    if not isinstance(html_body, str):
        return ""
    soup = BeautifulSoup(html_body, "html.parser")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        a.replace_with(f"{text} ({href})" if href else text)
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text(separator="\n")
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def split_issue_title_and_desc(text: str):
    text = " ".join((text or "").split()).strip()
    if not text:
        return "", "", ""

    separators = [
        " tak, aby ",
        " tak aby ",
        " pre lepšie ",
        " pre lepšiu ",
        " pre bezpečnejšie ",
        " pre správne ",
        " pre rýchlejšie ",
        " – ",
        " - ",
        ", aby ",
    ]

    for sep in separators:
        if sep in text:
            left, right = text.split(sep, 1)
            left = left.strip(" ,.;:-")
            right = right.strip(" ,.;:-")
            if left and right:
                return left, sep.strip(), right

    return text, "", ""


def limit_issue_list(details_html: str, max_items: int = 5) -> str:
    if not isinstance(details_html, str) or not details_html.strip():
        return ""

    details_html = html.unescape(details_html)
    soup = BeautifulSoup(details_html, "html.parser")
    ul = soup.find("ul")

    if not ul:
        return details_html

    ul["style"] = "margin: 0 0 24px 18px; padding: 0; list-style-type: disc; color: #333333;"

    lis = ul.find_all("li")
    if not lis:
        return details_html

    for li in lis[max_items:]:
        li.decompose()

    for li in ul.find_all("li"):
        li["style"] = "font-size: 15px; color: #333333; line-height: 1.7; margin-bottom: 8px;"
        text = " ".join(li.get_text(" ", strip=True).split())
        if not text:
            continue

        title, sep, desc = split_issue_title_and_desc(text)
        li.clear()

        strong = soup.new_tag("strong")
        strong["style"] = "color:#111111;"
        strong.string = title
        li.append(strong)

        if sep and desc:
            if sep in ["–", "-"]:
                li.append(f" {sep} {desc}")
            else:
                li.append(f" {sep} {desc}")

    return str(ul)


def html_list_to_bullets(details_html: str, max_items: int = 5) -> str:
    if not isinstance(details_html, str) or not details_html.strip():
        return "- Na Vašej doméne sme identifikovali viacero bodov na zlepšenie."

    details_html = html.unescape(details_html)
    soup = BeautifulSoup(details_html, "html.parser")
    items = []

    for li in soup.find_all("li")[:max_items]:
        txt = " ".join(li.get_text(" ", strip=True).split())
        if txt:
            items.append(f"- {txt}")

    if not items:
        return "- Na Vašej doméne sme identifikovali viacero bodov na zlepšenie."
    return "\n".join(items)


def create_mime_message(sender: dict, to_email: str, subject: str, text_body: str, html_body: str | None = None):
    if html_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(text_body, "plain", "utf-8")

    msg["Subject"] = subject
    # Kódovať iba meno, nie celé "Meno <adresa>" – inak relay nevidí v From žiadnu
    # adresu a prepíše ju na "...@smtp.hostcreators.sk" (DMARC fail + spam zamietnutia).
    msg["From"] = email.utils.formataddr((sender["from_name"], sender["from_addr"]), charset="utf-8")
    msg["To"] = to_email
    msg["Message-ID"] = email.utils.make_msgid(domain="salelogics.sk")
    msg["Reply-To"] = sender["reply_to"]
    msg["Date"] = email.utils.formatdate(localtime=True)
    return msg


def validate_outgoing_message(msg, sender: dict) -> None:
    """Pravidlo bota: v hlavičke From musí byť skutočná adresa odosielateľa mimo
    encoded-word a hlavička musí byť ASCII. Inak relay From prepíše na
    "...@smtp.hostcreators.sk" a správy zamietne DMARC/antispam (incident 2026-10)."""
    from_hdr = str(msg["From"] or "").strip()
    expected = sender["from_addr"]
    _, addr = email.utils.parseaddr(from_hdr)
    shape_ok = from_hdr == expected or from_hdr.endswith(f"<{expected}>")
    if addr.lower() != expected.lower() or not from_hdr.isascii() or not shape_ok:
        raise ValueError(f"HEADER_INVALID: From={from_hdr!r} neobsahuje <{expected}> mimo encoded-word")


def _smtp_error_text(error: Exception, stage: str) -> str:
    code = getattr(error, "smtp_code", None)
    detail = getattr(error, "smtp_error", str(error))
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    code_text = f" SMTP {code}" if code is not None else ""
    return f"{stage}: {type(error).__name__}{code_text}: {detail}"


def _smtp_send_one(sender: dict, to_email: str, subject: str, text_body: str, html_body: str | None = None) -> dict:
    result = {
        "status": "FAILED",
        "error_msg": "",
        "delivery_ok": False,
        "message_id": "",
        "sent_at": "",
        "transient": False,
    }
    smtp_conn = imap_conn = None
    stage = "prepare"
    try:
        msg = create_mime_message(sender, to_email, subject, text_body, html_body)
        validate_outgoing_message(msg, sender)
        raw_message = msg.as_string()
        result["message_id"] = msg.get("Message-ID", "")

        sent = False
        attempt = 0
        stop = False
        while not sent and attempt < 3 and not stop:
            attempt += 1
            try:
                def sendmail():
                    nonlocal smtp_conn, stage
                    # Čakanie na slot musí prebehnúť pred otvorením SMTP.
                    # Inak server môže počas pacingu ukončiť nečinné spojenie.
                    stage = "smtp_connect"
                    if smtp_conn is not None:
                        try:
                            smtp_conn.quit()
                        except Exception:
                            pass
                    smtp_conn = smtplib.SMTP_SSL(
                        sender["smtp_host"], sender["smtp_port"],
                        timeout=NETWORK_TIMEOUT_SECONDS,
                    )
                    stage = "smtp_auth"
                    smtp_conn.login(sender["email_user"], sender["email_pass"])
                    before_send = sender.get("_before_smtp_send")
                    if before_send:
                        before_send()
                    stage = "smtp_send"
                    return smtp_conn.sendmail(sender["from_addr"], [to_email], raw_message)
                pacer = sender.get("_send_pacer")
                if pacer is None:
                    sendmail()
                else:
                    pacer.send(sender["_send_index"], sender["key"], sendmail)
                sent = True
            except (
                smtplib.SMTPServerDisconnected,
                socket.timeout,
                TimeoutError,
            ) as error:
                if stage != "smtp_send":
                    result["error_msg"] = _smtp_error_text(error, stage)
                    continue  # správa ešte nebola odovzdaná SMTP
                # Server mohol správu prijať pred stratou spojenia. Automatický
                # retry by mohol porušiť pravidlo jedného emailu na doménu.
                result["error_msg"] = f"Nejasný výsledok SMTP, bez automatického opakovania: {_smtp_error_text(error, stage)}"
                break
            except (
                smtplib.SMTPConnectError,
                smtplib.SMTPHeloError,
            ) as error:
                result["error_msg"] = _smtp_error_text(error, stage)
            except (socket.gaierror, ConnectionError) as error:
                result["error_msg"] = _smtp_error_text(error, stage)
                if stage == "smtp_send":
                    # Správa mohla byť prijatá – bez automatického opakovania (jeden email na doménu).
                    break
                # DNS/spojenie zlyhalo pred odovzdaním správy → bezpečné opakovať a nie je to chyba adresáta.
                result["transient"] = True
            except smtplib.SMTPRecipientsRefused as rr:
                # Server odmietol adresáta ešte pred odoslaním obsahu (RCPT TO) –
                # adresa neexistuje/neprijíma poštu. Žiadny obsah sa neposlal.
                code, raw_msg = next(iter(rr.recipients.values()))
                msg_txt = raw_msg.decode("utf-8", "replace") if isinstance(raw_msg, bytes) else str(raw_msg)
                result["status"] = "INVALID" if code >= 500 else "FAILED"
                result["error_msg"] = f"{stage}: SMTPRecipientsRefused SMTP {code}: {msg_txt}"
                stop = True
                break
            except smtplib.SMTPResponseException as re_err:
                result["error_msg"] = _smtp_error_text(re_err, stage)
                stop = True
                break

        if sent:
            result["error_msg"] = ""
            result["transient"] = False
            try:
                stage = "imap_connect"
                imap_conn = imaplib.IMAP4_SSL(
                    sender["imap_host"], sender["imap_port"],
                    timeout=NETWORK_TIMEOUT_SECONDS,
                )
                stage = "imap_auth"
                imap_conn.login(sender["email_user"], sender["email_pass"])
                stage = "imap_save"
                imap_conn.append(
                    sender["imap_sent_folder"],
                    "\\Seen",
                    Time2Internaldate(time.time()),
                    raw_message.encode("utf-8"),
                )
                result["delivery_ok"] = True
            except Exception as ie:
                result["error_msg"] = f"Sent but not saved: {_smtp_error_text(ie, stage)}"

            result["status"] = "OK"
            result["sent_at"] = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    except Exception as e:
        result["error_msg"] = _smtp_error_text(e, stage)
    finally:
        for conn in (smtp_conn, imap_conn):
            if conn is not None:
                try:
                    conn.quit() if isinstance(conn, smtplib.SMTP) else conn.logout()
                except Exception:
                    pass

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  TESTOVACÍ EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def run_test_send(mode: str,
                  sender: dict,
                  wnp_template_str: str = None,
                  campaign_templates: dict = None,
                  c6_collab_template: str = None,
                  matus_ai_template_str: str = None,
                  test_campaign: str = "C1") -> bool:
    divider = "─" * 56
    print(f"\n{divider}")
    print(f"  TESTOVACÍ EMAIL  →  {TEST_RECIPIENT}")
    print(f"  Odosielateľ:     {sender['from_name']} <{sender['from_addr']}>")
    print(f"  Doména:          {TEST_DOMAIN}")
    print(f"{divider}")

    sig_ctx = {
        "SENDER_NAME": sender["from_name"],
        "SENDER_TITLE": sender["sender_title"],
        "SENDER_PHONE": sender["sender_phone"],
    }

    if mode == "matus_ai":
        html_body = Template(matus_ai_template_str).render(
            DOMAIN=TEST_DOMAIN,
            SENDER_NAME=sender["from_name"],
            SENDER_TITLE=sender["sender_title"],
            SENDER_ADDR=sender["from_addr"],
        )
        text_body = html_to_plain_text(html_body)
        subject = MATUS_AI_SUBJECT

        print(f"  Predmet: {subject}")
        print(f"{divider}")
        print("  Odosielam...", end=" ", flush=True)

        res = _smtp_send_one(sender, TEST_RECIPIENT, subject, text_body, html_body)

        if res["status"] == "OK":
            saved = "uložené do Sent ✔" if res["delivery_ok"] else "NEULOŽENÉ do Sent ⚠"
            print(f"✔  OK  ({saved})")
            print(f"  Odoslané: {res['sent_at']}")
            print(f"  Message-ID: {res['message_id']}")
            return True
        else:
            print("✘  CHYBA")
            print(f"  Detail: {res['error_msg']}")
            return False

    fake_details = (
        "<ul>"
        "<li>doplniť robots.txt tak, aby neblokoval dôležité stránky pre Google</li>"
        "<li>vytvoriť a pripojiť sitemapu (sitemap.xml) pre lepšie prechádzanie stránok</li>"
        "<li>dopísať ALT texty k obrázkom – pomôže to SEO aj prístupnosti</li>"
        "<li>prekonvertovať hlavné obrázky do WebP/AVIF pre rýchlejšie načítanie</li>"
        "<li>nastaviť HSTS (Strict-Transport-Security) pre bezpečnejšie HTTPS</li>"
        "<li>doplniť meta description tak, aby lepšie vystihoval obsah stránky</li>"
        "</ul>"
    )

    if mode == "wnp_plain":
        limited_html = limit_issue_list(fake_details, max_items=WNP_MAX_ISSUES)
        plain_issues = html_list_to_bullets(fake_details, max_items=WNP_MAX_ISSUES)

        jinja_tpl = Template(wnp_template_str)
        html_body = jinja_tpl.render(
            DOMAIN=TEST_DOMAIN,
            YEAR="2026",
            PERSONALIZED_DETAILS_HTML=limited_html,
            **sig_ctx
        )
        text_body = Template(WNP_TEXT_TEMPLATE).render(
            DOMAIN=TEST_DOMAIN,
            YEAR="2026",
            TOP_ISSUES_TEXT=plain_issues,
            **sig_ctx
        )
        subject = WNP_SUBJECT

    elif mode == "wnp_html":
        if test_campaign == "C6":
            html_body = Template(c6_collab_template).render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                PERSONALIZED_DETAILS_HTML=limit_issue_list(fake_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            text_body = Template(C6_TEXT_TEMPLATE).render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                TOP_ISSUES_TEXT=html_list_to_bullets(fake_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            subject = C6_COLLAB_SUBJECT
        else:
            limited_html = limit_issue_list(fake_details, max_items=WNP_MAX_ISSUES)
            plain_issues = html_list_to_bullets(fake_details, max_items=WNP_MAX_ISSUES)

            jinja_tpl = Template(wnp_template_str)
            html_body = jinja_tpl.render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                PERSONALIZED_DETAILS_HTML=limited_html,
                **sig_ctx
            )
            text_body = Template(WNP_TEXT_TEMPLATE).render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                TOP_ISSUES_TEXT=plain_issues,
                **sig_ctx
            )
            subject = WNP_SUBJECT

    else:
        if test_campaign == "C6":
            html_body = Template(c6_collab_template).render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                PERSONALIZED_DETAILS_HTML=limit_issue_list(fake_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            text_body = Template(C6_TEXT_TEMPLATE).render(
                DOMAIN=TEST_DOMAIN,
                YEAR="2026",
                TOP_ISSUES_TEXT=html_list_to_bullets(fake_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            subject = C6_COLLAB_SUBJECT
        else:
            if test_campaign not in campaign_templates:
                print(f"  ✘  Šablóna pre kampaň '{test_campaign}' nebola nájdená!")
                return False

            print(f"  Kampaň: {test_campaign}")
            context = {
                "DOMAIN": TEST_DOMAIN,
                "PERSONALIZED_SUMMARY": "",
                "PERSONALIZED_DETAILS_HTML": limit_issue_list(fake_details, max_items=5),
                "EMAIL_SUBJECT": "Návrh na zlepšenie webu.",
                "BLUE_URL": GLOBAL_BLUE_URL,
                "BLUE_LABEL": GLOBAL_BLUE_LABEL,
                "YEAR": "2026",
                **sig_ctx,
            }
            html_body = Template(campaign_templates[test_campaign]).render(**context)
            text_body = html_to_plain_text(html_body)
            subject = "Návrh na zlepšenie webu."

    print(f"  Predmet: {subject}")
    print(f"{divider}")
    print("  Odosielam...", end=" ", flush=True)

    if mode == "wnp_plain":
        res = _smtp_send_one(sender, TEST_RECIPIENT, subject, text_body, None)
    else:
        res = _smtp_send_one(sender, TEST_RECIPIENT, subject, text_body, html_body)

    if res["status"] == "OK":
        saved = "uložené do Sent ✔" if res["delivery_ok"] else "NEULOŽENÉ do Sent ⚠"
        print(f"✔  OK  ({saved})")
        print(f"  Odoslané: {res['sent_at']}")
        print(f"  Message-ID: {res['message_id']}")
        return True
    else:
        print("✘  CHYBA")
        print(f"  Detail: {res['error_msg']}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE FUNKCIE
# ═══════════════════════════════════════════════════════════════════════════════

def make_wnp_template_fn(template_str: str, mode: str, sender: dict, c6_collab_template: str | None = None):
    wnp_jinja_tpl = Template(template_str)
    wnp_text_tpl = Template(WNP_TEXT_TEMPLATE)
    c6_jinja_tpl = Template(c6_collab_template) if c6_collab_template else None
    c6_text_tpl = Template(C6_TEXT_TEMPLATE) if c6_collab_template else None

    sig_ctx = {
        "SENDER_NAME": sender["from_name"],
        "SENDER_TITLE": sender["sender_title"],
        "SENDER_PHONE": sender["sender_phone"],
    }

    def template_fn(row):
        domain = clean_csv_value(row.get("domain", ""))
        campaign = row_campaign(row)

        raw_details = build_personalized_details_html(row)
        personalized_details = limit_issue_list(raw_details, max_items=WNP_MAX_ISSUES)
        top_issues_text = html_list_to_bullets(raw_details, max_items=WNP_MAX_ISSUES)

        # Režim 2: default WNP HTML, ale ak je riadok C6, pošli C6 collab template
        if mode == "wnp_html" and campaign == "C6" and c6_jinja_tpl and c6_text_tpl:
            html_body = c6_jinja_tpl.render(
                DOMAIN=domain,
                YEAR="2026",
                PERSONALIZED_DETAILS_HTML=limit_issue_list(raw_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            text_body = c6_text_tpl.render(
                DOMAIN=domain,
                YEAR="2026",
                TOP_ISSUES_TEXT=html_list_to_bullets(raw_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            return C6_COLLAB_SUBJECT, text_body, html_body

        html_body = wnp_jinja_tpl.render(
            DOMAIN=domain,
            YEAR="2026",
            PERSONALIZED_DETAILS_HTML=personalized_details,
            **sig_ctx
        )

        text_body = wnp_text_tpl.render(
            DOMAIN=domain,
            YEAR="2026",
            TOP_ISSUES_TEXT=top_issues_text,
            **sig_ctx
        )

        if mode == "wnp_plain":
            return WNP_SUBJECT, text_body, None
        else:
            return WNP_SUBJECT, text_body, html_body

    return template_fn


def make_campaigns_template_fn(templates: dict, c6_collab_template: str, sender: dict):
    c6_jinja_tpl = Template(c6_collab_template)
    c6_text_tpl = Template(C6_TEXT_TEMPLATE)

    sig_ctx = {
        "SENDER_NAME": sender["from_name"],
        "SENDER_TITLE": sender["sender_title"],
        "SENDER_PHONE": sender["sender_phone"],
    }

    def template_fn(row):
        domain = clean_csv_value(row.get("domain", ""))
        campaign = row_campaign(row)

        if ACTIVE_CAMPAIGNS and campaign not in ACTIVE_CAMPAIGNS:
            print(f"  → Preskočené (kampaň '{campaign}' nie je v ACTIVE_CAMPAIGNS): {domain}")
            return None

        raw_details = build_personalized_details_html(row)
        personalized_details = limit_issue_list(raw_details, max_items=5)

        if campaign == "C6":
            html_body = c6_jinja_tpl.render(
                DOMAIN=domain,
                YEAR="2026",
                PERSONALIZED_DETAILS_HTML=limit_issue_list(raw_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            text_body = c6_text_tpl.render(
                DOMAIN=domain,
                YEAR="2026",
                TOP_ISSUES_TEXT=html_list_to_bullets(raw_details, max_items=C6_MAX_ISSUES),
                **sig_ctx
            )
            return C6_COLLAB_SUBJECT, text_body, html_body

        if campaign not in templates:
            print(f"  → Preskočené (neznáma kampaň '{campaign}'): {domain}")
            return None

        blue_url = str(row.get("blue_url", "") or "").strip() or GLOBAL_BLUE_URL

        context = {
            "DOMAIN": domain,
            "PERSONALIZED_SUMMARY": "",
            "PERSONALIZED_DETAILS_HTML": personalized_details,
            "EMAIL_SUBJECT": "Návrh na zlepšenie webu.",
            "BLUE_URL": blue_url,
            "BLUE_LABEL": GLOBAL_BLUE_LABEL,
            "YEAR": "2026",
            **sig_ctx,
        }
        html_body = Template(templates[campaign]).render(**context)
        text_body = html_to_plain_text(html_body)
        return "Návrh na zlepšenie webu.", text_body, html_body

    return template_fn


def make_matus_ai_template_fn(template_str: str, sender: dict):
    """Jedna fixná AI šablóna pre každú doménu – bez C1–C6 rozlišovania."""
    jinja_tpl = Template(template_str)
    sig_ctx = {
        "SENDER_NAME": sender["from_name"],
        "SENDER_TITLE": sender["sender_title"],
        "SENDER_ADDR": sender["from_addr"],
    }

    def template_fn(row):
        domain = clean_csv_value(row.get("domain", ""))
        html_body = jinja_tpl.render(DOMAIN=domain, **sig_ctx)
        text_body = html_to_plain_text(html_body)
        return MATUS_AI_SUBJECT, text_body, html_body

    return template_fn


# ═══════════════════════════════════════════════════════════════════════════════
#  ODOSIELACIA SLUČKA
# ═══════════════════════════════════════════════════════════════════════════════

def row_identity(domain: str, campaign: str, email_addr: str) -> tuple[str, str, str]:
    """Stabilný identifikátor pokusu nezávislý od indexu v konkrétnom CSV."""
    return (
        clean_csv_value(domain).lower(),
        clean_csv_value(campaign).upper(),
        clean_csv_value(email_addr).lower(),
    )


def domain_key(value) -> str:
    domain = clean_csv_value(value).lower()
    domain = re.sub(r"^https?://", "", domain).split("/", 1)[0].rstrip(".")
    return domain.removeprefix("www.")


def scan_log_latest(path: str) -> dict:
    """Posledný stav každého kontaktu v logu: (doména, email) -> (STATUS, error).
    Log je append-only, takže neskorší riadok (napr. BOUNCED po OK, alebo OK po
    BOUNCED pri opakovaní) prepíše skorší. BOUNCED = správa sa vrátila a čaká na
    opakovanie; ostatné stavy (OK/INVALID/FAILED/REJECTED) kontakt uzatvárajú."""
    latest = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"domain", "email_address", "status"}.issubset(reader.fieldnames or []):
            raise ValueError(f"Neplatná hlavička logu {path}")
        for record in reader:
            status = clean_csv_value(record.get("status")).upper()
            if not status:
                continue
            key = (domain_key(record.get("domain")), clean_csv_value(record.get("email_address")).lower())
            latest[key] = (status, clean_csv_value(record.get("error")))
    return latest


def build_retry_queue(df: pd.DataFrame, latest: dict, blocked_domains: set, blocked_contacts: set):
    """Fronta opakovania z BOUNCED kontaktov jedného logu: zoznam (index riadku v CSV,
    kategória). Kategórie sa striedajú (round-robin), takže prvé odoslania tvoria
    pestrú vzorku poskytovateľov (Gmail/Microsoft/Websupport...). Vracia aj počty
    preskočených (doména už doručená inou adresou / chýba v CSV)."""
    dom_to_idx = {}
    for idx, value in zip(df.index, df["domain"].tolist()):
        key = domain_key(value)
        if key and key not in dom_to_idx:
            dom_to_idx[key] = int(idx)
    buckets, seen = {}, set()
    delivered_elsewhere = missing = 0
    for (domain, contact), (status, error) in latest.items():
        if status != "BOUNCED":
            continue
        if domain in blocked_domains or contact in blocked_contacts:
            delivered_elsewhere += 1
            continue
        idx = dom_to_idx.get(domain)
        if idx is None:
            missing += 1
            continue
        if idx in seen:
            continue
        seen.add(idx)
        buckets.setdefault(error.split("|", 1)[0] or "?", []).append(idx)
    lists = [buckets[k] for k in sorted(buckets)]
    order = []
    while any(lists):
        for items in lists:
            if items:
                idx = items.pop(0)
                order.append(idx)
    return order, delivered_elsewhere, missing


def parallel_start_index(state_file: str, primary_state_file: str) -> int:
    # Zdieľaný stav je autoritatívny. Poškodený JSON nesmie spustiť posielanie od nuly.
    path = state_file if os.path.exists(state_file) else primary_state_file
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    start = state.get("start_index", 0)
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise ValueError(f"Neplatný start_index v {path}")
    return start


class SendPacer:
    """Poradie riadkov a rozostupy pri samotnom SMTP sendmail, aj pri retry."""

    def __init__(self, global_delay: float, sender_delays: dict):
        self.delay = max(MIN_GLOBAL_SEND_SECONDS, global_delay)
        self.sender_delays = sender_delays
        self.condition = threading.Condition()
        self.order = []
        self.last_global = None
        self.last_end = 0.0
        self.last_sender = {}
        self.stopped = False
        self.stop_event = threading.Event()

    def register(self, index):
        with self.condition:
            self.order.append(index)

    def cancel(self, index):
        with self.condition:
            if index in self.order:
                self.order.remove(index)
            self.condition.notify_all()

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.stopped = True
            self.condition.notify_all()

    def send(self, index, sender_key, sendmail):
        with self.condition:
            while not self.stopped and index in self.order and self.order[0] != index:
                self.condition.wait()
            if self.stopped:
                raise KeyboardInterrupt
            now = time.monotonic()
            deadline = now
            if self.last_global is not None:
                deadline = max(deadline, self.last_global + self.delay, self.last_end + MIN_GAP_AFTER_SEND_SECONDS)
            if sender_key in self.last_sender:
                deadline = max(deadline, self.last_sender[sender_key] + self.sender_delays[sender_key])
            if deadline > now:
                if self.stop_event.wait(deadline - now):
                    raise KeyboardInterrupt
            started = time.monotonic()
            try:
                return sendmail()
            finally:
                # Rozostup sa počíta od ZAČIATKU pokusu (trvanie SMTP/IMAP sa doň zarátava, inak
                # tempo klesne pod limit); po skončení pokusu zostáva len krátka poistná pauza.
                self.last_global = started
                self.last_end = time.monotonic()
                self.last_sender[sender_key] = started
                if index in self.order:
                    self.order.remove(index)
                self.condition.notify_all()


def load_contact_state(df: pd.DataFrame, runs: list):
    """Spoločná pravda o kontaktoch z logov VŠETKÝCH účtov a režimov:
    domains/contacts = už uzavreté (posledný stav OK/INVALID/FAILED/REJECTED – nikdy sa neposielajú znova),
    retry = fronta nedoručených (BOUNCED) pre každý účet zvlášť, stats = (doručené inou adresou, doména nie je v CSV)."""
    logs = {r["log_csv"] for r in runs}
    logs.update((WNP_LOG_CSV_FILE, LOG_CSV_FILE, MATUS_AI_LOG_CSV_FILE))
    logs.add(sender_paths("matus", WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE)[1])
    logs.add(sender_paths("matus", STATE_FILE, LOG_CSV_FILE, LOG_HTML_FILE)[1])
    domains, contacts, latest_by_log = set(), set(), {}
    for path in logs:
        if not os.path.exists(path):
            continue
        latest_by_log[path] = scan_log_latest(path)
        for (domain, contact), (status, _error) in latest_by_log[path].items():
            if status != "BOUNCED":  # BOUNCED = nedoručené, čaká vo fronte opakovania
                domains.add(domain)
                contacts.add(contact)
    retry, stats = {}, {}
    for r in runs:
        order, elsewhere, missing = build_retry_queue(df, latest_by_log.get(r["log_csv"], {}), domains, contacts)
        retry[r["key"]] = order
        stats[r["key"]] = (elsewhere, missing)
    return domains, contacts, retry, stats


class ParallelCoordinator:
    """Jeden CSV kurzor, striedanie účtov a trvalé rozpracované rezervácie."""

    def __init__(self, df, runs, state_file, primary_state_file):
        self.df = df
        self.state_file = state_file
        self.cursor = parallel_start_index(state_file, primary_state_file)
        if self.cursor > len(df) and isinstance(df.index, pd.RangeIndex) and df.index.start == 0:
            raise ValueError("Spoločná pozícia je za koncom CSV")
        if os.path.exists(state_file):
            with open(state_file, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("csv_file") != os.path.basename(CSV_FILE):
                raise ValueError("Spoločný stav patrí inému CSV; najprv skontroluj pokračovanie")
        self.rows = iter(df.loc[df.index >= self.cursor].iterrows())
        self.deferred = None
        self.active = [r["key"] for r in runs]
        if "maros" in self.active:
            self.active.remove("maros")
            self.active.insert(0, "maros")
        self.turn = self.active[0]
        self.senders = {r["key"]: r["profile"]["from_addr"] for r in runs}
        self.condition = threading.Condition()
        self.state_guard = threading.RLock()
        self.pending = {}
        self.reassignable = {}
        self.domains = set()
        self.contacts = set()
        self.stopped = False
        self.exhausted = False
        delays = {r["key"]: max(MIN_GLOBAL_SEND_SECONDS, r["delay"]) for r in runs}
        self.pacer = SendPacer(min(delays.values()) / len(runs), delays)
        # História oboch účtov a všetkých režimov, nie len aktuálneho účtu.
        self.domains, self.contacts, retry_orders, retry_stats = load_contact_state(df, runs)
        self.retry = {}
        for r in runs:
            order = retry_orders[r["key"]]
            elsewhere, missing = retry_stats[r["key"]]
            self.retry[r["key"]] = deque(order)
            if order or elsewhere or missing:
                print(f"  [{r['key']}] Fronta opakovania: {len(order)} domén "
                      f"(preskočené – doručené inou adresou: {elsewhere}, doména nie je v CSV: {missing})")
                if order:
                    sample = ", ".join(f"#{i} {domain_key(df.at[i, 'domain'])}" for i in order[:3])
                    print(f"  [{r['key']}] Posiela sa ako prvé (riadok CSV, doména): {sample} … "
                          f"posledný v poradí: #{order[-1]}")
        print(f"  Nové riadky: spoločný kurzor od {self.cursor}; účty ich berú striedavo "
              f"({', '.join(r['key'] for r in runs)}), ktorý riadok dostane kto sa určí až za behu.")
        self.owners = {domain_key(d): owner for d, owner in _load_domain_owners(DOMAIN_OWNER_FILE).items()}
        if os.path.exists(state_file):
            for index, reservation in saved.get("pending", {}).items():
                if isinstance(reservation, dict):
                    row = df.loc[int(index)]
                    contact, _ = select_primary_email(row)
                    logged = domain_key(row.get("domain")) in self.domains or contact.lower() in self.contacts
                    if reservation.get("phase") == "sending" and not logged:
                        raise ValueError(
                            f"Riadok {index} má nejasný výsledok SMTP. Over priečinok Sent a log "
                            "pred obnovením; automatické opakovanie je zablokované."
                        )
                    if reservation.get("sender") not in self.active and not logged:
                        if reservation.get("phase") != "reserved":
                            raise ValueError(f"Riadok {index} nemá bezpečne prevoditeľnú rezerváciu.")
                        self.reassignable[int(index)] = self.owners.get(domain_key(row.get("domain")))

    def _save(self):
        with self.state_guard:
            save_state({
                "csv_file": os.path.basename(CSV_FILE),
                # Opakovania (retry) sú mimo kurzora a nesmú posunúť pozíciu späť.
                "start_index": min((i for i, v in self.pending.items() if not v.get("retry")),
                                   default=self.cursor),
                "next_index": self.cursor,
                "pending": {str(i): reservation for i, reservation in self.pending.items()},
            }, self.state_file)

    def mark_sending(self, index):
        with self.state_guard:
            self.pending[index]["phase"] = "sending"
            self._save()

    def take(self, key, template_fn):
        with self.condition:
            while not self.stopped and not self.exhausted and self.turn != key:
                self.condition.wait()
            if self.stopped or self.exhausted:
                return None
            while not self.stopped and not self.exhausted:
                while self.turn != key and not self.stopped and not self.exhausted:
                    self.condition.wait()
                if self.stopped or self.exhausted:
                    return None
                if self.retry.get(key):
                    task = self._take_retry(key, template_fn)
                    if task is not None:
                        return task
                    continue
                item = self.deferred if self.deferred is not None else next(self.rows, None)
                self.deferred = None
                if item is None:
                    break
                idx, row = item
                idx = int(idx)
                self.cursor = idx + 1
                domain = domain_key(row.get("domain"))
                email_addr, _ = select_primary_email(row)
                contact = email_addr.lower()
                if not domain or not contact or domain in self.domains or contact in self.contacts:
                    continue
                rendered = template_fn(row)
                if rendered is None:
                    continue
                owner = self.owners.get(domain)
                if idx in self.reassignable and owner != self.senders[key]:
                    if not claim_domain(domain, self.senders[key], DOMAIN_OWNER_FILE,
                                        DOMAIN_OWNER_LOCK_FILE, previous_owner=self.reassignable[idx]):
                        raise ValueError(f"Vlastníctvo domény na riadku {idx} sa zmenilo; posielanie zastavené.")
                    self.owners[domain] = self.senders[key]
                    owner = self.senders[key]
                if owner is not None and owner != self.senders[key]:
                    owner_key = next((k for k in self.active if self.senders[k] == owner), None)
                    if owner_key is not None:
                        self.deferred = (idx, row)
                        self.cursor = idx
                        self.turn = owner_key
                        self.condition.notify_all()
                    continue
                if not claim_domain(domain, self.senders[key], DOMAIN_OWNER_FILE, DOMAIN_OWNER_LOCK_FILE):
                    continue
                self.domains.add(domain)
                self.contacts.add(contact)
                with self.state_guard:
                    self.pending[idx] = {"sender": key, "phase": "reserved"}
                    self._save()  # rezervácia je trvalá ešte pred sieťovým odoslaním
                self.pacer.register(idx)
                self.turn = self.active[(self.active.index(key) + 1) % len(self.active)]
                self.condition.notify_all()
                return idx, row, email_addr, rendered
            self.exhausted = True
            self._save()
            self.condition.notify_all()
            return None

    def _take_retry(self, key, template_fn):
        """Opakované odoslanie na doménu, ktorej správa sa vrátila (BOUNCED). Volá sa pod
        self.condition. Platia rovnaké pravidlá ako pri nových riadkoch: jeden email na
        doménu, potlačené adresy, doménový zámok, pacing a limity účtu."""
        queue = self.retry[key]
        while queue:
            idx = queue.popleft()
            row = self.df.loc[idx]
            domain = domain_key(row.get("domain"))
            email_addr, _ = select_primary_email(row)
            contact = email_addr.lower()
            if not domain or not contact or domain in self.domains or contact in self.contacts:
                continue
            rendered = template_fn(row)
            if rendered is None:
                continue
            owner = self.owners.get(domain)
            if owner is not None and owner != self.senders[key]:
                continue
            if not claim_domain(domain, self.senders[key], DOMAIN_OWNER_FILE, DOMAIN_OWNER_LOCK_FILE):
                continue
            self.domains.add(domain)
            self.contacts.add(contact)
            with self.state_guard:
                self.pending[idx] = {"sender": key, "phase": "reserved", "retry": True}
                self._save()
            self.pacer.register(idx)
            self.turn = self.active[(self.active.index(key) + 1) % len(self.active)]
            self.condition.notify_all()
            return idx, row, email_addr, rendered
        return None

    def is_retry(self, index) -> bool:
        return bool(self.pending.get(index, {}).get("retry"))

    def complete(self, index):
        with self.condition:
            with self.state_guard:
                self.pending.pop(index, None)
                self._save()
            self.pacer.cancel(index)

    def deactivate(self, key):
        with self.condition:
            if key in self.active:
                position = self.active.index(key)
                self.active.remove(key)
                if self.turn == key and self.active:
                    self.turn = self.active[position % len(self.active)]
            self.condition.notify_all()

    def stop(self):
        # Prebudíme pacing ešte pred čakaním na zámok koordinátora.
        self.pacer.stop_event.set()
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        self.pacer.stop()


def send_loop(df: pd.DataFrame, template_fn, sender: dict, state_file: str,
              log_csv: str, log_html: str, send_delay_seconds: float,
              session_limit: int | None = None, label: str = "",
              coordinator=None) -> None:
    tag = f"[{label}] " if label else ""
    key = sender["key"]
    if coordinator is None:
        run = {"key": key, "profile": sender, "state_file": state_file,
               "log_csv": log_csv, "delay": send_delay_seconds}
        shared_path = os.path.splitext(state_file)[0] + "_parallel.json"
        coordinator = ParallelCoordinator(df, [run], shared_path, state_file)
    state = load_state(state_file)
    state, _ = migrate_state_to_csv(state, df, CSV_FILE)
    sent_count = state.get("sent_count_window", 0)
    window_start = datetime.fromisoformat(state["window_start"]) if state.get("window_start") else datetime.now(timezone.utc)
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    session_sent = 0
    session_failed = 0
    started = time.monotonic()
    # Poistka pred rezerváciou čohokoľvek: hlavička From musí byť platná (pozri validate_outgoing_message).
    validate_outgoing_message(create_mime_message(sender, "preflight@example.com", "preflight", "preflight"), sender)

    def progress_line(prefix: str) -> str:
        hours = max((time.monotonic() - started) / 3600.0, 1e-9)
        retry_left = len(coordinator.retry.get(key, ()))
        new_left = max(0, len(df) - coordinator.cursor)
        return (f"{tag}{prefix} odoslané: {session_sent}, chyby: {session_failed} | "
                f"fronta opakovania zostáva: {retry_left} | nové riadky zostáva: ~{new_left} | "
                f"okno {sent_count}/{WINDOW_LIMIT} | tempo ~{session_sent / hours:.0f}/hod")

    print(f"{tag}ŠTART: {len(coordinator.retry.get(key, ()))} domén vo fronte opakovania, potom nové riadky "
          f"od {coordinator.cursor} (zostáva ~{max(0, len(df) - coordinator.cursor)}). "
          f"Limit za hodinu {WINDOW_LIMIT}, rozostup ~{send_delay_seconds:.1f} s.")
    try:
        while not coordinator.stopped:
            if session_limit is not None and session_sent >= session_limit:
                print(f"{tag}LIMIT: dosiahnutý limit {session_limit} emailov pre tento beh.")
                break
            now = datetime.now(timezone.utc)
            if now - window_start >= timedelta(minutes=WINDOW_MINUTES):
                window_start, sent_count = now, 0
            if sent_count >= WINDOW_LIMIT:
                print(f"{tag}LIMIT: {WINDOW_LIMIT} emailov v {WINDOW_MINUTES}-min. okne.")
                break
            task = coordinator.take(key, template_fn)
            if task is None:
                break
            idx, row, email_addr, (subject, text_body, html_body) = task
            is_retry = coordinator.is_retry(idx)
            retry_tag = " [OPAKOVANIE]" if is_retry else ""
            profile = dict(sender, _send_pacer=coordinator.pacer, _send_index=idx,
                           _before_smtp_send=lambda: coordinator.mark_sending(idx))
            res = _smtp_send_one(profile, email_addr, subject, text_body, html_body)
            domain = clean_csv_value(row.get("domain", ""))
            campaign = row_campaign(row) or "WNP"
            if res["status"] == "OK":
                sent_count += 1
                session_sent += 1
                print(f"{tag}[{idx}] OK #{session_sent}{retry_tag}: {domain} → {email_addr} ({campaign})")
            else:
                session_failed += 1
                if res.get("transient"):
                    # Sieťová chyba pred odovzdaním správy: kontakt nezostane uzavretý (FAILED),
                    # ale čaká vo fronte opakovania a pošle sa pri ďalšom behu.
                    res["status"] = "BOUNCED"
                print(f"{tag}[{idx}] {res['status']}{retry_tag}: {domain} → {email_addr}: {res['error_msg']}")
            if (session_sent + session_failed) % 10 == 0:
                print(progress_line("STAV –"))
            append_logs([{
                "index": idx, "domain": domain, "campaign": campaign,
                "email_address": email_addr, "subject": subject,
                "status": res["status"], "error": res["error_msg"],
                "sent_at": res["sent_at"], "delivery_ok": res["delivery_ok"],
                "message_id": res["message_id"], "sender": sender["from_addr"],
            }], log_csv, log_html)
            # Log je uložený skôr než posunieme spoločný kurzor.
            if not is_retry:  # opakovanie nesmie posunúť pozíciu späť
                state.update(last_index=idx, start_index=idx + 1)
            state.update(sent_count_window=sent_count, window_start=window_start.isoformat())
            save_state(state, state_file)
            coordinator.complete(idx)
            if res["status"] == "FAILED" and any(
                code in res["error_msg"].lower() for code in ("421", "450", "451", "454")
            ):
                print(f"{tag}STOP: serverový rate limit.")
                break
    except KeyboardInterrupt:
        coordinator.stop()
        print(f"\n{tag}Prerušené. Rozpracovaná pozícia zostáva uložená.")
    except Exception:
        coordinator.stop()
        raise
    finally:
        coordinator.deactivate(key)
        print(progress_line("KONIEC –"))


# ═══════════════════════════════════════════════════════════════════════════════
#  SPRACOVANIE BOUNCE-OV (IMAP) A FRONTA OPAKOVANIA
# ═══════════════════════════════════════════════════════════════════════════════
#
# Bounce (DSN) sa spáruje s odoslanou správou cez pôvodné Message-ID a do logu sa
# dopíše nový riadok (log je append-only; platí posledný stav kontaktu):
#   INVALID  – adresa/doména neexistuje (nikdy znova),
#   BOUNCED  – správa sa vrátila kvôli poškodenej hlavičke From (incident 2026-10);
#              kontakt čaká vo fronte opakovania a pošle sa znova podľa pravidiel bota,
#   REJECTED – zamietnutie pri správnej hlavičke (spam, plná schránka...); neopakuje sa
#              automaticky, len sa eviduje.

BOUNCE_FOLDERS_DEFAULT = {"maros": ["INBOX", "Processed"], "matus": ["INBOX", "nedorucene emaily"]}
BOUNCE_SINCE_DEFAULT = "19-Sep-2026"
_INVALID_STATUS_CODES = {"5.1.0", "5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.4.1", "5.4.4"}
_INVALID_MARKERS = (
    "does not exist", "no such user", "user unknown", "unknown user", "no mailbox",
    "invalid recipient", "recipient not found", "host or domain name not found",
    "no such recipient", "mailbox not found", "no user ",
)
_FULL_MARKERS = ("quota", "mailbox full", "out of storage", "over quota")
_MANGLED_FROM_RE = re.compile(r'<[^>]+@[^>]+>"\s*@')


def classify_bounce(status: str, diag: str, mangled_from: bool) -> str:
    text = (diag or "").lower()
    code = (status or "").strip()
    # Status v DSN býva generický (5.0.0); skutočný kód je často až v texte ("#5.1.0 Address rejected").
    codes_in_text = set(re.findall(r"\b5\.\d\.\d{1,3}\b", text))
    if (code in _INVALID_STATUS_CODES or codes_in_text & _INVALID_STATUS_CODES
            or any(m in text for m in _INVALID_MARKERS)):
        return "INVALID"
    if code == "5.2.2" or any(m in text for m in _FULL_MARKERS):
        return "REJECTED"
    return "BOUNCED" if mangled_from else "REJECTED"


def bounce_family(status: str, diag: str) -> str:
    text = (diag or "").lower()
    if any(k in text for k in ("dmarc", "spf", "unauthenticated", "5.7.26", "5.7.509")):
        return "AUTH"
    if "spam" in text:
        return "SPAM"
    return "OTHER"


def parse_dsn(raw: bytes) -> dict | None:
    from email import policy
    msg = email.message_from_bytes(raw, policy=policy.default)
    info = {"orig_mid": "", "orig_from": "", "recipient": "", "status": "", "action": "", "diag": ""}
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            for block in part.get_payload():
                final = block.get("Final-Recipient")
                if final and not info["recipient"]:
                    info["recipient"] = str(final).split(";")[-1].strip().lower()
                    info["status"] = str(block.get("Status", "")).strip()
                    info["diag"] = " ".join(str(block.get("Diagnostic-Code", "")).split())
                    info["action"] = str(block.get("Action", "")).strip().lower()
        elif ctype in ("message/rfc822", "text/rfc822-headers"):
            payload = part.get_payload()
            header = payload[0] if isinstance(payload, list) and payload else None
            if header is not None and not info["orig_mid"]:
                info["orig_mid"] = str(header.get("Message-ID", "")).strip()
                info["orig_from"] = str(header.get("From", ""))
    if not info["recipient"]:
        return None
    if info["action"] and info["action"] != "failed":
        return None  # delayed/relayed – nie je to trvalé zlyhanie
    return info


def process_bounces(profile: dict, log_csv: str, apply: bool = False, since: str = BOUNCE_SINCE_DEFAULT) -> dict:
    """Prejde bounce priečinky schránky (read-only IMAP) a spáruje DSN s OK riadkami logu.
    apply=False len vypíše štatistiku; apply=True zálohuje log a dopíše INVALID/BOUNCED/REJECTED."""
    import shutil
    summary = {"dsn": 0, "unparsed": 0, "matched": 0, "already": 0, "unmatched": 0,
               "INVALID": 0, "BOUNCED": 0, "REJECTED": 0, "categories": {}}
    with open(log_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    latest_mid, ok_by_mid, ok_by_email = {}, {}, {}
    for r in rows:
        key = (domain_key(r.get("domain")), clean_csv_value(r.get("email_address")).lower())
        status = clean_csv_value(r.get("status")).upper()
        mid = clean_csv_value(r.get("message_id"))
        latest_mid[key] = (status, mid)
        if status == "OK":
            ok_by_mid[mid] = (key, r)
            ok_by_email[key[1]] = (key, r)

    folders = profile.get("bounce_folders") or BOUNCE_FOLDERS_DEFAULT.get(profile["key"], ["INBOX"])
    label = profile["from_name"]
    print(f"  [{label}] Berú sa do úvahy IBA vrátené správy, ktoré odoslal tento bot (párovanie cez Message-ID z logu).")
    print(f"  [{label}] Log načítaný ({len(rows)} záznamov). Pripájam sa na IMAP, priečinky: {', '.join(folders)} "
          f"(čítanie bez zmien; pri tisíckach správ to trvá niekoľko minút)...", flush=True)
    conn = imaplib.IMAP4_SSL(profile["imap_host"], profile["imap_port"], timeout=60)
    conn.login(profile["email_user"], profile["email_pass"])
    seen_dsn, events = set(), {}
    try:
        for folder in folders:
            typ, _ = conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                continue
            typ, data = conn.search(None, "SINCE", since, "OR", "FROM", "MAILER-DAEMON", "FROM", "postmaster")
            ids = data[0].split() if typ == "OK" else []
            print(f"  [{label}] Priečinok '{folder}': {len(ids)} správ na spracovanie", flush=True)
            for start in range(0, len(ids), 50):
                if start and start % 500 == 0:
                    print(f"  [{label}]   ... spracované {start}/{len(ids)}", flush=True)
                chunk = b",".join(ids[start:start + 50])
                typ, md = conn.fetch(chunk, "(BODY.PEEK[])")
                for item in md or []:
                    if not isinstance(item, tuple):
                        continue
                    info = parse_dsn(item[1])
                    if info is None:
                        summary["unparsed"] += 1
                        continue
                    summary["dsn"] += 1
                    # Iba správy odoslané týmto botom: párovanie podľa Message-ID (cudzia pošta sa ignoruje).
                    # Náhradné párovanie podľa adresy sa používa len ak DSN pôvodné Message-ID neobsahuje.
                    match = (ok_by_mid.get(info["orig_mid"]) if info["orig_mid"]
                             else ok_by_email.get(info["recipient"]))
                    if match is None:
                        summary["unmatched"] += 1
                        continue
                    key, row = match
                    if latest_mid.get(key) != ("OK", clean_csv_value(row.get("message_id"))):
                        summary["already"] += 1  # už spracované alebo medzitým znova odoslané
                        continue
                    if key in events:
                        continue
                    mangled = bool(_MANGLED_FROM_RE.search(info["orig_from"]))
                    verdict = classify_bounce(info["status"], info["diag"], mangled)
                    family = bounce_family(info["status"], info["diag"])
                    category = f"{family}:{info['status'] or '?'}"
                    events[key] = (verdict, category, info, row)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    new_rows = []
    for key, (verdict, category, info, row) in events.items():
        summary["matched"] += 1
        summary[verdict] += 1
        summary["categories"][f"{verdict} {category}"] = summary["categories"].get(f"{verdict} {category}", 0) + 1
        new_row = {col: "" for col in header}
        new_row.update({
            "index": row.get("index", ""), "domain": row.get("domain", ""),
            "campaign": row.get("campaign", ""), "email_address": row.get("email_address", ""),
            "subject": row.get("subject", ""), "status": verdict,
            "error": f"{category}|{info['diag'][:150]}", "sent_at": "", "delivery_ok": "False",
            "message_id": row.get("message_id", ""), "sender": row.get("sender", profile["from_addr"]),
        })
        new_rows.append({col: new_row.get(col, "") for col in header})
    if apply and new_rows:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(log_csv, f"{log_csv}.bak_{stamp}")
        append_logs(new_rows, log_csv, "")
    return summary


def print_bounce_summary(label: str, summary: dict) -> None:
    print(f"\n  [{label}] DSN správ: {summary['dsn']} (nerozpoznané/oneskorené: {summary['unparsed']}), "
          f"spárovaných s odoslaním: {summary['matched']}, "
          f"už spracované: {summary['already']}, bez páru v logu: {summary['unmatched']}")
    print(f"    INVALID (adresa/doména neexistuje): {summary['INVALID']}")
    print(f"    BOUNCED (chyba From → fronta opakovania): {summary['BOUNCED']}")
    print(f"    REJECTED (zamietnuté pri správnej hlavičke, bez opakovania): {summary['REJECTED']}")
    for name, count in sorted(summary["categories"].items(), key=lambda x: -x[1])[:10]:
        print(f"      {count:6d}  {name}")


def _summarize_log(log_csv: str) -> dict:
    summary = {"total": 0, "ok": 0, "invalid": 0, "failed": 0, "bounced": 0, "rejected": 0, "unique_domains_ok": 0,
               "delivered_now": 0, "queue": 0}
    if not os.path.exists(log_csv):
        return summary
    try:
        df = pd.read_csv(log_csv, encoding="utf-8-sig")
    except Exception:
        return summary
    summary["total"] = len(df)
    if "status" in df.columns:
        summary["ok"] = int((df["status"] == "OK").sum())
        summary["invalid"] = int((df["status"] == "INVALID").sum())
        summary["failed"] = int((df["status"] == "FAILED").sum())
        summary["bounced"] = int((df["status"] == "BOUNCED").sum())
        summary["rejected"] = int((df["status"] == "REJECTED").sum())
        if "domain" in df.columns:
            summary["unique_domains_ok"] = int(df.loc[df["status"] == "OK", "domain"].nunique())
    try:
        latest = scan_log_latest(log_csv)
        summary["delivered_now"] = sum(1 for v in latest.values() if v[0] == "OK")
        summary["queue"] = sum(1 for v in latest.values() if v[0] == "BOUNCED")
    except Exception:
        summary["delivered_now"] = summary["queue"] = 0
    return summary


def print_files_overview(profiles: dict) -> None:
    print("\n" + "=" * 64)
    print("  KAM BOT ZAPISUJE DÁTA")
    print("=" * 64)
    print(f"  Konfigurácia odosielateľov (heslá, podpisy):  {SENDERS_CONFIG_FILE}")
    print(f"  Doménový zámok (kto vlastní ktorú doménu):    {DOMAIN_OWNER_FILE}")
    print(f"  Zdrojová CSV databáza (domény/emaily):        {CSV_FILE}")
    print(f"  Beží iba jeden bot naraz (lock súbor):         {LOCK_FILE}")

    print("\n  Web na prenájom (stav postupu / log každého pokusu):")
    for key in profiles:
        s, c, h = sender_paths(key, WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE)
        print(f"    {key:8s} → stav: {s:35s} log: {c}")

    print("\n  Kampane C1–C6 (stav postupu / log každého pokusu):")
    for key in profiles:
        s, c, h = sender_paths(key, STATE_FILE, LOG_CSV_FILE, LOG_HTML_FILE)
        print(f"    {key:8s} → stav: {s:35s} log: {c}")

    print("\n  AI webové riešenie (Matúš, fixná šablóna, bez kampaní):")
    print(f"    matus    → stav: {MATUS_AI_STATE_FILE:35s} log: {MATUS_AI_LOG_CSV_FILE}")

    print("\n  Do log CSV sa pri každom pokuse zapisuje: index riadku, doména,")
    print("  emailová adresa, predmet, status (OK/INVALID/FAILED), chybová správa,")
    print("  čas odoslania, či sa uložil do priečinka Sent, Message-ID a odosielateľ.")


def print_sending_overview(profiles: dict) -> None:
    print("\n" + "=" * 64)
    print("  PREHĽAD ODOSIELANIA (Web na prenájom)")
    print("=" * 64)
    for key, profile in profiles.items():
        _, log_csv, _ = sender_paths(key, WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE)
        s = _summarize_log(log_csv)
        print(f"\n  {profile['from_name']} <{profile['from_addr']}>")
        print(f"    log súbor:                {log_csv}")
        print(f"    záznamov spolu:           {s['total']}")
        print(f"    úspešne odoslané (OK):    {s['ok']}  (unikátnych domén: {s['unique_domains_ok']})")
        print(f"    nefunkčné adresy (INVALID): {s['invalid']}")
        print(f"    chyby/zlyhania (FAILED):  {s['failed']}")
        print(f"    aktuálne doručené (posledný stav OK): {s['delivered_now']} | čaká vo fronte opakovania (BOUNCED): {s['queue']} | zamietnuté bez opakovania (REJECTED): {s['rejected']}")

    if "matus" in profiles:
        s = _summarize_log(MATUS_AI_LOG_CSV_FILE)
        p = profiles["matus"]
        print(f"\n  {p['from_name']} <{p['from_addr']}> – AI webové riešenie (samostatná kampaň)")
        print(f"    log súbor:                {MATUS_AI_LOG_CSV_FILE}")
        print(f"    záznamov spolu:           {s['total']}")
        print(f"    úspešne odoslané (OK):    {s['ok']}  (unikátnych domén: {s['unique_domains_ok']})")
        print(f"    nefunkčné adresy (INVALID): {s['invalid']}")
        print(f"    chyby/zlyhania (FAILED):  {s['failed']}")
        print(f"    aktuálne doručené (posledný stav OK): {s['delivered_now']} | čaká vo fronte opakovania (BOUNCED): {s['queue']} | zamietnuté bez opakovania (REJECTED): {s['rejected']}")

    owners = _load_domain_owners(DOMAIN_OWNER_FILE)
    print(f"\n  Doménový zámok ({DOMAIN_OWNER_FILE}): {len(owners)} domén má priradeného vlastníka")
    counts: dict = {}
    for v in owners.values():
        counts[v] = counts.get(v, 0) + 1
    for addr, cnt in counts.items():
        print(f"    {addr}: {cnt} domén")
    if not owners:
        print("    (zatiaľ žiadne – vytvorí sa pri prvom ostrom behu)")


def export_invalid_emails_report(profiles: dict, out_csv: str = "invalid_emails_report.csv") -> None:
    frames = []
    log_files_by_sender = []
    for key in profiles:
        _, log_csv, _ = sender_paths(key, WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE)
        log_files_by_sender.append((key, log_csv))
    if "matus" in profiles:
        log_files_by_sender.append(("matus_ai", MATUS_AI_LOG_CSV_FILE))

    for key, log_csv in log_files_by_sender:
        if not os.path.exists(log_csv):
            continue
        try:
            df = pd.read_csv(log_csv, encoding="utf-8-sig")
        except Exception:
            continue
        if "status" not in df.columns:
            continue
        invalid_df = df.loc[df["status"] == "INVALID"].copy()
        if not invalid_df.empty:
            invalid_df["sender_key"] = key
            frames.append(invalid_df)

    if not frames:
        print("  Žiadne nefunkčné adresy zatiaľ neboli zaznamenané.")
        return

    result = pd.concat(frames, ignore_index=True)
    result.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  ✔  Export hotový: {out_csv} ({len(result)} záznamov)")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if not acquire_lock():
        print("  Ukončené – bot už beží alebo lock súbor existuje.")
        return

    try:
        profiles = load_sender_profiles()
        mode = choose_mode()

        if mode == "overview":
            print_files_overview(profiles)
            print_sending_overview(profiles)
            print(f"\n{'─' * 56}")
            if ask_yes_no("Exportovať zoznam nefunkčných (INVALID) adries do CSV?"):
                export_invalid_emails_report(profiles)
            if ask_yes_no("Prerenderovať HTML náhľad logov (môže pri veľkom logu chvíľu trvať)?"):
                for key in profiles:
                    _, log_csv, log_html = sender_paths(key, WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE)
                    if regenerate_log_html(log_csv, log_html):
                        print(f"  ✔  {log_html} aktualizovaný")
            return

        if mode == "bounces":
            targets = [("maros", WNP_LOG_CSV_FILE), ("matus", MATUS_AI_LOG_CSV_FILE)]
            for key, log_csv in targets:
                profile = profiles.get(key)
                if not profile or not os.path.exists(log_csv):
                    continue
                summary = process_bounces(profile, log_csv, apply=False)
                print_bounce_summary(profile["from_name"], summary)
                if summary["matched"] and ask_yes_no(
                        f"Zapísať {summary['matched']} záznamov do {log_csv} (záloha sa vytvorí) a zaradiť BOUNCED do frontu?"):
                    process_bounces(profile, log_csv, apply=True)
                    print(f"  ✔  {log_csv} aktualizovaný; fronta sa pošle pri ďalšom ostrom behu daného účtu.")
            return

        wnp_template_str = None
        campaign_templates = None
        c6_collab_template = None

        if mode in ("wnp_plain", "wnp_html"):
            print(f"Načítavam šablónu: {WNP_TEMPLATE_FILE}")
            wnp_template_str = load_wnp_template()
            if mode == "wnp_html":
                c6_collab_template = load_c6_collab_template()
            base_state, base_log_csv, base_log_html = WNP_STATE_FILE, WNP_LOG_CSV_FILE, WNP_LOG_HTML_FILE
        else:
            print("Načítavam šablóny C1–C6...")
            campaign_templates = load_campaign_templates()
            c6_collab_template = load_c6_collab_template()
            base_state, base_log_csv, base_log_html = STATE_FILE, LOG_CSV_FILE, LOG_HTML_FILE

        # Matúšova AI šablóna je fixná a nezávislá od zvoleného režimu vyššie –
        # ak chýba, Matúš sa nižšie len preskočí (ostatní odosielatelia nie sú ovplyvnení).
        try:
            matus_ai_template_str = load_matus_ai_template()
        except FileNotFoundError as e:
            matus_ai_template_str = None
            print(f"  ⚠  {e}")

        print("\nNačítavam CSV databázu (môže to chvíľu trvať)...")
        df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", low_memory=False)
        df = normalize_columns(df)

        selected_keys = choose_senders(profiles)

        runs = []
        for key in selected_keys:
            profile = profiles[key]
            if not profile.get("email_pass") or profile["email_pass"] == "REPLACE_ME":
                print(f"  ✘  Chýba heslo pre '{profile['from_name']}' v {SENDERS_CONFIG_FILE} "
                      f"(pole 'email_pass' pre '{key}'). Tento odosielateľ sa preskakuje.")
                continue

            print(f"\n{'═' * 56}")
            print(f"  NASTAVENIE PRE: {profile['from_name']} <{profile['from_addr']}>")
            print(f"{'═' * 56}")

            rate, delay = ask_emails_per_hour(
                profile["from_name"],
                profile["emails_per_hour_default"],
                profile["emails_per_hour_max"],
            )
            session_limit = ask_session_limit(profile["from_name"])

            if key == "matus":
                # Matúš má vlastnú, fixnú AI šablónu – nezávisle od zvoleného
                # režimu (WNP/kampane) a s vlastnými, oddelenými súbormi.
                if not matus_ai_template_str:
                    print(f"  ✘  Chýba šablóna '{MATUS_AI_TEMPLATE_FILE}' – Matúš sa preskakuje.")
                    continue
                run_mode = "matus_ai"
                template_fn = make_matus_ai_template_fn(matus_ai_template_str, profile)
                state_file = MATUS_AI_STATE_FILE
                log_csv = MATUS_AI_LOG_CSV_FILE
                log_html = MATUS_AI_LOG_HTML_FILE
            else:
                run_mode = mode
                if mode in ("wnp_plain", "wnp_html"):
                    template_fn = make_wnp_template_fn(
                        wnp_template_str, mode, profile, c6_collab_template=c6_collab_template
                    )
                else:
                    template_fn = make_campaigns_template_fn(campaign_templates, c6_collab_template, profile)
                state_file, log_csv, log_html = sender_paths(key, base_state, base_log_csv, base_log_html)

            state = load_state(state_file)
            state, _ = migrate_state_to_csv(state, df, CSV_FILE)

            runs.append({
                "key": key,
                "profile": profile,
                "run_mode": run_mode,
                "template_fn": template_fn,
                "state_file": state_file,
                "log_csv": log_csv,
                "log_html": log_html,
                "delay": delay,
                "rate": rate,
                "state": state,
                "session_limit": session_limit,
            })

        if not runs:
            print("\n  Žiadny platný odosielateľ na spustenie (skontroluj senders_config.json). Končím.")
            return

        shared_state_file = os.path.splitext(base_state)[0] + "_parallel.json"
        start_index = parallel_start_index(shared_state_file, base_state)
        print(f"\n  Spoločné pokračovanie: riadok {start_index} (index CSV od nuly).")
        print("  Riadky sa prideľujú striedavo; už oslovené domény/kontakty sa preskakujú.")
        delays = {r["key"]: max(MIN_GLOBAL_SEND_SECONDS, r["delay"]) for r in runs}
        pacer = SendPacer(min(delays.values()) / len(runs), delays)
        print(f"  Spoločný rozostup: najmenej {pacer.delay:.2f} s; limity účtov zostávajú zachované.")
        _, _, preview_retry, preview_stats = load_contact_state(df, runs)
        for run in runs:
            preview_state = dict(run["state"], start_index=start_index)
            show_database_overview(df, preview_state, run["log_csv"], run["rate"],
                                   label=run["profile"]["from_name"], queue=len(preview_retry[run["key"]]))
            elsewhere, missing = preview_stats[run["key"]]
            if elsewhere or missing:
                print(f"  (do fronty sa nezaradilo: {elsewhere} – doména už doručená inou adresou, "
                      f"{missing} – doména nie je v novom CSV)")

        print(f"\n{'─' * 56}")
        for test_index, run in enumerate(runs, -len(runs)):
            profile = run["profile"]
            if ask_yes_no(f"Chceš odoslať testovací email pre {profile['from_name']} na {TEST_RECIPIENT}?"):
                run_mode = run["run_mode"]
                test_campaign = "C1"
                if run_mode in ("wnp_html", "campaigns"):
                    test_campaign = choose_test_campaign()

                pacer.register(test_index)
                try:
                    success = run_test_send(
                        mode=run_mode,
                        sender=dict(profile, _send_pacer=pacer, _send_index=test_index),
                        wnp_template_str=wnp_template_str,
                        campaign_templates=campaign_templates,
                        c6_collab_template=c6_collab_template,
                        matus_ai_template_str=matus_ai_template_str,
                        test_campaign=test_campaign,
                    )
                finally:
                    pacer.cancel(test_index)

                print(f"\n{'─' * 56}")
                if success:
                    print(f"  ✔  Test OK pre {profile['from_name']} – email dorazil na {TEST_RECIPIENT}")
                else:
                    print(f"  ✘  Test ZLYHAL pre {profile['from_name']}")
                    if not ask_yes_no("Test sa nepodaril. Aj tak pokračovať s ostrým posielaním pre tohto odosielateľa?"):
                        run["skip"] = True

        runs = [r for r in runs if not r.get("skip")]
        if not runs:
            print("\n  Žiadny odosielateľ nepokračuje do ostrého posielania. Končím.")
            return

        if not ask_yes_no(f"\nPokračovať s ostrým posielaním pre {len(runs)} odosielateľa/ov?"):
            print("  Ukončené. Spusti bot znova pre ostré posielanie.")
            return

        # Zachováme vlastníctvo domén oslovených pôvodným účtom.
        bootstrap_domain_owners_from_legacy_logs(
            DOMAIN_OWNER_FILE, DOMAIN_OWNER_LOCK_FILE,
            [WNP_LOG_CSV_FILE, LOG_CSV_FILE], profiles["maros"]["from_addr"],
        )
        coordinator = ParallelCoordinator(df, runs, shared_state_file, base_state)
        coordinator.pacer = pacer
        print(f"\n  Začínam ostré posielanie ({len(runs)} odosielateľ(ov))...")
        print(f"  CSV riadkov:  {len(df)}")
        print(f"  Spoločný progres: {shared_state_file}")
        for r in runs:
            print(f"  → {r['profile']['from_name']}: state={r['state_file']}, log={r['log_csv']}")
        print(f"{'─' * 56}\n")

        if len(runs) == 1:
            r = runs[0]
            send_loop(
                df, r["template_fn"], r["profile"], r["state_file"], r["log_csv"], r["log_html"],
                r["delay"], r["session_limit"], label=r["profile"]["from_name"],
                coordinator=coordinator,
            )
        else:
            threads = []
            errors = []
            def worker(run):
                try:
                    send_loop(df, run["template_fn"], run["profile"], run["state_file"],
                              run["log_csv"], run["log_html"], run["delay"], run["session_limit"],
                              label=run["profile"]["from_name"], coordinator=coordinator)
                except Exception as error:
                    errors.append(error)
                    coordinator.stop()
                    coordinator.deactivate(run["key"])
            for r in runs:
                t = threading.Thread(
                    target=worker, args=(r,),
                )
                threads.append(t)
            for t in threads:
                t.start()
            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                coordinator.stop()
                print("\n  Zastavujem oba účty a čakám na uloženie rozpracovaného posielania...")
            finally:
                for t in threads:
                    t.join()
            if errors:
                raise RuntimeError(f"Odosielanie zastavené kvôli chybe: {errors[0]}") from errors[0]
    finally:
        release_lock()


if __name__ == "__main__":
    main()
