import os
import smtplib
import imaplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imaplib import Time2Internaldate
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import email.utils
from email.utils import formataddr
from email.header import Header

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ============== KONFIGURÁCIA EMAIL ==============

SMTP_HOST = "smtp.hostcreators.sk"
SMTP_PORT = 465

IMAP_HOST = "imap.hostcreators.sk"
IMAP_PORT = 993

EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

FROM_NAME = "ART4YOU"
FROM_ADDR = "mikrocement@art4you.sk"
REPLY_TO_ADDR = "mikrocement@art4you.sk"

IMAP_SENT_FOLDER = "Sent"

LOCAL_TZ = ZoneInfo("Europe/Bratislava")

TO_EMAIL = os.environ.get("TO_EMAIL", "")

SUBJECT = "Mikrocement a profily pro vaše projekty"

BODY_TEXT = """Dobrý den,

jsme výhradním dovozcem mikrocementů značky Quarelli a distributorem systému HARDPOX.

Kromě mikrocementů dodáváme také kompletní sortiment profilů (terče, schodové ukončovací profily, okapové systémy, distanční podložky a balkonové profily).

Hledáme partnery, se kterými můžeme dlouhodobě spolupracovat na realizaci moderních a odolných povrchů.

KOHO HLEDÁME:
- stavební firmy
- interiérové designéry
- architekty
- realizační týmy a řemeslníky

CO NABÍZÍME:
- prémiové mikrocementy Quarelli a systém HARDPOX
- kompletní sortiment profilů
- technickou podporu, konzultace a školení
- spolupráci na konkrétních projektech
- výhodné partnerské podmínky

PROČ SPOLUPRACOVAT:
- rostoucí poptávka po mikrocementu
- možnost rozšířit služby
- flexibilní spolupráce

Naše realizace: https://www.art4you.sk/realizacie/
Naše profily: https://eshop.art4you.sk/profily-terce/

S pozdravem,

ART4YOU – mikrocementové povrchy
www.art4you.sk
art4you-IG s.r.o.
Sady nad Torysou 270
044 41 Sady nad Torysou, Slovensko

IČO: 48148415
DIČ: 2120067312
IČ DPH: SK2120067312
"""

# ============== KONFIGURÁCIA GOOGLE SHEETS ==============

GS_CREDENTIALS_FILE = "art4you-email-marketing-fa1116fb8e7f.json"
GS_SPREADSHEET_ID = "19D0Hv5YzSqOmpn5iby3ICI7Fg8VwER1840S30LLf8MU"
GS_SHEET_RANGE = "Log!A1"  # list 'Log'

def get_sheets_service():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GS_CREDENTIALS_FILE, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)
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

# ============== EMAIL SPRÁVA ==============

def create_message(to_email: str, subject: str, body_text: str):
    msg = MIMEMultipart("alternative")

    msg["Subject"] = subject
    msg["From"] = formataddr((str(Header(FROM_NAME, 'utf-8')), FROM_ADDR))
    msg["To"] = to_email
    msg["Reply-To"] = REPLY_TO_ADDR
    msg["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = email.utils.make_msgid(domain="art4you.sk")

    # Plain text verzia (fallback)
    part1 = MIMEText(body_text, "plain", "utf-8")

    # HTML verzia
    html = """\
<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #222;">
    <p>Dobrý den,</p>

    <p>
      jsme výhradním dovozcem mikrocementů značky <b>Quarelli</b>
      a distributorem systému <b>HARDPOX</b>.
    </p>

    <p>
      Kromě mikrocementů dodáváme také kompletní sortiment
      <a href="https://eshop.art4you.sk/profily-terce/">profilů</a>
      (terče, schodové ukončovací profily, okapové systémy,
      distanční podložky a balkonové profily).
    </p>

    <p>
      Hledáme partnery, se kterými můžeme dlouhodobě spolupracovat
      na velkoobchodním odběru těchto materiálů nebo na konečné realizaci moderních a odolných povrchů.
    </p>

    <p><b>KOHO HLEDÁME:</b><br>
      &bull; stavební firmy<br>
      &bull; interiérové designéry<br>
      &bull; architekty<br>
      &bull; realizační týmy a řemeslníky
    </p>

    <p><b>CO NABÍZÍME:</b><br>
      &bull; prémiové mikrocementy <b>Quarelli</b> a systém <b>HARDPOX</b><br>
      &bull; kompletní sortiment profilů<br>
      &bull; technickou podporu, konzultace a školení<br>
      &bull; spolupráci na konkrétních projektech<br>
      &bull; výhodné partnerské podmínky
    </p>

    <p><b>PROČ SPOLUPRACOVAT:</b><br>
      &bull; rostoucí poptávka po mikrocementu<br>
      &bull; možnost rozšířit vaše služby<br>
      &bull; flexibilní forma spolupráce
    </p>

    <p>
      Naše realizace:
      <a href="https://www.art4you.sk/realizacie/">www.art4you.sk/realizacie</a>
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

# ============== MAIN ==============

def main():
    smtp = None
    imap = None
    gs_service = None

    try:
        # init email
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        smtp.login(EMAIL_USER, EMAIL_PASS)

        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, EMAIL_PASS)

        # init sheets
        gs_service = get_sheets_service()

        msg = create_message(TO_EMAIL, SUBJECT, BODY_TEXT)
        raw_message = msg.as_string()
        message_id = msg.get("Message-ID", "")

        smtp.send_message(msg)
        print(f"OK: odoslané na {TO_EMAIL}")

        sent_local = datetime.now(LOCAL_TZ)
        sent_at_str = sent_local.strftime("%Y-%m-%d %H:%M:%S %Z")
        status = "OK"
        error_msg = ""

        try:
            imap.append(
                IMAP_SENT_FOLDER,
                "\\Seen",
                Time2Internaldate(time.time()),
                raw_message.encode("utf-8")
            )
            print("OK: uložené do priečinka Sent")
        except Exception as imap_err:
            print(f"WARNING: nepodarilo sa uložiť do Sent: {imap_err}")
            error_msg = f"Sent but not saved to Sent: {imap_err}"

        # log do Google Sheets – jeden riadok
        try:
            append_gsheet_log(gs_service, [
                sent_at_str,
                "",                 # ico
                "TEST",             # nazov
                TO_EMAIL,
                status,
                error_msg,
                message_id,
            ])
            print("OK: log zapísaný do Google Sheets")
        except Exception as gs_err:
            print(f"WARNING: nepodarilo sa zapísať do Google Sheets: {gs_err}")

    except Exception as e:
        print(f"ERR: {e}")
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
