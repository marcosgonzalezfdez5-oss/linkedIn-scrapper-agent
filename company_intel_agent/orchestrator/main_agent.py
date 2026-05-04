"""
Orchestrator — coordinates the three sub-agents:

  1. LinkedInCompanyAgent  (sequential, first — CEO name needed for step 2)
  2. LinkedInCEOAgent  ┐
                        ├─ run in parallel via asyncio.gather
  3. NewsAgent         ┘
"""
import asyncio
import json
from typing import Optional

from company_intel_agent.agents.linkedin_company_agent import LinkedInCompanyAgent
from company_intel_agent.agents.linkedin_ceo_agent import LinkedInCEOAgent
from company_intel_agent.agents.news_agent import NewsAgent
from company_intel_agent.models.schemas import CEOData, CompanyIntelligence
from company_intel_agent.utils.logger import get_logger

logger = get_logger("Orchestrator")


class OrchestratorAgent:
    def __init__(self):
        self._company_agent = LinkedInCompanyAgent()
        self._ceo_agent = LinkedInCEOAgent()
        self._news_agent = NewsAgent()

    async def run(self, company_name: str) -> dict:
        company_name = company_name.strip()
        logger.info(f"=== Starting research for: '{company_name}' ===")

        # Step 1: Company LinkedIn — must run first to discover the CEO name
        company_data = await self._company_agent.find(company_name)
        ceo_name: Optional[str] = self._company_agent._ceo_name
        logger.info(f"CEO discovered: {ceo_name!r}")

        # Step 2 & 3: CEO LinkedIn + News — run concurrently
        async def _get_ceo_data() -> CEOData:
            if ceo_name:
                return await self._ceo_agent.find(ceo_name, company_name)
            logger.info("No CEO name found — skipping CEO LinkedIn lookup")
            return CEOData()

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

    def run_sync(self, company_name: str) -> dict:
        """Convenience wrapper for non-async callers."""
        return asyncio.run(self.run(company_name))
