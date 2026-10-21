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
LOCAL_TZ = ZoneInfo("Europe/Bratislava")

SUBJECT = os.environ.get("SUBJECT", "Mikrocement pre vaše projekty")

BODY_TEXT = """Dobrý deň,

sme výhradným dovozcom a distribútorom mikrocementov značky Quarelli pre Slovensko.
Mikrocement HARDPOX je unikátny epoxidový mikrocement, ktorý svojimi vlastnosťami vysoko prevažuje nad ostatnými značkami mikrocementov.

V čom spočívajú jeho benefity?
- aplikácia toho istého materiálu na steny a podlahy
- vodeodolnosť aj bez laku
- extrémna tvrdosť
- vysoká variabilita textúr a vzorov
- materiál je vodou riediteľný
- bez zápachu, s nízkym VOC

Ponúkame ucelené riešenia a technologické postupy.
Povrchy HARDPOX sú tvrdšie a odolnejšie než bežné liate podlahy.

ČO ZÍSKATE SPOLUPRÁCOU S NAMI:
- materiály s vynikajúcimi vlastnosťami
- školenia pre realizátorov
- telefonickú technickú podporu pri riešení problémov
- konzultácie
- pomoc pri naceňovaní zákaziek
- produkty, o ktoré sa viete oprieť
- sortiment náradia, ktoré potrebujete k práci
- atraktívny systém zliav pri nákupoch materiálu
- aktívne vám pomáhame vyhľadávať zákazky z vášho regiónu

Hľadáme partnerov, s ktorými vieme dlhodobo spolupracovať na veľkoobchodnom odbere týchto mikrocementov, alebo konečnej realizácii moderných a odolných povrchov.

KOHO HĽADÁME:
- stavebné firmy
- interiérových dizajnérov
- architektov
- realizačné tímy a remeselníkov

PREČO SPOLUPRACOVAŤ:
- rastúci dopyt po mikrocemente
- možnosť rozšíriť vaše služby
- flexibilná forma spolupráce

Naše realizácie:
[https://www.art4you.sk/realizacie/](https://www.art4you.sk/realizacie/)

Naše mikrocementy:
[https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/](https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/)

Odolnosť nášho mikrocementu si môžete pozrieť v krátkom videu:
[https://youtube.com/shorts/qh5nfK8_We0?feature=share](https://youtube.com/shorts/qh5nfK8_We0?feature=share)

S pozdravom

ART4YOU – mikrocementové povrchy
[https://www.art4you.sk/](https://www.art4you.sk/)

art4you-IG s.r.o.
Sady nad Torysou 270,
044 41 Sady nad Torysou

IČO: 48148415
DIČ: 2120067312
IČ DPH: SK2120067312
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

CSV_PATH = os.environ.get("CSV_PATH", "output_emails_cleaned.csv")

# 0: input_line_number, 1: domain, 2: registrar_id, 3: expiry_date, 4: emails, 5: error_note
COL_EMAILS_INDEX = int(os.environ.get("COL_EMAILS_INDEX", "4"))
COL_NAZOV_INDEX = int(os.environ.get("COL_NAZOV_INDEX", "1"))
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
    sent_emails = load_sent_emails_cache()
    if sent_emails:
        return sent_emails

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
  <body style="font-family: Arial, sans-serif; line-height: 1.6;">
    <p>Dobrý deň,</p>
    <p>
      sme výhradným dovozcom a distribútorom mikrocementov značky <b>Quarelli</b> pre Slovensko.
      Mikrocement <b>HARDPOX</b> je unikátny epoxidový mikrocement, ktorý svojimi vlastnosťami
      vysoko prevažuje nad ostatnými značkami mikrocementov.
    </p>
    <p><b>V čom spočívajú jeho benefity?</b><br>
      - aplikácia toho istého materiálu na steny a podlahy<br>
      - vodeodolnosť aj bez laku<br>
      - extrémna tvrdosť<br>
      - vysoká variabilita textúr a vzorov<br>
      - materiál je vodou riediteľný<br>
      - bez zápachu, s nízkym VOC
    </p>
    <p>
      Ponúkame ucelené riešenia a technologické postupy.<br>
      Povrchy <b>HARDPOX</b> sú tvrdšie a odolnejšie než bežné liate podlahy.
    </p>
    <p><b>ČO ZÍSKATE SPOLUPRÁCOU S NAMI:</b><br>
      - materiály s vynikajúcimi vlastnosťami<br>
      - školenia pre realizátorov<br>
      - telefonickú technickú podporu pri riešení problémov<br>
      - konzultácie<br>
      - pomoc pri naceňovaní zákaziek<br>
      - produkty, o ktoré sa viete oprieť<br>
      - sortiment náradia, ktoré potrebujete k práci<br>
      - atraktívny systém zliav pri nákupoch materiálu<br>
      - aktívne vám pomáhame vyhľadávať zákazky z vášho regiónu
    </p>
    <p>
      Hľadáme partnerov, s ktorými vieme dlhodobo spolupracovať
      na veľkoobchodnom odbere týchto mikrocementov, alebo konečnej realizácii
      moderných a odolných povrchov.
    </p>
    <p><b>KOHO HĽADÁME:</b><br>
      - stavebné firmy<br>
      - interiérových dizajnérov<br>
      - architektov<br>
      - realizačné tímy a remeselníkov
    </p>
    <p><b>PREČO SPOLUPRACOVAŤ:</b><br>
      - rastúci dopyt po mikrocemente<br>
      - možnosť rozšíriť vaše služby<br>
      - flexibilná forma spolupráce
    </p>
    <p>
      Naše realizácie:
      <a href="https://www.art4you.sk/realizacie/">https://www.art4you.sk/realizacie/</a>
    </p>
    <p>
      Naše mikrocementy:
      <a href="https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/">https://eshop.art4you.sk/hardpox-epoxidovy-mikrocement/</a>
    </p>
    <p>
      Odolnosť nášho mikrocementu si môžete pozrieť v krátkom videu:
      <a href="https://youtube.com/shorts/qh5nfK8_We0?feature=share">https://youtube.com/shorts/qh5nfK8_We0?feature=share</a>
    </p>
    <p>
      S pozdravom<br>
      <b>ART4YOU – mikrocementové povrchy</b><br>
      <a href="https://www.art4you.sk/">www.art4you.sk</a><br><br>
      art4you-IG s.r.o.<br>
      Sady nad Torysou 270,<br>
      044 41 Sady nad Torysou<br><br>
      IČO: 48148415<br>
      DIČ: 2120067312<br>
      IČ DPH: SK2120067312
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
    allowed_suffixes = (".sk", ".cz", "@gmail.com")
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


def imap_connect():
    print("Pripájam sa na IMAP server...")
    imap_client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap_client.login(EMAIL_USER, EMAIL_PASS)
    print("IMAP prihlásenie OK.")
    return imap_client


def main():
    smtp = None
    imap = None
    gs_service = None

    print("ŠTART skriptu...")

    try:
        smtp = smtp_connect()
        imap = imap_connect()
        gs_service = get_sheets_service()

        sent_emails = ensure_sent_emails_cache(gs_service, imap)
        print(f"Počet emailov v odoslanej cache na začiatku: {len(sent_emails)}")

        last_row_sent, last_row_scanned = load_progress()

        unfinished_batch = last_row_scanned > last_row_sent

        if unfinished_batch:
            print("\nZISTENIE: Máš rozbehnutý beh (balík).")
            print(f"- Posledný riadok s POSLANÝM emailom: {last_row_sent}")
            print(f"- Posledný riadok, po ktorý boli firmy NAČÍTANÉ: {last_row_scanned}")
            choice = input(
                "Chceš dokončiť tento balík (D) alebo začať nový (N)? [D/N] (Enter = D): "
            ).strip().upper()
            if choice not in ("D", "N"):
                choice = "D"

            if choice == "D":
                start_row_for_run = max(last_row_sent + 1, START_ROW)
                end_row_for_run = last_row_scanned
                print("\nRežim: DOKONČIŤ ROZBEHNUTÝ BALÍK.")
                print(f"Budú sa spracúvať riadky od {start_row_for_run} do {end_row_for_run}.")
            else:
                start_row_for_run = max(last_row_scanned + 1, START_ROW)
                end_row_for_run = None
                print("\nRežim: NOVÝ BEH.")
                print(f"Budú sa načítavať firmy od riadku {start_row_for_run} ďalej.")
        else:
            start_row_for_run = max(last_row_sent + 1, START_ROW)
            end_row_for_run = None
            print("\nŽiadny rozbehnutý balík, začínam nový beh.")
            print(f"Budú sa načítavať firmy od riadku {start_row_for_run} ďalej.")

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

            try:
                smtp.send_message(msg)
                print(f"[{idx + 1}/{total}] OK: odoslané na {to_email} (firma riadok {firm_row})")
            except smtplib.SMTPServerDisconnected as send_err:
                print(f"[{idx + 1}/{total}] WARNING: SMTP disconnected, skúšam reconnect: {send_err}")
                status = "ERROR"
                error_msg = f"Send error (disconnect): {send_err}"

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

            except (
                smtplib.SMTPConnectError,
                smtplib.SMTPHeloError,
                smtplib.SMTPSenderRefused,
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPDataError,
                smtplib.SMTPException,
            ) as send_err:
                status = "ERROR"
                error_msg = f"Send error: {send_err}"
                print(f"[{idx + 1}/{total}] ERR: nepodarilo sa odoslať na {to_email}: {send_err}")

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