import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CEOLinkedInAgent")

_TITLE_PATTERNS = [
    re.compile(r'(CEO|Chief Executive Officer|Co-Founder(?: & CEO)?|Founder(?: & CEO)?)', re.IGNORECASE),
    re.compile(r'([A-Za-z\s]+) at \w', re.IGNORECASE),
]


class LinkedInCEOAgent:
    def __init__(self):
        self._search = ParallelSearchClient()

    async def find(self, ceo_name: str, company_name: str) -> CEOData:
        logger.info(f"Searching LinkedIn profile for CEO: '{ceo_name}' at '{company_name}'")

        results = await asyncio.to_thread(
            self._search.search,
            objective=f"Find the LinkedIn profile URL for {ceo_name}, CEO of {company_name}",
            queries=[
                f"{ceo_name} {company_name} site:linkedin.com/in",
                f"{ceo_name} CEO {company_name} LinkedIn profile",
            ],
        )

        linkedin_url = self._extract_linkedin_profile_url(results)
        title = self._extract_title(results, ceo_name)
        summary = self._extract_summary(results, ceo_name)

        logger.info(f"CEO result — linkedin={linkedin_url}, title={title}")
        return CEOData(
            name=ceo_name,
            linkedin=linkedin_url,
            title=title,
            summary=summary,
        )

    # ------------------------------------------------------------------ helpers

    def _extract_linkedin_profile_url(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            if 'linkedin.com/in/' in r.url:
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'in':
                    return f"https://www.linkedin.com/in/{parts[1]}/"
        return None

    def _extract_title(self, results: list[SearchResult], ceo_name: str) -> Optional[str]:
        for r in results:
            text = f"{r.title} {r.excerpt}"
            # First look for explicit title patterns near the CEO name
            for pattern in _TITLE_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(1).strip()
        return "CEO"  # Default to CEO since that's what we searched for

    def _extract_summary(self, results: list[SearchResult], ceo_name: str) -> Optional[str]:
        first_name = ceo_name.split()[0]
        for r in results:
            excerpt = r.excerpt.strip()
            if len(excerpt) > 80 and first_name.lower() in excerpt.lower():
                # Trim to ~300 chars at a sentence boundary
                if len(excerpt) > 300:
                    cut = excerpt[:300].rfind('.')
                    excerpt = excerpt[:cut + 1] if cut > 100 else excerpt[:300]
                return excerpt
        # Fallback: use first excerpt with enough content
        for r in results:
            if len(r.excerpt.strip()) > 60:
                return r.excerpt.strip()[:300]
        return None
