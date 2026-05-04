"""
Verification layer for CEO identity and company website.

CEOVerifier — three independent checks:
  A) General corroboration search ("current CEO of {company}")
  B) LinkedIn slug ↔ name match (instant, no API call)
  C) News corroboration ("{company} CEO news 2025")
  A and C run concurrently; combined mentions drive the confidence score.
  Self-corrects both the name (if unconfirmed) and the LinkedIn URL (if slug mismatches).

WebsiteVerifier — two concurrent searches:
  1) LinkedIn company page snippet (often contains the official website)
  2) Direct "official website of {company}" search
  Frequency-votes across results to pick the most-confirmed domain.
"""
import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.names import (
    extract_ceo_name_from_results,
    is_plausible_person_name,
)
from company_intel_agent.utils.logger import get_logger

logger = get_logger("Verifier")

# ── shared patterns ─────────────────────────────────────────────────────────

_URL_PATTERN = re.compile(r'https?://[^\s,\'"<>()]+')
_LINKEDIN_PROFILE_PATTERN = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)')

_NOISE_DOMAINS = {
    'linkedin.com', 'facebook.com', 'twitter.com', 'x.com', 'instagram.com',
    'youtube.com', 'wikipedia.org', 'google.com', 'glassdoor.com',
    'crunchbase.com', 'bloomberg.com', 'forbes.com', 'techcrunch.com',
    'wsj.com', 'reuters.com', 'businesswire.com', 'prnewswire.com',
}


# ── CEO verifier ─────────────────────────────────────────────────────────────

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
        initial_name_valid = is_plausible_person_name(ceo_name)

        # Checks A and C run concurrently — general web + news
        corr_results, news_results = await asyncio.gather(
            asyncio.to_thread(
                self._search.search,
                objective=f"Who is the current CEO of {company_name}?",
                queries=[
                    f"current CEO of {company_name}",
                    f"{company_name} CEO leadership team",
                ],
            ),
            asyncio.to_thread(
                self._search.search,
                objective=f"Recent news articles naming the current CEO of {company_name}",
                queries=[
                    f"{company_name} CEO news 2025",
                    f"{company_name} CEO announcement statement",
                ],
            ),
        )

        all_corr = corr_results + news_results

        # Check B: slug match (sync, instant)
        url_confirmed = self._slug_matches_name(ceo_name, linkedin_url)

        # Score: count total mentions across all corroboration sources
        mention_count = self._count_name_mentions(ceo_name, all_corr) if initial_name_valid else 0
        if mention_count >= 2:
            name_score = "high"
        elif mention_count >= 1:
            name_score = "medium"
        else:
            name_score = "low"

        logger.info(
            f"CEO check — name='{ceo_name}' web_mentions={self._count_name_mentions(ceo_name, corr_results)} "
            f"news_mentions={self._count_name_mentions(ceo_name, news_results)} "
            f"total={mention_count} name_score={name_score} slug_ok={url_confirmed}"
        )

        corrected_name = ceo_name
        corrected_url = linkedin_url

        # Self-correct name if not corroborated
        if name_score == "low":
            reasons.append(f"'{ceo_name}' confirmed in 0 sources (web+news) — attempting correction")
            candidate = extract_ceo_name_from_results(all_corr, company_name)
            if candidate and candidate.lower() != ceo_name.lower():
                corrected_name = candidate
                url_confirmed = self._slug_matches_name(corrected_name, linkedin_url)
                reasons.append(f"CEO name corrected: '{ceo_name}' -> '{corrected_name}'")
            else:
                reasons.append("No alternative name found in web+news results — keeping original")

        # Self-correct LinkedIn URL if slug doesn't match
        if not url_confirmed and is_plausible_person_name(corrected_name):
            reasons.append(
                f"LinkedIn slug doesn't match '{corrected_name}' — searching for correct profile"
            )
            corrected_url = await self._find_linkedin_url(corrected_name, company_name)
            if corrected_url:
                reasons.append(f"Corrected LinkedIn URL: {corrected_url}")
            else:
                reasons.append("Could not find a slug-matching LinkedIn profile")
        elif not is_plausible_person_name(corrected_name):
            reasons.append(f"'{corrected_name}' is not a plausible person name - skipping LinkedIn correction")
            corrected_name = None
            corrected_url = None

        final_slug_ok = self._slug_matches_name(corrected_name, corrected_url)
        confidence = self._score_confidence(name_score, final_slug_ok)

        return VerificationResult(
            name=corrected_name,
            linkedin=corrected_url,
            confidence=confidence,
            reasons=reasons,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _slug_matches_name(self, name: Optional[str], url: Optional[str]) -> bool:
        if not is_plausible_person_name(name) or not url or 'linkedin.com/in/' not in url:
            return False
        slug = urlparse(url).path.strip('/').split('/')[-1].lower()
        slug_tokens = set(slug.split('-'))
        name_tokens = [t.lower() for t in name.split() if len(t) > 1]
        return all(token in slug_tokens or token in slug for token in name_tokens)

    def _count_name_mentions(self, name: str, results: list[SearchResult]) -> int:
        if not is_plausible_person_name(name):
            return 0
        parts = name.lower().split()
        first, last = parts[0], parts[-1]
        return sum(
            1 for r in results
            if first in f"{r.title} {r.excerpt}".lower()
            and last in f"{r.title} {r.excerpt}".lower()
        )

    async def _find_linkedin_url(self, name: str, company: str) -> Optional[str]:
        results = await asyncio.to_thread(
            self._search.search,
            objective=f"LinkedIn profile for {name}, CEO of {company}",
            queries=[
                f"{name} {company} site:linkedin.com/in",
                f"{name} CEO LinkedIn profile",
            ],
        )
        for r in results:
            if 'linkedin.com/in/' not in r.url or not self._slug_matches_name(name, r.url):
                for match in _LINKEDIN_PROFILE_PATTERN.finditer(r.excerpt):
                    candidate = f"https://www.linkedin.com/in/{match.group(1).strip('/')}/"
                    if self._slug_matches_name(name, candidate):
                        return candidate
                continue
            parts = urlparse(r.url).path.strip('/').split('/')
            if len(parts) >= 2 and parts[0] == 'in':
                return f"https://www.linkedin.com/in/{parts[1]}/"
        return None

    def _score_confidence(self, name_score: str, url_ok: bool) -> str:
        if name_score == "high" and url_ok:
            return "high"
        if name_score == "low":
            return "low"
        return "medium"


# ── website verifier ─────────────────────────────────────────────────────────

@dataclass
class WebsiteResult:
    url: Optional[str]
    confidence: str  # "high" | "medium" | "low"


class WebsiteVerifier:
    def __init__(self):
        self._search = ParallelSearchClient()

    async def verify(self, company_name: str, candidate_url: Optional[str]) -> WebsiteResult:
        # Two concurrent searches
        linkedin_results, direct_results = await asyncio.gather(
            asyncio.to_thread(
                self._search.search,
                objective=f"Find the official website URL for {company_name} from their LinkedIn page",
                queries=[f"{company_name} site:linkedin.com/company"],
            ),
            asyncio.to_thread(
                self._search.search,
                objective=f"Official homepage URL for {company_name}",
                queries=[
                    f"official website of {company_name}",
                    f"{company_name} homepage",
                ],
            ),
        )

        all_results = linkedin_results + direct_results

        # Collect all candidate domains from result URLs + excerpt URL patterns
        domain_counter: Counter = Counter()
        for r in all_results:
            # Count domain of the result URL itself
            d = self._root_domain(r.url)
            if d and not self._is_noisy(d):
                domain_counter[d] += 1

            # Also mine URLs mentioned inside excerpts (LinkedIn pages embed the website)
            for url_match in _URL_PATTERN.findall(r.excerpt):
                d = self._root_domain(url_match)
                if d and not self._is_noisy(d):
                    domain_counter[d] += 2  # extra weight: explicitly mentioned in text

        logger.info(f"Website domain votes for '{company_name}': {dict(domain_counter.most_common(5))}")

        candidate_domain = self._root_domain(candidate_url) if candidate_url else None
        matching_domains = [
            (domain, count) for domain, count in domain_counter.most_common()
            if self._domain_matches_company(domain, company_name)
        ]

        if candidate_domain and matching_domains:
            candidate_count = domain_counter.get(candidate_domain, 0)
            subdomain_matches = [
                (domain, count) for domain, count in matching_domains
                if (
                    domain != candidate_domain
                    and domain.endswith(f".{candidate_domain}")
                    and count >= max(2, int(candidate_count * 0.6))
                )
            ]
            subdomain_matches.sort(
                key=lambda item: (not item[0].startswith('apps.'), -item[1])
            )
            if subdomain_matches:
                domain, count = subdomain_matches[0]
                best_url = self._find_url_for_domain(domain, all_results) or f"https://{domain}/"
                confidence = "high" if count >= 2 else "medium"
                logger.info(
                    f"Website selected from confirmed subdomain: '{best_url}' "
                    f"({count} votes, confidence={confidence})"
                )
                return WebsiteResult(url=best_url, confidence=confidence)

        if (
            candidate_domain
            and candidate_domain in domain_counter
            and self._domain_matches_company(candidate_domain, company_name)
        ):
            count = domain_counter[candidate_domain]
            confidence = "high" if count >= 2 else "medium"
            logger.info(f"Website '{candidate_url}' confirmed with {count} votes -> {confidence}")
            return WebsiteResult(url=self._normalize_url(candidate_url), confidence=confidence)

        # Candidate not found or not provided — use the most-voted domain
        if matching_domains:
            best_domain, count = matching_domains[0]
            best_url = self._find_url_for_domain(best_domain, all_results) or f"https://{best_domain}/"
            confidence = "high" if count >= 2 else "medium"
            logger.info(
                f"Website selected by company-domain match: '{best_url}' "
                f"({count} votes, confidence={confidence})"
            )
            return WebsiteResult(url=best_url, confidence=confidence)

        if domain_counter:
            best_domain, count = domain_counter.most_common(1)[0]
            # Try to reconstruct the URL from search results
            best_url = self._find_url_for_domain(best_domain, all_results) or f"https://{best_domain}/"
            logger.info(
                f"Website candidate not confirmed — substituting '{best_url}' "
                f"({count} votes, confidence=low)"
            )
            return WebsiteResult(url=best_url, confidence="low")

        # Nothing found at all
        return WebsiteResult(url=candidate_url, confidence="low")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _root_domain(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        try:
            return urlparse(url).netloc.lower().replace('www.', '').split(':')[0] or None
        except Exception:
            return None

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _is_noisy(self, domain: str) -> bool:
        return any(noise in domain for noise in _NOISE_DOMAINS)

    def _domain_matches_company(self, domain: str, company_name: str) -> bool:
        normalized_domain = re.sub(r'[^a-z0-9]', '', domain.lower())
        terms = [
            term for term in re.findall(r'[a-z0-9]+', company_name.lower())
            if len(term) >= 4
        ]
        return bool(terms) and all(term in normalized_domain for term in terms)

    def _find_url_for_domain(self, domain: str, results: list[SearchResult]) -> Optional[str]:
        for r in results:
            if self._root_domain(r.url) == domain:
                return self._normalize_url(r.url)
            for url_match in _URL_PATTERN.findall(r.excerpt):
                if self._root_domain(url_match) == domain:
                    return self._normalize_url(url_match)
        return None


# ── shared helper ────────────────────────────────────────────────────────────
