import requests
from bs4 import BeautifulSoup
from datetime import datetime
from utils import get_supabase_client, generate_fingerprint, save_rfp, log_scrape, clean_text
import re

BASE_URL = "https://pr-webs-vendor.des.wa.gov/BidCalendar.aspx"
SOURCE_NAME = "WEBS - Washington Electronic Business Solution"

def parse_due_date(date_str: str):
    """Parse WEBS date format MM/DD/YY into ISO format."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%y").isoformat()
    except:
        return None

def scrape_webs_page(session, page_num: int = 1) -> list:
    """Scrape a single page of WEBS bid calendar."""
    rfps = []

    try:
        response = session.get(BASE_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)

        soup = BeautifulSoup(response.text, "lxml")

        # Find the main data grid
        grid = soup.find("table", {"id": "DataGrid1"})
        if not grid:
            print("Could not find DataGrid1 on page")
            return rfps

        rows = grid.find_all("tr")

        current_rfp = {}

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            # Look for rows that contain RFP links
            link = row.find("a", href=lambda x: x and "Search_BidDetails" in str(x))
            if link:
                # This is a title row — start a new RFP
                if current_rfp.get("title"):
                    rfps.append(current_rfp)

                title = clean_text(link.get_text())
                detail_url = "https://pr-webs-vendor.des.wa.gov/" + link["href"]

                # Get the ref number if present
                ref_span = row.find("b", string=re.compile(r"Ref #"))
                ref_number = None
                if ref_span:
                    ref_number = clean_text(ref_span.find_next_sibling(string=True))

                # Get contact name (last cell in the row usually)
                contact = None
                cell_texts = [clean_text(c.get_text()) for c in cells]
                if len(cell_texts) >= 3:
                    contact = cell_texts[-1]

                # Get close date (first cell)
                close_date = None
                if cell_texts:
                    close_date = parse_due_date(cell_texts[0])

                current_rfp = {
                    "title": title,
                    "detail_url": detail_url,
                    "ref_number": ref_number,
                    "contact_name": contact,
                    "due_date": close_date,
                    "source_name": SOURCE_NAME,
                    "source_platform": "WEBS",
                    "status": "active",
                    "agency": None,
                    "description": None,
                    "includes_inclusion_plan": False
                }

            # Look for description rows
            elif current_rfp and len(cells) == 1:
                text = clean_text(cells[0].get_text())
                if text and not text.startswith("Additional") and not text.startswith("Includes"):
                    if not current_rfp.get("description"):
                        current_rfp["description"] = text

                # Check for inclusion plan
                if "Includes an Inclusion Plan: Y" in str(row):
                    current_rfp["includes_inclusion_plan"] = True

                # Check for agency info
                if "Pre-Bid Conference" in str(row) or "Deadline" in str(row):
                    pass  # These are metadata rows, skip

        # Don't forget the last RFP
        if current_rfp.get("title"):
            rfps.append(current_rfp)

    except Exception as e:
        print(f"Error scraping WEBS page {page_num}: {e}")

    return rfps

def get_rfp_details(session, rfp: dict) -> dict:
    """Fetch the detail page for an RFP to get agency and more info."""
    try:
        response = session.get(rfp["detail_url"], headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)

        soup = BeautifulSoup(response.text, "lxml")

        # Try to find the agency/organization name
        org_label = soup.find(string=re.compile(r"Organization", re.I))
        if org_label:
            parent = org_label.find_parent()
            if parent:
                next_el = parent.find_next_sibling()
                if next_el:
                    rfp["agency"] = clean_text(next_el.get_text())

        # Try to get posted date
        posted_label = soup.find(string=re.compile(r"Posted|Published", re.I))
        if posted_label:
            parent = posted_label.find_parent()
            if parent:
                next_el = parent.find_next_sibling()
                if next_el:
                    rfp["posted_date"] = parse_due_date(clean_text(next_el.get_text()))

    except Exception as e:
        print(f"Error fetching detail for {rfp.get('title')}: {e}")

    return rfp

def run():
    """Main scraper function."""
    print(f"Starting WEBS scraper at {datetime.now()}")
    supabase = get_supabase_client()

    session = requests.Session()
    all_rfps = []
    total_new = 0
    total_updated = 0
    error_msg = None

    try:
        # WEBS has multiple pages — scrape all of them
        # We'll scrape pages until we get an empty page
        for page in range(1, 20):  # Max 20 pages safety limit
            print(f"Scraping page {page}...")
            rfps = scrape_webs_page(session, page)

            if not rfps:
                print(f"No RFPs found on page {page}, stopping")
                break

            all_rfps.extend(rfps)
            print(f"Found {len(rfps)} RFPs on page {page}")

            # If fewer than 20 results, probably the last page
            if len(rfps) < 20:
                break

        print(f"Total RFPs scraped: {len(all_rfps)}")

        # Save each RFP to database
        for rfp in all_rfps:
            # Generate fingerprint for deduplication
            rfp["fingerprint"] = generate_fingerprint(
                rfp.get("title", ""),
                rfp.get("agency", rfp.get("source_name", "")),
                rfp.get("due_date", "")
            )

            # Set source URL
            rfp["source_url"] = BASE_URL

            result = save_rfp(supabase, rfp)
            total_new += result["new"]
            total_updated += result["updated"]

        status = "success"
        print(f"Done! {total_new} new, {total_updated} updated")

    except Exception as e:
        error_msg = str(e)
        status = "failed"
        print(f"Scraper failed: {e}")

    finally:
        log_scrape(
            supabase=supabase,
            source_name=SOURCE_NAME,
            status=status,
            rfps_found=len(all_rfps),
            rfps_new=total_new,
            rfps_updated=total_updated,
            error_message=error_msg
        )

if __name__ == "__main__":
    run()
```

Commit that file too.

---

**Fourth and final file for now — click "Add file" → "Create new file"**

Type:
```
.github/workflows/scrape.yml
