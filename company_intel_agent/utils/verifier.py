"""
CEO verification pipeline.

Two independent checks run after the initial CEO search:
  A) Name corroboration — independent search counts how many sources confirm the name
  B) LinkedIn slug match — checks that the URL slug contains the CEO's name tokens

Self-correction:
  - If name not corroborated (0 sources) → attempt to extract correct name from verify results
  - If LinkedIn slug doesn't match the (possibly corrected) name → targeted re-search
"""
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CEOVerifier")

# CEO name extraction patterns — same logic used in linkedin_company_agent
_CEO_PATTERNS = [
    re.compile(r'CEO[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'Chief Executive Officer[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'founded by ([A-Z][a-z]+(?: [A-Z][a-z]+)+)', re.IGNORECASE),
    re.compile(r'([A-Z][a-z]+(?: [A-Z][a-z]+)+),? (?:CEO|Chief Executive)', re.IGNORECASE),
]


@dataclass
class VerificationResult:
    name: Optional[str]
    linkedin: Optional[str]
    confidence: str                    # "high" | "medium" | "low"
    reasons: list[str] = field(default_factory=list)


class CEOVerifier:
    def __init__(self):
        self._search = ParallelSearchClient()

    async def verify(
        self,
        ceo_name: str,
        company_name: str,
        linkedin_url: Optional[str],
    ) -> VerificationResult:
        reasons: list[str] = []

        # Check A: independent corroboration search
        corr_results = await asyncio.to_thread(
            self._search.search,
            objective=f"Who is the current CEO of {company_name}?",
            queries=[
                f"current CEO of {company_name}",
                f"{company_name} CEO leadership team",
            ],
        )

        # Check B: slug match (sync, instant)
        url_confirmed = self._slug_matches_name(ceo_name, linkedin_url)

        # Score name corroboration
        mention_count = self._count_name_mentions(ceo_name, corr_results)
        if mention_count >= 2:
            name_score = "high"
        elif mention_count >= 1:
            name_score = "medium"
        else:
            name_score = "low"

        logger.info(
            f"Verification — name='{ceo_name}' mentions={mention_count} "
            f"name_score={name_score} slug_match={url_confirmed}"
        )

        corrected_name = ceo_name
        corrected_url = linkedin_url

        # Self-correct name if not corroborated
        if name_score == "low":
            reasons.append(f"'{ceo_name}' found in 0 independent sources — attempting correction")
            candidate = _extract_ceo_name_from_results(corr_results)
            if candidate and candidate.lower() != ceo_name.lower():
                corrected_name = candidate
                url_confirmed = self._slug_matches_name(corrected_name, linkedin_url)
                reasons.append(f"Corrected CEO name: '{ceo_name}' → '{corrected_name}'")
            else:
                reasons.append("Could not extract alternative name — keeping original")

        # Self-correct LinkedIn URL if slug doesn't match
        if not url_confirmed:
            reasons.append(
                f"LinkedIn slug doesn't match '{corrected_name}' — searching for correct profile"
            )
            corrected_url = await self._find_linkedin_url(corrected_name, company_name)
            if corrected_url:
                reasons.append(f"Found corrected LinkedIn URL: {corrected_url}")
            else:
                reasons.append("Could not find a matching LinkedIn profile")

        final_slug_ok = self._slug_matches_name(corrected_name, corrected_url)
        confidence = self._score_confidence(name_score, final_slug_ok)

        return VerificationResult(
            name=corrected_name,
            linkedin=corrected_url,
            confidence=confidence,
            reasons=reasons,
        )

    # ------------------------------------------------------------------ helpers

    def _slug_matches_name(self, name: Optional[str], url: Optional[str]) -> bool:
        """True if all parts of the person's name appear in the LinkedIn URL slug."""
        if not name or not url or 'linkedin.com/in/' not in url:
            return False
        parsed = urlparse(url)
        slug_tokens = set(parsed.path.strip('/').split('/')[-1].lower().split('-'))
        name_tokens = [t.lower() for t in name.split() if len(t) > 1]
        return all(token in slug_tokens for token in name_tokens)

    def _count_name_mentions(self, name: str, results: list[SearchResult]) -> int:
        """Count results whose title+excerpt contain both first and last name."""
        if not name or ' ' not in name:
            return 0
        parts = name.lower().split()
        first, last = parts[0], parts[-1]
        count = 0
        for r in results:
            text = f"{r.title} {r.excerpt}".lower()
            if first in text and last in text:
                count += 1
        return count

    async def _find_linkedin_url(self, name: str, company: str) -> Optional[str]:
        """Targeted search to find a LinkedIn profile URL matching the given name."""
        results = await asyncio.to_thread(
            self._search.search,
            objective=f"LinkedIn profile for {name}, CEO of {company}",
            queries=[
                f"{name} {company} site:linkedin.com/in",
                f"{name} CEO LinkedIn profile",
            ],
        )
        for r in results:
            if 'linkedin.com/in/' in r.url and self._slug_matches_name(name, r.url):
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'in':
                    return f"https://www.linkedin.com/in/{parts[1]}/"
        # Return first linkedin.com/in result even if slug doesn't match (best effort)
        for r in results:
            if 'linkedin.com/in/' in r.url:
                parsed = urlparse(r.url)
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'in':
                    return f"https://www.linkedin.com/in/{parts[1]}/"
        return None

    def _score_confidence(self, name_score: str, url_ok: bool) -> str:
        if name_score == "high" and url_ok:
            return "high"
        if name_score == "low":
            return "low"
        return "medium"


def _extract_ceo_name_from_results(results: list[SearchResult]) -> Optional[str]:
    """Extract a candidate CEO name from search results using regex patterns."""
    for r in results:
        text = f"{r.title} {r.excerpt}"
        for pattern in _CEO_PATTERNS:
            match = pattern.search(text)
            if match:
                name = match.group(1).strip()
                words = name.split()
                if 2 <= len(words) <= 4 and not name.isupper():
                    return name
    return None
