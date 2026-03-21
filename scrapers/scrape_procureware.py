"""
Procureware Bid Portal Scraper
Covers: City of Spokane, Snohomish County, Community Transit, Grant County PUD
Strategy: Plain HTML pages — no JavaScript required.
Uses requests + BeautifulSoup, no Playwright needed (faster, lighter).
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

# All WA Procureware portals — add more here as needed
PORTALS = [
    {
        "name": "City of Spokane",
        "url": "https://spokane.procureware.com/Bids",
        "base_url": "https://spokane.procureware.com",
    },
    {
        "name": "Snohomish County",
        "url": "https://snoco.procureware.com/Bids",
        "base_url": "https://snoco.procureware.com",
    },
    {
        "name": "Community Transit",
        "url": "https://commtrans.procureware.com/Bids",
        "base_url": "https://commtrans.procureware.com",
    },
]

SOURCE_NAME = "Procureware Bid Portal"

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
    """Parse common date formats found on Procureware pages."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).isoformat()
        except ValueError:
            continue
    return None


def scrape_portal(portal):
    """
    Scrape a single Procureware portal.
    Procureware uses standard paginated HTML tables — no JavaScript needed.
    """
    rfps = []
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Loading {portal['name']} bids page...")
    try:
        resp = session.get(portal["url"], timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Error loading {portal['url']}: {e}")
        return rfps

    soup = BeautifulSoup(resp.text, "lxml")

    # Debug: show page title to confirm we loaded the right page
    title_tag = soup.find("title")
    print(f"  Page title: {title_tag.get_text()[:80] if title_tag else 'N/A'}")

    # Procureware typically lists bids in a table or a list of div cards.
    # We look for both patterns.

    # Pattern 1: Table rows with bid links
    bid_rows = soup.find_all("tr", class_=lambda c: c and "bid" in c.lower())
    if not bid_rows:
        # Pattern 2: Any table row containing a link to a bid detail page
        all_rows = soup.find_all("tr")
        bid_rows = [
            r for r in all_rows
            if r.find("a", href=lambda h: h and ("bid" in h.lower() or "detail" in h.lower()))
        ]

    # Pattern 3: Div-based listings
    if not bid_rows:
        bid_rows = soup.find_all("div", class_=lambda c: c and any(
            kw in c.lower() for kw in ("bid", "solicitation", "opportunity", "project")
        ))

    print(f"  Found {len(bid_rows)} bid rows/cards")

    # If no rows found at all, dump a snippet for debugging
    if not bid_rows:
        print("  WARNING: No bid listings found. Page snippet:")
        print(resp.text[:1000])
        return rfps

    for row in bid_rows:
        # Extract the title and detail link
        link = row.find("a", href=True)
        if not link:
            continue

        title = clean_text(link.get_text())
        if not title or len(title) < 5:
            continue

        href = link["href"]
        if href.startswith("http"):
            detail_url = href
        elif href.startswith("/"):
            detail_url = portal["base_url"] + href
        else:
            detail_url = portal["base_url"] + "/" + href

        # Extract all cell/div text values for the row
        cells = row.find_all(["td", "div"])
        cell_texts = [clean_text(c.get_text()) for c in cells if clean_text(c.get_text())]

        # Try to find a due date among cell texts
        due_date = None
        for text in cell_texts:
            # Look for date patterns like 04/15/2026 or April 15, 2026
            import re
            date_match = re.search(
                r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\w+ \d{1,2},?\s*\d{4})\b", text
            )
            if date_match:
                due_date = parse_date(date_match.group(1))
                if due_date:
                    break

        # Try to find a ref/bid number
        ref_number = None
        for text in cell_texts:
            if re.match(r"^[\w\-]{3,20}$", text) and text != title:
                ref_number = text
                break

        rfps.append({
            "title": title,
            "description": None,
            "agency": portal["name"],
            "department": None,
            "source_url": portal["url"],
            "detail_url": detail_url,
            "ref_number": ref_number,
            "status": "active",
            "rfp_type": None,
            "due_date": due_date,
            "posted_date": None,
            "source_name": SOURCE_NAME,
            "source_platform": "Procureware",
            "contact_name": None,
            "contact_email": None,
            "includes_inclusion_plan": False,
        })

    return rfps


def run():
    print(f"Starting Procureware scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None

    try:
        for portal in PORTALS:
            print(f"\n--- Scraping {portal['name']} ---")
            rfps = scrape_portal(portal)
            print(f"Found {len(rfps)} RFPs for {portal['name']}")

            if rfps:
                sample = rfps[0]
                print(f"  Sample title:   {sample.get('title', '')[:60]}")
                print(f"  Sample due:     {sample.get('due_date')}")

            # Generate fingerprints
            for rfp in rfps:
                rfp["fingerprint"] = generate_fingerprint(
                    rfp.get("title", ""),
                    rfp.get("agency", ""),
                    rfp.get("due_date", "")
                )

            all_rfps.extend(rfps)

        print(f"\nTotal Procureware RFPs: {len(all_rfps)}")

        if all_rfps:
            batch_size = 50
            for i in range(0, len(all_rfps), batch_size):
                batch = all_rfps[i:i + batch_size]
                supabase.table("rfps").upsert(batch, on_conflict="fingerprint").execute()
                total_saved += len(batch)
                print(f"Saved batch of {len(batch)} RFPs")

        status = "success"
        print(f"Done! {total_saved} RFPs saved")

    except Exception as e:
        error_msg = str(e)
        status = "failed"
        print(f"Scraper failed: {e}")
        import traceback
        traceback.print_exc()

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
