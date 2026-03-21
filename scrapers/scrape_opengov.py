"""
OpenGov Procurement Portal Scraper
Covers: City of Seattle, Pierce County
Strategy: Playwright intercepts the React SPA's background JSON API calls
instead of parsing HTML — much faster and more reliable.
"""

import asyncio
import json
import re
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

# All WA OpenGov portals — add more here as needed
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


def parse_due_date(date_str):
    """Parse ISO date strings returned by the OpenGov API."""
    if not date_str:
        return None
    try:
        # OpenGov returns formats like "2026-04-15T17:00:00.000Z"
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
    except Exception:
        try:
            # Fallback: strip time portion if above fails
            return datetime.strptime(date_str[:10], "%Y-%m-%d").isoformat()
        except Exception:
            return None


def extract_rfps_from_json(data, portal):
    """
    Parse solicitations from the JSON payload returned by the OpenGov API.
    The response structure can vary slightly; we handle the most common shapes.
    """
    rfps = []

    # The API returns a list at different keys depending on the endpoint
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("projects", "solicitations", "data", "results", "items"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break

    print(f"  Parsing {len(items)} items from {portal['name']} JSON response")

    for item in items:
        # Skip non-open solicitations
        status = str(item.get("status", "")).lower()
        if status in ("closed", "awarded", "cancelled", "draft"):
            continue

        title = clean_text(item.get("title") or item.get("name") or "")
        if not title:
            continue

        # Build detail URL from project ID
        project_id = item.get("id") or item.get("projectId") or item.get("project_id")
        detail_url = None
        if project_id:
            detail_url = f"https://procurement.opengov.com/portal/{portal['portal_slug']}/projects/{project_id}"

        # Due date — field names vary
        due_date_raw = (
            item.get("dueDate")
            or item.get("due_date")
            or item.get("closingDate")
            or item.get("closing_date")
            or item.get("submissionDeadline")
        )
        due_date = parse_due_date(due_date_raw)

        # Posted date
        posted_raw = (
            item.get("postedDate")
            or item.get("posted_date")
            or item.get("releaseDate")
            or item.get("release_date")
            or item.get("createdAt")
            or item.get("created_at")
        )
        posted_date = parse_due_date(posted_raw)

        # Department / agency
        department = (
            item.get("department")
            or item.get("departmentName")
            or item.get("department_name")
            or item.get("organization")
        )
        if department and isinstance(department, dict):
            department = department.get("name") or department.get("title")

        # Ref / project number
        ref_number = (
            item.get("projectId")
            or item.get("project_id")
            or item.get("referenceNumber")
            or item.get("reference_number")
            or item.get("bidNumber")
            or str(project_id) if project_id else None
        )

        # Contact info
        contact_name = None
        contact_email = None
        contact = item.get("contact") or item.get("procurementContact")
        if contact and isinstance(contact, dict):
            contact_name = contact.get("name") or contact.get("fullName")
            contact_email = contact.get("email")
        elif isinstance(contact, str):
            contact_name = contact

        # Description / scope
        description = clean_text(
            item.get("description")
            or item.get("summary")
            or item.get("scope")
            or ""
        )
        if description and len(description) > 600:
            sentences = description.split(". ")
            description = ". ".join(sentences[:4])
            if not description.endswith("."):
                description += "."

        # Solicitation type
        rfp_type = item.get("solicitationType") or item.get("type") or item.get("projectType")

        rfps.append({
            "title": title,
            "description": description or None,
            "agency": portal["name"],
            "department": str(department) if department else None,
            "source_url": portal["url"],
            "detail_url": detail_url,
            "ref_number": str(ref_number) if ref_number else None,
            "status": "active",
            "rfp_type": str(rfp_type) if rfp_type else None,
            "due_date": due_date,
            "posted_date": posted_date,
            "source_name": SOURCE_NAME,
            "source_platform": "OpenGov",
            "contact_name": contact_name,
            "contact_email": contact_email,
            "includes_inclusion_plan": False,
        })

    return rfps


async def scrape_portal(portal):
    """
    Load the OpenGov portal in a headless browser, intercept all JSON API
    responses, and extract solicitation data from them.
    """
    from playwright.async_api import async_playwright

    all_rfps = []
    captured_responses = []

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

        # Intercept JSON responses from the OpenGov API
        async def handle_response(response):
            url = response.url
            # Target API calls that look like solicitation/project listings
            if any(keyword in url for keyword in (
                "projects", "solicitations", "procurements", "bids", "opportunities"
            )) and "opengov" in url:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type or "javascript" in content_type:
                    try:
                        body = await response.json()
                        captured_responses.append({"url": url, "data": body})
                        print(f"  Intercepted JSON from: {url}")
                    except Exception:
                        pass

        page.on("response", handle_response)

        print(f"Loading {portal['name']} portal...")
        try:
            await page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        except Exception as e:
            print(f"  Warning during page load: {e}")

        # Give the SPA extra time to finish its API calls
        await asyncio.sleep(5)

        await browser.close()

    # Parse RFPs from all captured JSON responses
    print(f"  Captured {len(captured_responses)} JSON responses")
    seen_ids = set()

    for resp in captured_responses:
        rfps = extract_rfps_from_json(resp["data"], portal)
        for rfp in rfps:
            fp = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", ""),
                rfp.get("due_date", "")
            )
            rfp["fingerprint"] = fp
            if fp not in seen_ids:
                seen_ids.add(fp)
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
            print(f"Found {len(rfps)} active RFPs for {portal['name']}")

            # Log a sample for debugging
            if rfps:
                sample = rfps[0]
                print(f"  Sample title:   {sample.get('title', '')[:60]}")
                print(f"  Sample due:     {sample.get('due_date')}")
                print(f"  Sample dept:    {sample.get('department')}")

            all_rfps.extend(rfps)

        print(f"\nTotal RFPs across all OpenGov portals: {len(all_rfps)}")

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
