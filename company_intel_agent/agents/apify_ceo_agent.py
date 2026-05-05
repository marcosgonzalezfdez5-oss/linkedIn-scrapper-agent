import asyncio
from typing import Any, Optional

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.logger import get_logger

logger = get_logger("ApifyCEOAgent")


class ApifyCEOAgent:
    async def find(self, linkedin_url: str) -> CEOData | None:
        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel CEO fallback")
            return None

        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Apify CEO lookup failed; using fallback: {exc}")
            return None

        if not items:
            logger.info("Apify CEO lookup returned no items; using fallback")
            return None

        item = items[0]
        return CEOData(
            name=self._pick(item, "fullName", "name", "full_name"),
            linkedin=self._pick(item, "profileUrl", "linkedinUrl", "url", "linkedin") or linkedin_url,
            title=self._pick(item, "headline", "title", "position", "occupation"),
            summary=self._pick(item, "summary", "about", "description"),
            confidence="high",
        )

    def _run_actor(self, linkedin_url: str) -> list[dict[str, Any]]:
        client = get_apify_client()
        if client is None:
            return []

        run = client.actor(settings.APIFY_PROFILE_ACTOR_ID).call(
            run_input={settings.APIFY_PROFILE_INPUT_FIELD: linkedin_url}
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
