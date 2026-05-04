import os
import re


def _load_parallel_key() -> str:
    key_env = os.path.join(os.path.dirname(__file__), '..', '..', 'key.env')
    try:
        with open(key_env) as f:
            match = re.search(r'PARALLEL_API_KEY=(.+)', f.read())
            if match:
                return match.group(1).strip()
    except FileNotFoundError:
        pass
    raise ValueError("PARALLEL_API_KEY not found. Set the env var or add it to key.env")


PARALLEL_API_KEY: str = os.environ.get('PARALLEL_API_KEY') or _load_parallel_key()

NEWS_MAX_ITEMS = 5
SEARCH_MAX_RESULTS = 8
