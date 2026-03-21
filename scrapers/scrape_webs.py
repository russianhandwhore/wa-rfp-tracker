import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text
import re
from bs4 import BeautifulSoup

BASE_URL = "https://pr-webs-vendor.des.wa.gov/BidCalendar.aspx"
SOURCE_NAME = "WEBS - Washington Electronic Business Solution"

# WA state agency codes found in ref numbers -> full agency names
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
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%y").isoformat()
    except:
        return None


def extract_agency_from_ref(ref_number):
    """Try to match a known agency code from the ref number."""
    if not ref_number:
        return None
    ref_upper = ref_number.upper()
    # Try longest match first
    for code in sorted(AGENCY_CODES.keys(), key=len, reverse=True):
        if code in ref_upper:
            return AGENCY_CODES[code]
    return None


def extract_agency_from_description(description):
    """Try to extract agency name from description text."""
    if not description:
        return None
    # Pattern: "Washington State Department of ..."
    match = re.search(
        r'Washington\s+State\s+(Department\s+of\s+[\w\s&]+?)(?:\s*[,\.\(]|$)',
        description, re.IGNORECASE
    )
    if match:
        return "WA " + match.group(1).strip()
    # Pattern: "Department of ..."
    match = re.search(
        r'\b(Department\s+of\s+[\w\s&]{3,40}?)(?:\s*[,\.\("]|hereafter|$)',
        description, re.IGNORECASE
    )
    if match:
        name = match.group(1).strip()
        if len(name) < 60:
            return name
    return None


def parse_rfps_from_html(html):
    rfps = []
    soup = BeautifulSoup(html, "lxml")
    grid = soup.find("table", {"id": "DataGrid1"})
    if not grid:
        print("DataGrid1 not found in HTML")
        return rfps

    rows = grid.find_all("tr")
    current_rfp = None

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue

        link = row.find("a", href=lambda x: x and "Search_BidDetails" in str(x))
        if link:
            if current_rfp and current_rfp.get("title"):
                rfps.append(current_rfp)

            title = clean_text(link.get_text())
            detail_url = "https://pr-webs-vendor.des.wa.gov/" + link["href"]

            ref_span = row.find("b", string=re.compile(r"Ref #"))
            ref_number = None
            if ref_span:
                ref_number = clean_text(ref_span.find_next_sibling(string=True))

            cell_texts = [clean_text(c.get_text()) for c in cells]
            close_date = parse_due_date(cell_texts[0]) if cell_texts else None

            # WEBS has 4 cols: Close Date | Title+Ref | Contact | Additional Data
            # Contact is second-to-last, "Additional Data" is last
            contact = None
            if len(cell_texts) >= 3:
                candidate = cell_texts[-2]
                if candidate and "Additional" not in candidate and len(candidate) > 2:
                    contact = candidate
            if not contact and len(cell_texts) >= 2:
                candidate = cell_texts[-1]
                if candidate and "Additional" not in candidate and len(candidate) > 2:
                    contact = candidate

            current_rfp = {
                "title": title,
                "detail_url": detail_url,
                "ref_number": ref_number,
                "contact_name": contact,
                "due_date": close_date,
                "agency": extract_agency_from_ref(ref_number),
                "source_name": SOURCE_NAME,
                "source_platform": "WEBS",
                "source_url": BASE_URL,
                "status": "active",
                "description": None,
                "rfp_type": None,
                "includes_inclusion_plan": False,
                "description_lines": []
            }

        elif current_rfp is not None:
            row_text = clean_text(row.get_text())

            if "Includes an Inclusion Plan: Y" in str(row):
                current_rfp["includes_inclusion_plan"] = True

            if not row_text:
                continue
            if "Includes an Inclusion Plan" in row_text:
                continue
            if "Additional Data" in row_text:
                continue
            if "Pre-Bid Conference" in row_text:
                continue
            if "Deadline for Submitting" in row_text:
                continue
            if row_text.startswith("Selective"):
                row_text = row_text[len("Selective"):].strip()

            if len(row_text) > 20:
                current_rfp["description_lines"].append(row_text)

    if current_rfp and current_rfp.get("title"):
        rfps.append(current_rfp)

    for rfp in rfps:
        lines = rfp.pop("description_lines", [])
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

        # If agency not found from ref, try description
        if not rfp.get("agency") and rfp.get("description"):
            rfp["agency"] = extract_agency_from_description(rfp["description"])

    return rfps


def deduplicate(rfps):
    seen = {}
    for rfp in rfps:
        fp = rfp.get("fingerprint")
        if fp and fp not in seen:
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
            print("Error loading initial page: " + str(e))
            await browser.close()
            return all_rfps

        page_num = 1
        max_pages = 25

        while page_num <= max_pages:
            print("Scraping page " + str(page_num) + "...")
            await asyncio.sleep(1)
            html = await page.content()
            rfps = parse_rfps_from_html(html)
            print("Found " + str(len(rfps)) + " RFPs on page " + str(page_num))

            if rfps:
                sample = rfps[0]
                print("  title:   " + str(sample.get("title", ""))[:60])
                print("  ref:     " + str(sample.get("ref_number")))
                print("  agency:  " + str(sample.get("agency")))
                print("  contact: " + str(sample.get("contact_name")))

            if not rfps:
                break

            all_rfps.extend(rfps)

            control_id = get_next_page_control(html, page_num + 1)
            if not control_id:
                print("No next page, done at page " + str(page_num))
                break

            print("Going to page " + str(page_num + 1) + "...")
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    await page.evaluate("__doPostBack('" + control_id + "', '')")
                await page.wait_for_selector("#DataGrid1", timeout=30000)
                await asyncio.sleep(1)
                page_num += 1
            except Exception as e:
                print("Navigation error: " + str(e))
                break

        await browser.close()

    return all_rfps


def run():
    print("Starting WEBS scraper at " + str(datetime.now()))
    supabase = get_supabase_client()
    all_rfps = []
    total_new = 0
    error_msg = None

    try:
        all_rfps = asyncio.run(scrape_all_pages())
        print("Total RFPs scraped: " + str(len(all_rfps)))

        for rfp in all_rfps:
            rfp["fingerprint"] = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", rfp.get("source_name", "")),
                rfp.get("due_date", "")
            )

        all_rfps = deduplicate(all_rfps)
        print("Total after dedup: " + str(len(all_rfps)))

        has_agency = sum(1 for r in all_rfps if r.get("agency"))
        print("RFPs with agency: " + str(has_agency) + " / " + str(len(all_rfps)))

        if all_rfps:
            batch_size = 50
            for i in range(0, len(all_rfps), batch_size):
                batch = all_rfps[i:i + batch_size]
                supabase.table("rfps").upsert(batch, on_conflict="fingerprint").execute()
                total_new += len(batch)
                print("Saved batch of " + str(len(batch)) + " RFPs")

        status = "success"
        print("Done! " + str(total_new) + " RFPs saved")

    except Exception as e:
        error_msg = str(e)
        status = "failed"
        print("Scraper failed: " + str(e))

    finally:
        log_scrape(
            supabase=supabase,
            source_name=SOURCE_NAME,
            status=status,
            rfps_found=len(all_rfps),
            rfps_new=total_new,
            rfps_updated=0,
            error_message=error_msg
        )


if __name__ == "__main__":
    run()
