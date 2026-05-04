import re
from typing import Optional
from urllib.parse import urlparse

from company_intel_agent.utils.search import SearchResult


_NAME = r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){1,3}"

_CEO_PATTERNS = [
    re.compile(rf'(?i:CEO)[:\s]+({_NAME})'),
    re.compile(rf'(?i:Chief Executive Officer)[:\s]+({_NAME})'),
    re.compile(rf'(?i:founded by) \[?({_NAME})'),
    re.compile(rf'(?i:founded in \d{{4}} by) \[?({_NAME})'),
    re.compile(rf'({_NAME}),? (?i:CEO|Chief Executive)'),
    re.compile(rf'({_NAME})\s+(?i:entrepreneur,\s+)?(?i:co-founder|founder)\b'),
    re.compile(rf'({_NAME})\s+##\s+(?i:co-founder|founder)\b'),
]

_BAD_NAME_TOKENS = {
    'and', 'or', 'the', 'of', 'for', 'with', 'by', 'at', 'in', 'on',
    'founder', 'cofounder', 'co-founder', 'ceo', 'chief', 'executive',
    'officer', 'company', 'team', 'leadership', 'startup', 'website',
}


def is_plausible_person_name(name: Optional[str]) -> bool:
    if not name:
        return False

    words = name.strip().split()
    if not 2 <= len(words) <= 4:
        return False

    for word in words:
        normalized = word.lower().strip(".,:;()[]{}'\"")
        if "'s" in word.lower() or normalized in _BAD_NAME_TOKENS:
            return False
        if len(word) <= 3 and word.upper() == word:
            return False
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]*", word):
            return False

    return True


def extract_ceo_name_from_results(
    results: list[SearchResult],
    company_name: Optional[str] = None,
) -> Optional[str]:
    candidates: dict[str, tuple[int, int, int, str]] = {}

    for index, r in enumerate(results):
        text = f"{r.title} {r.excerpt}"
        for pattern in _CEO_PATTERNS:
            match = pattern.search(text)
            if match:
                name = match.group(1).strip()
                if is_plausible_person_name(name):
                    key = name.lower()
                    score, count, best_index, display_name = candidates.get(
                        key,
                        (0, 0, index, name),
                    )
                    candidates[key] = (
                        score + _company_match_score(r, company_name) + 1,
                        count + 1,
                        min(best_index, index),
                        display_name,
                    )

    if not candidates:
        return None

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item[0], -item[1], item[2]),
    )
    return ranked[0][3]


def _company_match_score(result: SearchResult, company_name: Optional[str]) -> int:
    if not company_name:
        return 0

    text = f"{result.title} {result.excerpt}".lower()
    domain = urlparse(result.url).netloc.lower().replace('www.', '')
    terms = [
        term for term in re.findall(r'[a-z0-9]+', company_name.lower())
        if len(term) >= 4
    ]

    score = 0
    if company_name.lower() in text:
        score += 4
    for term in terms:
        if term in text:
            score += 2
        if term in re.sub(r'[^a-z0-9]', '', domain):
            score += 3
    return score
