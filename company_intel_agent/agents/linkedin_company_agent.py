import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.models.schemas import CompanyData
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CompanyLinkedInAgent")

# Patterns for extracting CEO name from search snippets
_CEO_PATTERNS = [
    re.compile(r'CEO[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'Chief Executive Officer[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'founded by ([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'([A-Z][a-z]+(?: [A-Z][a-z]+)+),? (?:CEO|Chief Executive)', re.IGNORECASE),
]

# Patterns for company size
_SIZE_PATTERNS = [
    re.compile(r'(\d[\d,]+\+?\s*employees)', re.IGNORECASE),
    re.compile(r'(\d+[-–]\d+\s*employees)', re.IGNORECASE),
    re.compile(r'company size[:\s]+([\w\s,+-]+employees)', re.IGNORECASE),
]

_NOISE_DOMAINS = {'linkedin.com', 'facebook.com', 'twitter.com', 'x.com',
                  'instagram.com', 'youtube.com', 'wikipedia.org', 'google.com'}


class LinkedInCompanyAgent:
    def __init__(self):
        self._search = ParallelSearchClient()
        self._ceo_name: Optional[str] = None  # set after find(), read by orchestrator

    async def find(self, company_name: str) -> CompanyData:
        logger.info(f"Searching company intelligence for: '{company_name}'")

        linkedin_res, meta_res, size_res = await asyncio.gather(
            asyncio.to_thread(
                self._search.search,
                objective=f"Find the official LinkedIn company page URL for {company_name}",
                queries=[
                    f"{company_name} site:linkedin.com/company",
                    f'"{company_name}" linkedin company page',
                ],
            ),
            asyncio.to_thread(
                self._search.search,
                objective=f"CEO name, official website, and description of {company_name}",
                queries=[
                    f"{company_name} CEO founder official website",
                    f"{company_name} about company overview",
                ],
            ),
            asyncio.to_thread(
                self._search.search,
                objective=f"Number of employees and company size for {company_name}",
                queries=[
                    f"{company_name} number of employees headcount",
                    f"{company_name} company size",
                ],
            ),
        )

        all_results = linkedin_res + meta_res + size_res

        linkedin_url = self._extract_linkedin_company_url(linkedin_res + meta_res)
        ceo_name = self._extract_ceo_name(all_results)
        website = self._extract_website(meta_res, company_name)
        description = self._extract_description(meta_res + linkedin_res, company_name)
        size = self._extract_size(size_res + meta_res)

        self._ceo_name = ceo_name

        logger.info(
            f"Company result — linkedin={linkedin_url}, website={website}, "
            f"ceo={ceo_name}, size={size}"
        )
        return CompanyData(
            name=company_name,
            linkedin=linkedin_url,
            website=website,
            description=description,
            size=size,
        )

    # ------------------------------------------------------------------ helpers

    def _extract_linkedin_company_url(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            if 'linkedin.com/company' in r.url:
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                # Normalize to base company URL (drop trailing sub-paths)
                if len(parts) >= 2 and parts[0] == 'company':
                    return f"https://www.linkedin.com/company/{parts[1]}/"
        return None

    def _extract_ceo_name(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            text = f"{r.title} {r.excerpt}"
            for pattern in _CEO_PATTERNS:
                match = pattern.search(text)
                if match:
                    name = match.group(1).strip()
                    # Basic sanity: must look like a real name (2 words, not all caps)
                    words = name.split()
                    if 2 <= len(words) <= 4 and not name.isupper():
                        return name
        return None

    def _extract_website(self, results: list[SearchResult], company_name: str) -> Optional[str]:
        company_slug = company_name.lower().replace(' ', '')
        for r in results:
            parsed = urlparse(r.url)
            domain = parsed.netloc.lower().replace('www.', '')
            if any(d in domain for d in _NOISE_DOMAINS):
                continue
            # Prefer domains that contain part of the company name
            if company_slug[:5] in domain.replace('-', '').replace('.', ''):
                return f"{parsed.scheme}://{parsed.netloc}/"
        # Fall back to first non-noisy result
        for r in results:
            parsed = urlparse(r.url)
            domain = parsed.netloc.lower().replace('www.', '')
            if not any(d in domain for d in _NOISE_DOMAINS):
                return f"{parsed.scheme}://{parsed.netloc}/"
        return None

    def _extract_description(self, results: list[SearchResult], company_name: str) -> Optional[str]:
        for r in results:
            excerpt = r.excerpt.strip()
            if len(excerpt) > 60 and company_name.lower().split()[0] in excerpt.lower():
                # Return first two sentences
                sentences = re.split(r'(?<=[.!?])\s+', excerpt)
                return ' '.join(sentences[:2])
        return None

    def _extract_size(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            text = f"{r.title} {r.excerpt}"
            for pattern in _SIZE_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(1).strip()
        return None
