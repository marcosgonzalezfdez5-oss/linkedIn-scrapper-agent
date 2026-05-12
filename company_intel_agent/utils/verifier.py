"""
Shared dataclasses and regex helpers used by the AI verifier agents.

CEOVerifier and WebsiteVerifier have been replaced by
agents/ceo_verifier_agent.py and agents/company_verifier_agent.py.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

_URL_PATTERN = re.compile(r'https?://[^\s,\'"<>()]+')
_LINKEDIN_PROFILE_PATTERN = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)')

_NOISE_DOMAINS = {
    'linkedin.com', 'facebook.com', 'twitter.com', 'x.com', 'instagram.com',
    'youtube.com', 'wikipedia.org', 'google.com', 'glassdoor.com',
    'crunchbase.com', 'bloomberg.com', 'forbes.com', 'techcrunch.com',
    'wsj.com', 'reuters.com', 'businesswire.com', 'prnewswire.com',
}


@dataclass
class VerificationResult:
    name: Optional[str]
    linkedin: Optional[str]
    confidence: str          # "high" | "medium" | "low"
    reasons: list[str] = field(default_factory=list)


@dataclass
class WebsiteResult:
    url: Optional[str]
    confidence: str          # "high" | "medium" | "low"
