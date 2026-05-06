import asyncio
import re
from typing import Any, Optional

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.logger import get_logger
from company_intel_agent.utils.verifier import CEOVerifier

logger = get_logger("ApifyCEOAgent")

_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "corp", "co", "company", "gmbh", "sa", "ag", "plc"}


def _normalize_company(name: str) -> set[str]:
    tokens = re.findall(r'[a-z0-9]+', name.lower())
    return {t for t in tokens if t not in _LEGAL_SUFFIXES and len(t) >= 2}


class ApifyCEOAgent:
    def __init__(self):
        self._verifier = CEOVerifier()

    async def find(self, ceo_name: str, company_name: str) -> CEOData | None:
        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel CEO fallback")
            return None

        linkedin_url = await self._verifier._find_linkedin_url(ceo_name, company_name)
        if not linkedin_url:
            logger.info(f"No LinkedIn URL found for '{ceo_name}' — skipping Apify profile scrape")
            return None

        logger.info(f"Discovered LinkedIn URL for '{ceo_name}': {linkedin_url}")

        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Apify CEO lookup failed; using fallback: {exc}")
            return None

        if not items:
            logger.info("Apify CEO lookup returned no items; using fallback")
            return None

        item = items[0]

        scraped_company = self._pick(item, "currentCompany", "current_company", "company", "companyName")
        if not self._company_matches(company_name, scraped_company):
            logger.warning(
                f"Profile current_company='{scraped_company}' does not match target "
                f"'{company_name}' — discarding Apify result"
            )
            return None

        return CEOData(
            name=self._pick(item, "fullName", "name", "full_name"),
            linkedin=self._pick(item, "profileUrl", "linkedinUrl", "url", "linkedin") or linkedin_url,
            title=self._pick(item, "currentTitle", "headline", "title", "position", "occupation"),
            summary=self._pick(item, "summary", "about", "bio", "description"),
            confidence="high",
        )

    def _run_actor(self, linkedin_url: str) -> list[dict[str, Any]]:
        client = get_apify_client()
        if client is None:
            return []

        run = client.actor(settings.APIFY_PROFILE_ACTOR_ID).call(
            run_input={settings.APIFY_PROFILE_INPUT_FIELD: [linkedin_url]}
        )
        if not run or not run.get("defaultDatasetId"):
            return []
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())

    def _company_matches(self, target: str, scraped: Optional[str]) -> bool:
        if not scraped:
            return False
        t_tokens = _normalize_company(target)
        s_tokens = _normalize_company(scraped)
        return bool(t_tokens) and t_tokens.issubset(s_tokens)

    def _pick(self, item: dict[str, Any], *keys: str) -> Optional[Any]:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None
