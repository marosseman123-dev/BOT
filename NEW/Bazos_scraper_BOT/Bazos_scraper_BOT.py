import argparse
import csv
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
}


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def fetch(session, url, timeout=30):
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def extract_first(pattern, text, flags=0, group=1):
    m = re.search(pattern, text, flags)
    return clean_text(m.group(group)) if m else ""


def normalize_url(url):
    parsed = urlparse(url)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path,
        parsed.params,
        query,
        ""
    ))
    return normalized


def is_bazos_domain(url):
    host = urlparse(url).netloc.lower()
    return host.endswith("bazos.sk") or host.endswith("bazos.cz")


def detect_country(url):
    host = urlparse(url).netloc.lower()
    if host.endswith("bazos.sk"):
        return "sk"
    if host.endswith("bazos.cz"):
        return "cz"
    return ""


def extract_listing_id_from_url(url):
    m = re.search(r"/inzerat/(\d+)/", url, re.I)
    return m.group(1) if m else ""


def parse_list_page(html, current_url):
    soup = BeautifulSoup(html, "lxml")
    listings = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue

        full_url = normalize_url(urljoin(current_url, href))

        if not is_bazos_domain(full_url):
            continue

        if "/inzerat/" not in full_url:
            continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        title = clean_text(a.get_text(" ", strip=True))

        if not title:
            title_tag = a.find_parent(["h1", "h2", "h3"])
            if title_tag:
                title = clean_text(title_tag.get_text(" ", strip=True))

        listings.append({
            "title": title,
            "listing_url": full_url,
        })

    return listings


def extract_total_count(html):
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    m = re.search(r"Zobrazeno\s+\d+\s*-\s*\d+\s+inzerát[ůu]\s+z\s+([\d\s]+)", text, re.I)
    if m:
        return int(re.sub(r"\s+", "", m.group(1)))
    return None


def get_current_range_start(html):
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    m = re.search(r"Zobrazeno\s+(\d+)\s*-\s*(\d+)\s+inzerát[ůu]", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def find_pagination_links(html, current_url):
    soup = BeautifulSoup(html, "lxml")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt = clean_text(a.get_text(" ", strip=True))
        full = normalize_url(urljoin(current_url, href))

        if not is_bazos_domain(full):
            continue

        # CZ paging: /notebook/20/
        # SK paging: /notebooky-20/ alebo podobné
        if re.search(r"/\d+/\??", urlparse(full).path) or re.search(r"-\d+/?$", urlparse(full).path):
            links.append((txt, full))

    return links


def find_next_page(html, current_url):
    soup = BeautifulSoup(html, "lxml")

    # 1. Preferuj explicitné "Další / Ďalšia / Next"
    preferred_texts = {"další", "dalsi", "ďalšia", "ďalsia", "next", ">"}
    for a in soup.find_all("a", href=True):
        txt = clean_text(a.get_text(" ", strip=True)).lower()
        if txt in preferred_texts:
            return normalize_url(urljoin(current_url, a["href"]))

    # 2. Zober číselné odkazy v pageri a vyber najbližší väčší offset
    current_path = urlparse(current_url).path

    def extract_offset(path):
        # CZ /notebook/20/
        m = re.search(r"/(\d+)/?$", path)
        if m:
            return int(m.group(1))
        # SK /notebooky-20/
        m = re.search(r"-(\d+)/?$", path)
        if m:
            return int(m.group(1))
        return 0

    current_offset = extract_offset(current_path)
    candidates = []

    for txt, full in find_pagination_links(html, current_url):
        off = extract_offset(urlparse(full).path)
        if off > current_offset:
            candidates.append((off, full))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    return None


def parse_detail_page(html, url):
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    title = ""
    h1 = soup.find("h1", class_=re.compile(r"nadpisdetail", re.I)) or soup.find("h1")
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))

    price_text = ""
    for pattern in [
        r"Cena:\s*([^\n\r|]+?)\s*(?:\||Kontakt|Zobrazeno|Topování|ID:|$)",
        r"(\d[\d\s\xa0]*\s*(?:Kč|€))",
        r"\b(Dohodou|Cena dohodou)\b",
    ]:
        price_text = extract_first(pattern, page_text, re.I)
        if price_text:
            break

    description = ""
    desc = soup.find("div", class_=re.compile(r"popisdetail", re.I)) or soup.find("div", id=re.compile(r"popis", re.I))
    if desc:
        description = clean_text(desc.get_text("\n", strip=True))

    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = clean_text(meta_desc["content"])

    listing_id = extract_listing_id_from_url(url)

    location = ""
    m = re.search(r"(Lokalita|Okres):\s*([^\n\r|]+)", page_text, re.I)
    if m:
        location = clean_text(m.group(2))

    phone = extract_first(r"Telefon:\s*([+\d][\d\s]+)", page_text, re.I)

    author = ""
    for pattern in [r"Jméno:\s*([^\n\r|]+)", r"Meno:\s*([^\n\r|]+)"]:
        author = extract_first(pattern, page_text, re.I)
        if author:
            break

    posted_date = ""
    m = re.search(r"(Zobrazeno|Vloženo|Pridané):\s*([^\n\r|]+)", page_text, re.I)
    if m:
        posted_date = clean_text(m.group(2))

    return {
        "description": description,
        "listing_id": listing_id,
        "detail_title": title,
        "detail_price_text": price_text,
        "detail_url": url,
        "location": location,
        "phone": phone,
        "author": author,
        "posted_date": posted_date,
        "country": detect_country(url),
    }


def load_scraped_urls(output):
    scraped_urls = set()
    if os.path.exists(output):
        try:
            with open(output, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("listing_url"):
                        scraped_urls.add(normalize_url(row["listing_url"]))
        except Exception as e:
            print(f"[VAROVANIE] Nepodarilo sa prečítať existujúce CSV: {e}")
    return scraped_urls


def scrape_all(start_url, output, delay, max_pages):
    if not is_bazos_domain(start_url):
        raise ValueError("URL musí byť z bazos.sk alebo bazos.cz")

    session = requests.Session()
    session.headers.update(HEADERS)

    scraped_urls = load_scraped_urls(output)

    page_url = normalize_url(start_url)
    page_seen = set()
    listing_urls_seen = set()
    list_rows = []
    page_count = 0

    pages_bar = tqdm(desc="1. Fáza (Stránky)", unit="page")

    try:
        while page_url:
            if page_url in page_seen:
                print(f"[INFO] URL stránky už bola spracovaná, končím: {page_url}")
                break

            if max_pages and page_count >= max_pages:
                break

            page_seen.add(page_url)
            pages_bar.set_postfix_str(page_url[:90])

            try:
                html = fetch(session, page_url)
            except Exception as e:
                tqdm.write(f"[PAGE ERROR] {page_url} -> {e}")
                break

            range_start, range_end = get_current_range_start(html)
            total_count = extract_total_count(html)

            if range_start and range_end and total_count:
                tqdm.write(f"[INFO] Strana zobrazuje {range_start}-{range_end} z {total_count}")

            page_items = parse_list_page(html, page_url)

            if not page_items:
                tqdm.write(f"[INFO] Žiadne inzeráty na stránke: {page_url}")
                break

            new_on_page = 0
            for item in page_items:
                norm_url = normalize_url(item["listing_url"])
                if norm_url not in listing_urls_seen:
                    listing_urls_seen.add(norm_url)
                    list_rows.append(item)
                    new_on_page += 1

            page_count += 1
            pages_bar.update(1)

            next_url = find_next_page(html, page_url)

            # Ak je pager, pokračujeme podľa neho, aj keby stránka mala duplicitné URL.
            if next_url:
                page_url = next_url
                time.sleep(delay)
                continue

            # Keď pager už nie je, sme na konci.
            if new_on_page == 0:
                tqdm.write(f"[INFO] Stránka nepriniesla nové URL a nenašiel sa ďalší pager, končím: {page_url}")
                break

            break

    except KeyboardInterrupt:
        tqdm.write("\n[INFO] Získavanie zoznamu prerušené! Presúvam sa na 2. fázu so získanými URL...")
    finally:
        pages_bar.close()

    pending_rows = [
        item for item in list_rows
        if normalize_url(item["listing_url"]) not in scraped_urls
    ]

    print("\nStav:")
    print(f" - Už v minulosti stiahnuté: {len(scraped_urls)}")
    print(f" - Nájdené URL v zozname: {len(list_rows)}")
    print(f" - Nové na stiahnutie: {len(pending_rows)}")

    if not pending_rows:
        print("Žiadne nové inzeráty. Končím.")
        return []

    fieldnames = [
        "title",
        "listing_url",
        "description",
        "listing_id",
        "detail_title",
        "detail_price_text",
        "detail_url",
        "location",
        "phone",
        "author",
        "posted_date",
        "country",
    ]

    file_exists = os.path.exists(output) and os.path.getsize(output) > 0
    detail_rows = []
    details_bar = tqdm(total=len(pending_rows), desc="2. Fáza (Detaily)", unit="listing")

    with open(output, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        try:
            for item in pending_rows:
                try:
                    html = fetch(session, item["listing_url"])
                    detail = parse_detail_page(html, item["listing_url"])
                    merged = {**item, **detail}

                    writer.writerow(merged)
                    f.flush()
                    detail_rows.append(merged)
                except Exception as e:
                    tqdm.write(f"[DETAIL ERROR] {item['listing_url']} -> {e}")

                details_bar.update(1)
                time.sleep(delay)

        except KeyboardInterrupt:
            tqdm.write("\n[INFO] Sťahovanie detailov prerušené! Dáta sú už uložené v CSV.")
        finally:
            details_bar.close()

    return detail_rows


def main():
    parser = argparse.ArgumentParser(
        description="Bazos scraper (bazos.sk + bazos.cz) with real pager parsing, resume and CSV export."
    )
    parser.add_argument("url", nargs="?", help="Start Bazoš listing URL")
    parser.add_argument("-o", "--output", default="bazos_full_export.csv", help="Output CSV")
    parser.add_argument("-d", "--delay", type=float, default=1.5, help="Delay between requests")
    parser.add_argument("-m", "--max-pages", type=int, default=0, help="Max pages, 0 = unlimited")
    args = parser.parse_args()

    url = args.url
    if not url:
        url = input("Prosím, zadaj URL adresu Bazoš kategórie, ktorú chceš scrapovať: ").strip()

    if not url:
        print("URL nebola zadaná. Ukončujem program.")
        return

    try:
        rows = scrape_all(
            start_url=url,
            output=args.output,
            delay=args.delay,
            max_pages=args.max_pages if args.max_pages > 0 else None,
        )
        if rows:
            print(f"\nHotovo. Pridaných {len(rows)} nových záznamov do {args.output}")
        else:
            print("\nHotovo. Neboli pridané žiadne nové záznamy.")
    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()