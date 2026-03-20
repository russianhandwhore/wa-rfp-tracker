import hashlib
import os
from supabase import create_client, Client
from datetime import datetime

def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def generate_fingerprint(title: str, agency: str, due_date: str = None) -> str:
    raw = f"{title.lower().strip()}{agency.lower().strip()}{due_date or ''}"
    return hashlib.md5(raw.encode()).hexdigest()

def save_rfp(supabase: Client, rfp: dict) -> dict:
    try:
        existing = supabase.table("rfps")\
            .select("id")\
            .eq("fingerprint", rfp["fingerprint"])\
            .execute()

        if existing.data:
            supabase.table("rfps")\
                .update(rfp)\
                .eq("fingerprint", rfp["fingerprint"])\
                .execute()
            return {"new": 0, "updated": 1}
        else:
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
    try:
        source = supabase.table("sources")\
            .select("id")\
            .eq("name", source_name)\
            .execute()

        source_id = source.data[0]["id"] if source.data else None

        supabase.table("scrape_logs").insert({
            "source_id": source_id,
            "source_name": source_name,
