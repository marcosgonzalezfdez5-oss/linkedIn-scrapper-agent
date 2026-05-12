import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.models.schemas import CompanyData
from company_intel_agent.utils.llm_client import LLMClient
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.agents.company_verifier_agent import CompanyVerifierAgent
from company_intel_agent.utils.names import extract_ceo_name_from_results
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CompanyLinkedInAgent")

_SIZE_PATTERNS = [
    re.compile(r'(\d[\d,]+\+?\s*employees)', re.IGNORECASE),
    re.compile(r'(\d+[-–]\d+\s*employees)', re.IGNORECASE),
    re.compile(r'company size[:\s]+([\w\s,+-]+employees)', re.IGNORECASE),
]

_NOISE_DOMAINS = {'linkedin.com', 'facebook.com', 'twitter.com', 'x.com',
                  'instagram.com', 'youtube.com', 'wikipedia.org', 'google.com'}


class LinkedInCompanyAgent:
    def __init__(self, llm: LLMClient):
        self._search = ParallelSearchClient()
        self._verifier = CompanyVerifierAgent(llm, self._search)

    async def find(self, company_name: str) -> tuple[CompanyData, Optional[str]]:
        logger.info(f"Searching company intelligence for: '{company_name}'")

        # Three searches run in parallel
        linkedin_res, meta_res, founder_res, size_res = await asyncio.gather(
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
                objective=f"Founders and executive team for {company_name}",
                queries=[
                    f"{company_name} founder co-founder LinkedIn",
                    f"{company_name} founders executive team",
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

        all_results = linkedin_res + meta_res + founder_res + size_res

        linkedin_url = self._extract_linkedin_company_url(linkedin_res + meta_res)
        ceo_name = self._extract_ceo_name(all_results, company_name)
        raw_website = self._extract_website(meta_res, company_name)
        description = self._extract_description(meta_res + linkedin_res, company_name)
        size = self._extract_size(size_res + meta_res)

        # Verify and correct website + find LinkedIn if missing
        site_result = await self._verifier.verify(company_name, raw_website)
        if site_result.website != raw_website:
            logger.info(
                f"Website corrected: '{raw_website}' -> '{site_result.website}' "
                f"(confidence={site_result.website_confidence})"
            )

        resolved_linkedin = linkedin_url or site_result.linkedin
        logger.info(
            f"Company result — linkedin={resolved_linkedin}, website={site_result.website} "
            f"[{site_result.website_confidence}], ceo={ceo_name}, size={size}"
        )

        company_data = CompanyData(
            name=company_name,
            linkedin=resolved_linkedin,
            website=site_result.website,
            website_confidence=site_result.website_confidence,
            description=description,
            size=size,
        )
        return company_data, ceo_name

    # ── helpers ──────────────────────────────────────────────────────────────

    def _extract_linkedin_company_url(self, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            if 'linkedin.com/company' in r.url:
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'company':
                    return f"https://www.linkedin.com/company/{parts[1]}/"
        return None

    def _extract_ceo_name(self, results: list[SearchResult], company_name: str) -> Optional[str]:
        return extract_ceo_name_from_results(results, company_name)

    def _extract_website(self, results: list[SearchResult], company_name: str) -> Optional[str]:
        for r in results:
            parsed = urlparse(r.url)
            domain = parsed.netloc.lower().replace('www.', '')
            if any(d in domain for d in _NOISE_DOMAINS):
                continue
            if self._domain_matches_company(domain, company_name):
                return f"{parsed.scheme}://{parsed.netloc}/"
        for r in results:
            parsed = urlparse(r.url)
            domain = parsed.netloc.lower().replace('www.', '')
            if not any(d in domain for d in _NOISE_DOMAINS):
                return f"{parsed.scheme}://{parsed.netloc}/"
        return None

    def _domain_matches_company(self, domain: str, company_name: str) -> bool:
        normalized_domain = re.sub(r'[^a-z0-9]', '', domain.lower())
        terms = [
            term for term in re.findall(r'[a-z0-9]+', company_name.lower())
            if len(term) >= 4
        ]
        return bool(terms) and all(term in normalized_domain for term in terms)

    def _extract_description(self, results: list[SearchResult], company_name: str) -> Optional[str]:
        for r in results:
            excerpt = r.excerpt.strip()
            if len(excerpt) > 60 and company_name.lower().split()[0] in excerpt.lower():
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
