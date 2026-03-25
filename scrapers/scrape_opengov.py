"""
OpenGov Procurement Portal Scraper
Covers: City of Seattle, Pierce County

Strategy: fetch the /portal/embed/ URL with plain requests — no Playwright needed.
The embed endpoint returns server-side rendered HTML with window.__data fully
populated, bypassing Cloudflare bot protection entirely.
"""

import re
import json
import time
import requests
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
        "embed_url": "https://procurement.opengov.com/portal/embed/seattle/project-list?departmentId=all&status=all",
        "portal_url": "https://procurement.opengov.com/portal/seattle",
    },
    {
        "name": "Pierce County",
        "portal_slug": "piercecountywa",
        "embed_url": "https://procurement.opengov.com/portal/embed/piercecountywa/project-list?departmentId=all&status=all",
        "portal_url": "https://procurement.opengov.com/portal/piercecountywa",
    },
]

SOURCE_NAME = "OpenGov Procurement Portal"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

OPEN_STATUSES = {"open", "active", "upcoming", "preview", "coming_soon"}
SKIP_STATUSES = {"closed", "awarded", "cancelled", "canceled", "draft", "complete", "archived"}


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
    except Exception:
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").isoformat()
        except Exception:
            return None


def extract_rows_from_html(html):
    """
    Extract govProjects rows from window.__data embedded in the HTML.
    The rows array itself is valid JSON; the surrounding __data blob has
    JS functions but we target only the rows array.
    """
    match = re.search(
        r'"govProjects"\s*:\s*\{"count"\s*:\s*\d+\s*,\s*"rows"\s*:\s*(\[.*?\])\s*\}',
        html,
        re.DOTALL,
    )
    if not match:
        print("    window.__data rows not found in HTML")
        return []
    try:
        rows = json.loads(match.group(1))
        print(f"    Extracted {len(rows)} rows from window.__data")
        return rows
    except json.JSONDecodeError as e:
        print(f"    JSON parse error on rows: {e}")
        return []


def rows_to_rfps(rows, portal):
    rfps = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        status_raw = str(row.get("status", "")).lower()
        is_coming_soon = row.get("comingSoon", False)

        if status_raw in SKIP_STATUSES:
            continue
        if status_raw not in OPEN_STATUSES and not is_coming_soon:
            continue

        title = clean_text(row.get("title", ""))
        if not title:
            continue

        project_id = row.get("id")
        gov_code = (row.get("government") or {}).get("code") or portal["portal_slug"]
        detail_url = (
            f"https://procurement.opengov.com/portal/{gov_code}/projects/{project_id}"
            if project_id else portal["portal_url"]
        )

        dept = row.get("department") or {}
        dept_name = dept.get("name") if isinstance(dept, dict) else None

        template = row.get("template") or {}
        rfp_type = template.get("title") if isinstance(template, dict) else None

        summary_html = row.get("summary") or ""
        description = None
        if summary_html:
            text = BeautifulSoup(summary_html, "html.parser").get_text(separator=" ").strip()
            description = text[:500] if text else None

        if status_raw in ("open", "active"):
            status = "active"
        else:
            status = "upcoming"

        rfps.append({
            "title": title,
            "ref_number": str(row["financialId"])[:100] if row.get("financialId") else None,
            "detail_url": detail_url,
            "source_url": portal["portal_url"],
            "due_date": parse_date(row.get("proposalDeadline")),
            "posted_date": parse_date(row.get("releaseProjectDate")),
            "status": status,
            "description": description,
            "department": str(dept_name)[:200] if dept_name else None,
            "rfp_type": str(rfp_type)[:100] if rfp_type else None,
            "agency": portal["name"],
            "source_name": SOURCE_NAME,
            "source_platform": "OpenGov",
            "contact_name": None,
            "contact_email": None,
            "categories": [],
            "includes_inclusion_plan": False,
        })
    return rfps


def scrape_portal(portal):
    print(f"  Fetching embed URL for {portal['name']}...")
    try:
        resp = requests.get(portal["embed_url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Request failed: {e}")
        return []

    print(f"  HTTP {resp.status_code}, {len(resp.text):,} chars")
    rows = extract_rows_from_html(resp.text)
    if not rows:
        return []

    rfps = rows_to_rfps(rows, portal)
    print(f"  {len(rfps)} active/upcoming RFPs after filtering")

    # Deduplicate by fingerprint
    seen = set()
    unique = []
    for rfp in rfps:
        fp = generate_fingerprint(
            rfp.get("ref_number") or rfp.get("title", ""),
            portal["name"],
            rfp.get("due_date", "") or "",
        )
        rfp["fingerprint"] = fp
        if fp not in seen:
            seen.add(fp)
            unique.append(rfp)

    return unique


def run():
    print(f"Starting OpenGov scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None
    status = "failed"

    try:
        for i, portal in enumerate(PORTALS):
            print(f"\n--- Scraping {portal['name']} ---")
            rfps = scrape_portal(portal)
            print(f"  Found {len(rfps)} unique RFPs for {portal['name']}")
            if rfps:
                print(f"  Sample: {rfps[0].get('title', '')[:60]}")
                print(f"  Due:    {rfps[0].get('due_date')}")
            all_rfps.extend(rfps)
            if i < len(PORTALS) - 1:
                time.sleep(2)

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
