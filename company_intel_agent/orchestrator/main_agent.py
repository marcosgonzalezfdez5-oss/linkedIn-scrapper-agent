"""
Orchestrator coordinates company lookup, CEO lookup, and news lookup.

Company and CEO LinkedIn data use Apify first, then silently fall back to the
existing Parallel-backed agents. News remains Parallel-backed.
"""
import asyncio
from typing import Optional

from company_intel_agent.agents.apify_company_agent import ApifyCompanyAgent
from company_intel_agent.agents.apify_ceo_agent import ApifyCEOAgent
from company_intel_agent.agents.linkedin_company_agent import LinkedInCompanyAgent
from company_intel_agent.agents.linkedin_ceo_agent import LinkedInCEOAgent
from company_intel_agent.agents.news_agent import NewsAgent
from company_intel_agent.models.schemas import CEOData, CompanyIntelligence
from company_intel_agent.config import settings
from company_intel_agent.utils.logger import get_logger
from company_intel_agent.utils.names import extract_ceo_name_from_results
from company_intel_agent.utils.search import ParallelSearchClient

logger = get_logger("Orchestrator")


def _urls_equivalent(a: str, b: str) -> bool:
    """Compare two LinkedIn profile URLs ignoring scheme, www., and trailing slash."""
    def _norm(u: str) -> str:
        return u.lower().replace('https://', '').replace('http://', '').replace('www.', '').rstrip('/')
    return _norm(a) == _norm(b)


class OrchestratorAgent:
    def __init__(self):
        self._apify_company_agent = ApifyCompanyAgent()
        self._apify_ceo_agent = ApifyCEOAgent()
        self._company_agent = LinkedInCompanyAgent()
        self._ceo_agent = LinkedInCEOAgent()
        self._news_agent = NewsAgent()

    async def run(self, company_name: str) -> dict:
        company_name = company_name.strip()
        logger.info(f"=== Starting research for: '{company_name}' ===")
        logger.info(f"Apify actors: {'enabled' if settings.APIFY_TOKEN else 'disabled (APIFY_TOKEN not set — using Parallel fallback)'}")

        company_data = await self._apify_company_agent.find(company_name)

        if company_data is None:
            company_data = await self._company_agent.find(company_name)
            ceo_name = self._company_agent._ceo_name
        else:
            ceo_name = await self._discover_ceo_name(company_name)

        logger.info(f"CEO discovered: name={ceo_name!r}")

        async def _get_ceo_data() -> CEOData:
            if not ceo_name:
                logger.info("No CEO name found; skipping CEO lookup")
                return CEOData()

            apify_data, rejected_url = await self._apify_ceo_agent.find(ceo_name, company_name)
            if apify_data is not None:
                return apify_data

            fallback = await self._ceo_agent.find(ceo_name, company_name)

            if fallback.linkedin:
                if rejected_url and _urls_equivalent(rejected_url, fallback.linkedin):
                    logger.info(
                        f"Suppressing LinkedIn '{fallback.linkedin}' — already rejected by Apify "
                        "(profile current_company does not match target)"
                    )
                    fallback = fallback.model_copy(update={"linkedin": None, "confidence": "medium"})
                else:
                    # Fallback found a URL not previously checked — verify it with Apify
                    url_ok = await self._apify_ceo_agent.verify_url(fallback.linkedin, company_name)
                    if not url_ok:
                        logger.info(
                            f"Suppressing LinkedIn '{fallback.linkedin}' — rejected by secondary Apify verification"
                        )
                        fallback = fallback.model_copy(update={"linkedin": None, "confidence": "medium"})

            return fallback

        ceo_data, news_items = await asyncio.gather(
            _get_ceo_data(),
            self._news_agent.find(company_name),
        )

        result = CompanyIntelligence(
            company=company_data,
            ceo=ceo_data,
            news=news_items,
        )

        logger.info("=== Research complete ===")
        return result.model_dump()

    async def _discover_ceo_name(self, company_name: str) -> Optional[str]:
        search = ParallelSearchClient()
        results = await asyncio.to_thread(
            search.search,
            objective=f"Who is the current CEO or founder of {company_name}?",
            queries=[
                f"{company_name} CEO founder",
                f"current CEO of {company_name}",
            ],
        )
        name = extract_ceo_name_from_results(results, company_name)
        logger.info(f"Discovered CEO name from web: {name!r}")
        return name

    async def run_many(self, names: list[str], concurrency: int = 7) -> list[dict]:
        sem = asyncio.Semaphore(concurrency)

        async def _one(name: str) -> dict:
            async with sem:
                try:
                    return {"input": name, "ok": True, "result": await self.run(name)}
                except Exception as e:
                    logger.error(f"Run failed for {name!r}: {e}")
                    return {"input": name, "ok": False, "error": str(e)}

        return list(await asyncio.gather(*(_one(n) for n in names)))

    def run_sync(self, company_name: str) -> dict:
        """Convenience wrapper for non-async callers."""
        return asyncio.run(self.run(company_name))
