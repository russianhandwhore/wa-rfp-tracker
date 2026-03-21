"""
Procureware Bid Portal Scraper
Covers: City of Spokane, Snohomish County, Community Transit

Fixes:
- Uses dateutil.parser for robust multi-format date parsing
- Clicks the "Open Bids" tab (or equivalent) before scraping
  so we only see active bids, not the full historical archive
- Falls back gracefully if no Open tab found
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
from dateutil import parser as dateutil_parser

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
CONCURRENCY = 5

GUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Noise words to strip before passing to dateutil
DATE_NOISE = re.compile(
    r"\(in \d+ days?\)|\(overdue\)|\(today\)|due|open|close[sd]?|deadline|by",
    re.IGNORECASE
)


def parse_date_robust(raw_text):
    """
    Robust date parsing using dateutil.
    Strips noise words, then lets dateutil handle any format.
    Returns ISO string or None.
    """
    if not raw_text:
        return None

    # Strip noise words like "Due", "(in 17 days)", "Closes", etc.
    cleaned = DATE_NOISE.sub("", raw_text).strip(" ,:-")

    # Remove time-zone abbreviations that confuse dateutil (e.g. "PT", "PST", "PDT")
    cleaned = re.sub(r"\b(PT|PST|PDT|MT|MST|MDT|CT|CST|CDT|ET|EST|EDT)\b", "", cleaned).strip()

    if not cleaned:
        return None

    try:
        dt = dateutil_parser.parse(cleaned, fuzzy=True)
        return dt.isoformat()
    except Exception:
        return None


def is_future(date_iso):
    """
    Return True if the date is today or in the future.
    Returns True if date is None — better to show a bid with unknown date
    than silently drop it.
    """
    if not date_iso:
        return True
    try:
        dt = datetime.fromisoformat(date_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(tz=timezone.utc)
    except Exception:
        return True


def extract_date_from_container(container_text):
    """
    Pull out the most date-like substring from a container, then parse it.
    Tries specific patterns first, then falls back to dateutil fuzzy parsing.
    """
    # Pattern 1: MM/DD/YYYY or MM/DD/YY (most common on Procureware)
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", container_text)
    if m:
        result = parse_date_robust(m.group(1))
        if result:
            return result

    # Pattern 2: Month DD, YYYY  e.g. "March 25, 2026"
    m = re.search(r"\b([A-Z][a-z]+ \d{1,2},?\s*\d{4})\b", container_text)
    if m:
        result = parse_date_robust(m.group(1))
        if result:
            return result

    # Pattern 3: YYYY-MM-DD
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", container_text)
    if m:
        result = parse_date_robust(m.group(1))
        if result:
            return result

    return None


def parse_listing_page(html, portal):
    """Parse the rendered listing page HTML into minimal bid entries."""
    entries = []
    soup = BeautifulSoup(html, "lxml")

    bid_links = [
        a for a in soup.find_all("a", href=True)
        if GUID_PATTERN.search(a["href"]) and "/Bids/" in a["href"]
    ]

    print(f"  Found {len(bid_links)} bid links")

    # Show raw container text for first 3 bids so we can see the date format
    print("  === RAW CONTAINER SAMPLE (first 3) ===")
    for link in bid_links[:3]:
        container = link.find_parent(["tr", "div", "li"]) or link
        print(f"  {container.get_text(' | ', strip=True)[:200]}")
    print("  ===")

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

        ref_number = clean_text(link.get_text()) or None
        container = link.find_parent(["tr", "div", "li"]) or link
        container_text = container.get_text(" ", strip=True)
        due_date = extract_date_from_container(container_text)

        entries.append({
            "ref_number": ref_number,
            "detail_url": detail_url,
            "due_date": due_date,
        })

    return entries


async def try_click_open_tab(page):
    """
    Try to click an 'Open', 'Active', or 'Current' filter tab on the
    Procureware bids page. Returns True if a tab was clicked.
    """
    # Common tab/button labels for open bids on Procureware
    open_labels = ["Open", "Active", "Current", "Open Bids", "Active Bids"]

    for label in open_labels:
        try:
            # Look for buttons, tabs, or links with these labels
            el = page.get_by_role("tab", name=re.compile(label, re.I))
            if await el.count() > 0:
                await el.first.click()
                await asyncio.sleep(2)
                print(f"  Clicked '{label}' tab")
                return True

            el = page.get_by_role("button", name=re.compile(label, re.I))
            if await el.count() > 0:
                await el.first.click()
                await asyncio.sleep(2)
                print(f"  Clicked '{label}' button")
                return True

            el = page.get_by_role("link", name=re.compile(f"^{label}$", re.I))
            if await el.count() > 0:
                await el.first.click()
                await asyncio.sleep(2)
                print(f"  Clicked '{label}' link")
                return True

        except Exception:
            continue

    print("  No Open/Active tab found — scraping all visible bids")
    return False


async def fetch_detail(page, entry, portal, semaphore):
    """Fetch detail page for real title, description, and document links."""
    async with semaphore:
        detail_url = entry["detail_url"]
        docs_url = detail_url + "?t=BidDocuments"

        title = entry.get("ref_number") or ""
        description = None
        documents = []

        try:
            await page.goto(docs_url, timeout=40000, wait_until="networkidle")
            await asyncio.sleep(3)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # Extract real title
            for selector in ["h2", "h3", ".bid-title", ".project-title", "[class*='title']"]:
                tag = soup.select_one(selector)
                if tag:
                    text = clean_text(tag.get_text())
                    if text and len(text) > 5 and text not in (
                        "Bids", "Home", "Documents", "Activities", "City of Spokane Procurement",
                        "Snohomish County Purchasing Portal", "Community Transit Procurement"
                    ):
                        title = text
                        break

            # Extract description
            for selector in [".bid-description", ".description", "[class*='description']"]:
                tag = soup.select_one(selector)
                if tag:
                    text = clean_text(tag.get_text())
                    if text and len(text) > 30:
                        description = text[:600]
                        break

            # Extract document download links
            doc_links = soup.find_all(
                "a",
                href=re.compile(r"/(BidDocument|Document|File)/Download/", re.I)
            )
            for doc in doc_links:
                doc_href = doc["href"]
                doc_name = clean_text(doc.get_text()) or "Document"
                doc_url = (
                    doc_href if doc_href.startswith("http")
                    else portal["base_url"] + doc_href
                )
                documents.append({"name": doc_name, "url": doc_url})

            # Fallback: look for PDF/Word/ZIP file links
            if not documents:
                for a in soup.find_all("a", href=re.compile(r"\.(pdf|docx?|xlsx?|zip)($|\?)", re.I)):
                    href = a["href"]
                    name = clean_text(a.get_text()) or href.split("/")[-1]
                    url = href if href.startswith("http") else portal["base_url"] + href
                    documents.append({"name": name, "url": url})

            print(f"    {entry.get('ref_number','?')}: '{title[:40]}' | {len(documents)} docs")

        except Exception as e:
            print(f"    Error on {detail_url}: {e}")

        return {"title": title, "description": description, "documents": documents}


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

        # Step 1: Load listing page and try to filter to open bids
        listing_page = await context.new_page()
        print(f"  Loading {portal['name']}...")
        try:
            await listing_page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning: {e}")

        await asyncio.sleep(3)

        # Try to click an "Open Bids" tab/button
        await try_click_open_tab(listing_page)

        # Wait for any re-render after tab click
        await asyncio.sleep(3)

        listing_html = await listing_page.content()
        await listing_page.close()

        entries = parse_listing_page(listing_html, portal)

        # Filter to future bids (includes bids with no due date)
        future_entries = [e for e in entries if is_future(e.get("due_date"))]
        print(f"  {len(future_entries)} future/undated bids out of {len(entries)} total")

        if not future_entries:
            print("  No active bids found for this portal right now")
            await browser.close()
            return all_rfps

        # Step 2: Fetch detail pages in parallel
        semaphore = asyncio.Semaphore(CONCURRENCY)
        detail_page = await context.new_page()

        tasks = [
            fetch_detail(detail_page, entry, portal, semaphore)
            for entry in future_entries
        ]
        details = await asyncio.gather(*tasks)

        await detail_page.close()
        await browser.close()

    # Step 3: Build final RFP objects
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
            print(f"  Result: {len(rfps)} active RFPs saved")
            if rfps:
                print(f"  Sample: {rfps[0].get('title','')[:60]}")
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
