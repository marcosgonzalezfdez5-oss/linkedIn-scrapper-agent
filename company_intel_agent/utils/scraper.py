from typing import Optional

import requests
from bs4 import BeautifulSoup
from parallel import Parallel

from company_intel_agent.config.settings import PARALLEL_API_KEY
from company_intel_agent.utils.logger import get_logger

logger = get_logger("scraper")


def extract_page_text(url: str, objective: str = "Extract company information") -> Optional[str]:
    """Extract readable text from a URL. Tries Parallel Extract first, falls back to BeautifulSoup."""
    try:
        client = Parallel(api_key=PARALLEL_API_KEY)
        result = client.extract(url=url, objective=objective)
        text = getattr(result, 'markdown', None) or getattr(result, 'text', None)
        if text:
            logger.info(f"Parallel extract succeeded for {url}")
            return text
    except Exception as e:
        logger.warning(f"Parallel extract failed for {url}: {e}")

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CompanyIntelAgent/1.0)"},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        logger.info(f"BeautifulSoup fallback succeeded for {url}")
        return text[:6000]
    except Exception as e:
        logger.error(f"All extraction methods failed for {url}: {e}")
        return None
