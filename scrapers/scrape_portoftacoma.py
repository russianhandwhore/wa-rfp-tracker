"""
Port of Tacoma Procurement Scraper
Source: https://www.portoftacoma.com/business/contracting/procurement

Strategy:
  1. Fetch listing page — find all /business/contracting/procurement/<slug> links
  2. Fetch each detail page — parse bid number, summary, contact, bids due, docs
  3. Only keep RFPs where Bids Due is in the future (open only)

Static Drupal CMS — requests + BeautifulSoup only, no Playwright needed.
"""

import json
import re
import time
import traceback
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
from categorize import categorize_rfp

SOURCE_NAME     = "Port of Tacoma Procurement"
SOURCE_PLATFORM = "Port of Tacoma"
AGENCY          = "Port of Tacoma"
BASE_URL        = "https://www.portoftacoma.com"
LISTING_URL     = "https://www.portoftacoma.com/business/contracting/procurement"
DETAIL_WORKERS  = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def parse_date(date_str):
    """Parse dates like 'Wed, 04/22/2026 - 02:00PM' or '04/22/2026'."""
    if not date_str:
        return None
    s = date_str.strip()
    # Strip day-of-week prefix e.g. "Wed, "
    s = re.sub(r'^[A-Za-z]+,\s*', '', s)
    # Strip time portion e.g. " - 02:00PM"
    s = re.sub(r'\s*-\s*\d+:\d+[AP]M.*$', '', s).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def get_open_links():
    """
    Paginate through all procurement listing pages and return URLs of Open RFPs only.
    Reads directly from the table rows — avoids picking up nav/footer links.
    """
    links = []
    seen  = set()
    page  = 0

    while True:
        url = f"{LISTING_URL}?page={page}"
        print(f"  Fetching listing page {page}: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [ERROR] Could not fetch listing page {page}: {e}")
            break

        soup  = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table")
        if not table:
            print(f"  No table found on page {page} — stopping pagination")
            break

        rows_found = 0
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            rows_found += 1
            # Columns: Bid # | Title | Service Type | Status
            status = clean_text(cells[3].get_text()).lower()
            if status != "open":
                continue

            link = cells[1].find("a", href=True)
            if not link:
                continue

            href = link["href"].rstrip("/")
            full_url = BASE_URL + href if href.startswith("/") else href
            if full_url not in seen:
                seen.add(full_url)
                bid_num      = clean_text(cells[0].get_text())
                service_type = clean_text(cells[2].get_text())
                links.append({
                    "url":          full_url,
                    "bid_num":      bid_num,
                    "service_type": service_type,
                })

        print(f"  Page {page}: {rows_found} rows, {len([l for l in links if l])} open so far")

        # Stop if no more pages — check for a "next page" link
        next_link = soup.find("a", title=lambda t: t and "next page" in t.lower())
        if not next_link:
            break

        page += 1
        time.sleep(0.5)  # polite delay

    return links


def fetch_detail(url):
    """
    Fetch one Port of Tacoma procurement detail page.
    Returns a dict of parsed fields, or None if closed/expired/invalid.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"    [WARN] HTTP {resp.status_code} for {url}")
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # Title — find the h2 that is the actual procurement title
        # The page has multiple h2s ("Submit Questions", "Holders list", and the real title)
        title = None
        main = soup.find("main") or soup.find(id="main-content") or soup
        skip_headings = {"submit questions", "procurement", "holders list", "breadcrumb"}
        for h2 in main.find_all("h2"):
            text = clean_text(h2.get_text())
            if text and text.lower() not in skip_headings:
                title = text
                break

        # Fallback: page <title> minus site name
        if not title:
            page_title = soup.find("title")
            if page_title:
                title = clean_text(page_title.get_text()).replace(" | Port of Tacoma", "").strip()

        if not title:
            return None

        page_text = soup.get_text(" ", strip=True)

        # Bid Number
        bid_num = None
        m = re.search(r'Bid Number:\s*([A-Z0-9][\w\-]+)', page_text)
        if m:
            bid_num = m.group(1).strip()

        # Bids Due date
        bids_due = None
        m = re.search(r'Bids Due:\s*([\w,/:\s\-APM]+?)(?:\s{2,}|Pre-Bid|Questions Due|Contact:|Estimated|$)', page_text)
        if m:
            bids_due = parse_date(m.group(1))

        # Skip if already expired or no due date (likely closed)
        if not bids_due:
            return None
        if datetime.fromisoformat(bids_due) < datetime.now():
            return None

        # Procurement Summary (description) — text between "Procurement Summary:" and next field
        description = None
        m = re.search(r'Procurement Summary:\s*(.+?)(?:Contact:|Bids Due:|Estimated Cost)', page_text)
        if m:
            desc = clean_text(m.group(1))
            description = desc[:600] if desc else None

        # Contact name and email from mailto link
        contact_name  = None
        contact_email = None
        contact_link  = soup.find("a", href=re.compile(r'^mailto:'))
        if contact_link:
            href = contact_link["href"]
            email_m = re.match(r'mailto:([^?]+)', href)
            if email_m:
                contact_email = email_m.group(1).strip().lower()
            link_text = clean_text(contact_link.get_text())
            if link_text and "@" not in link_text:
                # Link text is "Name, Title" — keep just the name
                contact_name = link_text.split(",")[0].strip()

        # Documents — all S3 PDF links on the page
        docs = []
        for a in soup.find_all("a", href=re.compile(r'portoftacoma\.com.*\.pdf', re.IGNORECASE)):
            doc_url  = a["href"]
            doc_name = clean_text(a.get_text()) or doc_url.split("/")[-1]
            docs.append({"name": doc_name, "url": doc_url})

        raw_data = json.dumps({
            "bid_number": bid_num,
            "documents":  docs,
            "source_url": url,
        })

        return {
            "title":         title,
            "ref_number":    bid_num,
            "detail_url":    url,
            "due_date":      bids_due,
            "description":   description,
            "contact_name":  contact_name,
            "contact_email": contact_email,
            "raw_data":      raw_data,
        }

    except Exception as e:
        print(f"    [WARN] Detail fetch failed {url}: {e}")
        return None


def run():
    print(f"Starting Port of Tacoma scraper at {datetime.now()}")
    supabase    = None
    all_rfps    = []
    total_saved = 0
    error_msg   = None
    status      = "failed"

    try:
        supabase = get_supabase_client()

        # Step 1: paginate through listing and collect open RFP links
        open_links = get_open_links()
        print(f"  Found {len(open_links)} open procurement links across all pages")

        if not open_links:
            print("  [WARN] No open procurements found — all may be closed or page structure changed")
            status = "success"
            return

        # Step 2: fetch all detail pages concurrently
        print(f"  Fetching {len(open_links)} detail pages ({DETAIL_WORKERS} at a time)...")
        details = []
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
            future_to_entry = {executor.submit(fetch_detail, entry["url"]): entry for entry in open_links}
            for future in as_completed(future_to_entry):
                entry = future_to_entry[future]
                try:
                    result = future.result()
                except Exception as e:
                    print(f"    [WARN] {entry['url']}: {e}")
                    result = None
                if result:
                    # Enrich with service_type from listing table
                    result["service_type"] = entry.get("service_type")
                    # Use bid_num from listing if detail page didn't parse one
                    if not result.get("ref_number"):
                        result["ref_number"] = entry.get("bid_num")
                    details.append(result)

        print(f"  {len(details)} open (future due date) RFPs found")

        # Step 3: build and save RFP records
        for detail in details:
            categories  = categorize_rfp(detail["title"], detail["description"])
            fingerprint = generate_fingerprint(
                detail["ref_number"] or detail["title"],
                SOURCE_PLATFORM,
                detail["due_date"] or "",
            )

            all_rfps.append({
                "title":                   detail["title"],
                "ref_number":              detail["ref_number"],
                "detail_url":              detail["detail_url"],
                "source_url":              LISTING_URL,
                "due_date":                detail["due_date"],
                "posted_date":             None,
                "status":                  "active",
                "description":             detail["description"],
                "department":              None,
                "rfp_type":                detail.get("service_type") or None,
                "agency":                  AGENCY,
                "source_name":             SOURCE_NAME,
                "source_platform":         SOURCE_PLATFORM,
                "contact_name":            detail["contact_name"],
                "contact_email":           detail["contact_email"],
                "categories":              categories,
                "includes_inclusion_plan": False,
                "fingerprint":             fingerprint,
                "raw_data":                detail["raw_data"],
            })

        print(f"\nTotal Port of Tacoma open RFPs: {len(all_rfps)}")
        if all_rfps:
            print(f"  Sample: {all_rfps[0]['title'][:60]}")
            print(f"  Due:    {all_rfps[0]['due_date']}")
            print(f"  Ref:    {all_rfps[0]['ref_number']}")

        if all_rfps:
            for i in range(0, len(all_rfps), 50):
                batch = all_rfps[i:i + 50]
                supabase.table("rfps").upsert(batch, on_conflict="fingerprint").execute()
                total_saved += len(batch)
                print(f"Saved batch of {len(batch)}")

        status = "success"
        print(f"Done! {total_saved} RFPs saved")

    except Exception as e:
        error_msg = str(e)
        status    = "failed"
        print(f"Scraper failed: {e}")
        traceback.print_exc()

    finally:
        if supabase:
            log_scrape(
                supabase=supabase,
                source_name=SOURCE_NAME,
                status=status,
                rfps_found=len(all_rfps),
                rfps_new=total_saved,
                rfps_updated=0,
                error_message=error_msg,
            )


if __name__ == "__main__":
    run()
