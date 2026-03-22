"""
ProcureWare Public-Mode Adapter
Covers: Snohomish County, City of Spokane, Community Transit

Architecture:
- One reusable adapter for the whole ProcureWare portal family
- Config-driven portal list
- Phase 1: discover open bids from /Bids listing page
- Phase 2: open each /Bids/{GUID} detail page sequentially (no race condition)
- Phase 3: detect public docs, results tab, login-gated docs
- Dedupe on external_id (GUID) first
- One bad bid page never kills the run
- Counts: discovered / opened / saved / skipped / failed / login-gated

Rules followed:
- No UI changes
- No WEBS changes
- No login scraping
- No private API reverse engineering
- Full replacement file only
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

# ---------------------------------------------------------------------------
# Portal config — add new WA ProcureWare portals here only
# ---------------------------------------------------------------------------
PORTALS = [
    {
        "portal_name": "Snohomish County",
        "base_url": "https://snoco.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": True,
        "supports_guest_documents": True,
        "portal_timezone": "America/Los_Angeles",
    },
    {
        "portal_name": "City of Spokane",
        "base_url": "https://spokane.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": False,
        "supports_guest_documents": True,
        "portal_timezone": "America/Los_Angeles",
    },
    {
        "portal_name": "Community Transit",
        "base_url": "https://commtrans.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": False,
        "supports_guest_documents": True,
        "portal_timezone": "America/Los_Angeles",
    },
]

SOURCE_NAME = "ProcureWare Bid Portal"
SOURCE_PLATFORM = "Procureware"

# GUID pattern used in /Bids/{GUID} URLs
GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.IGNORECASE,
)

# Status texts that indicate a bid is currently open to submissions
OPEN_STATUSES = frozenset({
    "open for bidding", "available", "open", "active",
    "accepting submissions", "in progress",
})

# Headings that are portal chrome, not bid titles
SKIP_HEADINGS = frozenset({
    "bids", "home", "documents", "activities", "contracts",
    "doc library", "login", "register",
})

DATE_NOISE_RE = re.compile(
    r"\(in \d+ days?\)|\(overdue\)|\(today\)|due|close[sd]?|deadline",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(raw_text):
    """Parse any recognisable date string. Returns ISO string or None."""
    if not raw_text:
        return None
    cleaned = DATE_NOISE_RE.sub("", raw_text).strip(" ,:-")
    cleaned = re.sub(
        r"\b(PT|PST|PDT|MT|MST|MDT|CT|CST|CDT|ET|EST|EDT)\b", "", cleaned
    ).strip()
    if not cleaned:
        return None
    try:
        return dateutil_parser.parse(cleaned, fuzzy=False).isoformat()
    except Exception:
        return None


def extract_dates(text):
    """Return all parsed ISO dates found in text, in order of appearance."""
    dates = []
    for m in re.finditer(
        r"\b(\d{1,2}/\d{1,2}/\d{4})(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?\b", text
    ):
        parsed = parse_date(m.group(1))
        if parsed:
            dates.append(parsed)
    return dates


def is_open(container_text):
    """Return True if the container text contains an open-bid status phrase."""
    lower = container_text.lower()
    return any(s in lower for s in OPEN_STATUSES)


def guid_from_url(url):
    """Extract the GUID from a /Bids/{GUID} URL. Returns string or None."""
    m = GUID_RE.search(url)
    return m.group(0).lower() if m else None


def absolute_url(href, base_url):
    """Return an absolute URL from a possibly-relative href."""
    if not href:
        return None
    if href.startswith("http"):
        return href
    return base_url + ("" if href.startswith("/") else "/") + href.lstrip("/")


def make_empty_record(portal):
    """Return a record with all normalised fields set to safe defaults."""
    return {
        # Identity
        "source_platform": SOURCE_PLATFORM,
        "source_name": SOURCE_NAME,
        "source_url": portal["base_url"] + portal["bids_path"],
        "source_portal": portal["base_url"],
        # Core fields
        "external_id": None,
        "title": None,
        "ref_number": None,          # solicitation_number from listing
        "agency": portal["portal_name"],
        "department": None,
        "status": None,
        "due_date": None,
        "posted_date": None,
        "description": None,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        # Doc/results detection
        "has_public_documents": False,
        "has_results_tab": False,
        "has_login_required_documents": False,
        # DB fields
        "detail_url": None,
        "rfp_type": None,
        "includes_inclusion_plan": False,
        "categories": [],
        "raw_data": None,
        "fingerprint": None,
    }


def build_fingerprint(record):
    """
    Dedupe priority per brief:
    1. external_id (GUID) — most reliable
    2. portal + solicitation_number + due_date + title
    3. portal + title + due_date
    """
    if record.get("external_id"):
        return record["source_portal"] + "|" + record["external_id"]
    key = "|".join([
        record.get("source_portal", ""),
        record.get("ref_number", "") or "",
        record.get("due_date", "") or "",
        (record.get("title", "") or "").lower().strip(),
    ])
    return generate_fingerprint(key, "", "")


# ---------------------------------------------------------------------------
# Phase 1 — listing page
# ---------------------------------------------------------------------------

def parse_listing_page(html, portal):
    """
    Parse the /Bids listing page.
    Returns list of minimal dicts: {external_id, detail_url, ref_number, due_date, status_text}
    Only includes bids whose container text contains an open status.
    """
    entries = []
    soup = BeautifulSoup(html, "lxml")
    base = portal["base_url"]

    bid_links = [
        a for a in soup.find_all("a", href=True)
        if GUID_RE.search(a["href"]) and "/Bids/" in a["href"]
    ]

    print(f"  Listing: {len(bid_links)} total bid links found")

    seen_guids = set()
    skipped_closed = 0

    for link in bid_links:
        href = link["href"]
        guid = guid_from_url(href)
        if not guid or guid in seen_guids:
            continue
        seen_guids.add(guid)

        detail_url = absolute_url(href, base)
        container = link.find_parent(["tr", "div", "li"]) or link
        container_text = container.get_text(" ", strip=True)

        # Filter: only open bids
        if not is_open(container_text):
            skipped_closed += 1
            continue

        # Status text (first matching phrase found)
        status_text = next(
            (s for s in OPEN_STATUSES if s in container_text.lower()), "open"
        )

        # Ref/solicitation number = link text (e.g. "RFP-25-0546BC")
        ref_number = clean_text(link.get_text()) or None

        # Due date = last date in container (posted date comes first)
        all_dates = extract_dates(container_text)
        due_date = all_dates[-1] if all_dates else None

        entries.append({
            "external_id": guid,
            "detail_url": detail_url,
            "ref_number": ref_number,
            "due_date": due_date,
            "status_text": status_text,
        })

    print(f"  Listing: {len(entries)} open, {skipped_closed} closed/skipped")
    for e in entries[:3]:
        print(f"    guid={e['external_id']} ref={e['ref_number']} due={e['due_date']}")

    return entries


# ---------------------------------------------------------------------------
# Phase 2 + 3 — detail page (sequential, no race condition)
# ---------------------------------------------------------------------------

LOGIN_INDICATORS = frozenset({
    "log in", "login", "sign in", "register to", "must be logged",
    "requires login", "not authorized",
})


def detect_login_gate(soup):
    """Return True if the page content suggests a login wall."""
    text = soup.get_text(" ", strip=True).lower()
    return any(phrase in text for phrase in LOGIN_INDICATORS)


def scrape_detail_html(html, entry, portal):
    """
    Parse a rendered /Bids/{GUID} detail page.
    Returns dict of enriched fields. Missing fields stay None/False.
    One exception here does NOT propagate — caller handles it.
    """
    soup = BeautifulSoup(html, "lxml")
    base = portal["base_url"]

    result = {
        "title": entry.get("ref_number"),   # fallback
        "description": None,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "posted_date": None,
        "has_public_documents": False,
        "has_results_tab": False,
        "has_login_required_documents": False,
        "documents": [],
        "login_gated": False,
    }

    # Check for login gate on whole page
    if detect_login_gate(soup):
        result["login_gated"] = True
        result["has_login_required_documents"] = True
        return result

    # --- Title ---
    portal_name_lower = portal["portal_name"].lower()
    for sel in ["h2", "h3", "h4", ".bid-title", "[class*='title']", "[class*='Title']"]:
        tag = soup.select_one(sel)
        if tag:
            text = clean_text(tag.get_text())
            if (
                text
                and len(text) > 5
                and text.lower() not in SKIP_HEADINGS
                and portal_name_lower not in text.lower()
            ):
                result["title"] = text
                break

    # --- Description ---
    for sel in [
        "[class*='description']", "[class*='Description']",
        "[class*='summary']", "[class*='Summary']",
        "[class*='scope']", "[class*='Scope']",
        "p",
    ]:
        tags = soup.select(sel)
        for tag in tags:
            text = clean_text(tag.get_text())
            if text and len(text) > 40:
                result["description"] = text[:600]
                break
        if result["description"]:
            break

    # --- Contact info ---
    # ProcureWare contact sections often use label: value pattern
    page_text = soup.get_text(" ", strip=True)

    email_m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", page_text)
    if email_m:
        result["contact_email"] = email_m.group(0).lower()

    phone_m = re.search(
        r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}", page_text
    )
    if phone_m:
        result["contact_phone"] = phone_m.group(0).strip()

    # Contact name: look for "Contact:" or "Buyer:" label patterns
    contact_m = re.search(
        r"(?:Contact|Buyer|Procurement Contact)\s*[:\-]\s*([A-Z][a-z]+\s+[A-Z][a-z]+)",
        page_text,
    )
    if contact_m:
        result["contact_name"] = contact_m.group(1).strip()

    # --- Posted date ---
    posted_m = re.search(
        r"(?:Posted|Published|Release Date?)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
        page_text, re.IGNORECASE,
    )
    if posted_m:
        result["posted_date"] = parse_date(posted_m.group(1))

    # --- Tabs detection ---
    all_text_lower = page_text.lower()
    result["has_results_tab"] = "results" in all_text_lower or "award" in all_text_lower

    # --- Documents ---
    doc_links = soup.find_all(
        "a",
        href=re.compile(r"/(BidDocument|Document|File|Download)/", re.I),
    )
    for doc in doc_links:
        doc_href = doc.get("href", "")
        if detect_login_gate(BeautifulSoup(str(doc), "lxml")):
            result["has_login_required_documents"] = True
            continue
        doc_url = absolute_url(doc_href, base)
        doc_name = clean_text(doc.get_text()) or doc_href.split("/")[-1]
        result["documents"].append({"name": doc_name, "url": doc_url})

    if result["documents"]:
        result["has_public_documents"] = True

    # Fallback: PDF/ZIP direct links
    if not result["documents"]:
        for a in soup.find_all("a", href=re.compile(r"\.(pdf|docx?|xlsx?|zip)($|\?)", re.I)):
            href = a.get("href", "")
            url = absolute_url(href, base)
            name = clean_text(a.get_text()) or href.split("/")[-1]
            result["documents"].append({"name": name, "url": url})
        if result["documents"]:
            result["has_public_documents"] = True

    return result


# ---------------------------------------------------------------------------
# Portal scraper
# ---------------------------------------------------------------------------

async def scrape_portal(portal):
    """
    Scrape one ProcureWare portal.
    Returns (list_of_rfps, counts_dict).
    """
    from playwright.async_api import async_playwright

    rfps = []
    counts = {
        "discovered": 0,
        "opened": 0,
        "saved": 0,
        "skipped": 0,
        "failed": 0,
        "login_gated": 0,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        # ── Phase 1: listing ──────────────────────────────────────────────
        listing_page = await context.new_page()
        listing_url = portal["base_url"] + portal["bids_path"]
        print(f"  Loading listing: {listing_url}")
        try:
            await listing_page.goto(listing_url, timeout=60000, wait_until="networkidle")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"  [ERROR] Listing page failed for {portal['portal_name']}: {e}")
            await browser.close()
            return rfps, counts

        listing_html = await listing_page.content()
        await listing_page.close()

        entries = parse_listing_page(listing_html, portal)
        counts["discovered"] = len(entries)

        if not entries:
            print(f"  No open bids found for {portal['portal_name']}")
            await browser.close()
            return rfps, counts

        # ── Phase 2 + 3: detail pages — SEQUENTIAL (no race condition) ───
        detail_page = await context.new_page()

        for entry in entries:
            detail_url = entry["detail_url"]
            counts["opened"] += 1

            try:
                await detail_page.goto(
                    detail_url, timeout=40000, wait_until="networkidle"
                )
                await asyncio.sleep(2)
                detail_html = await detail_page.content()
            except Exception as e:
                print(f"  [WARN] Failed to load {detail_url}: {e}")
                counts["failed"] += 1
                continue

            try:
                enriched = scrape_detail_html(detail_html, entry, portal)
            except Exception as e:
                print(f"  [WARN] Failed to parse {detail_url}: {e}")
                counts["failed"] += 1
                continue

            if enriched.get("login_gated"):
                counts["login_gated"] += 1
                print(f"  [GATED] {entry['ref_number']} — login required")
                # Still save with what we have from the listing page
            
            record = make_empty_record(portal)
            record["external_id"] = entry["external_id"]
            record["detail_url"] = detail_url
            record["ref_number"] = entry.get("ref_number")
            record["due_date"] = entry.get("due_date")
            record["status"] = entry.get("status_text", "open")

            record["title"] = enriched.get("title") or entry.get("ref_number") or "Untitled"
            record["description"] = enriched.get("description")
            record["contact_name"] = enriched.get("contact_name")
            record["contact_email"] = enriched.get("contact_email")
            record["contact_phone"] = enriched.get("contact_phone")
            record["posted_date"] = enriched.get("posted_date")
            record["has_public_documents"] = enriched.get("has_public_documents", False)
            record["has_results_tab"] = enriched.get("has_results_tab", False)
            record["has_login_required_documents"] = enriched.get("has_login_required_documents", False)

            docs = enriched.get("documents", [])
            record["raw_data"] = json.dumps({
                "documents": docs,
                "login_gated": enriched.get("login_gated", False),
            })

            record["fingerprint"] = build_fingerprint(record)

            print(
                f"  [{counts['opened']}/{counts['discovered']}] "
                f"{record['ref_number']} | '{str(record['title'])[:40]}' "
                f"| due={record['due_date']} | docs={len(docs)}"
            )

            rfps.append(record)
            counts["saved"] += 1

        await detail_page.close()
        await browser.close()

    return rfps, counts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    print(f"Starting ProcureWare scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None
    global_counts = {
        "discovered": 0, "opened": 0, "saved": 0,
        "skipped": 0, "failed": 0, "login_gated": 0,
    }

    try:
        for portal in PORTALS:
            print(f"\n{'='*50}")
            print(f"Portal: {portal['portal_name']}")
            print(f"{'='*50}")

            rfps, counts = asyncio.run(scrape_portal(portal))

            for k in global_counts:
                global_counts[k] += counts.get(k, 0)

            print(
                f"\n  {portal['portal_name']} summary: "
                f"discovered={counts['discovered']} "
                f"opened={counts['opened']} "
                f"saved={counts['saved']} "
                f"failed={counts['failed']} "
                f"login_gated={counts['login_gated']}"
            )

            all_rfps.extend(rfps)

        print(f"\nTotal across all portals: {len(all_rfps)} RFPs")
        print(
            f"Global counts: discovered={global_counts['discovered']} "
            f"opened={global_counts['opened']} "
            f"saved={global_counts['saved']} "
            f"failed={global_counts['failed']} "
            f"login_gated={global_counts['login_gated']}"
        )

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
        status = "failed"
        print(f"[ERROR] Scraper run failed: {e}")
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
