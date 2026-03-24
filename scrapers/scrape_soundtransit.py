"""
Sound Transit Procurement Snapshot Scraper
Source: https://www.soundtransit.org/sites/default/files/documents/snapshot-current.pdf

Published bi-weekly. Three sections:
  - Materials, Technology and Services (MTS)
  - Construction
  - Architecture and Engineering (AE)

Phases → status:
  Advertising    → active   (currently accepting submissions)
  Evaluating     → active   (submitted, under evaluation)
  In Development → upcoming (future — not yet advertised)
"""

import requests
import json
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_supabase_client, generate_fingerprint, log_scrape, clean_text

try:
    import pdfplumber
except ImportError:
    os.system("pip install pdfplumber --break-system-packages -q")
    import pdfplumber

PDF_URL = "https://www.soundtransit.org/sites/default/files/documents/snapshot-current.pdf"
VENDOR_PORTAL = "https://www.biddingo.com/soundtransit"
SOURCE_PLATFORM = "Sound Transit"
SOURCE_NAME = "Sound Transit - Procurement Snapshot"

# Contact by section
CONTACT_BY_SECTION = {
    "mts": "MTSprocurementhelp@soundtransit.org",
    "construction": "DCCprocurementhelp@soundtransit.org",
    "ae": "DCCprocurementhelp@soundtransit.org",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Phase → status + label
PHASE_MAP = {
    "advertising":    {"status": "active",   "label": "Advertising"},
    "evaluating":     {"status": "active",   "label": "Evaluating"},
    "in development": {"status": "upcoming", "label": "Upcoming"},
}


def download_pdf():
    """Download the snapshot PDF and return bytes."""
    resp = requests.get(PDF_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content


def parse_date(val):
    """Parse MM/DD/YY or MM/DD/YYYY date strings."""
    if not val or val.strip().upper() in ("TBD", "", "-"):
        return None
    val = val.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val, fmt).isoformat()
        except ValueError:
            continue
    return None


def detect_section(text):
    """Return section key from a line of text."""
    t = text.lower().strip()
    if "architecture and engineering" in t:
        return "ae"
    if "construction" in t:
        return "construction"
    if "materials, technology" in t or "materials technology" in t:
        return "mts"
    return None


def is_header_row(cells):
    """Skip table header rows."""
    joined = " ".join(str(c or "").lower() for c in cells)
    return any(k in joined for k in [
        "procurement title", "procurement id", "phase", "solicitation"
    ])


def extract_rows_from_pdf(pdf_bytes):
    import io
    rows = []
    current_section = "mts"

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            for line in page_text.split("\n"):
                sec = detect_section(line)
                if sec:
                    current_section = sec

            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 2:
                        continue
                    cells = [str(c or "").strip() for c in row]

                    sec = detect_section(cells[0])
                    if sec:
                        current_section = sec
                        continue

                    if is_header_row(cells):
                        continue

                    title = cells[0] if cells else ""
                    if not title or len(title) < 4:
                        continue
                    if any(k in title.lower() for k in [
                        "this report", "for questions", "noa -", "noia -",
                        "refresh date", "snapshot", "future dates"
                    ]):
                        continue

                    while len(cells) < 8:
                        cells.append("")

                    rows.append({
                        "title":             clean_text(cells[0]),
                        "proc_id":           clean_text(cells[1]),
                        "process":           clean_text(cells[2]),
                        "phase":             clean_text(cells[3]).lower(),
                        "solicitation_date": parse_date(cells[4]),
                        "prebid_date":       parse_date(cells[5]),
                        "submittal_due":     parse_date(cells[6]),
                        "noia_noa":          parse_date(cells[7]),
                        "section":           current_section,
                    })

    return rows
def build_records(rows):
    """Convert extracted rows to RFP records."""
    rfps = []
    seen = set()

    for row in rows:
        title = row["title"]
        proc_id = row["proc_id"]

        if not title or not proc_id:
            continue

        if proc_id in seen:
            continue
        seen.add(proc_id)

        phase_key = row["phase"].strip().lower()
        phase_info = None
        for k, v in PHASE_MAP.items():
            if k in phase_key:
                phase_info = v
                break
        if not phase_info:
                phase_info = {"status": "active", "label": phase_key.title()}

        contact_email = CONTACT_BY_SECTION.get(row["section"], "MTSprocurementhelp@soundtransit.org")

        due_date = row["submittal_due"] or row["solicitation_date"]

        fingerprint = generate_fingerprint(proc_id, SOURCE_PLATFORM, "")

        rfp = {
            "title":           title,
            "ref_number":      proc_id,
            "due_date":        due_date,
            "status":          phase_info["status"],
            "source_platform": SOURCE_PLATFORM,
            "source_name":     SOURCE_NAME,
            "source_url":      PDF_URL,
            "detail_url":      VENDOR_PORTAL,
            "agency":          "Sound Transit",
            "department":      None,
            "description":     None,
            "contact_name":    None,
            "contact_email":   contact_email,
            "posted_date":     row["solicitation_date"],
            "rfp_type":        row["process"] or None,
            "includes_inclusion_plan": False,
            "categories":      [],
            "fingerprint":     fingerprint,
            "raw_data":        json.dumps({
                "phase":             phase_info["label"],
                "phase_label":       phase_info["label"],
                "section":           row["section"],
                "process":           row["process"],
                "solicitation_date": row["solicitation_date"],
                "prebid_date":       row["prebid_date"],
                "submittal_due":     row["submittal_due"],
                "noia_noa":          row["noia_noa"],
                "pdf_url":           PDF_URL,
            }),
        }
        rfps.append(rfp)

    return rfps


def run():
    print(f"Starting Sound Transit scraper at {datetime.now()}")
    supabase = get_supabase_client()
    all_rfps = []
    total_saved = 0
    error_msg = None
    status = "failed"

    try:
        print(f"  Downloading PDF: {PDF_URL}")
        pdf_bytes = download_pdf()
        print(f"  Downloaded {len(pdf_bytes):,} bytes")

        rows = extract_rows_from_pdf(pdf_bytes)
        print(f"  Extracted {len(rows)} rows from PDF")

        all_rfps = build_records(rows)

        by_phase = {}
        for r in all_rfps:
            phase = json.loads(r["raw_data"])["phase_label"]
            by_phase[phase] = by_phase.get(phase, 0) + 1
        for phase, count in sorted(by_phase.items()):
            print(f"    {phase}: {count}")

        if all_rfps:
            s = all_rfps[0]
            raw = json.loads(s["raw_data"])
            print(f"  Sample: {s['title'][:55]} | phase={raw['phase_label']} | due={s['due_date']}")

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
