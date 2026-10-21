# sending_bot.py

import pandas as pd
from jinja2 import Template
import smtplib
import imaplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imaplib import Time2Internaldate
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import email.utils
import re
import html
from bs4 import BeautifulSoup  # pip install beautifulsoup4

# ─── SMTP / IMAP ─────────────────────────────────────────────────────────────
SMTP_HOST = "smtp.hostcreators.sk"
SMTP_PORT = 465

IMAP_HOST = "imap.hostcreators.sk"
IMAP_PORT = 993

EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

FROM_NAME  = "Webnaprenajom"
FROM_ADDR  = "maros@webnaprenajom.sk"
REPLY_TO_ADDR = "maros@webnaprenajom.sk"

IMAP_SENT_FOLDER = "Sent"

# ─── SÚBORY ──────────────────────────────────────────────────────────────────
CSV_FILE = "audit_results_2026_marketing_v4_1.csv"

# Kampane C1–C6
STATE_FILE     = "bot_state.json"
LOG_CSV_FILE   = "send_log.csv"
LOG_HTML_FILE  = "send_log.html"

# Web na prenájom
WNP_TEMPLATE_FILE = "web-na-prenajom-email.html"
WNP_SUBJECT       = "Moderný web a AI riešenia pre Vašu firmu"
WNP_STATE_FILE    = "bot_state_wnp.json"
WNP_LOG_CSV_FILE  = "send_log_wnp.csv"
WNP_LOG_HTML_FILE = "send_log_wnp.html"

# ─── KONFIGURÁCIA ────────────────────────────────────────────────────────────
LOCAL_TZ = ZoneInfo("Europe/Bratislava")

# Prázdna množina = všetky kampane C1–C6; inak napr. {"C1", "C3"}
ACTIVE_CAMPAIGNS = set()

GLOBAL_BLUE_URL   = "https://boost.salelogics.sk/"
GLOBAL_BLUE_LABEL = "Zistiť potenciál stránky"

TEST_RECIPIENT = "maros.seman@azet.sk"
TEST_DOMAIN    = "testdomena.sk"


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERAKTÍVNE MENU
# ═══════════════════════════════════════════════════════════════════════════════

def choose_mode() -> str:
    """Vráti 'wnp' alebo 'campaigns'."""
    banner = (
        "\n"
        "╔══════════════════════════════════════════════════════════╗\n"
        "║          EMAIL SENDING BOT  –  výber šablóny            ║\n"
        "╠══════════════════════════════════════════════════════════╣\n"
        "║  1  →  Web na prenájom  (web-na-prenajom-email.html)    ║\n"
        "║  2  →  Kampane C1–C6   (template_c1.html … c6.html)    ║\n"
        "╚══════════════════════════════════════════════════════════╝"
    )
    print(banner)
    while True:
        choice = input("\nZadaj číslo (1 alebo 2): ").strip()
        if choice == "1":
            print("\n✔  Zvolený režim: Web na prenájom\n")
            return "wnp"
        elif choice == "2":
            print("\n✔  Zvolený režim: Kampane C1–C6\n")
            return "campaigns"
        else:
            print("  ⚠  Neplatná voľba. Zadaj 1 alebo 2.")


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


# ═══════════════════════════════════════════════════════════════════════════════
#  NAČÍTANIE ŠABLÓN
# ═══════════════════════════════════════════════════════════════════════════════

def load_campaign_templates() -> dict:
    templates = {}
    for code in ["C1", "C2", "C3", "C4", "C5", "C6"]:
        filename = f"template_{code.lower()}.html"
        with open(filename, "r", encoding="utf-8") as f:
            templates[code] = f.read()
    return templates


def load_wnp_template() -> str:
    if not os.path.exists(WNP_TEMPLATE_FILE):
        raise FileNotFoundError(
            f"\nŠablóna '{WNP_TEMPLATE_FILE}' nebola nájdená!\n"
            "Uisti sa, že súbor existuje v rovnakom adresári ako bot."
        )
    with open(WNP_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════════════════
#  SPRÁVA STAVU
# ═══════════════════════════════════════════════════════════════════════════════

def load_state(state_file: str) -> dict:
    if not os.path.exists(state_file):
        return {"last_index": -1, "start_index": 0,
                "sent_count_window": 0, "window_start": None}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return {"last_index": -1, "start_index": 0,
                "sent_count_window": 0, "window_start": None}
    state.setdefault("start_index", 0)
    state.setdefault("sent_count_window", 0)
    state.setdefault("window_start", None)
    return state


def save_state(state: dict, state_file: str) -> None:
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGOVANIE
# ═══════════════════════════════════════════════════════════════════════════════

def append_logs(new_records: list, log_csv: str, log_html: str) -> None:
    if not new_records:
        return
    new_df = pd.DataFrame(new_records)
    if os.path.exists(log_csv):
        try:
            old_df = pd.read_csv(log_csv, encoding="utf-8-sig")
            df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            df = new_df
    else:
        df = new_df
    df.to_csv(log_csv, index=False, encoding="utf-8-sig")
    with open(log_html, "w", encoding="utf-8") as f:
        f.write(df.to_html(index=False, escape=False))


# ═══════════════════════════════════════════════════════════════════════════════
#  POMOCNÉ FUNKCIE
# ═══════════════════════════════════════════════════════════════════════════════

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


def limit_issue_list(details_html: str, max_items: int = 5) -> str:
    if not isinstance(details_html, str) or not details_html.strip():
        return ""
    soup = BeautifulSoup(details_html, "html.parser")
    ul = soup.find("ul")
    if not ul:
        return details_html
    lis = ul.find_all("li")
    if not lis:
        return details_html
    for li in lis[max_items:]:
        li.decompose()
    for li in ul.find_all("li"):
        text = li.get_text(strip=True)
        if not text:
            continue
        if " – " in text:
            title, rest = text.split(" – ", 1)
        elif "-" in text:
            title, rest = text.split("-", 1)
        else:
            title, rest = text, ""
        li.clear()
        strong = soup.new_tag("strong")
        strong.string = title.strip()
        li.append(strong)
        if rest.strip():
            li.append(" – " + rest.strip())
    return str(ul)


def create_mime_message(to_email: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"]    = subject
    msg["From"]       = f"{FROM_NAME} <{FROM_ADDR}>"
    msg["To"]         = to_email
    msg["Message-ID"] = email.utils.make_msgid(domain="salelogics.sk")
    msg["Reply-To"]   = REPLY_TO_ADDR
    msg.attach(MIMEText(html_to_plain_text(html_body), "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def _smtp_send_one(to_email: str, subject: str, html_body: str) -> dict:
    """
    Nízkoúrovňová funkcia: pripojí sa, odošle jeden email, uloží do Sent.
    Vráti dict so status, error_msg, delivery_ok, message_id.
    """
    result = {
        "status": "FAILED",
        "error_msg": "",
        "delivery_ok": False,
        "message_id": "",
        "sent_at": "",
    }
    smtp_conn = imap_conn = None
    try:
        smtp_conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        smtp_conn.login(EMAIL_USER, EMAIL_PASS)

        imap_conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap_conn.login(EMAIL_USER, EMAIL_PASS)

        msg         = create_mime_message(to_email, subject, html_body)
        raw_message = msg.as_string()
        result["message_id"] = msg.get("Message-ID", "")

        sent    = False
        attempt = 0
        stop    = False
        while not sent and attempt < 3 and not stop:
            attempt += 1
            try:
                smtp_conn.sendmail(FROM_ADDR, [to_email], raw_message)
                sent = True
            except (smtplib.SMTPServerDisconnected,
                    smtplib.SMTPConnectError,
                    smtplib.SMTPHeloError):
                try:
                    if smtp_conn:
                        try:
                            smtp_conn.quit()
                        except Exception:
                            pass
                    smtp_conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
                    smtp_conn.login(EMAIL_USER, EMAIL_PASS)
                except Exception as ce:
                    result["error_msg"] = str(ce)
                    time.sleep(5)
            except smtplib.SMTPResponseException as re_err:
                result["error_msg"] = str(re_err.smtp_error)
                stop = True
                break

        if sent:
            try:
                imap_conn.append(
                    IMAP_SENT_FOLDER,
                    "\\Seen",
                    Time2Internaldate(time.time()),
                    raw_message.encode("utf-8"),
                )
                result["delivery_ok"] = True
            except Exception as ie:
                result["error_msg"] = f"Sent but not saved: {ie}"

            result["status"]  = "OK"
            result["sent_at"] = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    except Exception as e:
        result["error_msg"] = str(e)
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
                  wnp_template_str: str = None,
                  campaign_templates: dict = None,
                  test_campaign: str = "C1") -> bool:
    """
    Odošle JEDEN testovací email na TEST_RECIPIENT.
    Vráti True ak odoslanie prebehlo úspešne.
    """
    divider = "─" * 56
    print(f"\n{divider}")
    print(f"  TESTOVACÍ EMAIL  →  {TEST_RECIPIENT}")
    print(f"  Doména:          {TEST_DOMAIN}")
    print(f"{divider}")

    if mode == "wnp":
        # ── Web na prenájom test ────────────────────────────────────────────
        jinja_tpl = Template(wnp_template_str)
        html_body = jinja_tpl.render(DOMAIN=TEST_DOMAIN, YEAR="2026")
        subject   = f"[TEST WNP] {WNP_SUBJECT}"

    else:
        # ── Kampane test ────────────────────────────────────────────────────
        if test_campaign not in campaign_templates:
            print(f"  ✘  Šablóna pre kampaň '{test_campaign}' nebola nájdená!")
            return False

        print(f"  Kampaň: {test_campaign}")
        fake_details = (
            "<ul>"
            "<li>Pomalé načítavanie – stránka sa načíta nad 4 sekundy</li>"
            "<li>Chýbajúce meta popisy – 12 stránok bez description</li>"
            "<li>Neoprimalizované obrázky – celková veľkosť 8 MB</li>"
            "</ul>"
        )
        context = {
            "DOMAIN":                    TEST_DOMAIN,
            "PERSONALIZED_SUMMARY":      "",
            "PERSONALIZED_DETAILS_HTML": limit_issue_list(fake_details, max_items=5),
            "EMAIL_SUBJECT":             "Návrh na zlepšenie webu.",
            "BLUE_URL":                  GLOBAL_BLUE_URL,
            "BLUE_LABEL":                GLOBAL_BLUE_LABEL,
            "YEAR":                      "2026",
        }
        html_body = Template(campaign_templates[test_campaign]).render(**context)
        subject   = f"[TEST {test_campaign}] Návrh na zlepšenie webu."

    print(f"  Predmet: {subject}")
    print(f"{divider}")
    print("  Odosielam...", end=" ", flush=True)

    res = _smtp_send_one(TEST_RECIPIENT, subject, html_body)

    if res["status"] == "OK":
        saved = "uložené do Sent ✔" if res["delivery_ok"] else "NEULOŽENÉ do Sent ⚠"
        print(f"✔  OK  ({saved})")
        print(f"  Odoslané: {res['sent_at']}")
        print(f"  Message-ID: {res['message_id']}")
        return True
    else:
        print(f"✘  CHYBA")
        print(f"  Detail: {res['error_msg']}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE FUNKCIE
# ═══════════════════════════════════════════════════════════════════════════════

def make_wnp_template_fn(template_str: str):
    jinja_tpl = Template(template_str)

    def template_fn(row):
        domain    = str(row.get("domain", "")).strip()
        html_body = jinja_tpl.render(DOMAIN=domain, YEAR="2026")
        return WNP_SUBJECT, html_body

    return template_fn


def make_campaigns_template_fn(templates: dict):

    def template_fn(row):
        domain   = str(row.get("domain", "")).strip()
        campaign = str(row.get("campaign", "")).strip()

        if campaign not in templates:
            print(f"  → Preskočené (neznáma kampaň '{campaign}'): {domain}")
            return None

        if ACTIVE_CAMPAIGNS and campaign not in ACTIVE_CAMPAIGNS:
            print(f"  → Preskočené (kampaň '{campaign}' nie je v ACTIVE_CAMPAIGNS): {domain}")
            return None

        raw_details          = str(row.get("personalized_details_html", "") or "").strip()
        personalized_details = limit_issue_list(raw_details, max_items=5)
        blue_url             = str(row.get("blue_url", "") or "").strip() or GLOBAL_BLUE_URL

        context = {
            "DOMAIN":                    domain,
            "PERSONALIZED_SUMMARY":      "",
            "PERSONALIZED_DETAILS_HTML": personalized_details,
            "EMAIL_SUBJECT":             "Návrh na zlepšenie webu.",
            "BLUE_URL":                  blue_url,
            "BLUE_LABEL":                GLOBAL_BLUE_LABEL,
            "YEAR":                      "2026",
        }
        html_body = Template(templates[campaign]).render(**context)
        return "Návrh na zlepšenie webu.", html_body

    return template_fn


# ═══════════════════════════════════════════════════════════════════════════════
#  ODOSIELACIA SLUČKA
# ═══════════════════════════════════════════════════════════════════════════════

def send_loop(df: pd.DataFrame, template_fn, state_file: str,
              log_csv: str, log_html: str) -> None:

    state             = load_state(state_file)
    start_index       = state.get("start_index", 0)
    sent_count_window = state.get("sent_count_window", 0)
    window_start_str  = state.get("window_start")

    if window_start_str:
        window_start = datetime.fromisoformat(window_start_str)
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
    else:
        window_start               = datetime.now(timezone.utc)
        state["window_start"]      = window_start.isoformat()
        state["sent_count_window"] = 0
        save_state(state, state_file)

    already_ok: set = set()
    if os.path.exists(log_csv):
        try:
            log_df = pd.read_csv(log_csv, encoding="utf-8-sig")
            if "email_address" in log_df.columns:
                for _, r in log_df.iterrows():
                    if str(r.get("status", "")) == "OK":
                        i  = int(r.get("index", -1))
                        em = str(r.get("email_address", "")).strip()
                        if i >= 0 and em:
                            already_ok.add((i, em))
        except Exception:
            already_ok = set()

    last_send_time = None

    try:
        for idx, row in df.iterrows():
            if idx < start_index:
                continue

            domain = str(row.get("domain", "")).strip()
            if not domain:
                continue

            raw_emails_field = row.get("emails", "")
            all_emails = (
                extract_all_emails(raw_emails_field)
                if "emails" in df.columns else []
            )
            if not all_emails:
                all_emails = [f"owner@{domain}"]
            all_emails = all_emails[:5]

            result = template_fn(row)
            if result is None:
                continue
            subject, html_body = result

            # Rate-limit okno
            now_utc    = datetime.now(timezone.utc)
            window_age = now_utc - window_start
            if window_age >= timedelta(minutes=59):
                window_start      = now_utc
                sent_count_window = 0

            if sent_count_window >= 199:
                print(f"[{idx}] LIMIT: 199 emailov v 59-min. okne – končím.")
                break

            for email_addr in all_emails:
                if (idx, email_addr) in already_ok or (idx, "") in already_ok:
                    continue

                now_utc = datetime.now(timezone.utc)
                if last_send_time is not None:
                    elapsed = (now_utc - last_send_time).total_seconds()
                    if elapsed < 20:
                        time.sleep(20 - elapsed)

                stop_after_this = False
                res = _smtp_send_one(email_addr, subject, html_body)

                # Skontroluj rate-limit kódy
                if res["status"] == "FAILED" and res["error_msg"]:
                    err_lower = res["error_msg"].lower()
                    if any(c in err_lower for c in ["421", "450", "451", "454"]):
                        stop_after_this = True

                if res["status"] == "OK":
                    last_send_time    = datetime.now(timezone.utc)
                    sent_count_window += 1
                    campaign_tag      = str(row.get("campaign", "WNP"))
                    print(f"[{idx}] OK: {domain} → {email_addr} ({campaign_tag})")
                else:
                    print(f"[{idx}] ERR: {domain} → {email_addr}: {res['error_msg']}")

                append_logs(
                    [{
                        "index":         idx,
                        "domain":        domain,
                        "campaign":      str(row.get("campaign", "WNP")),
                        "email_address": email_addr,
                        "subject":       subject,
                        "status":        res["status"],
                        "error":         res["error_msg"],
                        "sent_at":       res["sent_at"],
                        "delivery_ok":   res["delivery_ok"],
                        "message_id":    res["message_id"],
                    }],
                    log_csv, log_html,
                )

                if res["status"] == "OK":
                    already_ok.add((idx, email_addr))

                state["last_index"]        = idx
                state["start_index"]       = idx + 1
                state["sent_count_window"] = sent_count_window
                state["window_start"]      = window_start.isoformat()
                save_state(state, state_file)

                if stop_after_this:
                    print(f"[{idx}] STOP: rate limit – ukladám stav a končím.")
                    raise KeyboardInterrupt

    except KeyboardInterrupt:
        print("\nPrerušené. Stav uložený.")
        save_state(state, state_file)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # ── 1. Výber režimu ─────────────────────────────────────────────────────
    mode = choose_mode()

    # ── 2. Načítanie šablón podľa režimu ────────────────────────────────────
    wnp_template_str   = None
    campaign_templates = None

    if mode == "wnp":
        print(f"Načítavam šablónu: {WNP_TEMPLATE_FILE}")
        wnp_template_str = load_wnp_template()
        template_fn      = make_wnp_template_fn(wnp_template_str)
        state_file       = WNP_STATE_FILE
        log_csv          = WNP_LOG_CSV_FILE
        log_html         = WNP_LOG_HTML_FILE
    else:
        print("Načítavam šablóny C1–C6...")
        campaign_templates = load_campaign_templates()
        template_fn        = make_campaigns_template_fn(campaign_templates)
        state_file         = STATE_FILE
        log_csv            = LOG_CSV_FILE
        log_html           = LOG_HTML_FILE

    # ── 3. Testovací email ───────────────────────────────────────────────────
    print(f"\n{'─'*56}")
    if ask_yes_no(f"Chceš odoslať testovací email na {TEST_RECIPIENT}?"):

        test_campaign = "C1"
        if mode == "campaigns":
            test_campaign = choose_test_campaign()

        success = run_test_send(
            mode               = mode,
            wnp_template_str   = wnp_template_str,
            campaign_templates = campaign_templates,
            test_campaign      = test_campaign,
        )

        print(f"\n{'─'*56}")
        if success:
            print(f"  ✔  Test OK – email dorazil na {TEST_RECIPIENT}")
            if not ask_yes_no("Pokračovať s ostrým posielaním na CSV?"):
                print("  Ukončené. Spusti bot znova pre ostré posielanie.")
                return
        else:
            print(f"  ✘  Test ZLYHAL")
            if not ask_yes_no("Test sa nepodaril. Aj tak pokračovať s ostrým posielaním?"):
                print("  Ukončené.")
                return

    # ── 4. Ostré posielanie ──────────────────────────────────────────────────
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
    print(f"\n  Začínam ostré posielanie...")
    print(f"  CSV riadkov:  {len(df)}")
    print(f"  State súbor:  {state_file}")
    print(f"  Log CSV:      {log_csv}")
    print(f"{'─'*56}\n")

    send_loop(df, template_fn, state_file, log_csv, log_html)


if __name__ == "__main__":
    main()