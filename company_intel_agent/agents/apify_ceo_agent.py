import asyncio
import re
from typing import Any, Optional
from urllib.parse import urlparse

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.llm_client import LLMClient
from company_intel_agent.utils.search import ParallelSearchClient
from company_intel_agent.agents.ceo_verifier_agent import CEOVerifierAgent
from company_intel_agent.utils.logger import get_logger

logger = get_logger("ApifyCEOAgent")

_LINKEDIN_PROFILE_PATTERN = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)')


class ApifyCEOAgent:
    def __init__(self, llm: LLMClient):
        self._search = ParallelSearchClient()
        self._verifier = CEOVerifierAgent(llm, self._search)

    async def find(
        self,
        ceo_name: str,
        company_name: str,
        company_linkedin_url: Optional[str] = None,
    ) -> tuple[CEOData | None, str | None]:
        """Return (CEOData, None) on success, or (None, None) on failure/unavailable."""
        if not settings.APIFY_TOKEN:
            logger.info("APIFY_TOKEN not configured; using Parallel CEO fallback")
            return None, None

        linkedin_url = await self._discover_linkedin_url(ceo_name, company_name)
        if not linkedin_url:
            logger.info(f"No LinkedIn URL found for '{ceo_name}' — skipping Apify profile scrape")
            return None, None

        logger.info(f"Discovered LinkedIn URL for '{ceo_name}': {linkedin_url}")

        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Apify CEO lookup failed; using fallback: {exc}")
            return None, None

        if not items:
            logger.info("Apify CEO lookup returned no items; using fallback")
            return None, None

        item = items[0]

        # AI verifier: pass raw actor data so it can disambiguate same-name cases
        verified = await self._verifier.verify(
            ceo_name=ceo_name,
            company_name=company_name,
            linkedin_url=self._pick(item, "profileUrl", "linkedinUrl", "url", "linkedin") or linkedin_url,
            actor_data=item,
        )

        for reason in verified.reasons:
            logger.info(f"[CEOVerifier] {reason}")
        logger.info(
            f"[CEOVerifier] name='{verified.name}' linkedin={verified.linkedin} "
            f"confidence={verified.confidence}"
        )

        if verified.confidence == "low":
            logger.warning(
                f"AI verifier rejected profile for '{ceo_name}' at '{company_name}' "
                "(low confidence) — flagging URL for suppression"
            )
            return None, linkedin_url

        verified_linkedin = verified.linkedin or linkedin_url

        verified_flag = self._pick(item, "verified", "isVerified")
        if verified_flag is None:
            verification_obj = item.get("verification")
            if isinstance(verification_obj, dict):
                verified_flag = verification_obj.get("verified") or verification_obj.get("isVerified")
        if isinstance(verified_flag, str):
            verified_flag = verified_flag.lower() in ("true", "yes", "1")

        return CEOData(
            name=verified.name or self._pick(item, "fullName", "name", "full_name"),
            linkedin=verified_linkedin,
            title=self._pick(item, "currentTitle", "headline", "title", "position", "occupation"),
            summary=self._pick(item, "summary", "about", "bio", "description"),
            confidence=verified.confidence,
            verified=bool(verified_flag) if verified_flag is not None else None,
        ), None

    async def _discover_linkedin_url(self, name: str, company: str) -> Optional[str]:
        results = await asyncio.to_thread(
            self._search.search,
            objective=f"LinkedIn profile for {name}, CEO of {company}",
            queries=[
                f"{name} {company} site:linkedin.com/in",
                f"{name} CEO LinkedIn profile",
            ],
        )
        for r in results:
            if 'linkedin.com/in/' in r.url:
                parts = urlparse(r.url).path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'in':
                    return f"https://www.linkedin.com/in/{parts[1]}/"
            for match in _LINKEDIN_PROFILE_PATTERN.finditer(r.excerpt):
                return f"https://www.linkedin.com/in/{match.group(1).strip('/')}/"
        return None

    async def verify_url(
        self,
        linkedin_url: str,
        company_name: str,
        company_linkedin_url: Optional[str] = None,
    ) -> bool:
        """Scrape linkedin_url and verify via AI agent that it matches company_name."""
        if not settings.APIFY_TOKEN:
            return True
        logger.info(f"Secondary Apify verification for URL: {linkedin_url}")
        try:
            items = await asyncio.to_thread(self._run_actor, linkedin_url)
        except Exception as exc:
            logger.warning(f"Secondary Apify verification failed; keeping URL: {exc}")
            return True
        if not items:
            logger.info("Secondary Apify verification returned no items — keeping URL")
            return True

        item = items[0]
        scraped_name = self._pick(item, "fullName", "name", "full_name") or ""
        verified = await self._verifier.verify(
            ceo_name=scraped_name,
            company_name=company_name,
            linkedin_url=linkedin_url,
            actor_data=item,
        )
        matches = verified.confidence != "low"
        logger.info(f"Secondary verification: confidence={verified.confidence}, accept={matches}")
        return matches

    def _run_actor(self, linkedin_url: str) -> list[dict[str, Any]]:
        client = get_apify_client()
        if client is None:
            return []

        logger.info(f"Calling Apify profile actor: {settings.APIFY_PROFILE_ACTOR_ID} (input: {linkedin_url})")
        run = client.actor(settings.APIFY_PROFILE_ACTOR_ID).call(
            run_input={
                settings.APIFY_PROFILE_INPUT_FIELD: [linkedin_url],
                "includeExperience": True,
                "includeEducation": True,
                "includeSkills": False,
                "slowMode": False,
                "maxConcurrency": 5,
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
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
