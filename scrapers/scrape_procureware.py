"""
Procureware Bid Portal Scraper
Covers: City of Spokane, Snohomish County, Community Transit

Fixes:
1. Only saves bids with a future due date
2. Visits each detail page in parallel (5 at a time) to get real title + description
3. Scrapes document download links from the BidDocuments tab
4. Stores documents in raw_data as {"documents": [{"name": "...", "url": "..."}]}
"""

import asyncio
import json
import re
from datetime import datetime, timezone
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
from bs4 import BeautifulSoup

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

# Max concurrent detail-page fetches — keeps us polite and avoids timeouts
CONCURRENCY = 5

GUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).isoformat()
        except ValueError:
            continue
    return None


def is_future(date_iso):
    """Return True if the ISO date string is in the future."""
    if not date_iso:
        return False
    try:
        dt = datetime.fromisoformat(date_iso)
        # Make timezone-aware if needed
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(tz=timezone.utc)
    except Exception:
        return False


def parse_listing_page(html, portal):
    """
    Parse the main /Bids listing page.
    Returns a list of minimal dicts: {ref_number, detail_url, due_date}
    Full title/description comes from the detail page.
    """
    entries = []
    soup = BeautifulSoup(html, "lxml")

    bid_links = [
        a for a in soup.find_all("a", href=True)
        if GUID_PATTERN.search(a["href"]) and "/Bids/" in a["href"]
    ]

    print(f"  Found {len(bid_links)} bid links on listing page")

    seen = set()
    for link in bid_links:
        href = link["href"]
        if href in seen:
            continue
        seen.add(href)

        detail_url = (
            href if href.startswith("http")
            else portal["base_url"] + (href if href.startswith("/") else "/" + href)
        )

        # The link text is the ref/bid number (e.g. "RFP 6488-26")
        ref_number = clean_text(link.get_text()) or None

        # Look for a due date in the surrounding row
        container = link.find_parent(["tr", "div", "li"]) or link
        container_text = container.get_text(" ", strip=True)
        due_date = None
        m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", container_text)
        if m:
            due_date = parse_date(m.group(1))

        entries.append({
            "ref_number": ref_number,
            "detail_url": detail_url,
            "due_date": due_date,
        })

    return entries


async def fetch_detail(page, entry, portal, semaphore):
    """
    Visit a single bid detail page and extract:
    - Real title / description
    - Document list from the BidDocuments tab
    """
    async with semaphore:
        detail_url = entry["detail_url"]
        docs_url = detail_url + "?t=BidDocuments"

        title = entry.get("ref_number") or ""
        description = None
        documents = []

        try:
            # Load the BidDocuments tab directly
            await page.goto(docs_url, timeout=40000, wait_until="networkidle")
            await asyncio.sleep(3)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # --- Extract real title ---
            # Procureware shows bid name in an h2/h3 or a specific element
            for selector in ["h2", "h3", ".bid-title", ".project-title", "[class*='title']"]:
                tag = soup.select_one(selector)
                if tag:
                    text = clean_text(tag.get_text())
                    # Skip navigation/generic headings
                    if text and len(text) > 5 and text not in ("Bids", "Home", "Documents"):
                        title = text
                        break

            # --- Extract description ---
            for selector in [".bid-description", ".description", "[class*='description']", "p"]:
                tags = soup.select(selector)
                for tag in tags:
                    text = clean_text(tag.get_text())
                    if text and len(text) > 30:
                        description = text[:600]
                        break
                if description:
                    break

            # --- Extract document download links ---
            # Procureware document links follow: /BidDocument/Download/{guid}
            # or /Documents/Download/{guid}
            doc_links = soup.find_all(
                "a",
                href=re.compile(r"/(BidDocument|Document)/Download/", re.I)
            )
            for doc in doc_links:
                doc_href = doc["href"]
                doc_name = clean_text(doc.get_text()) or "Document"
                doc_url = (
                    doc_href if doc_href.startswith("http")
                    else portal["base_url"] + doc_href
                )
                documents.append({"name": doc_name, "url": doc_url})

            # Also look for any PDF/doc links on the page
            if not documents:
                for a in soup.find_all("a", href=re.compile(r"\.(pdf|docx?|xlsx?|zip)$", re.I)):
                    href = a["href"]
                    name = clean_text(a.get_text()) or href.split("/")[-1]
                    url = href if href.startswith("http") else portal["base_url"] + href
                    documents.append({"name": name, "url": url})

            print(f"    {entry['ref_number']}: title='{title[:40]}' docs={len(documents)}")

        except Exception as e:
            print(f"    Error fetching {detail_url}: {e}")

        return {
            "title": title,
            "description": description,
            "documents": documents,
        }


async def scrape_portal(portal):
    from playwright.async_api import async_playwright

    all_rfps = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        # --- Step 1: Load listing page ---
        listing_page = await context.new_page()
        print(f"  Loading {portal['name']} listing...")
        try:
            await listing_page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning: {e}")
        await asyncio.sleep(3)
        listing_html = await listing_page.content()
        await listing_page.close()

        entries = parse_listing_page(listing_html, portal)

        # --- Step 2: Filter to future bids only ---
        future_entries = [e for e in entries if is_future(e.get("due_date"))]
        print(f"  {len(future_entries)} future bids (out of {len(entries)} total)")

        if not future_entries:
            await browser.close()
            return all_rfps

        # --- Step 3: Fetch detail pages in parallel ---
        semaphore = asyncio.Semaphore(CONCURRENCY)
        detail_page = await context.new_page()

        tasks = [
            fetch_detail(detail_page, entry, portal, semaphore)
            for entry in future_entries
        ]
        details = await asyncio.gather(*tasks)

        await detail_page.close()
        await browser.close()

    # --- Step 4: Merge listing + detail data ---
    for entry, detail in zip(future_entries, details):
        title = detail.get("title") or entry.get("ref_number") or "Untitled"
        documents = detail.get("documents", [])

        rfp = {
            "title": title,
            "description": detail.get("description"),
            "agency": portal["name"],
            "department": None,
            "source_url": portal["url"],
            "detail_url": entry["detail_url"],
            "ref_number": entry.get("ref_number"),
            "status": "active",
            "rfp_type": None,
            "due_date": entry.get("due_date"),
            "posted_date": None,
            "source_name": SOURCE_NAME,
            "source_platform": "Procureware",
            "contact_name": None,
            "contact_email": None,
            "includes_inclusion_plan": False,
            "raw_data": json.dumps({"documents": documents}) if documents else None,
        }

        rfp["fingerprint"] = generate_fingerprint(
            rfp.get("title", ""),
            rfp.get("agency", ""),
            rfp.get("due_date", "")
        )
        all_rfps.append(rfp)

    return all_rfps


def run():
    print(f"Starting Procureware scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None

    try:
        for portal in PORTALS:
            print(f"\n--- Scraping {portal['name']} ---")
            rfps = asyncio.run(scrape_portal(portal))
            print(f"  Saved {len(rfps)} active RFPs for {portal['name']}")
            if rfps:
                print(f"  Sample title: {rfps[0].get('title', '')[:60]}")
                print(f"  Sample docs:  {rfps[0].get('raw_data', '')[:80]}")
            all_rfps.extend(rfps)

        print(f"\nTotal Procureware RFPs: {len(all_rfps)}")

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
