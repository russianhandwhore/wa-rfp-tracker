import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
import re

BASE_URL = "https://pr-webs-vendor.des.wa.gov/BidCalendar.aspx"
SOURCE_NAME = "WEBS - Washington Electronic Business Solution"


def parse_due_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%y").isoformat()
    except:
        return None


def parse_rfps_from_html(html):
    from bs4 import BeautifulSoup
    rfps = []
    soup = BeautifulSoup(html, "lxml")
    grid = soup.find("table", {"id": "DataGrid1"})
    if not grid:
        print("DataGrid1 not found in HTML")
        return rfps

    rows = grid.find_all("tr")
    current_rfp = {}

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue

        link = row.find("a", href=lambda x: x and "Search_BidDetails" in str(x))
        if link:
            if current_rfp.get("title"):
                rfps.append(current_rfp)

            title = clean_text(link.get_text())
            detail_url = "https://pr-webs-vendor.des.wa.gov/" + link["href"]

            ref_span = row.find("b", string=re.compile(r"Ref #"))
            ref_number = None
            if ref_span:
                ref_number = clean_text(ref_span.find_next_sibling(string=True))

            contact = None
            cell_texts = [clean_text(c.get_text()) for c in cells]
            if len(cell_texts) >= 3:
                contact = cell_texts[-1]

            close_date = None
            if cell_texts:
                close_date = parse_due_date(cell_texts[0])

            current_rfp = {
                "title": title,
                "detail_url": detail_url,
                "ref_number": ref_number,
                "contact_name": contact,
                "due_date": close_date,
                "source_name": SOURCE_NAME,
                "source_platform": "WEBS",
                "source_url": BASE_URL,
                "status": "active",
                "agency": None,
                "description": None,
                "includes_inclusion_plan": False
            }

        elif current_rfp and len(cells) == 1:
            text = clean_text(cells[0].get_text())
            if text and not text.startswith("Additional") and not text.startswith("Includes"):
                if not current_rfp.get("description"):
                    current_rfp["description"] = text
            if "Includes an Inclusion Plan: Y" in str(row):
                current_rfp["includes_inclusion_plan"] = True

    if current_rfp.get("title"):
        rfps.append(current_rfp)

    return rfps


def deduplicate(rfps):
    seen = {}
    for rfp in rfps:
        fp = rfp.get("fingerprint")
        if fp and fp not in seen:
            seen[fp] = rfp
    return list(seen.values())


def get_next_page_control(html, next_page_num):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    pagination = soup.find("td", {"align": "center"})
    if not pagination:
        return None
    for a in pagination.find_all("a"):
        if a.get_text().strip() == str(next_page_num):
            href = a.get("href", "")
            match = re.search(r"__doPostBack\('([^']+)'", href)
            if match:
                return match.group(1)
    return None


async def scrape_all_pages():
    all_rfps = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Loading WEBS bid calendar...")
        try:
            await page.goto(BASE_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_selector("#DataGrid1", timeout=30000)
            print("Page loaded successfully!")
        except Exception as e:
            print("Error loading initial page: " + str(e))
            await browser.close()
            return all_rfps

        page_num = 1
        max_pages = 25

        while page_num <= max_pages:
            print("Scraping page " + str(page_num) + "...")
            await asyncio.sleep(1)
            html = await page.content()
            rfps = parse_rfps_from_html(html)
            print("Found " + str(len(rfps)) + " RFPs on page " + str(page_num))

            if not rfps:
                print("No RFPs found, stopping")
                break

            all_rfps.extend(rfps)

            control_id = get_next_page_control(html, page_num + 1)

            if not control_id:
                print("No next page button found, done at page " + str(page_num))
                break

            print("Going to page " + str(page_num + 1) + "...")

            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    await page.evaluate("__doPostBack('" + control_id + "', '')")

                await page.wait_for_selector("#DataGrid1", timeout=30000)
                await asyncio.sleep(1)
                page_num += 1

            except Exception as e:
                print("Navigation error: " + str(e))
                break

        await browser.close()

    return all_rfps


def run():
    print("Starting WEBS scraper at " + str(datetime.now()))
    supabase = get_supabase_client()
    all_rfps = []
    total_new = 0
    error_msg = None

    try:
        all_rfps = asyncio.run(scrape_all_pages())
        print("Total RFPs scraped: " + str(len(all_rfps)))

        for rfp in all_rfps:
            rfp["fingerprint"] = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", rfp.get("source_name", "")),
                rfp.get("due_date", "")
            )

        all_rfps = deduplicate(all_rfps)
        print("Total after dedup: " + str(len(all_rfps)))

        if all_rfps:
            batch_size = 50
            for i in range(0, len(all_rfps), batch_size):
                batch = all_rfps[i:i + batch_size]
                supabase.table("rfps").upsert(batch, on_conflict="fingerprint").execute()
                total_new += len(batch)
                print("Saved batch of " + str(len(batch)) + " RFPs")

        status = "success"
        print("Done! " + str(total_new) + " RFPs saved")

    except Exception as e:
        error_msg = str(e)
        status = "failed"
        print("Scraper failed: " + str(e))

    finally:
        log_scrape(
            supabase=supabase,
            source_name=SOURCE_NAME,
            status=status,
            rfps_found=len(all_rfps),
            rfps_new=total_new,
            rfps_updated=0,
            error_message=error_msg
        )


if __name__ == "__main__":
    run()
