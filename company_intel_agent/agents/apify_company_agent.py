import asyncio
from typing import Any, Optional
from urllib.parse import urlparse

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CompanyData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.logger import get_logger
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult

logger = get_logger("ApifyCompanyAgent")


class ApifyCompanyAgent:
    async def find(self, company_name: str) -> CompanyData | None:
        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel company fallback")
            return None

        linkedin_url = await self._find_linkedin_url(company_name)
        if not linkedin_url:
            logger.info(f"LinkedIn URL not found for '{company_name}'; using Parallel fallback")
            return None

        logger.info(f"Resolved LinkedIn URL for '{company_name}': {linkedin_url}")

        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Apify company lookup failed; using fallback: {exc}")
            return None

        if not items:
            logger.info("Apify company lookup returned no items; using fallback")
            return None

        item = items[0]

        return CompanyData(
            name=self._pick(item, "name", "companyName", "company_name") or company_name,
            linkedin=self._pick(item, "linkedin", "linkedinUrl", "linkedin_url", "url", "companyUrl") or linkedin_url,
            website=self._pick(item, "website", "companyWebsite", "websiteUrl", "company_website"),
            website_confidence="high",
            description=self._pick(item, "description", "about", "overview", "summary"),
            size=self._stringify(self._pick(item, "employeeCount", "employees", "companySize", "size")),
        )

    async def _find_linkedin_url(self, company_name: str) -> Optional[str]:
        search = ParallelSearchClient()
        results = await asyncio.to_thread(
            search.search,
            objective=f"Find the official LinkedIn company page URL for {company_name}",
            queries=[
                f"{company_name} site:linkedin.com/company",
                f'"{company_name}" linkedin company page',
            ],
        )
        return self._extract_linkedin_company_url(results)

    def _extract_linkedin_company_url(self, results: list[SearchResult]) -> Optional[str]:
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

        run = client.actor(settings.APIFY_COMPANY_ACTOR_ID).call(
            run_input={settings.APIFY_COMPANY_INPUT_FIELD: linkedin_url}
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
