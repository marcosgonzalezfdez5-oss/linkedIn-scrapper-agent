import asyncio
from typing import Any, Optional
from urllib.parse import urlparse

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CompanyData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.llm_client import LLMClient
from company_intel_agent.utils.search import ParallelSearchClient
from company_intel_agent.agents.company_verifier_agent import CompanyVerifierAgent
from company_intel_agent.utils.logger import get_logger

logger = get_logger("ApifyCompanyAgent")


class ApifyCompanyAgent:
    def __init__(self, llm: LLMClient):
        self._search = ParallelSearchClient()
        self._verifier = CompanyVerifierAgent(llm, self._search)

    async def find(self, company_name: str) -> CompanyData | None:
        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel company fallback")
            return None

        linkedin_url = await self._find_linkedin_url(company_name)

        if not linkedin_url:
            logger.info(
                f"LinkedIn URL not found for '{company_name}' — "
                "running AI verifier agent to gather company data from web"
            )
            result = await self._verifier.verify(company_name, None)
            return CompanyData(
                name=company_name,
                linkedin=result.linkedin,
                website=result.website,
                website_confidence=result.website_confidence,
            )

        logger.info(f"Resolved LinkedIn URL for '{company_name}': {linkedin_url}")

        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Apify company lookup failed; running AI verifier fallback: {exc}")
            result = await self._verifier.verify(company_name, None)
            return CompanyData(
                name=company_name,
                linkedin=result.linkedin or linkedin_url,
                website=result.website,
                website_confidence=result.website_confidence,
            )

        if not items:
            logger.info("Apify company lookup returned no items; running AI verifier fallback")
            result = await self._verifier.verify(company_name, None)
            return CompanyData(
                name=company_name,
                linkedin=result.linkedin or linkedin_url,
                website=result.website,
                website_confidence=result.website_confidence,
            )

        item = items[0]

        # AI verifier: confirm/correct website and find LinkedIn if actor missed it
        candidate_website = self._pick(item, "website", "companyWebsite", "websiteUrl", "company_website")
        verified = await self._verifier.verify(company_name, candidate_website, actor_data=item)

        resolved_linkedin = (
            self._pick(item, "linkedin", "linkedinUrl", "linkedin_url", "url", "companyUrl")
            or verified.linkedin
            or linkedin_url
        )

        return CompanyData(
            name=self._pick(item, "name", "companyName", "company_name") or company_name,
            linkedin=resolved_linkedin,
            website=verified.website,
            website_confidence=verified.website_confidence,
            description=self._pick(item, "description", "about", "overview", "summary"),
            size=self._stringify(self._pick(item, "employeeCount", "employees", "companySize", "size")),
        )

    async def _find_linkedin_url(self, company_name: str) -> Optional[str]:
        results = await asyncio.to_thread(
            self._search.search,
            objective=f"Find the official LinkedIn company page URL for {company_name}",
            queries=[
                f"{company_name} site:linkedin.com/company",
                f'"{company_name}" linkedin company page',
            ],
        )
        return self._extract_linkedin_company_url(results)

    def _extract_linkedin_company_url(self, results) -> Optional[str]:
        for r in results:
            if 'linkedin.com/company' in r.url:
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'company':
                    return f"https://www.linkedin.com/company/{parts[1]}/"
        return None

    def _run_actor(self, linkedin_url: str) -> list[dict[str, Any]]:
        client = get_apify_client()
        if client is None:
            return []

        logger.info(f"Calling Apify company actor: {settings.APIFY_COMPANY_ACTOR_ID} (input: {linkedin_url})")
        run = client.actor(settings.APIFY_COMPANY_ACTOR_ID).call(
            run_input={
                settings.APIFY_COMPANY_INPUT_FIELD: [linkedin_url],
                "maxResults": 1,
                "maxConcurrency": 2,
                "requestDelay": 5,
                "proxyConfiguration": {"useApifyProxy": True},
            }
        )
        if not run or not run.get("defaultDatasetId"):
            return []
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())

    def _pick(self, item: dict[str, Any], *keys: str) -> Optional[Any]:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None

    def _stringify(self, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return str(value)
