import asyncio
from typing import Any, Optional

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CompanyData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.logger import get_logger

logger = get_logger("ApifyCompanyAgent")


class ApifyCompanyAgent:
    def __init__(self):
        self._ceo_linkedin: Optional[str] = None
        self._ceo_name: Optional[str] = None

    async def find(self, company_name: str) -> CompanyData | None:
        self._ceo_linkedin = None
        self._ceo_name = None

        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel company fallback")
            return None

        try:
            items = await asyncio.to_thread(self._run_actor, company_name)
        except Exception as exc:
            logger.warning(f"Apify company lookup failed; using fallback: {exc}")
            return None

        if not items:
            logger.info("Apify company lookup returned no items; using fallback")
            return None

        item = items[0]
        self._ceo_linkedin = self._extract_ceo_linkedin(item)
        self._ceo_name = self._extract_ceo_name(item)

        return CompanyData(
            name=self._pick(item, "name", "companyName", "company_name") or company_name,
            linkedin=self._pick(item, "linkedin", "linkedinUrl", "linkedin_url", "url", "companyUrl"),
            website=self._pick(item, "website", "companyWebsite", "websiteUrl", "company_website"),
            website_confidence="high",
            description=self._pick(item, "description", "about", "overview", "summary"),
            size=self._stringify(self._pick(item, "employeeCount", "employees", "companySize", "size")),
        )

    def _run_actor(self, company_name: str) -> list[dict[str, Any]]:
        client = get_apify_client()
        if client is None:
            return []

        run = client.actor(settings.APIFY_COMPANY_ACTOR_ID).call(
            run_input={settings.APIFY_COMPANY_INPUT_FIELD: company_name}
        )
        if not run or not run.get("defaultDatasetId"):
            return []
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())

    def _extract_ceo_linkedin(self, item: dict[str, Any]) -> Optional[str]:
        ceo = item.get("ceo")
        if isinstance(ceo, dict):
            url = self._pick(ceo, "linkedinUrl", "profileUrl", "url", "linkedin")
            if url:
                return url

        for key in ("executives", "leadership", "people", "employees"):
            values = item.get(key)
            if not isinstance(values, list):
                continue
            for person in values:
                if not isinstance(person, dict):
                    continue
                text = " ".join(
                    str(person.get(field, ""))
                    for field in ("title", "headline", "position", "role")
                ).lower()
                if "ceo" in text or "chief executive officer" in text:
                    return self._pick(person, "profileUrl", "linkedinUrl", "url", "linkedin")
        return self._pick(item, "ceoLinkedin", "ceoLinkedIn", "ceoLinkedinUrl", "ceoProfileUrl")

    def _extract_ceo_name(self, item: dict[str, Any]) -> Optional[str]:
        ceo = item.get("ceo")
        if isinstance(ceo, dict):
            name = self._pick(ceo, "name", "fullName", "full_name")
            if name:
                return name

        for key in ("executives", "leadership", "people", "employees"):
            values = item.get(key)
            if not isinstance(values, list):
                continue
            for person in values:
                if not isinstance(person, dict):
                    continue
                text = " ".join(
                    str(person.get(field, ""))
                    for field in ("title", "headline", "position", "role")
                ).lower()
                if "ceo" in text or "chief executive officer" in text:
                    return self._pick(person, "name", "fullName", "full_name")
        return self._pick(item, "ceoName", "ceo_name")

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
