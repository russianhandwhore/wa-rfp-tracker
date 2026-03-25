"""
King County E-Procurement Scraper
Source: https://fa-epvh-saasfaprod1.fa.ocs.oraclecloud.com/fscmUI/faces/NegotiationAbstracts?prcBuId=300000001727151

Oracle Fusion public-facing solicitation abstracts page.
Full table is server-rendered — Playwright loads it, BeautifulSoup parses it.
No login required to read the listing.

Columns scraped: Solicitation ID, Title, Type, Status, Posting Date, Open Date, Close Date
Detail links are ADF AJAX (no direct URL) — links point to the portal listing page.

Dedup strategy: base solicitation ID (strip amendment suffix ,N).
Keep highest-version record per solicitation; skip Closed/Awarded/Canceled.
"""

import asyncio
import json
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

from bs4 import BeautifulSoup

PORTAL_URL = (
    "https://fa-epvh-saasfaprod1.fa.ocs.oraclecloud.com"
    "/fscmUI/faces/NegotiationAbstracts?prcBuId=300000001727151"
)
SOURCE_PLATFORM = "King County"
SOURCE_NAME = "King County E-Procurement"
AGENCY = "King County"

# Statuses to include (case-insensitive)
INCLUDE_STATUSES = {"active", "amended", "upcoming", "preview"}

# Status → our status field
STATUS_MAP = {
    "active":   "active",
    "amended":  "active",
    "upcoming": "upcoming",
    "preview":  "upcoming",
}


def parse_oracle_date(val):
    """Parse Oracle date strings like '3/23/26 01:34:14 PM' or '3/23/26'."""
    if not val:
        return None
    val = val.strip()
    for fmt in ("%m/%d/%y %I:%M:%S %p", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val, fmt).isoformat()
        except ValueError:
            continue
    return None


def base_solicitation_id(sol_id):
    """Strip amendment suffix: 'KC001604,2' → 'KC001604'."""
    return sol_id.split(",")[0].strip()


def amendment_version(sol_id):
    """Return amendment number: 'KC001604,2' → 2, 'KC001604' → 0."""
    parts = sol_id.split(",")
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def parse_rows(html):
    """Parse all data rows from the Oracle ADF table HTML."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", attrs={"_afrrk": True})
    records = []

    for row in rows:
        cells = row.find_all("td", class_="xen")
        if len(cells) < 7:
            continue

        def cell_text(idx):
            span = cells[idx].find("span", class_="x2ey")
            return clean_text(span.get_text()) if span else ""

        sol_id      = cell_text(0)
        title       = cell_text(1)
        sol_type    = cell_text(2)
        status_raw  = cell_text(3).lower()
        posting_str = cell_text(4)
        open_str    = cell_text(5)
        close_str   = cell_text(6)

        if not sol_id or not title:
            continue

        if status_raw not in INCLUDE_STATUSES:
            continue

        records.append({
            "sol_id":       sol_id,
            "title":        title,
            "sol_type":     sol_type,
            "status_raw":   status_raw,
            "posting_date": parse_oracle_date(posting_str),
            "open_date":    parse_oracle_date(open_str),
            "close_date":   parse_oracle_date(close_str),
        })

    return records


def dedup_records(records):
    """Keep only the highest-version amendment per base solicitation ID."""
    best = {}
    for r in records:
        base = base_solicitation_id(r["sol_id"])
        ver  = amendment_version(r["sol_id"])
        if base not in best or ver > amendment_version(best[base]["sol_id"]):
            best[base] = r
    return list(best.values())


def build_rfp(record):
    """Convert a parsed row into an RFP dict for Supabase."""
    sol_id     = record["sol_id"]
    base_id    = base_solicitation_id(sol_id)
    status_raw = record["status_raw"]
    status     = STATUS_MAP.get(status_raw, "active")

    # due_date: prefer close_date, fall back to open_date
    due_date   = record["close_date"] or record["open_date"]

    fingerprint = generate_fingerprint(base_id, SOURCE_PLATFORM, "")

    return {
        "title":           record["title"],
        "ref_number":      sol_id,
        "due_date":        due_date,
        "status":          status,
        "source_platform": SOURCE_PLATFORM,
        "source_name":     SOURCE_NAME,
        "source_url":      PORTAL_URL,
        "detail_url":      PORTAL_URL,
        "agency":          AGENCY,
        "department":      None,
        "description":     None,
        "contact_name":    None,
        "contact_email":   "procurement.web@kingcounty.gov",
        "posted_date":     record["posting_date"],
        "rfp_type":        record["sol_type"] or None,
        "includes_inclusion_plan": False,
        "categories":      [],
        "fingerprint":     fingerprint,
        "raw_data":        json.dumps({
            "solicitation_id":  sol_id,
            "solicitation_type": record["sol_type"],
            "status_raw":       status_raw,
            "open_date":        record["open_date"],
            "close_date":       record["close_date"],
            "posting_date":     record["posting_date"],
            "portal_url":       PORTAL_URL,
        }),
    }


async def fetch_html():
    """Use Playwright to load the Oracle ADF page and return rendered HTML."""
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

        try:
            print(f"  Loading: {PORTAL_URL}")
            await page.goto(PORTAL_URL, timeout=60000, wait_until="networkidle")

            # Wait for actual table rows to appear
            await page.wait_for_selector("tr[_afrrk]", timeout=30000)
            html = await page.content()
            print(f"  Page loaded ({len(html):,} bytes)")
            return html

        except Exception as e:
            print(f"  [ERROR] Failed to load page: {e}")
            return None

        finally:
            await browser.close()


def run():
    print(f"Starting King County scraper at {datetime.now()}")
    supabase    = get_supabase_client()
    all_rfps    = []
    total_saved = 0
    error_msg   = None
    status      = "failed"

    try:
        html = asyncio.run(fetch_html())
        if not html:
            raise RuntimeError("Failed to retrieve page HTML")

        raw_records = parse_rows(html)
        print(f"  Rows parsed (pre-dedup): {len(raw_records)}")

        deduped = dedup_records(raw_records)
        print(f"  Rows after dedup: {len(deduped)}")

        all_rfps = [build_rfp(r) for r in deduped]

        # Log status breakdown
        by_status = {}
        for r in all_rfps:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        for k, v in sorted(by_status.items()):
            print(f"    {k}: {v}")

        if all_rfps:
            s   = all_rfps[0]
            raw = json.loads(s["raw_data"])
            print(f"  Sample: {s['title'][:55]} | status={s['status']} | due={s['due_date']}")

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
        status    = "failed"
        print(f"[ERROR] Scraper failed: {e}")
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
