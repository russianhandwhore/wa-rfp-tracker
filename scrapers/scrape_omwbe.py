"""
OMWBE Bids & Contracting Opportunities Scraper
Source: https://omwbe.wa.gov/small-business-assistance/bids-contracting-opportunities

Static Drupal CMS — requests + BeautifulSoup only, no Playwright.
Listing page: title + closing date + detail URL
Detail pages: description (max 320 chars) + organization/agency
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

SOURCE_URL = "https://omwbe.wa.gov/small-business-assistance/bids-contracting-opportunities"
BASE_URL = "https://omwbe.wa.gov"
SOURCE_NAME = "OMWBE - Office of Minority and Women's Business Enterprises"
SOURCE_PLATFORM = "OMWBE"
MAX_DESC_CHARS = 320
DETAIL_WORKERS = 10  # concurrent detail page fetches

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Known nav/footer noise to strip from description
NOISE_PHRASES = [
    "OMWBE Academy", "Bids & Contracting Opportunities",
    "Doing Business with Government", "Supplier Diversity",
    "Small Business Assistance", "Skip to main content",
    "Select language", "Calendar of Events",
]


def parse_date(date_str):
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    print(f"  [WARN] Unrecognised date: '{s}'")
    return None


def fingerprint_from_url(detail_url):
    """Hash the detail URL to make a short unique fingerprint."""
    slug = detail_url.rstrip("/").split("/")[-1]
    return generate_fingerprint(slug, "OMWBE", "")


def fetch_detail(detail_url):
    """
    Fetch one OMWBE detail page and extract description + organization.
    Returns (description, organization) — both may be None.
    Never raises — bad pages return (None, None).
    """
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None, None, None, None
        soup = BeautifulSoup(resp.text, "lxml")

        # Remove nav/header/footer/sidebar noise
        for tag in soup.find_all(["nav", "header", "footer",
                                   "script", "style", "aside"]):
            tag.decompose()

        # Organization: Drupal renders fields as label+value pairs.
        # Common patterns: .field-label contains "Organization", next sibling is value
        # Or the page text contains "Organization: SomeName"
        organization = None
        page_text = soup.get_text(" ", strip=True)
        org_m = re.search(
            r"Organization\s*[:\-]\s*(.{3,100}?)(?:\s{2,}|Closing|Point|Description|$)",
            page_text, re.IGNORECASE
        )
        if org_m:
            org = clean_text(org_m.group(1))
            if org and len(org) < 100:
                organization = org
        # Fallback: Drupal field-label div
        if not organization:
            for label_tag in soup.find_all(class_=lambda c: c and "field-label" in c):
                if "organization" in label_tag.get_text().lower():
                    value_tag = label_tag.find_next_sibling()
                    if value_tag:
                        org = clean_text(value_tag.get_text())
                        if org and len(org) < 100:
                            organization = org
                            break

        # Description: main content area — longest meaningful text block
        # Try Drupal field__item / node body selectors first
        description = None
        for sel in [
            ".field--name-body", ".field-name-body",
            ".field--name-field-description", ".node__content",
            "article", ".view-mode-full",
        ]:
            tag = soup.select_one(sel)
            if tag:
                text = clean_text(tag.get_text(" ", strip=True))
                # Strip known noise
                for noise in NOISE_PHRASES:
                    text = text.replace(noise, "")
                text = " ".join(text.split()).strip()
                if len(text) > 40:
                    description = text[:MAX_DESC_CHARS]
                    break

        # Fallback: longest paragraph
        if not description:
            best = ""
            for p in soup.find_all("p"):
                text = clean_text(p.get_text())
                if len(text) > len(best) and len(text) > 40:
                    best = text
            if best:
                description = best[:MAX_DESC_CHARS]

        # Contact: OMWBE "Point of Contact" field often contains an email directly
        # e.g. "Point of Contact: barb.wakefield@parkstacoma.gov"
        # or "Point of Contact: Jane Smith"
        contact_name = None
        contact_email = None

        # Extract email anywhere in the page text
        email_m = re.search(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", page_text
        )
        if email_m:
            contact_email = email_m.group(0).lower()

        # Extract Point of Contact value — may be a name, email, or both
        poc_m = re.search(
            r"Point of Contact\s*[:\-]\s*(.{2,120}?)(?:\s{2,}|$|\n)",
            page_text, re.IGNORECASE
        )
        if poc_m:
            candidate = clean_text(poc_m.group(1))
            if "@" in candidate:
                # Extract just the email from the POC value (may have trailing noise)
                # Always use POC email — it is more specific than the general page email
                email_in_poc = re.search(
                    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", candidate
                )
                if email_in_poc:
                    contact_email = email_in_poc.group(0).lower()
            else:
                words = candidate.split()
                if 2 <= len(words) <= 6 and "http" not in candidate:
                    contact_name = candidate

        return description, organization, contact_name, contact_email

    except Exception as e:
        print(f"  [WARN] Detail fetch failed {detail_url}: {e}")
        return None, None, None, None


def scrape_listings():
    """Fetch listing page, then fetch detail pages concurrently."""
    rfps = []

    print(f"  Fetching listing page...")
    try:
        resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERROR] Could not fetch OMWBE listing: {e}")
        return rfps

    print(f"  Status: {resp.status_code} | {len(resp.text)} chars")
    soup = BeautifulSoup(resp.text, "lxml")

    table = soup.find("table")
    if not table:
        print("  [ERROR] No table found on OMWBE page")
        return rfps

    rows = table.find_all("tr")
    print(f"  Found {len(rows)} rows (including header)")

    # Build base records from listing
    base_records = []
    seen_slugs = set()

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        link = cells[0].find("a", href=True)
        if not link:
            continue

        title = clean_text(link.get_text()) or None
        if not title:
            continue

        href = link["href"]
        detail_url = href if href.startswith("http") else BASE_URL + href

        # Deduplicate by slug
        slug = fingerprint_from_url(detail_url)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        due_date = parse_date(clean_text(cells[1].get_text()))

        base_records.append({
            "title": title,
            "detail_url": detail_url,
            "due_date": due_date,
            "fingerprint": slug,
        })

    print(f"  {len(base_records)} unique listings found")
    if not base_records:
        return rfps

    # Fetch detail pages concurrently
    print(f"  Fetching {len(base_records)} detail pages ({DETAIL_WORKERS} at a time)...")
    details = {}
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        future_to_url = {
            executor.submit(fetch_detail, r["detail_url"]): r["detail_url"]
            for r in base_records
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                desc, org, contact, email = future.result()
            except Exception:
                desc, org, contact, email = None, None, None, None
            details[url] = (desc, org, contact, email)

    # Build final records
    for rec in base_records:
        desc, org, contact, email = details.get(rec["detail_url"], (None, None, None, None))

        rfp = {
            "title": rec["title"],
            "detail_url": rec["detail_url"],
            "due_date": rec["due_date"],
            "description": desc,
            "agency": org,
            "contact_name": contact,
            "contact_email": email,
            "source_url": SOURCE_URL,
            "source_name": SOURCE_NAME,
            "source_platform": SOURCE_PLATFORM,
            "status": "active",
            "department": None,
            "ref_number": None,
            "posted_date": None,
            "rfp_type": None,
            "includes_inclusion_plan": False,
            "categories": [],
            "raw_data": None,
            "fingerprint": rec["fingerprint"],
        }
        if len(rfps) <= 5:
            print(f"  [{len(rfps)}] title={str(rfp.get('title',''))[:40]}")
            print(f"      org={rfp.get('agency')} | contact={rfp.get('contact_name')} | email={rfp.get('contact_email')}")
        rfps.append(rfp)

    has_desc = sum(1 for r in rfps if r.get("description"))
    has_org = sum(1 for r in rfps if r.get("agency"))
    has_contact = sum(1 for r in rfps if r.get("contact_name") or r.get("contact_email"))
    print(f"  With description:  {has_desc}/{len(rfps)}")
    print(f"  With organization: {has_org}/{len(rfps)}")
    print(f"  With contact:      {has_contact}/{len(rfps)}")

    return rfps


def run():
    print(f"Starting OMWBE scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None
    status = "failed"

    try:
        all_rfps = scrape_listings()
        print(f"Total OMWBE listings: {len(all_rfps)}")

        if all_rfps:
            s = all_rfps[0]
            print(f"  Sample title: {str(s.get('title', ''))[:60]}")
            print(f"  Sample due:   {s.get('due_date')}")
            print(f"  Sample org:   {s.get('agency')}")
            print(f"  Sample desc:  {str(s.get('description', ''))[:80]}")

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
