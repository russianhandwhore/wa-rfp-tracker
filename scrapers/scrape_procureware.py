"""
ProcureWare Public-Mode Adapter
Covers: Snohomish County, City of Spokane, Community Transit

Phase 1: discover open bids from /Bids listing page
Phase 2: fetch detail pages 5 at a time (each gets own page, no race condition)
         extracts: real title, description, department, contact, documents
One bad detail page never kills the run.
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
# Portal config
# ---------------------------------------------------------------------------
PORTALS = [
    {
        "portal_name": "Snohomish County",
        "base_url": "https://snoco.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": True,
        "supports_guest_documents": True,
    },
    {
        "portal_name": "City of Spokane",
        "base_url": "https://spokane.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": False,
        "supports_guest_documents": True,
    },
    {
        "portal_name": "Community Transit",
        "base_url": "https://commtrans.procureware.com",
        "bids_path": "/Bids",
        "supports_guest_results": False,
        "supports_guest_documents": True,
    },
]

SOURCE_NAME = "ProcureWare Bid Portal"
SOURCE_PLATFORM = "Procureware"
CONCURRENCY = 5   # detail pages fetched in parallel

GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.IGNORECASE,
)

OPEN_STATUSES = frozenset({
    "open for bidding", "available", "open", "active",
    "accepting submissions", "in progress",
})

SKIP_HEADINGS = frozenset({
    "bids", "home", "documents", "activities", "contracts",
    "doc library", "login", "register",
})

LOGIN_PHRASES = frozenset({
    "log in", "login", "sign in", "register to",
    "must be logged", "requires login", "not authorized",
})

DATE_NOISE_RE = re.compile(
    r"\(in \d+ days?\)|\(overdue\)|\(today\)|due|close[sd]?|deadline",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(raw_text):
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
    dates = []
    for m in re.finditer(
        r"\b(\d{1,2}/\d{1,2}/\d{4})(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?\b", text
    ):
        parsed = parse_date(m.group(1))
        if parsed:
            dates.append(parsed)
    return dates


def is_open(container_text):
    lower = container_text.lower()
    return any(s in lower for s in OPEN_STATUSES)


def guid_from_url(url):
    m = GUID_RE.search(url)
    return m.group(0).lower() if m else None


def absolute_url(href, base_url):
    if not href:
        return None
    if href.startswith("http"):
        return href
    return base_url.rstrip("/") + "/" + href.lstrip("/")


def page_is_login_gated(soup):
    """
    Returns True only if the MAIN CONTENT area (not nav/header/footer)
    contains login-required language. Prevents the nav 'Log In' button
    from false-positive gating every public page.
    """
    # Remove nav, header, footer before checking
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()
    content_text = soup.get_text(" ", strip=True).lower()
    # Only gate if a strong login-required phrase appears in content
    strong_phrases = frozenset({
        "must be logged", "requires login", "not authorized",
        "you must log in to", "login required", "please log in to access",
    })
    return any(phrase in content_text for phrase in strong_phrases)


def make_empty_record(portal):
    return {
        "source_platform": SOURCE_PLATFORM,
        "source_name": SOURCE_NAME,
        "source_url": portal["base_url"] + portal["bids_path"],
        # source_portal, has_public_documents, has_results_tab,
        # has_login_required_documents are NOT DB columns — stored in raw_data
        "title": None,
        "ref_number": None,
        "agency": portal["portal_name"],
        "department": None,
        "status": None,
        "due_date": None,
        "posted_date": None,
        "description": None,
        "contact_name": None,
        "contact_email": None,
        "detail_url": None,
        "rfp_type": None,
        "includes_inclusion_plan": False,
        "categories": [],
        "raw_data": None,
        "fingerprint": None,
    }


def build_fingerprint(record, external_id=None):
    if external_id:
        return record.get("source_url", "") + "|" + external_id
    key = "|".join([
        record.get("source_url", ""),
        record.get("ref_number", "") or "",
        record.get("due_date", "") or "",
        (record.get("title", "") or "").lower().strip(),
    ])
    return generate_fingerprint(key, "", "")


# ---------------------------------------------------------------------------
# Phase 1 — listing page parser
# ---------------------------------------------------------------------------

def parse_listing_page(html, portal):
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

        if not is_open(container_text):
            skipped_closed += 1
            continue

        status_text = next(
            (s for s in OPEN_STATUSES if s in container_text.lower()), "open"
        )
        ref_number = clean_text(link.get_text()) or None
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
# Phase 2 — detail page parser (pure HTML, no browser here)
# ---------------------------------------------------------------------------

def parse_detail_html(html, entry, portal):
    """
    Extract enrichment fields from rendered detail page HTML.
    Returns dict — missing fields are None/False, never raises.
    """
    result = {
        "title": entry.get("ref_number"),  # fallback
        "description": None,
        "department": None,
        "contact_name": None,
        "contact_email": None,
        "posted_date": None,
        "has_public_documents": False,
        "has_results_tab": False,
        "has_login_required_documents": False,
        "documents": [],
        "login_gated": False,
    }

    soup = BeautifulSoup(html, "lxml")
    base = portal["base_url"]
    page_text = soup.get_text(" ", strip=True)

    # Login gate check — pass soup so nav/header/footer can be excluded
    if page_is_login_gated(soup):
        result["login_gated"] = True
        result["has_login_required_documents"] = True
        return result

    portal_name_lower = portal["portal_name"].lower()

    # --- Title ---
    # ProcureWare renders bid name in h2/h3, sometimes with a label before it
    for sel in ["h2", "h3", "h4", ".bid-title", "[class*='title']", "[class*='Title']"]:
        tag = soup.select_one(sel)
        if tag:
            text = clean_text(tag.get_text())
            if (
                text
                and len(text) > 5
                and text.lower() not in SKIP_HEADINGS
                and portal_name_lower not in text.lower()
                and not text.lower().startswith("bid")
            ):
                result["title"] = text
                break

    # --- Department ---
    # Look for "Department:" label pattern in page text
    dept_m = re.search(
        r"(?:Department|Division|Unit)\s*[:\-]\s*([A-Za-z][^\n\r]{3,60}?)(?:\s{2,}|$|\n)",
        page_text, re.IGNORECASE,
    )
    if dept_m:
        dept = dept_m.group(1).strip()
        if len(dept) < 80:
            result["department"] = dept

    # --- Description ---
    # Try class-based selectors first, then fall back to longest <p> on page
    for sel in [
        "[class*='description']", "[class*='Description']",
        "[class*='summary']", "[class*='Summary']",
        "[class*='scope']", "[class*='Scope']",
        "[class*='detail']", "[class*='Detail']",
    ]:
        tag = soup.select_one(sel)
        if tag:
            text = clean_text(tag.get_text())
            if text and len(text) > 40:
                result["description"] = text[:800]
                break

    # Fallback: longest <p> that isn't nav chrome
    if not result["description"]:
        best = ""
        for p in soup.find_all("p"):
            text = clean_text(p.get_text())
            if text and len(text) > len(best) and len(text) > 40:
                best = text
        if best:
            result["description"] = best[:800]

    # --- Contact ---
    email_m = re.search(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", page_text
    )
    if email_m:
        result["contact_email"] = email_m.group(0).lower()

    contact_m = re.search(
        r"(?:Contact|Buyer|Procurement Contact|Assigned To)\s*[:\-]\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
        page_text,
    )
    if contact_m:
        result["contact_name"] = contact_m.group(1).strip()

    # --- Posted date ---
    posted_m = re.search(
        r"(?:Posted|Published|Release\s*Date?|Issue\s*Date?)\s*[:\-]?\s*"
        r"(\d{1,2}/\d{1,2}/\d{4})",
        page_text, re.IGNORECASE,
    )
    if posted_m:
        result["posted_date"] = parse_date(posted_m.group(1))

    # --- Results tab ---
    result["has_results_tab"] = bool(
        re.search(r"\b(results|award|awarded|tabulation)\b", page_text, re.IGNORECASE)
    )

    # --- Documents ---
    doc_links = soup.find_all(
        "a",
        href=re.compile(r"/(BidDocument|Document|File|Download)/", re.I),
    )
    for doc in doc_links:
        doc_text = doc.get_text(" ", strip=True).lower()
        if any(p in doc_text for p in LOGIN_PHRASES):
            result["has_login_required_documents"] = True
            continue
        doc_href = doc.get("href", "")
        doc_url = absolute_url(doc_href, base)
        doc_name = clean_text(doc.get_text()) or doc_href.split("/")[-1]
        result["documents"].append({"name": doc_name, "url": doc_url})

    # Fallback: direct PDF/ZIP links
    if not result["documents"]:
        for a in soup.find_all(
            "a", href=re.compile(r"\.(pdf|docx?|xlsx?|zip)($|\?)", re.I)
        ):
            href = a.get("href", "")
            url = absolute_url(href, base)
            name = clean_text(a.get_text()) or href.split("/")[-1]
            result["documents"].append({"name": name, "url": url})

    if result["documents"]:
        result["has_public_documents"] = True

    return result


# ---------------------------------------------------------------------------
# Phase 2 — concurrent detail fetcher
# ---------------------------------------------------------------------------

async def fetch_one_detail(context, entry, portal, semaphore, counts):
    """
    Open one detail page in its own browser page.
    Returns enriched dict or None on failure.
    Each task owns its page — no shared page, no race condition.
    """
    async with semaphore:
        page = await context.new_page()
        try:
            await page.goto(
                entry["detail_url"], timeout=15000, wait_until="domcontentloaded"
            )
            await asyncio.sleep(1)
            html = await page.content()
            enriched = parse_detail_html(html, entry, portal)
            if enriched.get("login_gated"):
                counts["login_gated"] += 1
                print(f"  [GATED] {entry['ref_number']}")
            return enriched
        except Exception as e:
            counts["failed"] += 1
            print(f"  [WARN] Detail failed {entry['ref_number']}: {e}")
            return None
        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Portal scraper
# ---------------------------------------------------------------------------

async def scrape_portal(portal):
    from playwright.async_api import async_playwright

    rfps = []
    counts = {
        "discovered": 0, "opened": 0, "saved": 0,
        "skipped": 0, "failed": 0, "login_gated": 0,
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

        # Phase 1: listing
        listing_page = await context.new_page()
        listing_url = portal["base_url"] + portal["bids_path"]
        print(f"  Loading listing: {listing_url}")
        try:
            await listing_page.goto(
                listing_url, timeout=60000, wait_until="networkidle"
            )
            await asyncio.sleep(3)
        except Exception as e:
            print(f"  [ERROR] Listing failed for {portal['portal_name']}: {e}")
            await browser.close()
            return rfps, counts

        listing_html = await listing_page.content()
        await listing_page.close()

        entries = parse_listing_page(listing_html, portal)
        counts["discovered"] = len(entries)

        if not entries:
            print(f"  No open bids for {portal['portal_name']}")
            await browser.close()
            return rfps, counts

        # Phase 2: concurrent detail pages (5 at a time, each owns its page)
        print(f"  Fetching {len(entries)} detail pages ({CONCURRENCY} at a time)...")
        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = [
            fetch_one_detail(context, entry, portal, semaphore, counts)
            for entry in entries
        ]
        detail_results = await asyncio.gather(*tasks)

        await browser.close()

    # Build final records
    for entry, enriched in zip(entries, detail_results):
        counts["opened"] += 1
        external_id = entry["external_id"]  # used for fingerprint + raw_data only
        record = make_empty_record(portal)
        record["detail_url"] = entry["detail_url"]
        record["ref_number"] = entry.get("ref_number")
        record["due_date"] = entry.get("due_date")
        record["status"] = entry.get("status_text", "open")

        if enriched:
            record["title"] = enriched.get("title") or entry.get("ref_number") or "Untitled"
            record["description"] = enriched.get("description")
            record["department"] = enriched.get("department")
            record["contact_name"] = enriched.get("contact_name")
            record["contact_email"] = enriched.get("contact_email")
            record["posted_date"] = enriched.get("posted_date")
            docs = enriched.get("documents", [])
            record["raw_data"] = json.dumps({
                "external_id": external_id,
                "source_portal": portal["base_url"],
                "has_public_documents": enriched.get("has_public_documents", False),
                "has_results_tab": enriched.get("has_results_tab", False),
                "has_login_required_documents": enriched.get("has_login_required_documents", False),
                "documents": docs,
                "login_gated": enriched.get("login_gated", False),
            })
        else:
            # Detail fetch failed — save with listing data only
            record["title"] = entry.get("ref_number") or "Untitled"
            record["raw_data"] = json.dumps({
                "external_id": external_id,
                "source_portal": portal["base_url"],
            })

        record["fingerprint"] = build_fingerprint(record, external_id=external_id)

        print(
            f"  {record['ref_number']} | '{str(record['title'])[:45]}' "
            f"| due={record['due_date']} | dept={record['department']} "
            f"| contact={record['contact_name']}"
        )

        rfps.append(record)
        counts["saved"] += 1

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
                f"saved={counts['saved']} "
                f"failed={counts['failed']} "
                f"login_gated={counts['login_gated']}"
            )
            all_rfps.extend(rfps)

        print(f"\nTotal across all portals: {len(all_rfps)} RFPs")

        if all_rfps:
            for i in range(0, len(all_rfps), 50):
                batch = all_rfps[i:i + 50]
                try:
                    supabase.table("rfps").upsert(
                        batch, on_conflict="fingerprint"
                    ).execute()
                    total_saved += len(batch)
                    print(f"  Saved batch {i // 50 + 1}: {len(batch)} records")
                except Exception as batch_err:
                    print(f"  [ERROR] Batch {i // 50 + 1} failed: {batch_err}")

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
