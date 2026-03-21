"""
Procureware Bid Portal Scraper
Covers: City of Spokane, Snohomish County, Community Transit

Key fixes from log analysis:
- Filter by STATUS text ("Open for Bidding", "Available") — reliable and visible
- Use the LAST date in the container as the due date (posted date comes first)
- Spokane/Community Transit have no dates visible — status filter handles them
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

# Status text that means a bid is currently open
OPEN_STATUSES = {"open for bidding", "available", "open", "active", "accepting submissions"}

DATE_NOISE = re.compile(
    r"\(in \d+ days?\)|\(overdue\)|\(today\)|due|open|close[sd]?|deadline|by",
    re.IGNORECASE
)


def parse_date_robust(raw_text):
    """Use dateutil to parse any date format after stripping noise words."""
    if not raw_text:
        return None
    cleaned = DATE_NOISE.sub("", raw_text).strip(" ,:-")
    cleaned = re.sub(r"\b(PT|PST|PDT|MT|MST|MDT|CT|CST|CDT|ET|EST|EDT)\b", "", cleaned).strip()
    if not cleaned:
        return None
    try:
        dt = dateutil_parser.parse(cleaned, fuzzy=False)
        return dt.isoformat()
    except Exception:
        return None


def extract_all_dates(container_text):
    """
    Find ALL date strings in the container text and return them as parsed ISO strings.
    We return all of them so the caller can pick the right one (usually the last = due date).
    """
    dates = []
    # Match MM/DD/YYYY HH:MM AM/PM patterns (with optional time)
    pattern = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?\b")
    for m in pattern.finditer(container_text):
        parsed = parse_date_robust(m.group(1))
        if parsed:
            dates.append(parsed)
    return dates


def is_open_status(container_text):
    """Check if the container text contains a known open/active status phrase."""
    text_lower = container_text.lower()
    return any(status in text_lower for status in OPEN_STATUSES)


def is_future(date_iso):
    """Return True if date is today or future. Returns True if None."""
    if not date_iso:
        return True
    try:
        dt = datetime.fromisoformat(date_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(tz=timezone.utc)
    except Exception:
        return True


def parse_listing_page(html, portal):
    """Parse rendered listing HTML into bid entries."""
    entries = []
    soup = BeautifulSoup(html, "lxml")

    bid_links = [
        a for a in soup.find_all("a", href=True)
        if GUID_PATTERN.search(a["href"]) and "/Bids/" in a["href"]
    ]

    print(f"  Found {len(bid_links)} total bid links")

    seen = set()
    open_count = 0

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

        # PRIMARY FILTER: only include bids with an open status
        if not is_open_status(container_text):
            continue

        open_count += 1

        # Get all dates — use the LAST one as the due date
        # (posted/published date comes first, due date comes last)
        all_dates = extract_all_dates(container_text)
        due_date = all_dates[-1] if all_dates else None

        entries.append({
            "ref_number": ref_number,
            "detail_url": detail_url,
            "due_date": due_date,
            "container_text": container_text,  # keep for debugging
        })

    print(f"  {open_count} open/active bids found")

    # Debug: show first 3 open entries
    for e in entries[:3]:
        print(f"    ref={e['ref_number']} due={e['due_date']} status=open")

    # Strip debug field before returning
    for e in entries:
        e.pop("container_text", None)

    return entries


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

            # Extract real title from headings
            skip_titles = {
                "bids", "home", "documents", "activities",
                "city of spokane procurement",
                "snohomish county purchasing portal",
                "community transit procurement"
            }
            for selector in ["h2", "h3", ".bid-title", "[class*='title']"]:
                tag = soup.select_one(selector)
                if tag:
                    text = clean_text(tag.get_text())
                    if text and len(text) > 5 and text.lower() not in skip_titles:
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

            # Fallback: look for PDF/Word/ZIP links
            if not documents:
                for a in soup.find_all("a", href=re.compile(r"\.(pdf|docx?|xlsx?|zip)($|\?)", re.I)):
                    href = a["href"]
                    name = clean_text(a.get_text()) or href.split("/")[-1]
                    url = href if href.startswith("http") else portal["base_url"] + href
                    documents.append({"name": name, "url": url})

            print(f"    {entry.get('ref_number','?')}: '{title[:50]}' | {len(documents)} docs")

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

        # Load listing page
        listing_page = await context.new_page()
        print(f"  Loading {portal['name']}...")
        try:
            await listing_page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning: {e}")
        await asyncio.sleep(3)
        listing_html = await listing_page.content()
        await listing_page.close()

        entries = parse_listing_page(listing_html, portal)

        if not entries:
            print("  No open bids found for this portal right now")
            await browser.close()
            return all_rfps

        # Fetch detail pages in parallel
        semaphore = asyncio.Semaphore(CONCURRENCY)
        detail_page = await context.new_page()
        tasks = [fetch_detail(detail_page, entry, portal, semaphore) for entry in entries]
        details = await asyncio.gather(*tasks)
        await detail_page.close()
        await browser.close()

    # Build final RFP objects
    for entry, detail in zip(entries, details):
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
            print(f"  Result: {len(rfps)} active RFPs")
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
