import hashlib
import os
from supabase import create_client, Client
from datetime import datetime

# Initialize Supabase client
def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def generate_fingerprint(title: str, agency: str, due_date: str = None) -> str:
    """
    Generate a unique fingerprint for deduplication.
    Two RFPs with the same title + agency + due_date are considered duplicates.
    """
    raw = f"{title.lower().strip()}{agency.lower().strip()}{due_date or ''}"
    return hashlib.md5(raw.encode()).hexdigest()

def save_rfp(supabase: Client, rfp: dict) -> dict:
    """
    Save an RFP to the database.
    If fingerprint already exists, update it instead of inserting.
    Returns dict with counts of new vs updated.
    """
    try:
        # Check if this RFP already exists
        existing = supabase.table("rfps")\
            .select("id")\
            .eq("fingerprint", rfp["fingerprint"])\
            .execute()

        if existing.data:
            # Update existing record
            supabase.table("rfps")\
                .update(rfp)\
                .eq("fingerprint", rfp["fingerprint"])\
                .execute()
            return {"new": 0, "updated": 1}
        else:
            # Insert new record
            supabase.table("rfps")\
                .insert(rfp)\
                .execute()
            return {"new": 1, "updated": 0}

    except Exception as e:
        print(f"Error saving RFP: {e}")
        return {"new": 0, "updated": 0}

def log_scrape(supabase: Client, source_name: str, status: str,
               rfps_found: int, rfps_new: int, rfps_updated: int,
               error_message: str = None):
    """Log the result of a scrape run to the database."""
    try:
        # Get source ID
        source = supabase.table("sources")\
            .select("id")\
            .eq("name", source_name)\
            .execute()

        source_id = source.data[0]["id"] if source.data else None

        # Insert log entry
        supabase.table("scrape_logs").insert({
            "source_id": source_id,
            "source_name": source_name,
            "finished_at": datetime.utcnow().isoformat(),
            "status": status,
            "rfps_found": rfps_found,
            "rfps_new": rfps_new,
            "rfps_updated": rfps_updated,
            "error_message": error_message
        }).execute()

        # Update last_scraped_at on the source
        if source_id:
            supabase.table("sources").update({
                "last_scraped_at": datetime.utcnow().isoformat(),
                "last_scrape_status": status,
                "last_scrape_count": rfps_found
            }).eq("id", source_id).execute()

    except Exception as e:
        print(f"Error logging scrape: {e}")

def clean_text(text: str) -> str:
    """Clean up whitespace and special characters from scraped text."""
    if not text:
        return None
    return " ".join(text.split()).strip()
```

Commit that file the same way.

---

**Third file — click "Add file" → "Create new file"**

Type:
```
scrapers/scrape_webs.py
