from supabase import create_client, Client
from company_intel_agent.config.settings import SUPABASE_URL, SUPABASE_KEY


def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY required in env or key.env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)
