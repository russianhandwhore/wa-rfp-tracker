"""
Port of Seattle Procurement Scraper
Source: https://hosting.portseattle.org/sops/#/Solicitations
API:    https://hosting.portseattle.org/sopsapi/Solicitations (OData)

Scrapes both active (Open) and future solicitations.
"""

import requests
import sys
import os
from datetime import datetime
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
from categorize import categorize_rfp

SOURCE_NAME  = "Port of Seattle VendorConnect"
PLATFORM     = "Port of Seattle"
PORTAL_URL   = "https://hosting.portseattle.org/sops/#/Solicitations"
DETAIL_BASE  = "https://hosting.portseattle.org/sops/#/Solicitations/Detail"
API_BASE     = "https://hosting.portseattle.org/sopsapi/Solicitations"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# OData $expand — same fields the portal uses
EXPAND = (
    "SolicitationCategory($select=Name,Id),"
    "SolicitationStatus($select=Name,Id),"
    "Tags($expand=TagCategory($select=Name);$select=Name,Id)"
)
SELECT = "Id,ProcurementNumber,ProcurementTitle,BidDueDateTime"

PAGE_SIZE = 50

# Detail endpoint — fetches description, contact, department per solicitation
DETAIL_SELECT = (
    "BidDueDateTime,ProcurementNumber,ProcurementTitle,Description,"
    "PortContact,PortContactPhone,PortContactEmail,AdvertisementDate,DisplayFutureList"
)
DETAIL_EXPAND = "SolicitationStatus($select=Name),Department($select=Name)"


def build_url(future=False, skip=0):
    status_filter = (
        "DisplayFutureList eq true"
        if future else
        "DisplayFutureList eq false and "
        "(SolicitationStatus/Name eq 'Open' or SolicitationStatus/Name eq 'Future')"
    )
    params = {
        "$skip": skip,
        "$top": PAGE_SIZE,
        "$orderby": "BidDueDateTime desc",
        "$count": "true",
        "$filter": status_filter,
        "$expand": EXPAND,
        "$select": SELECT,
    }
    # Build manually to avoid double-encoding OData syntax
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{API_BASE}?{qs}"


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
    except Exception:
        return None


def fetch_detail(sol_id):
    """Fetch full detail for one solicitation: description, contact, department."""
    url = (
        f"{API_BASE}?$filter= Id eq {sol_id}"
        f"&$expand={DETAIL_EXPAND}"
        f"&$select={DETAIL_SELECT}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("value", [])
        return items[0] if items else {}
    except Exception as e:
        print(f"    Detail fetch failed for {sol_id}: {e}")
        return {}


def fetch_all(future=False):
    label = "future" if future else "active"
    all_items = []
    skip = 0

    while True:
        url = build_url(future=future, skip=skip)
        print(f"  Fetching {label} page (skip={skip})...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  Request failed: {e}")
            break

        items = data.get("value", [])
        total = data.get("@odata.count", 0)
        all_items.extend(items)
        print(f"  Got {len(items)} items (total: {total})")

        if len(all_items) >= total or len(items) < PAGE_SIZE:
            break
        skip += PAGE_SIZE

    return all_items


def item_to_rfp(item, detail=None, is_future=False):
    sol_id = item.get("Id", "")
    title  = clean_text(item.get("ProcurementTitle", ""))
    if not title:
        return None

    detail      = detail or {}
    ref_number  = item.get("ProcurementNumber") or None
    detail_url  = f"{DETAIL_BASE}/{sol_id}" if sol_id else PORTAL_URL
    due_date    = parse_date(item.get("BidDueDateTime"))

    # Category from SolicitationCategory
    cat_obj  = item.get("SolicitationCategory") or {}
    rfp_type = clean_text(cat_obj.get("Name", "")) or None

    # Status
    status_obj  = item.get("SolicitationStatus") or {}
    status_name = (status_obj.get("Name") or "").lower()
    if is_future or status_name == "future":
        status = "upcoming"
    else:
        status = "active"

    # Description — from detail endpoint, truncate to 800 chars
    raw_desc    = clean_text(detail.get("Description", "") or "")
    description = raw_desc[:800] if raw_desc else None

    # Contact — from detail endpoint
    contact_name  = clean_text(detail.get("PortContact", "") or "") or None
    contact_email = clean_text(detail.get("PortContactEmail", "") or "") or None

    # Department
    dept_obj   = detail.get("Department") or {}
    department = clean_text(dept_obj.get("Name", "") or "") or None

    # Posted date
    posted_date = parse_date(detail.get("AdvertisementDate"))

    return {
        "title":                 title,
        "ref_number":            str(ref_number)[:100] if ref_number else None,
        "detail_url":            detail_url,
        "source_url":            PORTAL_URL,
        "due_date":              due_date,
        "posted_date":           posted_date,
        "status":                status,
        "description":           description,
        "department":            str(department)[:200] if department else None,
        "rfp_type":              str(rfp_type)[:100] if rfp_type else None,
        "agency":                "Port of Seattle",
        "source_name":           SOURCE_NAME,
        "source_platform":       PLATFORM,
        "contact_name":          contact_name,
        "contact_email":         contact_email,
        "categories":      categorize_rfp(title, description),
        "includes_inclusion_plan": False,
    }


def run():
    print(f"Starting Port of Seattle scraper at {datetime.now()}")
    supabase     = get_supabase_client()
    all_rfps     = []
    total_saved  = 0
    error_msg    = None
    status       = "failed"

    try:
        # Active solicitations
        print("\n--- Active solicitations ---")
        active_items = fetch_all(future=False)
        for item in active_items:
            sol_id = item.get("Id", "")
            detail = fetch_detail(sol_id) if sol_id else {}
            rfp = item_to_rfp(item, detail=detail, is_future=False)
            if rfp:
                fp = generate_fingerprint(
                    rfp["ref_number"] or rfp["title"],
                    "Port of Seattle",
                    rfp["due_date"] or "",
                )
                rfp["fingerprint"] = fp
                all_rfps.append(rfp)

        # Future solicitations
        print("\n--- Future solicitations ---")
        future_items = fetch_all(future=True)
        for item in future_items:
            sol_id = item.get("Id", "")
            detail = fetch_detail(sol_id) if sol_id else {}
            rfp = item_to_rfp(item, detail=detail, is_future=True)
            if rfp:
                fp = generate_fingerprint(
                    rfp["ref_number"] or rfp["title"],
                    "Port of Seattle",
                    rfp["due_date"] or "",
                )
                rfp["fingerprint"] = fp
                all_rfps.append(rfp)

        # Deduplicate
        seen = set()
        unique = []
        for rfp in all_rfps:
            if rfp["fingerprint"] not in seen:
                seen.add(rfp["fingerprint"])
                unique.append(rfp)
        all_rfps = unique

        print(f"\nTotal Port of Seattle RFPs: {len(all_rfps)}")
        if all_rfps:
            print(f"  Sample: {all_rfps[0]['title'][:60]}")
            print(f"  Due:    {all_rfps[0]['due_date']}")

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
