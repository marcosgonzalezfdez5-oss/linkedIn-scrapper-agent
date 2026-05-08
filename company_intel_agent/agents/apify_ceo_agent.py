import asyncio
import re
from typing import Any, Optional
from urllib.parse import urlparse

from company_intel_agent.config import settings
from company_intel_agent.models.schemas import CEOData
from company_intel_agent.utils.apify_client import get_apify_client
from company_intel_agent.utils.logger import get_logger
from company_intel_agent.utils.search import ParallelSearchClient

logger = get_logger("ApifyCEOAgent")

_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "corp", "co", "company", "gmbh", "sa", "ag", "plc"}
_LINKEDIN_PROFILE_PATTERN = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)')


def _normalize_company(name: str) -> set[str]:
    tokens = re.findall(r'[a-z0-9]+', name.lower())
    return {t for t in tokens if t not in _LEGAL_SUFFIXES and len(t) >= 2}


class ApifyCEOAgent:
    def __init__(self):
        self._search = ParallelSearchClient()

    async def find(
        self,
        ceo_name: str,
        company_name: str,
        company_linkedin_url: Optional[str] = None,
    ) -> tuple[CEOData | None, str | None]:
        """Return (CEOData, None) on success, (None, rejected_url) when the scraped profile's
        company doesn't match, or (None, None) for all other failure/unavailable cases."""
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

        scraped_company = self._pick(item, "currentCompany", "current_company", "company", "companyName")
        scraped_company_url = self._extract_scraped_company_url(item)
        if not self._company_matches(
            company_name, scraped_company,
            target_url=company_linkedin_url,
            scraped_url=scraped_company_url,
        ):
            logger.warning(
                f"Profile current_company='{scraped_company}' does not match target "
                f"'{company_name}' — discarding Apify result and flagging URL for suppression"
            )
            return None, linkedin_url

        verified = self._pick(item, "verified", "isVerified")
        if verified is None:
            verification_obj = item.get("verification")
            if isinstance(verification_obj, dict):
                verified = verification_obj.get("verified") or verification_obj.get("isVerified")
        if isinstance(verified, str):
            verified = verified.lower() in ("true", "yes", "1")

        logger.info(f"LinkedIn verified status for '{ceo_name}': {verified}")

        return CEOData(
            name=self._pick(item, "fullName", "name", "full_name"),
            linkedin=self._pick(item, "profileUrl", "linkedinUrl", "url", "linkedin") or linkedin_url,
            title=self._pick(item, "currentTitle", "headline", "title", "position", "occupation"),
            summary=self._pick(item, "summary", "about", "bio", "description"),
            confidence="high",
            verified=bool(verified) if verified is not None else None,
        ), None

    async def _discover_linkedin_url(self, name: str, company: str) -> Optional[str]:
        """Find a LinkedIn profile URL for the CEO — no slug-match filter because
        the Apify actor verifies company match directly from the scraped profile."""
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
        """Scrape linkedin_url and verify that current_company matches company_name.
        Returns True (accept) if the company matches or if verification is unavailable.
        Returns False (reject) only when the actor explicitly returns a mismatched company."""
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
        scraped_company = self._pick(item, "currentCompany", "current_company", "company", "companyName")
        scraped_company_url = self._extract_scraped_company_url(item)
        matches = self._company_matches(
            company_name, scraped_company,
            target_url=company_linkedin_url,
            scraped_url=scraped_company_url,
        )
        logger.info(
            f"Secondary verification: current_company='{scraped_company}' "
            f"company_url='{scraped_company_url}', matches={matches}"
        )
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

    def _company_matches(
        self,
        target_name: str,
        scraped_name: Optional[str],
        target_url: Optional[str] = None,
        scraped_url: Optional[str] = None,
    ) -> bool:
        # Layer 1: slug compare from LinkedIn URLs is authoritative when available.
        t_slug = self._company_slug(target_url) if target_url else None
        s_slug = self._company_slug(scraped_url) if scraped_url else None
        if t_slug and s_slug:
            return t_slug == s_slug

        # Layer 2: name-token matching with significant-overlap fallback.
        if not scraped_name:
            return False
        t_tokens = _normalize_company(target_name)
        s_tokens = _normalize_company(scraped_name)
        if not t_tokens:
            return False
        if t_tokens.issubset(s_tokens):
            return True
        # Brand vs legal-name divergence (e.g. "Carl Zeiss Ag" vs "ZEISS Group"):
        # accept if any distinctive token (≥5 chars) appears in the scraped name.
        significant = {t for t in t_tokens if len(t) >= 5}
        return bool(significant) and bool(significant & s_tokens)

    def _company_slug(self, url: Optional[str]) -> Optional[str]:
        if not url or 'linkedin.com/company/' not in url:
            return None
        try:
            parts = urlparse(url).path.strip('/').split('/')
        except Exception:
            return None
        if len(parts) >= 2 and parts[0] == 'company' and parts[1]:
            return parts[1].lower()
        return None

    def _extract_scraped_company_url(self, item: dict[str, Any]) -> Optional[str]:
        direct = self._pick(
            item,
            "currentCompanyUrl", "currentCompanyLinkedinUrl",
            "companyUrl", "companyLinkedinUrl",
        )
        if isinstance(direct, str) and 'linkedin.com/company/' in direct:
            return direct

        current = item.get("currentCompany")
        if isinstance(current, dict):
            for key in ("url", "linkedinUrl", "linkedin", "companyUrl"):
                value = current.get(key)
                if isinstance(value, str) and 'linkedin.com/company/' in value:
                    return value

        experience = item.get("experience") or item.get("experiences")
        if isinstance(experience, list) and experience:
            first = experience[0]
            if isinstance(first, dict):
                for key in ("companyLinkedinUrl", "companyUrl", "url", "linkedinUrl"):
                    value = first.get(key)
                    if isinstance(value, str) and 'linkedin.com/company/' in value:
                        return value
                nested = first.get("company")
                if isinstance(nested, dict):
                    for key in ("url", "linkedinUrl", "linkedin"):
                        value = nested.get(key)
                        if isinstance(value, str) and 'linkedin.com/company/' in value:
                            return value
        return None

    def _pick(self, item: dict[str, Any], *keys: str) -> Optional[Any]:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None
