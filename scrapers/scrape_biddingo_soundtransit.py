"""
Sound Transit Biddingo Scraper
Source: https://biddingo.com/soundtransit
API:    https://api.biddingo.com/restapi/bidding/list/noauthorize/1/41195253

Fetches all active Sound Transit solicitations from Biddingo.
"""

import requests
import sys
import os
from datetime import datetime
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

SOURCE_NAME = "Sound Transit Biddingo"
PLATFORM    = "Sound Transit"
API_URL     = "https://api.biddingo.com/restapi/bidding/list/noauthorize/1/41195253"
DETAIL_BASE = "https://biddingo.com/soundtransit/bid/1/41195253"
PORTAL_URL  = "https://biddingo.com/soundtransit"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://biddingo.com/",
    "Origin": "https://biddingo.com",
    "Accept-Language": "en-US,en;q=0.9",
}

OPEN_STATUSES = {"open for bidding", "open"}


def parse_date(date_str):
    """Parse Biddingo date formats: 'MM/DD/YYYY HH:MM:SS AM/PM' or 'MM/DD/YYYY'"""
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def item_to_rfp(item):
    title = clean_text(item.get("tenderName", "") or "")
    if not title:
        return None

    bid_status = (item.get("bidStatus") or "").strip().lower()
    if bid_status not in OPEN_STATUSES:
        return None

    tender_id  = item.get("tenderId")
    ref_number = clean_text(item.get("tenderNumber", "") or "") or None
    detail_url = f"{DETAIL_BASE}/{tender_id}/verification" if tender_id else PORTAL_URL
    due_date   = parse_date(item.get("tenderClosingDate"))
    posted_date = parse_date(item.get("publishedDate"))

    return {
        "title":                 title,
        "ref_number":            str(ref_number)[:100] if ref_number else None,
        "detail_url":            detail_url,
        "source_url":            PORTAL_URL,
        "due_date":              due_date,
        "posted_date":           posted_date,
        "status":                "active",
        "description":           None,
        "department":            None,
        "rfp_type":              None,
        "agency":                "Sound Transit",
        "source_name":           SOURCE_NAME,
        "source_platform":       PLATFORM,
        "contact_name":          None,
        "contact_email":         None,
        "categories":            [],
        "includes_inclusion_plan": False,
    }


def run():
    print(f"Starting Sound Transit Biddingo scraper at {datetime.now()}")
    supabase    = get_supabase_client()
    all_rfps    = []
    total_saved = 0
    error_msg   = None
    status      = "failed"

    try:
        print(f"Fetching {API_URL}...")
        resp = requests.get(API_URL, headers=HEADERS, timeout=30)
        print(f"  HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
        print(f"  Response preview: {resp.text[:300]}")
        resp.raise_for_status()
        data = resp.json()

        bid_list = data.get("bidInfoList", [])
        print(f"Total bids returned: {len(bid_list)}")

        for item in bid_list:
            rfp = item_to_rfp(item)
            if not rfp:
                continue
            fp = generate_fingerprint(
                rfp["ref_number"] or rfp["title"],
                "Sound Transit",
                rfp["due_date"] or "",
            )
            rfp["fingerprint"] = fp
            all_rfps.append(rfp)

        # Deduplicate by fingerprint
        seen   = set()
        unique = []
        for rfp in all_rfps:
            if rfp["fingerprint"] not in seen:
                seen.add(rfp["fingerprint"])
                unique.append(rfp)
        all_rfps = unique

        print(f"\nActive Sound Transit RFPs: {len(all_rfps)}")
        if all_rfps:
            print(f"  Sample: {all_rfps[0]['title'][:60]}")
            print(f"  Due:    {all_rfps[0]['due_date']}")
            print(f"  Ref:    {all_rfps[0]['ref_number']}")

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
        status    = "failed"
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
