import hashlib
import os
from supabase import create_client, Client
from datetime import datetime

def get_supabase_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def generate_fingerprint(title, agency, due_date=None):
    raw = f"{title.lower().strip()}{agency.lower().strip()}{due_date or ''}"
    return hashlib.md5(raw.encode()).hexdigest()

def save_rfp(supabase, rfp):
    try:
        existing = supabase.table("rfps").select("id").eq("fingerprint", rfp["fingerprint"]).execute()
        if existing.data:
            supabase.table("rfps").update(rfp).eq("fingerprint", rfp["fingerprint"]).execute()
            return {"new": 0, "updated": 1}
        else:
            supabase.table("rfps").insert(rfp).execute()
            return {"new": 1, "updated": 0}
    except Exception as e:
        print(f"Error saving RFP: {e}")
        return {"new": 0, "updated": 0}

def log_scrape(supabase, source_name, status, rfps_found, rfps_new, rfps_updated, error_message=None):
    try:
        source = supabase.table("sources").select("id").eq("name", source_name).execute()
        source_id = source.data[0]["id"] if source.data else None
        log_data = {
            "source_i
