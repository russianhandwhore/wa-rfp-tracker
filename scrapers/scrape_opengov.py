"""
OpenGov Procurement Portal Scraper
Covers: City of Seattle, Pierce County
Strategy: Uses Playwright route interception (set up BEFORE navigation)
to capture all JSON API responses. Falls back to parsing rendered HTML
if no JSON is captured.
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
        "name": "City of Seattle",
        "portal_slug": "seattle",
        "url": "https://procurement.opengov.com/portal/seattle",
    },
    {
        "name": "Pierce County",
        "portal_slug": "piercecountywa",
        "url": "https://procurement.opengov.com/portal/piercecountywa",
    },
]

SOURCE_NAME = "OpenGov Procurement Portal"


def parse_date(date_str):
    """Parse ISO or common date strings."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
    except Exception:
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").isoformat()
        except Exception:
            return None


def extract_rfps_from_json(data, portal):
    """Parse RFPs from any JSON payload that looks like a list of solicitations."""
    rfps = []

    # Unwrap common envelope structures
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("projects", "solicitations", "data", "results", "items", "records", "value"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break

    if not items:
        return rfps

    print(f"    Parsing {len(items)} items from JSON payload")

    for item in items:
        if not isinstance(item, dict):
            continue

        # Skip closed/awarded solicitations
        status = str(item.get("status", "") or item.get("state", "")).lower()
        if status in ("closed", "awarded", "cancelled", "draft", "complete"):
            continue

        title = clean_text(item.get("title") or item.get("name") or "")
        if not title or len(title) < 5:
            continue

        # Build detail URL from project ID
        project_id = (
            item.get("id") or item.get("projectId") or
            item.get("project_id") or item.get("uuid")
        )
        detail_url = None
        if project_id:
            detail_url = (
                f"https://procurement.opengov.com/portal/"
                f"{portal['portal_slug']}/projects/{project_id}"
            )

        # Due date — try multiple field names
        due_date = parse_date(
            item.get("dueDate") or item.get("due_date") or
            item.get("closingDate") or item.get("closing_date") or
            item.get("submissionDeadline") or item.get("closeDate")
        )

        posted_date = parse_date(
            item.get("postedDate") or item.get("posted_date") or
            item.get("releaseDate") or item.get("release_date") or
            item.get("createdAt") or item.get("created_at")
        )

        # Department / sub-agency
        department = (
            item.get("department") or item.get("departmentName") or
            item.get("department_name") or item.get("organization")
        )
        if isinstance(department, dict):
            department = department.get("name") or department.get("title")

        # Reference / project number
        ref_number = (
            item.get("projectNumber") or item.get("project_number") or
            item.get("referenceNumber") or item.get("bidNumber") or
            item.get("solicitationNumber")
        )
        if not ref_number and project_id:
            ref_number = str(project_id)

        # Contact info
        contact_name, contact_email = None, None
        contact = item.get("contact") or item.get("procurementContact")
        if isinstance(contact, dict):
            contact_name = contact.get("name") or contact.get("fullName")
            contact_email = contact.get("email")
        elif isinstance(contact, str):
            contact_name = contact

        # Description / scope of work
        description = clean_text(
            item.get("description") or item.get("summary") or item.get("scope") or ""
        )
        if description and len(description) > 600:
            sentences = description.split(". ")
            description = ". ".join(sentences[:4])
            if not description.endswith("."):
                description += "."

        rfp_type = (
            item.get("solicitationType") or item.get("type") or
            item.get("projectType") or item.get("category")
        )

        rfps.append({
            "title": title,
            "description": description or None,
            "agency": portal["name"],
            "department": str(department)[:200] if department else None,
            "source_url": portal["url"],
            "detail_url": detail_url,
            "ref_number": str(ref_number)[:100] if ref_number else None,
            "status": "active",
            "rfp_type": str(rfp_type)[:100] if rfp_type else None,
            "due_date": due_date,
            "posted_date": posted_date,
            "source_name": SOURCE_NAME,
            "source_platform": "OpenGov",
            "contact_name": contact_name,
            "contact_email": contact_email,
            "includes_inclusion_plan": False,
        })

    return rfps


def extract_rfps_from_html(html, portal):
    """
    HTML fallback: parse rendered DOM after JS has run.
    OpenGov renders project cards with links like /portal/seattle/projects/12345
    """
    rfps = []
    soup = BeautifulSoup(html, "lxml")

    # Find all links pointing to project detail pages
    project_links = soup.find_all(
        "a",
        href=re.compile(r"/portal/[^/]+/projects/\d+", re.I)
    )
    print(f"    HTML fallback: found {len(project_links)} project links")

    seen_hrefs = set()
    for link in project_links:
        href = link["href"]
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        title = clean_text(link.get_text())
        if not title or len(title) < 5:
            # Title might be in a sibling/parent element
            parent = link.find_parent(["div", "li", "article"])
            if parent:
                heading = parent.find(["h1", "h2", "h3", "h4"])
                if heading:
                    title = clean_text(heading.get_text())

        if not title or len(title) < 5:
            continue

        detail_url = (
            href if href.startswith("http")
            else "https://procurement.opengov.com" + href
        )

        # Look for a due date in the surrounding card
        card = link.find_parent(["div", "li", "article", "tr"]) or link
        card_text = card.get_text(" ", strip=True)
        due_date = None
        date_match = re.search(
            r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b", card_text
        )
        if date_match:
            due_date = parse_date(date_match.group(1))

        rfps.append({
            "title": title,
            "description": None,
            "agency": portal["name"],
            "department": None,
            "source_url": portal["url"],
            "detail_url": detail_url,
            "ref_number": None,
            "status": "active",
            "rfp_type": None,
            "due_date": due_date,
            "posted_date": None,
            "source_name": SOURCE_NAME,
            "source_platform": "OpenGov",
            "contact_name": None,
            "contact_email": None,
            "includes_inclusion_plan": False,
        })

    return rfps


async def scrape_portal(portal):
    from playwright.async_api import async_playwright

    all_rfps = []
    captured_json = []

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

        # --- Set up interception BEFORE navigating ---
        # This ensures we don't miss any API calls that fire during initial load.
        async def intercept_response(response):
            try:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type and response.status == 200:
                    url = response.url
                    print(f"    [JSON {response.status}] {url[:120]}")
                    body = await response.json()
                    captured_json.append({"url": url, "data": body})
            except Exception:
                pass  # Ignore responses that fail to parse

        page.on("response", intercept_response)

        print(f"  Loading {portal['name']}...")
        try:
            # networkidle waits until all network activity has settled
            await page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning during navigation: {e}")

        # Extra buffer for any lazy-loaded API calls
        await asyncio.sleep(5)

        html = await page.content()
        await browser.close()

    print(f"  Captured {len(captured_json)} JSON responses for {portal['name']}")

    # Attempt to extract RFPs from captured JSON payloads
    seen_fps = set()

    for resp in captured_json:
        rfps = extract_rfps_from_json(resp["data"], portal)
        for rfp in rfps:
            fp = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", ""),
                rfp.get("due_date", "")
            )
            rfp["fingerprint"] = fp
            if fp not in seen_fps:
                seen_fps.add(fp)
                all_rfps.append(rfp)

    # Fall back to HTML parsing if JSON interception yielded nothing
    if not all_rfps:
        print("  No usable JSON captured — trying HTML fallback...")
        rfps = extract_rfps_from_html(html, portal)
        for rfp in rfps:
            fp = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", ""),
                rfp.get("due_date", "")
            )
            rfp["fingerprint"] = fp
            if fp not in seen_fps:
                seen_fps.add(fp)
                all_rfps.append(rfp)

    return all_rfps


def run():
    print(f"Starting OpenGov scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None

    try:
        for portal in PORTALS:
            print(f"\n--- Scraping {portal['name']} ---")
            rfps = asyncio.run(scrape_portal(portal))
            print(f"  Found {len(rfps)} active RFPs for {portal['name']}")
            if rfps:
                s = rfps[0]
                print(f"  Sample title: {s.get('title', '')[:60]}")
                print(f"  Sample due:   {s.get('due_date')}")
            all_rfps.extend(rfps)

        print(f"\nTotal OpenGov RFPs: {len(all_rfps)}")

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
