import smtplib
import imaplib
import time
import csv
import json
import os
import re

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imaplib import Time2Internaldate
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import email
import email.utils
from email.utils import formataddr, getaddresses
from email.header import Header

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# ============== KONFIGURÁCIA EMAIL ==============

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.hostcreators.sk")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "60"))

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.hostcreators.sk")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

EMAIL_USER = os.environ.get("EMAIL_USER", "mikrocement@art4you.sk")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

FROM_NAME = os.environ.get("FROM_NAME", "ART4YOU")
FROM_ADDR = os.environ.get("FROM_ADDR", "mikrocement@art4you.sk")
REPLY_TO_ADDR = os.environ.get("REPLY_TO_ADDR", "mikrocement@art4you.sk")

IMAP_SENT_FOLDER = os.environ.get("IMAP_SENT_FOLDER", "Sent")
LOCAL_TZ = ZoneInfo("Europe/Prague")

SUBJECT = os.environ.get("SUBJECT", "Mikrocement a profily pro vaše projekty")

BODY_TEXT = """Dobrý den,

jsme dovozcem a distributorem epoxidových mikrocementů značky Quarelli.
Epoxidový mikrocement HARDPOX je unikátní mikrocement, který svými vlastnostmi výrazně převyšuje ostatní značky mikrocementů.

V čem spočívají jeho přednosti?
• aplikace stejného materiálu na stěny i podlahy
• vodoodolnost i bez laku
• extrémní tvrdost
• vysoká variabilita textur a vzorů
• materiál je ředitelný vodou
• bez zápachu, s nízkým obsahem VOC

Nabízíme ucelená řešení a technologické postupy.
Povrchy HARDPOX jsou tvrdší a odolnější než běžné lité podlahy.

Kromě mikrocementů dodáváme také kompletní sortiment profilů:
• terče: https://eshop.art4you.sk/profily-terce/
• schodové ukončovací profily: https://eshop.art4you.sk/profily-schodove-ukoncovacie-profily/
• okapové systémy: https://eshop.art4you.sk/profily-odkvapove-systemy/
• distanční podložky: https://eshop.art4you.sk/profily-distancne-podlozky/
• balkonové profily: https://eshop.art4you.sk/profily-balkonove-profily/
  (včetně rohů, krytek, spojek a doplňků)

CO ZÍSKÁTE SPOLUPRACÍ S NÁMI:
• materiály s vynikajícími vlastnostmi
• školení pro realizátory
• telefonickou technickou podporu při řešení problémů
• konzultace
• pomoc při oceňování zakázek
• produkty, na které se můžete spolehnout
• sortiment nářadí, které k práci potřebujete
• atraktivní systém slev při nákupu materiálu
• aktivně vám pomáháme vyhledávat zakázky z vašeho regionu

Hledáme partnery, se kterými můžeme dlouhodobě spolupracovat na velkoobchodním odběru těchto materiálů nebo na konečné realizaci moderních a odolných povrchů.

KOHO HLEDÁME:
• stavební firmy
• interiérové designéry
• architekty
• realizační týmy a řemeslníky

PROČ SPOLUPRACOVAT:
• rostoucí poptávka po mikrocementu
• možnost rozšířit vaše služby
• flexibilní forma spolupráce

Naše realizace: https://www.art4you.sk/realizacie
Naše mikrocementy: https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/
Naše profily: https://eshop.art4you.sk/profily-terce/
Odolnost našeho mikrocementu si můžete prohlédnout v krátkém videu: https://youtube.com/shorts/qh5nfK8_We0?feature=share

S pozdravem

ART4YOU – mikrocementové povrchy
https://www.art4you.sk/

art4you-IG s.r.o.
Sady nad Torysou 270,
044 41 Sady nad Torysou, Slovensko

IČO: 48148415
DIČ: 2120067312
IČ DPH: SK2120067312

------------------------------------------------------------
Tento e-mail je obchodní nabídkou určenou podnikatelským subjektům.
Pokud si nepřejete dostávat další sdělení, odpovězte prosím na tento
e-mail s předmětem ODHLÁSIT a váš kontakt neprodleně odstraníme.
"""


# ============== KONFIGURÁCIA GOOGLE SHEETS ==============

GS_CREDENTIALS_FILE = os.environ.get(
    "GS_CREDENTIALS_FILE",
    "art4you-email-marketing-fa1116fb8e7f.json"
)
GS_SPREADSHEET_ID = os.environ.get(
    "GS_SPREADSHEET_ID",
    "19D0Hv5YzSqOmpn5iby3ICI7Fg8VwER1840S30LLf8MU"
)
GS_SHEET_RANGE = os.environ.get("GS_SHEET_RANGE", "Log!A1")
GS_READ_RANGE = os.environ.get("GS_READ_RANGE", "Log!A:G")


# ============== KONFIGURÁCIA CSV ==============

CSV_PATH = os.environ.get("CSV_PATH", "output_beton_relevant_cz_with_emails.csv")

# CZ CSV stĺpce (0-based):
# 0: input_line_number, 1: domain, 2: page_title, 3: meta_description,
# 4: schema_types, 5: keywords_matched, 6: is_relevant, 7: confidence,
# 8: error_note, 9: emails
COL_EMAILS_INDEX = int(os.environ.get("COL_EMAILS_INDEX", "9"))
COL_NAZOV_INDEX = int(os.environ.get("COL_NAZOV_INDEX", "2"))
COL_ICO_INDEX = int(os.environ.get("COL_ICO_INDEX", "-1"))

START_ROW = int(os.environ.get("START_ROW", "2"))
DELAY_BETWEEN_EMAILS = int(os.environ.get("DELAY_BETWEEN_EMAILS", "20"))
MAX_FIRMS = 0  # 0 = bez limitu
LOAD_PROGRESS_EVERY_N_ROWS = int(os.environ.get("LOAD_PROGRESS_EVERY_N_ROWS", "2000"))

DEBUG_ROW_EMAIL_COUNTS = os.environ.get("DEBUG_ROW_EMAIL_COUNTS", "false").lower() in (
    "1", "true", "yes", "y", "on"
)

PROGRESS_FILE = os.environ.get("PROGRESS_FILE", "progress.json")
SENT_EMAILS_FILE = os.environ.get("SENT_EMAILS_FILE", "sent_emails.json")


# ============== POMOCNÉ FUNKCIE ==============

def normalize_email(email_addr: str) -> str:
    if not email_addr:
        return ""
    e = str(email_addr).strip().lower()
    if e.startswith("mailto:"):
        e = e[7:]
    e = e.strip().strip("[]()<>;,\"' ")
    return e


def is_valid_email_basic(email_addr: str) -> bool:
    if not email_addr or "@" not in email_addr:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_addr) is not None


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        print("progress.json neexistuje, začínam od začiatku (pred riadkom START_ROW).")
        return START_ROW - 1, START_ROW - 1

    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        last_row_sent = int(data.get("last_row_sent", START_ROW - 1))
        last_row_scanned = int(data.get("last_row_scanned", START_ROW - 1))
        print(
            f"Načítaný progress.json, "
            f"last_row_sent = {last_row_sent}, last_row_scanned = {last_row_scanned}"
        )
        return last_row_sent, last_row_scanned
    except Exception as e:
        print(
            f"Chyba pri čítaní progress.json: {e}, "
            f"začínam od začiatku (pred riadkom START_ROW)."
        )
        return START_ROW - 1, START_ROW - 1


def save_progress(last_row_sent, last_row_scanned):
    tmp = {
        "last_row_sent": int(last_row_sent),
        "last_row_scanned": int(last_row_scanned),
    }
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, ensure_ascii=False, indent=2)
        print(
            f"Progress uložený: "
            f"last_row_sent = {last_row_sent}, last_row_scanned = {last_row_scanned}"
        )
    except Exception as e:
        print(f"CHYBA pri ukladaní progress.json: {e}")


def load_sent_emails_cache():
    if not os.path.exists(SENT_EMAILS_FILE):
        print("sent_emails.json neexistuje.")
        return set()

    try:
        with open(SENT_EMAILS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        sent = set()
        if isinstance(data, list):
            for item in data:
                e = normalize_email(item)
                if e:
                    sent.add(e)
        elif isinstance(data, dict):
            for key in data.keys():
                e = normalize_email(key)
                if e:
                    sent.add(e)

        print(f"Načítaných {len(sent)} emailov zo sent_emails.json")
        return sent
    except Exception as e:
        print(f"Chyba pri čítaní sent_emails.json: {e}")
        return set()


def save_sent_emails_cache(sent_emails):
    try:
        with open(SENT_EMAILS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(sent_emails), f, ensure_ascii=False, indent=2)
        print(f"sent_emails.json uložený, počet emailov: {len(sent_emails)}")
    except Exception as e:
        print(f"CHYBA pri ukladaní sent_emails.json: {e}")


def get_sheets_service():
    print("Inicializujem Google Sheets service...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GS_CREDENTIALS_FILE, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)
    print("Google Sheets service inicializovaný.")
    return service


def append_gsheet_log(service, row_values):
    body = {"values": [row_values]}
    service.spreadsheets().values().append(
        spreadsheetId=GS_SPREADSHEET_ID,
        range=GS_SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


def build_sent_cache_from_sheet(service):
    print("Načítavam historické emaily z Google Sheetu...")

    result = service.spreadsheets().values().get(
        spreadsheetId=GS_SPREADSHEET_ID,
        range=GS_READ_RANGE,
    ).execute()

    values = result.get("values", [])
    sent_emails = set()

    if not values:
        print("Google Sheet neobsahuje žiadne dáta.")
        return sent_emails

    for row in values[1:]:
        to_email = row[3].strip() if len(row) > 3 else ""
        status = row[4].strip().upper() if len(row) > 4 else ""

        e = normalize_email(to_email)
        if e and status == "OK":
            sent_emails.add(e)

    print(f"Zo Sheetu načítaných {len(sent_emails)} unikátnych úspešne odoslaných emailov.")
    if sent_emails:
        save_sent_emails_cache(sent_emails)
    return sent_emails


def build_sent_cache_from_imap(imap_client):
    print(f"Načítavam historické emaily z IMAP priečinka '{IMAP_SENT_FOLDER}'...")
    sent_emails = set()

    status, _ = imap_client.select(IMAP_SENT_FOLDER)
    if status != "OK":
        raise Exception(f"Nepodarilo sa otvoriť IMAP folder '{IMAP_SENT_FOLDER}'")

    status, data = imap_client.search(None, "ALL")
    if status != "OK":
        raise Exception("Nepodarilo sa načítať zoznam správ z IMAP Sent priečinka.")

    msg_ids = data[0].split()
    total = len(msg_ids)
    print(f"V IMAP Sent priečinku je {total} správ.")

    for idx, msg_id in enumerate(msg_ids, start=1):
        status, msg_data = imap_client.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (TO)])")
        if status != "OK":
            continue

        if not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        if not raw:
            continue

        msg = email.message_from_bytes(raw)
        to_header = msg.get("To", "")
        addresses = getaddresses([to_header])

        for _, addr in addresses:
            e = normalize_email(addr)
            if e and is_valid_email_basic(e):
                sent_emails.add(e)

        if idx % 1000 == 0:
            print(f"Spracovaných {idx}/{total} IMAP správ, unikátnych emailov doteraz: {len(sent_emails)}")

    print(f"Z IMAP načítaných {len(sent_emails)} unikátnych emailov.")
    if sent_emails:
        save_sent_emails_cache(sent_emails)
    return sent_emails


def ensure_sent_emails_cache(gs_service, imap_client):
    # Ak evidenčný súbor existuje, je smerodajný (aj keď je prázdny) –
    # neobnovujeme zo Sheetu/IMAP, aby sa nenaťahali staré (SK) kontakty.
    if os.path.exists(SENT_EMAILS_FILE):
        return load_sent_emails_cache()

    sent_emails = set()
    try:
        sent_emails = build_sent_cache_from_sheet(gs_service)
        if sent_emails:
            return sent_emails
    except Exception as e:
        print(f"WARNING: nepodarilo sa vytvoriť cache zo Sheetu: {e}")

    try:
        sent_emails = build_sent_cache_from_imap(imap_client)
        if sent_emails:
            return sent_emails
    except Exception as e:
        print(f"WARNING: nepodarilo sa vytvoriť cache z IMAP: {e}")

    print("Nebolo možné vytvoriť cache zo Sheetu ani z IMAP. Pokračujem s prázdnou cache.")
    return set()


def create_message(to_email: str, subject: str, body_text: str):
    msg = MIMEMultipart("alternative")

    msg["Subject"] = subject
    msg["From"] = formataddr((str(Header(FROM_NAME, "utf-8")), FROM_ADDR))
    msg["To"] = to_email
    msg["Reply-To"] = REPLY_TO_ADDR
    msg["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = email.utils.make_msgid(domain="art4you.sk")

    part1 = MIMEText(body_text, "plain", "utf-8")

    html = """
<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #222;">
    <p>Dobrý den,</p>
    <p>
      jsme dovozcem a distributorem epoxidových mikrocementů značky <b>Quarelli</b>.
      Epoxidový mikrocement <b>HARDPOX</b> je unikátní mikrocement, který svými vlastnostmi
      výrazně převyšuje ostatní značky mikrocementů.
    </p>
    <p><b>V čem spočívají jeho přednosti?</b><br>
      &bull; aplikace stejného materiálu na stěny i podlahy<br>
      &bull; vodoodolnost i bez laku<br>
      &bull; extrémní tvrdost<br>
      &bull; vysoká variabilita textur a vzorů<br>
      &bull; materiál je ředitelný vodou<br>
      &bull; bez zápachu, s nízkým obsahem VOC
    </p>
    <p>
      Nabízíme ucelená řešení a technologické postupy.<br>
      Povrchy <b>HARDPOX</b> jsou tvrdší a odolnější než běžné lité podlahy.
    </p>
    <p><b>Kromě mikrocementů dodáváme také kompletní sortiment profilů:</b><br>
      &bull; <a href="https://eshop.art4you.sk/profily-terce/">terče</a><br>
      &bull; <a href="https://eshop.art4you.sk/profily-schodove-ukoncovacie-profily/">schodové ukončovací profily</a><br>
      &bull; <a href="https://eshop.art4you.sk/profily-odkvapove-systemy/">okapové systémy</a><br>
      &bull; <a href="https://eshop.art4you.sk/profily-distancne-podlozky/">distanční podložky</a><br>
      &bull; <a href="https://eshop.art4you.sk/profily-balkonove-profily/">balkonové profily</a>
      (včetně <a href="https://eshop.art4you.sk/profily-rohy-balkonovych-profilov/">rohů</a>,
      <a href="https://eshop.art4you.sk/profily-krytky-balkonovych-profilov/">krytek</a>,
      <a href="https://eshop.art4you.sk/profily-spojky-balkonovych-profilov/">spojek</a> a
      <a href="https://eshop.art4you.sk/profily-balkonove-profily-doplnky/">doplňků</a>)
    </p>
    <p><b>CO ZÍSKÁTE SPOLUPRACÍ S NÁMI:</b><br>
      &bull; materiály s vynikajícími vlastnostmi<br>
      &bull; školení pro realizátory<br>
      &bull; telefonickou technickou podporu při řešení problémů<br>
      &bull; konzultace<br>
      &bull; pomoc při oceňování zakázek<br>
      &bull; produkty, na které se můžete spolehnout<br>
      &bull; sortiment nářadí, které k práci potřebujete<br>
      &bull; atraktivní systém slev při nákupu materiálu<br>
      &bull; aktivně vám pomáháme vyhledávat zakázky z vašeho regionu
    </p>
    <p>
      Hledáme partnery, se kterými můžeme dlouhodobě spolupracovat
      na velkoobchodním odběru těchto materiálů nebo na konečné realizaci
      moderních a odolných povrchů.
    </p>
    <p><b>KOHO HLEDÁME:</b><br>
      &bull; stavební firmy<br>
      &bull; interiérové designéry<br>
      &bull; architekty<br>
      &bull; realizační týmy a řemeslníky
    </p>
    <p><b>PROČ SPOLUPRACOVAT:</b><br>
      &bull; rostoucí poptávka po mikrocementu<br>
      &bull; možnost rozšířit vaše služby<br>
      &bull; flexibilní forma spolupráce
    </p>
    <p>
      Naše realizace:
      <a href="https://www.art4you.sk/realizacie">www.art4you.sk/realizacie</a>
    </p>
    <p>
      Naše mikrocementy:
      <a href="https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/">eshop.art4you.sk/hardpox-epoxidovy-mikrocement</a>
    </p>
    <p>
      Naše profily:
      <a href="https://eshop.art4you.sk/profily-terce/">eshop.art4you.sk/profily</a>
    </p>
    <p>
      Odolnost našeho mikrocementu si můžete prohlédnout v krátkém videu:
      <a href="https://youtube.com/shorts/qh5nfK8_We0?feature=share">youtube.com/shorts/qh5nfK8_We0</a>
    </p>
    <p>
      S pozdravem<br>
      <b>ART4YOU – mikrocementové povrchy</b><br>
      <a href="https://www.art4you.sk/">www.art4you.sk</a><br><br>
      art4you-IG s.r.o.<br>
      Sady nad Torysou 270,<br>
      044 41 Sady nad Torysou, Slovensko<br><br>
      IČO: 48148415<br>
      DIČ: 2120067312<br>
      IČ DPH: SK2120067312
    </p>
    <p style="font-size: 12px; color: #888; border-top: 1px solid #ddd; padding-top: 8px;">
      Tento e-mail je obchodní nabídkou určenou podnikatelským subjektům.
      Pokud si nepřejete dostávat další sdělení, odpovězte prosím na tento e-mail
      s předmětem <b>ODHLÁSIT</b> a váš kontakt neprodleně odstraníme.
    </p>
  </body>
</html>
"""
    part2 = MIMEText(html, "html", "utf-8")

    msg.attach(part1)
    msg.attach(part2)
    return msg


def extract_emails_from_cell(cell_value):
    if not cell_value:
        return []

    text = str(cell_value).strip()
    if not text:
        return []

    raw_parts = [p.strip() for p in text.split(",") if p.strip()]

    emails = []
    for part in raw_parts:
        sub_parts = (
            part.replace("(", " ")
            .replace(")", " ")
            .replace("<", " ")
            .replace(">", " ")
            .split()
        )
        for sp in sub_parts:
            if "@" in sp:
                e = normalize_email(sp)
                if e:
                    emails.append(e)
                break

    seen = set()
    uniq = []
    for e in emails:
        if e and e not in seen:
            seen.add(e)
            uniq.append(e)

    return uniq


def filter_email_domain(email_addr: str) -> bool:
    if not email_addr:
        return False
    email_lower = email_addr.lower()
    allowed_suffixes = (".cz", "@gmail.com")
    return email_lower.endswith(allowed_suffixes)


def pick_first_valid_unsent_email(cell_value, sent_emails=None):
    if sent_emails is None:
        sent_emails = set()

    for e in extract_emails_from_cell(cell_value):
        e = normalize_email(e)
        if not e:
            continue
        if not is_valid_email_basic(e):
            continue
        if not filter_email_domain(e):
            continue
        if e in sent_emails:
            continue
        return e

    return None


def load_all_emails_from_csv_with_end(path, start_row_for_run, end_row_for_run=None, sent_emails=None):
    print(f"Načítavam CSV súbor: {path}")

    if sent_emails is None:
        sent_emails = set()

    all_emails = []
    firms_with_emails = set()
    last_row_scanned_this_run = start_row_for_run - 1

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)

        for csv_row_num, row_tuple in enumerate(reader, start=1):
            if csv_row_num < start_row_for_run:
                continue

            if end_row_for_run is not None and csv_row_num > end_row_for_run:
                break

            last_row_scanned_this_run = csv_row_num

            if MAX_FIRMS and len(firms_with_emails) >= MAX_FIRMS:
                print(f"Dosiahnutý limit {MAX_FIRMS} firiem už pri načítavaní z CSV, končím.")
                break

            def cell_at(idx_zero_based):
                if idx_zero_based < 0 or idx_zero_based >= len(row_tuple):
                    return ""
                return row_tuple[idx_zero_based]

            emails_cell = cell_at(COL_EMAILS_INDEX)
            ico = cell_at(COL_ICO_INDEX)
            nazov = cell_at(COL_NAZOV_INDEX)

            first_email = pick_first_valid_unsent_email(emails_cell, sent_emails=sent_emails)

            if first_email:
                firms_with_emails.add(csv_row_num)
                all_emails.append(
                    {
                        "email": first_email,
                        "row": csv_row_num,
                        "ico": ico,
                        "nazov": nazov,
                    }
                )

                if DEBUG_ROW_EMAIL_COUNTS:
                    print(
                        f"Riadok {csv_row_num}: firma má vybraný 1 email, "
                        f"aktuálne firiem s emailom v tomto behu: {len(firms_with_emails)}"
                    )

            if (
                LOAD_PROGRESS_EVERY_N_ROWS
                and (csv_row_num - start_row_for_run + 1) % LOAD_PROGRESS_EVERY_N_ROWS == 0
            ):
                print(
                    f"… riadok {csv_row_num}: {len(firms_with_emails)} firiem s emailom v tomto behu "
                    f"(celkom {len(all_emails)} adries doteraz)"
                )

    print(f"Po načítaní a filtrovaní: {len(all_emails)} emailov od {len(firms_with_emails)} firiem.")
    print(f"V tomto behu bol naposledy načítaný riadok: {last_row_scanned_this_run}")
    return all_emails, last_row_scanned_this_run


def smtp_connect():
    print("Pripájam sa na SMTP server...")
    smtp_client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
    smtp_client.login(EMAIL_USER, EMAIL_PASS)
    print("SMTP prihlásenie OK.")
    return smtp_client


def smtp_ensure_alive(smtp_client):
    """Server zatvára nečinné spojenie po určitom čase (medzi emailmi čakáme
    DELAY_BETWEEN_EMAILS sekúnd). NOOP overí spojenie predtým, než sa naň
    skúsi poslať email, aby sa nepremárnil prvý pokus o odoslanie."""
    try:
        status, _ = smtp_client.noop()
        if status == 250:
            return smtp_client
    except Exception:
        pass

    print("SMTP spojenie je neaktívne (idle timeout), pripájam sa znova...")
    try:
        smtp_client.quit()
    except Exception:
        pass
    return smtp_connect()


def imap_connect():
    print("Pripájam sa na IMAP server...")
    imap_client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap_client.login(EMAIL_USER, EMAIL_PASS)
    print("IMAP prihlásenie OK.")
    return imap_client


def compute_db_overview(sent_emails):
    """Jeden prechod cez CSV – spočíta prehľad databázy."""
    domains = 0
    all_valid_emails = set()
    firms_with_valid = 0
    firms_waiting = 0

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader, start=1):
            if i < START_ROW:  # preskoč hlavičku
                continue
            domains += 1
            cell = row[COL_EMAILS_INDEX] if COL_EMAILS_INDEX < len(row) else ""
            valid = [
                e for e in extract_emails_from_cell(cell)
                if is_valid_email_basic(e) and filter_email_domain(e)
            ]
            if valid:
                firms_with_valid += 1
                all_valid_emails.update(valid)
                if any(e not in sent_emails for e in valid):
                    firms_waiting += 1

    return {
        "domains": domains,
        "total_emails": len(all_valid_emails),
        "emails_sent": len(all_valid_emails & sent_emails),
        "firms_with_valid": firms_with_valid,
        "firms_waiting": firms_waiting,
        "firms_done": firms_with_valid - firms_waiting,
    }


def print_overview(stats):
    waiting = stats["firms_waiting"]
    secs = waiting * DELAY_BETWEEN_EMAILS
    h, m = secs // 3600, (secs % 3600) // 60
    print("\n" + "=" * 60)
    print("  PREHĽAD DATABÁZY (CZ)")
    print("=" * 60)
    print(f"  Emailových adries v databáze spolu: {stats['total_emails']}")
    print(f"  Domén (firiem) v databáze:          {stats['domains']}")
    print(f"  Firiem s platným kontaktom:         {stats['firms_with_valid']}")
    print(f"  Už odoslané (adries z databázy):    {stats['emails_sent']}")
    print(f"  Ešte čaká na odoslanie (firiem):    {waiting}")
    print(f"  (pošle sa 1 email na firmu, tempo 1 email / {DELAY_BETWEEN_EMAILS} s)")
    print(f"  Odhadovaný čas odosielania:         ~{h} h {m} min")
    print("=" * 60)


def send_test_email(smtp, imap, to_email):
    print(f"\nOdosielam SKÚŠOBNÝ email na {to_email} ...")
    msg = create_message(to_email, SUBJECT, BODY_TEXT)
    raw_message = msg.as_string()
    try:
        smtp.send_message(msg)
        print(f"OK: skúšobný email odoslaný na {to_email}")
    except Exception as e:
        print(f"CHYBA pri odosielaní skúšobného emailu: {e}")
        return
    try:
        imap.append(
            IMAP_SENT_FOLDER,
            "\\Seen",
            Time2Internaldate(time.time()),
            raw_message.encode("utf-8"),
        )
        print("OK: skúšobný email uložený do priečinka Sent")
    except Exception as e:
        print(f"WARNING: skúšobný email sa neuložil do Sent: {e}")


def ask_emails_per_hour():
    """Opýta sa na tempo posielania a vráti rozostup medzi emailmi v sekundách."""
    global DELAY_BETWEEN_EMAILS
    default_per_hour = 3600 / DELAY_BETWEEN_EMAILS if DELAY_BETWEEN_EMAILS else 0
    while True:
        raw = input(
            f"\nKoľko emailov za hodinu chceš odoslať? "
            f"(Enter = predvolené, ~{default_per_hour:.0f}/h): "
        ).strip()
        if not raw:
            return
        try:
            per_hour = float(raw.replace(",", "."))
            if per_hour <= 0:
                raise ValueError
        except ValueError:
            print("Neplatné číslo, skús to znova.")
            continue
        DELAY_BETWEEN_EMAILS = max(1, round(3600 / per_hour))
        print(
            f"OK: rozostup medzi emailmi nastavený na {DELAY_BETWEEN_EMAILS} s "
            f"(~{per_hour:.0f} emailov/hod)."
        )
        return


def main():
    smtp = None
    imap = None
    gs_service = None

    print("ŠTART skriptu...")
    ask_emails_per_hour()

    try:
        smtp = smtp_connect()
        imap = imap_connect()
        gs_service = get_sheets_service()

        sent_emails = ensure_sent_emails_cache(gs_service, imap)
        print(f"Počet emailov v evidencii odoslaných: {len(sent_emails)}")

        # --- PREHĽAD DATABÁZY ---
        stats = compute_db_overview(sent_emails)
        print_overview(stats)

        # --- 1) SKÚŠOBNÉ EMAILY (na ľubovoľnú adresu, aj viackrát) ---
        while True:
            test_to = input(
                "\nZadaj adresu pre SKÚŠOBNÝ email a stlač Enter\n"
                "(prázdny Enter = preskočiť a pokračovať na ostré odosielanie): "
            ).strip()
            if not test_to:
                break
            test_to = normalize_email(test_to)
            if is_valid_email_basic(test_to):
                send_test_email(smtp, imap, test_to)
            else:
                print(f"Neplatná adresa '{test_to}', skús to znova.")

        # --- 2) POKRAČOVAŤ / ODZNOVA ---
        last_row_sent, last_row_scanned = load_progress()
        ans_mode = input(
            "\nChceš POKRAČOVAŤ v posielaní tam, kde si naposledy skončil (P),\n"
            "alebo začať ODZNOVA od začiatku databázy (O)? [P/O] (Enter = P): "
        ).strip().upper()

        if ans_mode in ("O", "ODZNOVA"):
            start_row_for_run = START_ROW
            last_row_sent = START_ROW - 1
            last_row_scanned = START_ROW - 1
            save_progress(last_row_sent, last_row_scanned)
            print(f"\nRežim: ODZNOVA – od začiatku databázy (riadok {start_row_for_run}).")
            print("Kontakty, ktorým už bolo odoslané, sa preskočia podľa evidencie odoslaných.")
        else:
            start_row_for_run = max(last_row_sent + 1, START_ROW)
            print(f"\nRežim: POKRAČOVAŤ – od riadku {start_row_for_run}.")
        end_row_for_run = None

        confirm = input(
            f"\nSpustiť odosielanie na čakajúce firmy (tempo 1 email / {DELAY_BETWEEN_EMAILS} s)? [A/N] (Enter = A): "
        ).strip().upper()
        if confirm not in ("", "A", "ANO", "Y", "YES"):
            print("Odosielanie zrušené používateľom.")
            return

        recipients, last_row_scanned_this_run = load_all_emails_from_csv_with_end(
            CSV_PATH,
            start_row_for_run,
            end_row_for_run,
            sent_emails=sent_emails,
        )

        if last_row_scanned_this_run > last_row_scanned:
            last_row_scanned = last_row_scanned_this_run
            save_progress(last_row_sent, last_row_scanned)

        total = len(recipients)
        print(f"Načítaných emailových adries po filtrovaní v tomto behu: {total}")

        if total == 0:
            print("V danom rozsahu riadkov nebol nájdený žiadny nový vhodný email na odoslanie, končím.")
            return

        sent_firms = set()

        for idx, rec in enumerate(recipients):
            to_email = normalize_email(rec["email"])
            firm_row = rec["row"]
            ico = rec.get("ico", "")
            nazov = rec.get("nazov", "")

            if to_email in sent_emails:
                print(f"[{idx + 1}/{total}] SKIP: {to_email} už existuje v sent cache.")
                continue

            if firm_row not in sent_firms:
                if MAX_FIRMS and len(sent_firms) >= MAX_FIRMS:
                    print(f"Dosiahnutý limit {MAX_FIRMS} firiem v tomto behu, končím.")
                    break
                sent_firms.add(firm_row)
                print(
                    f"Nová firma, riadok {firm_row}. "
                    f"Aktuálne počet firiem v tomto behu: {len(sent_firms)}"
                )

            print(
                f"Pripravujem a odosielam email na: {to_email} "
                f"(index {idx}/{total - 1}, riadok {firm_row})"
            )

            msg = create_message(to_email, SUBJECT, BODY_TEXT)
            raw_message = msg.as_string()
            message_id = msg.get("Message-ID", "")

            status = "OK"
            error_msg = ""

            smtp = smtp_ensure_alive(smtp)

            try:
                smtp.send_message(msg)
                print(f"[{idx + 1}/{total}] OK: odoslané na {to_email} (firma riadok {firm_row})")
            except (
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
                smtplib.SMTPHeloError,
                smtplib.SMTPSenderRefused,
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPDataError,
                smtplib.SMTPResponseException,
                smtplib.SMTPException,
                OSError,
            ) as send_err:
                print(f"[{idx + 1}/{total}] WARNING: SMTP send zlyhal, skúšam reconnect: {send_err}")
                status = "ERROR"
                error_msg = f"Send error (retry after reconnect): {send_err}"

                try:
                    smtp.quit()
                except Exception:
                    pass

                try:
                    smtp = smtp_connect()
                    print("SMTP reconnect OK, retry send...")
                    smtp.send_message(msg)
                    status = "OK"
                    error_msg = ""
                    print(f"[{idx + 1}/{total}] OK (retry): odoslané na {to_email} (firma riadok {firm_row})")
                except Exception as retry_err:
                    status = "ERROR"
                    error_msg = f"Send retry error: {retry_err}"
                    print(f"[{idx + 1}/{total}] ERR (retry): nepodarilo sa odoslať na {to_email}: {retry_err}")

            sent_local = datetime.now(LOCAL_TZ)
            sent_at_str = sent_local.strftime("%Y-%m-%d %H:%M:%S %Z")

            if status == "OK":
                try:
                    imap.append(
                        IMAP_SENT_FOLDER,
                        "\\Seen",
                        Time2Internaldate(time.time()),
                        raw_message.encode("utf-8"),
                    )
                    print("OK: uložené do priečinka Sent")
                except Exception as imap_err:
                    warn_msg = f"Sent but not saved to Sent: {imap_err}"
                    print(f"WARNING: {warn_msg}")
                    if error_msg:
                        error_msg += " | " + warn_msg
                    else:
                        error_msg = warn_msg

                    try:
                        try:
                            imap.logout()
                        except Exception:
                            pass
                        imap = imap_connect()
                        print("IMAP reconnect OK, retry append...")
                        imap.append(
                            IMAP_SENT_FOLDER,
                            "\\Seen",
                            Time2Internaldate(time.time()),
                            raw_message.encode("utf-8"),
                        )
                        print("OK: uložené do priečinka Sent po reconnecte")
                    except Exception as imap_retry_err:
                        warn_msg2 = f"Append retry error: {imap_retry_err}"
                        print(f"WARNING: {warn_msg2}")
                        if error_msg:
                            error_msg += " | " + warn_msg2
                        else:
                            error_msg = warn_msg2

            try:
                append_gsheet_log(
                    gs_service,
                    [
                        sent_at_str,
                        ico,
                        nazov,
                        to_email,
                        status,
                        error_msg,
                        message_id,
                    ],
                )
                print("OK: log zapísaný do Google Sheets")
            except Exception as gs_err:
                print(f"WARNING: nepodarilo sa zapísať do Google Sheets: {gs_err}")

            if status == "OK":
                sent_emails.add(to_email)
                save_sent_emails_cache(sent_emails)

                if firm_row > last_row_sent:
                    last_row_sent = firm_row
                    save_progress(last_row_sent, last_row_scanned)

            if idx < total - 1:
                print(f"Čakám {DELAY_BETWEEN_EMAILS} sekúnd pred ďalším emailom...")
                time.sleep(DELAY_BETWEEN_EMAILS)

        print("Skript dokončený.")

    except KeyboardInterrupt:
        print("Prerušené používateľom (KeyboardInterrupt).")
    except Exception as e:
        print(f"ERR (global): {e}")
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass


if __name__ == "__main__":
    main()
