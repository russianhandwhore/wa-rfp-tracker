import asyncio
import json
from playwright.async_api import async_playwright
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
import re
from bs4 import BeautifulSoup

BASE_URL = "https://pr-webs-vendor.des.wa.gov/BidCalendar.aspx"
WEBS_BASE = "https://pr-webs-vendor.des.wa.gov"
SOURCE_NAME = "WEBS - Washington Electronic Business Solution"

AGENCY_CODES = {
    "DCYF": "Dept. of Children, Youth & Families",
    "DNR": "Dept. of Natural Resources",
    "WDFW": "WA Dept. of Fish & Wildlife",
    "WSDOT": "WA State DOT",
    "DOT": "WA State DOT",
    "DSHS": "Dept. of Social & Health Services",
    "DOC": "Dept. of Corrections",
    "DOH": "Dept. of Health",
    "OFM": "Office of Financial Management",
    "AGR": "Dept. of Agriculture",
    "ECY": "Dept. of Ecology",
    "LNI": "Dept. of Labor & Industries",
    "L&I": "Dept. of Labor & Industries",
    "DES": "Dept. of Enterprise Services",
    "WSP": "WA State Patrol",
    "DOL": "Dept. of Licensing",
    "DVA": "Dept. of Veterans Affairs",
    "DFW": "WA Dept. of Fish & Wildlife",
    "HCA": "Health Care Authority",
    "HHSB": "Health Care Authority",
    "OAH": "Office of Administrative Hearings",
    "OSPI": "Office of Superintendent of Public Instruction",
    "SOS": "Office of Secretary of State",
    "LEG": "WA State Legislature",
    "ATG": "Office of Attorney General",
    "OAG": "Office of Attorney General",
    "LCB": "Liquor & Cannabis Board",
    "UTC": "Utilities & Transportation Commission",
    "WSSDA": "WA State School Directors Association",
    "WSAC": "Student Achievement Council",
    "SBA": "State Board for Community & Technical Colleges",
    "SBCTC": "State Board for Community & Technical Colleges",
    "WDVA": "Dept. of Veterans Affairs",
    "PW": "Dept. of Public Works",
    "COM": "Dept. of Commerce",
    "RCO": "Recreation & Conservation Office",
    "ESD": "Employment Security Dept.",
    "CTED": "Dept. of Commerce",
    "ISB": "Information Services Board",
    "WaTech": "WA Technology Solutions",
    "SGC": "State Gaming Commission",
    "WSGC": "WA State Gambling Commission",
    "WSIPP": "WA State Institute for Public Policy",
}


def parse_due_date(date_str):
    """
    Parse WEBS close dates. Tries 2-digit year first (most common on WEBS),
    then 4-digit year as fallback. Only catches ValueError — other exceptions
    propagate so they are not silently swallowed.
    """
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    print(f"  [WARN] Unrecognised date format: '{s}'")
    return None


def looks_like_name(s):
    """
    Returns True if a string looks like a real person's name.
    Defined at module level — not inside a loop.
    """
    if not s or len(s) > 40:
        return False
    if "Ref #" in s or "Additional" in s:
        return False
    if re.match(r"^\d", s):
        return False
    return len(s.strip().split()) >= 2


def build_detail_url(href):
    """
    Safely build an absolute URL from a relative href.
    Note: these URLs redirect to LoginPage.aspx when not authenticated —
    we store them for reference only, never scrape their content.
    """
    if not href:
        return None
    if href.startswith("http"):
        return href
    return WEBS_BASE + "/" + href.lstrip("/")


def extract_agency_from_ref(ref_number):
    """Try to match a known agency code from the ref number."""
    if not ref_number:
        return None
    ref_upper = ref_number.upper()
    for code in sorted(AGENCY_CODES.keys(), key=len, reverse=True):
        if code in ref_upper:
            return AGENCY_CODES[code]
    return None


def extract_agency_from_description(description):
    """Try to extract agency name from description text."""
    if not description:
        return None
    match = re.search(
        r"Washington\s+State\s+(Department\s+of\s+[\w\s&]+?)(?:\s*[,\.\(]|$)",
        description, re.IGNORECASE
    )
    if match:
        return "WA " + match.group(1).strip()
    match = re.search(
        r"\b(Department\s+of\s+[\w\s&]{3,40}?)(?:\s*[,\.\(\"']|hereafter|$)",
        description, re.IGNORECASE
    )
    if match:
        name = match.group(1).strip()
        if len(name) < 60:
            return name
    return None


def make_empty_record():
    """Return a record dict with all schema fields set to safe defaults.
    Fields prefixed with _ are internal and stripped before upsert."""
    return {
        "title": None,
        "detail_url": None,
        "ref_number": None,
        "contact_name": None,
        "contact_email": None,
        "due_date": None,
        "posted_date": None,
        "agency": None,
        "department": None,
        "source_name": SOURCE_NAME,
        "source_platform": "WEBS",
        "source_url": BASE_URL,
        "status": "active",
        "description": None,
        "rfp_type": None,
        "includes_inclusion_plan": False,
        "raw_data": None,
        "categories": [],
        # Internal — stripped before upsert
        "_description_lines": [],
        "_prebid_datetime": None,
        "_question_deadline": None,
        "_amendment_date": None,
    }


def parse_rfps_from_html(html, page_num=1):
    """
    Parse one page of the WEBS bid calendar.
    Per-record errors are caught and logged — one bad row does not
    abort the rest of the page.
    Optional fields (prebid, question deadline, amendment date) are
    parsed into raw_data. Missing optional fields do not crash parsing.
    """
    rfps = []
    soup = BeautifulSoup(html, "lxml")
    grid = soup.find("table", {"id": "DataGrid1"})
    if not grid:
        print(f"  [ERROR] Page {page_num}: DataGrid1 table not found in HTML")
        return rfps

    rows = grid.find_all("tr")
    current_rfp = None

    for row_idx, row in enumerate(rows):
        try:
            cells = row.find_all("td")
            if not cells:
                continue

            link = row.find("a", href=lambda x: x and "Search_BidDetails" in str(x))

            if link:
                # Guard: WEBS renders each bid as two consecutive <tr> rows
                # with the same Search_BidDetails href. If the href matches
                # the current record's detail_url it is a duplicate header row
                # — skip it so description lines keep accumulating correctly.
                this_url = build_detail_url(link.get("href", ""))
                if current_rfp and current_rfp.get("detail_url") == this_url:
                    continue

                # Save previous record before starting a new one
                if current_rfp and current_rfp.get("title"):
                    rfps.append(current_rfp)

                current_rfp = make_empty_record()

                current_rfp["title"] = clean_text(link.get_text()) or None
                current_rfp["detail_url"] = build_detail_url(link.get("href", ""))

                # Ref number is in a <b> tag labelled "Ref #"
                ref_span = row.find("b", string=re.compile(r"Ref #"))
                if ref_span:
                    raw_ref = ref_span.find_next_sibling(string=True)
                    if raw_ref:
                        current_rfp["ref_number"] = clean_text(str(raw_ref)) or None

                cell_texts = [clean_text(c.get_text()) for c in cells]

                # Due date: first cell but get_text() pulls nested content —
                # extract only the leading date portion with regex
                due_date_raw = None
                if cell_texts:
                    dm = re.match(r"(\d{1,2}/\d{1,2}/\d{2,4})", cell_texts[0].strip())
                    if dm:
                        due_date_raw = dm.group(1)
                current_rfp["due_date"] = parse_due_date(due_date_raw)

                # Contact name: WEBS embeds everything in one <td> so cell
                # positions are unreliable. Extract with regex: after the ref
                # number value, grab exactly FirstName LastName (two Title-cased
                # words), stopping before a digit or known noise token.
                contact = None
                row_text_full = clean_text(row.get_text())
                if current_rfp.get("ref_number"):
                    ref_escaped = re.escape(current_rfp["ref_number"])
                    m = re.search(
                        ref_escaped
                        + r"\s+([A-Z][a-z]+\s+[A-Z][a-z]+)"
                        + r"(?=\s+(?:\d|\b(?:Selective|The|This|To|WDFW|DOC|E&I|UW|WA|WSU)\b)|$)",
                        row_text_full
                    )
                    if m:
                        candidate = m.group(1).strip()
                        if looks_like_name(candidate):
                            contact = candidate
                # Fallback: try cell-based candidates
                if not contact:
                    for c in [
                        cell_texts[-2] if len(cell_texts) >= 3 else None,
                        cell_texts[-1] if len(cell_texts) >= 2 else None,
                    ]:
                        if looks_like_name(c):
                            contact = c
                            break
                current_rfp["contact_name"] = contact

                # Agency from ref number (description fallback applied post-parse)
                current_rfp["agency"] = extract_agency_from_ref(current_rfp["ref_number"])

            elif current_rfp is not None:
                row_text = clean_text(row.get_text())

                # Inclusion plan flag
                if "Includes an Inclusion Plan: Y" in str(row):
                    current_rfp["includes_inclusion_plan"] = True

                if not row_text:
                    continue

                # Pure noise rows — skip entirely
                if any(noise in row_text for noise in (
                    "Includes an Inclusion Plan",
                    "Additional Data",
                )):
                    continue

                # Pre-Bid Conference: parse datetime, do not add to description
                if row_text.startswith("Pre-Bid Conference:"):
                    raw = row_text[len("Pre-Bid Conference:"):].strip()
                    dm = re.match(r"(\d{1,2}/\d{1,2}/\d{2,4})", raw)
                    if dm and not current_rfp["_prebid_datetime"]:
                        current_rfp["_prebid_datetime"] = parse_due_date(dm.group(1))
                    continue

                # Deadline for Submitting Questions: parse date, do not add to description
                if row_text.startswith("Deadline for Submitting"):
                    dm = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", row_text)
                    if dm and not current_rfp["_question_deadline"]:
                        current_rfp["_question_deadline"] = parse_due_date(dm.group(1))
                    continue

                # Strip "Selective" prefix (WEBS procurement type token)
                if row_text.startswith("Selective"):
                    row_text = row_text[len("Selective"):].strip()

                # Strip leading date prefix from description lines.
                # Format: "MM/DD/YY  Description text..." — the date is the
                # amendment/posting date shown in the WEBS "Amendment Date" column.
                date_prefix_m = re.match(r"^(\d{1,2}/\d{1,2}/\d{2,4})\s+", row_text)
                if date_prefix_m:
                    if not current_rfp["_amendment_date"]:
                        current_rfp["_amendment_date"] = parse_due_date(
                            date_prefix_m.group(1)
                        )
                    row_text = row_text[date_prefix_m.end():].strip()

                if len(row_text) > 20:
                    current_rfp["_description_lines"].append(row_text)

        except Exception as e:
            print(f"  [WARN] Page {page_num}, row {row_idx}: skipped — {e}")
            continue

    # Flush the last record
    if current_rfp and current_rfp.get("title"):
        rfps.append(current_rfp)

    # Post-process: build description, pack extra fields into raw_data,
    # strip all internal _ fields before returning.
    for rfp in rfps:
        lines = rfp.pop("_description_lines", [])
        prebid = rfp.pop("_prebid_datetime", None)
        question_dl = rfp.pop("_question_deadline", None)
        amendment = rfp.pop("_amendment_date", None)

        # Build description from collected lines
        if lines:
            full_desc = " ".join(lines)
            if len(full_desc) > 600:
                sentences = full_desc.split(". ")
                full_desc = ". ".join(sentences[:4])
                if not full_desc.endswith("."):
                    full_desc += "."
            rfp["description"] = full_desc
        else:
            rfp["description"] = None

        # Pack optional parsed fields into raw_data (not DB columns)
        extra = {}
        if prebid:
            extra["prebid_datetime"] = prebid
        if question_dl:
            extra["question_deadline"] = question_dl
        if amendment:
            extra["amendment_date"] = amendment
        if extra:
            rfp["raw_data"] = json.dumps(extra)

        # Agency fallback: try description if ref lookup failed
        if not rfp.get("agency") and rfp.get("description"):
            rfp["agency"] = extract_agency_from_description(rfp["description"])

    return rfps


def deduplicate(rfps):
    seen = {}
    for rfp in rfps:
        fp = rfp.get("fingerprint")
        if not fp:
            continue
        if fp not in seen:
            seen[fp] = rfp
        else:
            # Keep whichever copy has more data — prefer non-null description
            existing = seen[fp]
            if not existing.get("description") and rfp.get("description"):
                seen[fp] = rfp
    return list(seen.values())


def get_next_page_control(html, next_page_num):
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
            print(f"[ERROR] Failed to load initial WEBS page: {e}")
            await browser.close()
            return all_rfps

        page_num = 1
        max_pages = 25

        while page_num <= max_pages:
            print(f"Scraping page {page_num}...")
            await asyncio.sleep(1)
            html = await page.content()
            rfps = parse_rfps_from_html(html, page_num=page_num)
            print(f"  Found {len(rfps)} RFPs on page {page_num}")

            if rfps:
                s = rfps[0]
                print(f"  Sample title:   {str(s.get('title', ''))[:60]}")
                print(f"  Sample ref:     {s.get('ref_number')}")
                print(f"  Sample due:     {s.get('due_date')}")
                print(f"  Sample agency:  {s.get('agency')}")
                print(f"  Sample contact: {s.get('contact_name')}")
                print(f"  Sample desc:    {str(s.get('description', ''))[:60]}")

            if not rfps:
                print(f"  No RFPs found on page {page_num} — stopping")
                break

            all_rfps.extend(rfps)

            control_id = get_next_page_control(html, page_num + 1)
            if not control_id:
                print(f"  No next page link found — done at page {page_num}")
                break

            print(f"  Navigating to page {page_num + 1}...")
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    await page.evaluate(f"__doPostBack('{control_id}', '')")
                await page.wait_for_selector("#DataGrid1", timeout=30000)
                await asyncio.sleep(1)
                page_num += 1
            except Exception as e:
                print(f"  [ERROR] Navigation to page {page_num + 1} failed: {e}")
                break

        await browser.close()

    return all_rfps


def run():
    print(f"Starting WEBS scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None

    try:
        all_rfps = asyncio.run(scrape_all_pages())
        print(f"Total RFPs scraped across all pages: {len(all_rfps)}")

        # Assign fingerprints — use ref_number first to avoid collisions
        for rfp in all_rfps:
            rfp["fingerprint"] = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("ref_number") or rfp.get("agency") or SOURCE_NAME,
                rfp.get("due_date", "")
            )

        all_rfps = deduplicate(all_rfps)
        print(f"Total after deduplication: {len(all_rfps)}")

        has_agency = sum(1 for r in all_rfps if r.get("agency"))
        has_contact = sum(1 for r in all_rfps if r.get("contact_name"))
        has_due_date = sum(1 for r in all_rfps if r.get("due_date"))
        has_desc = sum(1 for r in all_rfps if r.get("description"))
        print(f"  With agency:      {has_agency}/{len(all_rfps)}")
        print(f"  With contact:     {has_contact}/{len(all_rfps)}")
        print(f"  With due date:    {has_due_date}/{len(all_rfps)}")
        print(f"  With description: {has_desc}/{len(all_rfps)}")

        if all_rfps:
            batch_size = 50
            for i in range(0, len(all_rfps), batch_size):
                batch = all_rfps[i:i + batch_size]
                try:
                    supabase.table("rfps").upsert(batch, on_conflict="fingerprint").execute()
                    total_saved += len(batch)
                    print(f"  Saved batch {i // batch_size + 1}: {len(batch)} records")
                except Exception as batch_err:
                    print(f"  [ERROR] Batch {i // batch_size + 1} failed: {batch_err}")

        status = "success"
        print(f"Done — {total_saved} records saved to Supabase")

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
            error_message=error_msg
        )


if __name__ == "__main__":
    run()
