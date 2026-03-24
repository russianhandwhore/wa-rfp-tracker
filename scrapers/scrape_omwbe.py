"""
OMWBE Bids & Contracting Opportunities Scraper
Source: https://omwbe.wa.gov/small-business-assistance/bids-contracting-opportunities

Static Drupal CMS page — no JS rendering required.
Table has two columns: Project (title + link) | Closing Date
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

SOURCE_URL = "https://omwbe.wa.gov/small-business-assistance/bids-contracting-opportunities"
BASE_URL = "https://omwbe.wa.gov"
SOURCE_NAME = "OMWBE - Office of Minority and Women's Business Enterprises"
SOURCE_PLATFORM = "OMWBE"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def parse_date(date_str):
    """Parse MM/DD/YY date format from OMWBE table."""
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    print(f"  [WARN] Unrecognised date: '{s}'")
    return None


def scrape_listings():
    """Fetch and parse the OMWBE bids listing page."""
    rfps = []

    print(f"  Fetching {SOURCE_URL}...")
    try:
        resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERROR] Could not fetch OMWBE page: {e}")
        return rfps

    print(f"  Status: {resp.status_code} | {len(resp.text)} chars")

    soup = BeautifulSoup(resp.text, "lxml")

    # Bids are in a <table> with two columns: Project | Closing Date
    table = soup.find("table")
    if not table:
        print("  [ERROR] No table found on OMWBE page")
        return rfps

    rows = table.find_all("tr")
    print(f"  Found {len(rows)} table rows (including header)")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue  # skip header row

        # Column 0: title + link
        title_cell = cells[0]
        link = title_cell.find("a", href=True)
        if not link:
            continue

        title = clean_text(link.get_text()) or None
        if not title:
            continue

        href = link["href"]
        detail_url = (
            href if href.startswith("http")
            else BASE_URL + href
        )

        # Column 1: closing date
        date_text = clean_text(cells[1].get_text())
        due_date = parse_date(date_text)

        rfp = {
            "title": title,
            "detail_url": detail_url,
            "due_date": due_date,
            "source_url": SOURCE_URL,
            "source_name": SOURCE_NAME,
            "source_platform": SOURCE_PLATFORM,
            "status": "active",
            "agency": None,
            "department": None,
            "description": None,
            "ref_number": None,
            "contact_name": None,
            "contact_email": None,
            "posted_date": None,
            "rfp_type": None,
            "includes_inclusion_plan": False,
            "categories": [],
            "raw_data": None,
        }

        rfp["fingerprint"] = generate_fingerprint(
            rfp["title"],
            SOURCE_PLATFORM,
            rfp["due_date"] or "",
        )

        rfps.append(rfp)

    return rfps


def run():
    print(f"Starting OMWBE scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None
    status = "failed"

    try:
        all_rfps = scrape_listings()
        print(f"Total OMWBE listings scraped: {len(all_rfps)}")

        if all_rfps:
            s = all_rfps[0]
            print(f"  Sample title:   {str(s.get('title', ''))[:60]}")
            print(f"  Sample due:     {s.get('due_date')}")
            print(f"  Sample url:     {s.get('detail_url')}")

        if all_rfps:
            batch_size = 50
            for i in range(0, len(all_rfps), batch_size):
                batch = all_rfps[i:i + batch_size]
                try:
                    supabase.table("rfps").upsert(
                        batch, on_conflict="fingerprint"
                    ).execute()
                    total_saved += len(batch)
                    print(f"  Saved batch {i // batch_size + 1}: {len(batch)} records")
                except Exception as batch_err:
                    print(f"  [ERROR] Batch {i // batch_size + 1} failed: {batch_err}")

        status = "success"
        print(f"Done — {total_saved} records saved")

    except Exception as e:
        error_msg = str(e)
        status = "failed"
        print(f"[ERROR] Scraper run failed: {e}")

    finally:
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
