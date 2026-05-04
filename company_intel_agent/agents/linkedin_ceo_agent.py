import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.verifier import CEOVerifier
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CEOLinkedInAgent")

_TITLE_PATTERNS = [
    re.compile(r'(CEO|Chief Executive Officer|Co-Founder(?: & CEO)?|Founder(?: & CEO)?)', re.IGNORECASE),
    re.compile(r'([A-Za-z\s]+) at \w', re.IGNORECASE),
]

_LINKEDIN_PROFILE_PATTERN = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)')


class LinkedInCEOAgent:
    def __init__(self):
        self._search = ParallelSearchClient()
        self._verifier = CEOVerifier()

    async def find(self, ceo_name: str, company_name: str) -> CEOData:
        logger.info(f"Searching LinkedIn profile for CEO: '{ceo_name}' at '{company_name}'")

        # Step 1: initial profile search
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

        logger.info(f"Initial result — linkedin={linkedin_url}, title={title}")

        # Step 2: verification pass — corroborates name and validates URL slug
        verified = await self._verifier.verify(ceo_name, company_name, linkedin_url)

        for reason in verified.reasons:
            logger.info(f"[Verifier] {reason}")
        logger.info(
            f"[Verifier] final — name='{verified.name}' "
            f"linkedin={verified.linkedin} confidence={verified.confidence}"
        )

        return CEOData(
            name=verified.name,
            linkedin=verified.linkedin,
            title=title,
            summary=summary,
            confidence=verified.confidence,
        )

    # ------------------------------------------------------------------ helpers

    def _extract_linkedin_profile_url(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            linkedin_url = self._normalize_linkedin_profile_url(r.url)
            if linkedin_url:
                return linkedin_url
            for match in _LINKEDIN_PROFILE_PATTERN.finditer(r.excerpt):
                return f"https://www.linkedin.com/in/{match.group(1).strip('/')}/"
        return None

    def _normalize_linkedin_profile_url(self, url: str) -> Optional[str]:
        if 'linkedin.com/in/' not in url:
            return None
        parsed = urlparse(url)
        parts = parsed.path.strip('/').split('/')
        if len(parts) >= 2 and parts[0] == 'in':
            return f"https://www.linkedin.com/in/{parts[1]}/"
        return None

    def _extract_title(self, results: list[SearchResult], ceo_name: str) -> Optional[str]:
        for r in results:
            text = f"{r.title} {r.excerpt}"
            for pattern in _TITLE_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(1).strip()
        return "CEO"

    def _extract_summary(self, results: list[SearchResult], ceo_name: str) -> Optional[str]:
        first_name = ceo_name.split()[0]
        for r in results:
            excerpt = r.excerpt.strip()
            if len(excerpt) > 80 and first_name.lower() in excerpt.lower():
                if len(excerpt) > 300:
                    cut = excerpt[:300].rfind('.')
                    excerpt = excerpt[:cut + 1] if cut > 100 else excerpt[:300]
                return excerpt
        for r in results:
            if len(r.excerpt.strip()) > 60:
                return r.excerpt.strip()[:300]
        return None
