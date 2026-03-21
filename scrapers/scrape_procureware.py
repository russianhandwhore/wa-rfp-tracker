"""
Procureware Bid Portal Scraper
Covers: City of Spokane, Snohomish County, Community Transit
Strategy: Procureware is a .NET JavaScript SPA. Playwright renders the page
fully (wait_until="networkidle"), then BeautifulSoup parses the loaded DOM.
Bid links follow the pattern: /Bids/{guid}
"""

import asyncio
import re
from datetime import datetime
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

# Matches a GUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
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


def parse_rfps_from_html(html, portal):
    """
    Parse rendered Procureware HTML. Bid detail URLs contain a GUID,
    e.g. /Bids/2132eb6b-2db4-4ecd-be5f-e37e957cc72b
    We find all links matching that pattern to locate individual bids.
    """
    rfps = []
    soup = BeautifulSoup(html, "lxml")

    # Find all links whose href contains a GUID (bid detail pages)
    bid_links = [
        a for a in soup.find_all("a", href=True)
        if GUID_PATTERN.search(a["href"]) and "/Bids/" in a["href"]
    ]

    print(f"  Found {len(bid_links)} bid detail links")

    # Debug: show first few links found
    for a in bid_links[:3]:
        print(f"    Link text: '{a.get_text(strip=True)[:60]}' -> {a['href'][:80]}")

    seen_hrefs = set()
    for link in bid_links:
        href = link["href"]
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        # Build absolute URL
        if href.startswith("http"):
            detail_url = href
        elif href.startswith("/"):
            detail_url = portal["base_url"] + href
        else:
            detail_url = portal["base_url"] + "/" + href

        # Get the title from the link text or nearest heading
        title = clean_text(link.get_text())
        if not title or len(title) < 5:
            # Look in the parent container for a heading
            parent = link.find_parent(["tr", "div", "li", "article"])
            if parent:
                heading = parent.find(["h1", "h2", "h3", "h4", "strong", "b"])
                if heading:
                    title = clean_text(heading.get_text())

        if not title or len(title) < 5:
            continue

        # Look for dates in the surrounding row/card
        container = link.find_parent(["tr", "div", "li"]) or link
        container_text = container.get_text(" ", strip=True)

        due_date = None
        date_match = re.search(
            r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", container_text
        )
        if date_match:
            due_date = parse_date(date_match.group(1))

        # Look for a ref/bid number (short alphanumeric, not the title, not a date)
        ref_number = None
        for cell in (container.find_all("td") or container.find_all("span")):
            text = clean_text(cell.get_text())
            if (
                text and text != title and
                len(text) < 30 and
                not re.search(r"\s{2,}", text) and
                re.match(r"^[\w\-\.]{3,25}$", text) and
                not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", text)
            ):
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


async def scrape_portal(portal):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        print(f"  Loading {portal['name']}...")
        try:
            # networkidle ensures all AJAX calls have completed before we capture HTML
            await page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning during navigation: {e}")

        # Extra buffer in case of slow AJAX rendering
        await asyncio.sleep(4)

        html = await page.content()

        # Debug: show a snippet of the rendered page body text
        from bs4 import BeautifulSoup as BS
        preview = BS(html, "lxml").get_text(" ", strip=True)[:300]
        print(f"  Page preview: {preview}")

        await browser.close()

    return parse_rfps_from_html(html, portal)


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

            for rfp in rfps:
                rfp["fingerprint"] = generate_fingerprint(
                    rfp.get("title", ""),
                    rfp.get("agency", ""),
                    rfp.get("due_date", "")
                )

            print(f"  Found {len(rfps)} RFPs for {portal['name']}")
            if rfps:
                print(f"  Sample: {rfps[0].get('title', '')[:60]}")

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
