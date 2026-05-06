import os
import re

_KEY_ENV = os.path.join(os.path.dirname(__file__), '..', '..', 'key.env')

def _read_key_env() -> str:
    try:
        with open(_KEY_ENV) as f:
            return f.read()
    except FileNotFoundError:
        return ""

_KEY_ENV_CONTENT = _read_key_env()

def _get(var: str) -> str:
    val = os.environ.get(var)
    if val:
        return val
    match = re.search(rf'^{var}=(.+)', _KEY_ENV_CONTENT, re.MULTILINE)
    return match.group(1).strip() if match else ""


_parallel = _get('PARALLEL_API_KEY')
if not _parallel:
    raise ValueError("PARALLEL_API_KEY not found. Set the env var or add it to key.env")

PARALLEL_API_KEY: str = _parallel
NEWS_MAX_ITEMS = 5
SEARCH_MAX_RESULTS = 8

SUPABASE_URL: str = _get('SUPABASE_URL')
SUPABASE_KEY: str = _get('SUPABASE_KEY')

# Optional. When absent or invalid, Apify-backed agents fall back to the
# existing Parallel search agents.
APIFY_TOKEN: str = _get('APIFY_TOKEN')
APIFY_COMPANY_ACTOR_ID: str = _get('APIFY_COMPANY_ACTOR_ID') 
APIFY_PROFILE_ACTOR_ID: str = _get('APIFY_PROFILE_ACTOR_ID') 
APIFY_COMPANY_INPUT_FIELD: str = _get('APIFY_COMPANY_INPUT_FIELD') or 'companyUrls'
APIFY_PROFILE_INPUT_FIELD: str = _get('APIFY_PROFILE_INPUT_FIELD') or 'profileUrls'
