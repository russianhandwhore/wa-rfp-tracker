"""
City of Tacoma Procurement Scraper
Sources:
  - Supplies:     https://tacoma.gov/.../supplies-solicitations/
  - Services:     https://tacoma.gov/.../services-solicitations/
  - Public Works: https://tacoma.gov/.../public-works-and-improvements-solicitations/
  - Small Works:  https://tacoma.gov/.../small-works-roster/

Static HTML tables — requests + BeautifulSoup only, no Playwright needed.
Columns: Specification Number | Type | Due Date | Time Due | Title + PDF link | Date Issued
"""

import json
import traceback
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
from categorize import categorize_rfp

SOURCE_NAME     = "City of Tacoma Purchasing"
SOURCE_PLATFORM = "City of Tacoma"
AGENCY          = "City of Tacoma"
CONTACT_EMAIL   = "bids@tacoma.gov"
CONTACT_NAME    = "Tacoma Purchasing"

PAGES = [
    {
        "url":      "https://tacoma.gov/government/departments/finance/procurement-and-payables-division/purchasing/contracting-opportunities/supplies-solicitations/",
        "category": "Supplies",
        "label":    "Supplies",
    },
    {
        "url":      "https://tacoma.gov/government/departments/finance/procurement-and-payables-division/purchasing/contracting-opportunities/services-solicitations/",
        "category": "Services",
        "label":    "Services",
    },
    {
        "url":      "https://tacoma.gov/government/departments/finance/procurement-and-payables-division/purchasing/contracting-opportunities/public-works-and-improvements-solicitations/",
        "category": "Construction",
        "label":    "Public Works",
    },
    {
        "url":      "https://tacoma.gov/government/departments/finance/procurement-and-payables-division/purchasing/small-works-roster/",
        "category": "Construction",
        "label":    "Small Works",
    },
]

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
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def scrape_page(page_config):
    url      = page_config["url"]
    category = page_config["category"]
    label    = page_config["label"]
    rfps     = []

    print(f"  Fetching {label}: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 403:
            print(f"  [WARN] 403 Forbidden for {label} — skipping")
            return rfps
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERROR] Could not fetch {label}: {e}")
        return rfps

    print(f"  Status: {resp.status_code} | {len(resp.text):,} chars")
    soup = BeautifulSoup(resp.text, "lxml")

    # Find all tables on the page (there may be multiple — one per solicitation type)
    tables = soup.find_all("table")
    print(f"  Found {len(tables)} table(s)")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            spec_num  = clean_text(cells[0].get_text())
            rfp_type  = clean_text(cells[1].get_text())
            due_str   = clean_text(cells[2].get_text())
            # cells[3] = time due — we'll append to due_date as-is (ISO date only)
            title_cell = cells[4]
            issued_str = clean_text(cells[5].get_text()) if len(cells) > 5 else None

            # Title is plain text BEFORE the links in the cell
            # Links are: "Specification" (PDF), "Register for Bid Holders List", "Addendum"
            links = title_cell.find_all("a", href=True)
            spec_url = None
            for link in links:
                href = link["href"]
                if "cms.tacoma.gov" in href and ".pdf" in href.lower():
                    spec_url = href

            # Remove all link elements to isolate the plain-text title
            title_cell_copy = BeautifulSoup(str(title_cell), "lxml")
            for a in title_cell_copy.find_all("a"):
                a.decompose()
            title = clean_text(title_cell_copy.get_text())

            if not title or len(title) < 4:
                continue
            if not spec_num:
                continue

            due_date    = parse_date(due_str)
            posted_date = parse_date(issued_str)

            # Skip if already expired
            if due_date and datetime.fromisoformat(due_date) < datetime.now():
                continue

            raw_data = json.dumps({
                "spec_number": spec_num,
                "spec_pdf_url": spec_url,
                "page_category": label,
            })

            categories = categorize_rfp(title, None)
            # Override with page-level category if keyword match is empty
            if categories == ["Misc"] and category != "Misc":
                categories = [category]

            fingerprint = generate_fingerprint(spec_num, SOURCE_PLATFORM, "")

            rfp = {
                "title":                   title,
                "ref_number":              spec_num,
                "detail_url":              spec_url or url,
                "source_url":              url,
                "due_date":                due_date,
                "posted_date":             posted_date,
                "status":                  "active",
                "description":             None,
                "department":              None,
                "rfp_type":                rfp_type or None,
                "agency":                  AGENCY,
                "source_name":             SOURCE_NAME,
                "source_platform":         SOURCE_PLATFORM,
                "contact_name":            CONTACT_NAME,
                "contact_email":           CONTACT_EMAIL,
                "categories":              categories,
                "includes_inclusion_plan": False,
                "fingerprint":             fingerprint,
                "raw_data":                raw_data,
            }
            rfps.append(rfp)

    print(f"  Found {len(rfps)} active RFPs for {label}")
    return rfps


def run():
    print(f"Starting City of Tacoma scraper at {datetime.now()}")
    supabase    = None
    all_rfps    = []
    total_saved = 0
    error_msg   = None
    status      = "failed"

    try:
        supabase = get_supabase_client()
        for page_config in PAGES:
            rfps = scrape_page(page_config)
            all_rfps.extend(rfps)

        # Deduplicate by fingerprint
        seen   = set()
        unique = []
        for rfp in all_rfps:
            if rfp["fingerprint"] not in seen:
                seen.add(rfp["fingerprint"])
                unique.append(rfp)
        all_rfps = unique

        print(f"\nTotal City of Tacoma RFPs: {len(all_rfps)}")
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
