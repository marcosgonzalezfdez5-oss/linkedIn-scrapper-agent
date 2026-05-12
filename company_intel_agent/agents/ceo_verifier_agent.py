"""
AI-powered CEO verifier agent.

Replaces the old heuristic CEOVerifier. The LLM receives the candidate name,
company, optional LinkedIn URL, and optional raw actor data as context. It can
call search_web and search_news tools to corroborate identity and disambiguate
same-name cases, then returns a structured VerificationResult.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from company_intel_agent.utils.llm_client import LLMClient
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.verifier import VerificationResult
from company_intel_agent.utils.names import is_plausible_person_name
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CEOVerifierAgent")

_LINKEDIN_PROFILE_PATTERN = re.compile(
    r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/\s,\'"<>()]+)'
)

_SYSTEM_PROMPT = """\
You are a CEO identity verification agent. Your job is to verify whether a given person \
is the current CEO (or equivalent top executive) of a target company, and to find or correct \
their LinkedIn profile URL.

You will be given:
- The candidate CEO name
- The target company name
- An optional LinkedIn URL (may be wrong or missing)
- Optional raw data scraped from LinkedIn by an actor (may contain the wrong person)

Use the search tools to gather evidence. Watch carefully for same-name disambiguation — \
if multiple people share the name, confirm which one is actually at the target company.

After gathering enough evidence, respond ONLY with a JSON object (no markdown, no explanation):
{
  "name": "<corrected full name or null>",
  "linkedin": "<confirmed LinkedIn URL or null>",
  "confidence": "high" | "medium" | "low",
  "reasons": ["<reason 1>", "<reason 2>"]
}

Confidence rules:
- "high": name confirmed in 2+ independent sources AND LinkedIn slug matches name
- "medium": name confirmed in 1 source OR LinkedIn found but unverified
- "low": name unconfirmed or likely wrong person
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information about a person or company.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of search queries (1-3).",
                    },
                    "objective": {
                        "type": "string",
                        "description": "What you are trying to find.",
                    },
                },
                "required": ["queries", "objective"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Search recent news articles about a person or company.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of news search queries (1-3).",
                    },
                    "objective": {
                        "type": "string",
                        "description": "What you are trying to find.",
                    },
                },
                "required": ["queries", "objective"],
            },
        },
    },
]

_MAX_ITERATIONS = 3


class CEOVerifierAgent:
    def __init__(self, llm: LLMClient, search: ParallelSearchClient):
        self._llm = llm
        self._search = search

    async def verify(
        self,
        ceo_name: str,
        company_name: str,
        linkedin_url: Optional[str],
        actor_data: Optional[dict] = None,
    ) -> VerificationResult:
        logger.info(f"AI CEO verification: name='{ceo_name}' company='{company_name}' linkedin={linkedin_url}")

        user_content = self._build_user_message(ceo_name, company_name, linkedin_url, actor_data)
        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        for iteration in range(_MAX_ITERATIONS):
            response = await self._llm.chat(messages, _TOOLS)

            if response.tool_calls:
                messages.append({"role": "assistant", "content": response.content or "", "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in response.tool_calls
                ]})
                for tc in response.tool_calls:
                    result = await self._dispatch(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    })
                logger.info(f"[iter {iteration+1}] called {[tc.name for tc in response.tool_calls]}")
            else:
                parsed = self._parse_result(response.content or "")
                if parsed:
                    for r in parsed.reasons:
                        logger.info(f"[CEOVerifier] {r}")
                    logger.info(
                        f"[CEOVerifier] final — name='{parsed.name}' "
                        f"linkedin={parsed.linkedin} confidence={parsed.confidence}"
                    )
                    return parsed
                # LLM returned something unparseable — stop and fall back
                logger.warning("CEOVerifierAgent: could not parse LLM response, returning low-confidence result")
                break

        return VerificationResult(
            name=ceo_name if is_plausible_person_name(ceo_name) else None,
            linkedin=linkedin_url,
            confidence="low",
            reasons=["Verification incomplete after max iterations"],
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_user_message(
        self,
        ceo_name: str,
        company_name: str,
        linkedin_url: Optional[str],
        actor_data: Optional[dict],
    ) -> str:
        parts = [
            f"Candidate CEO name: {ceo_name}",
            f"Target company: {company_name}",
            f"LinkedIn URL: {linkedin_url or 'not provided'}",
        ]
        if actor_data:
            relevant = {k: actor_data.get(k) for k in (
                "fullName", "name", "full_name",
                "currentCompany", "current_company", "company",
                "currentTitle", "headline", "title",
                "summary", "about",
            ) if actor_data.get(k)}
            if relevant:
                parts.append(f"Actor-scraped data: {json.dumps(relevant, ensure_ascii=False)}")
        return "\n".join(parts)

    async def _dispatch(self, name: str, args: dict) -> str:
        queries = args.get("queries", [])
        objective = args.get("objective", "")
        results: list[SearchResult] = await asyncio.to_thread(
            self._search.search, objective=objective, queries=queries
        )
        formatted = "\n".join(
            f"[{r.url}] {r.title} — {r.excerpt[:200]}" for r in results
        )
        slug_notes = []
        for r in results:
            if 'linkedin.com/in/' in r.url:
                slug = urlparse(r.url).path.strip('/').split('/')[-1].lower()
                parts_name = [t.lower() for t in (args.get("queries", [""])[0]).split() if len(t) > 1]
                match_info = f"slug='{slug}'"
                slug_notes.append(f"{r.url} → {match_info}")
        if slug_notes:
            formatted += "\n\nLinkedIn slugs found:\n" + "\n".join(slug_notes)
        return formatted or "No results found."

    def _parse_result(self, content: str) -> Optional[VerificationResult]:
        try:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            return VerificationResult(
                name=data.get("name"),
                linkedin=data.get("linkedin"),
                confidence=data.get("confidence", "low"),
                reasons=data.get("reasons", []),
            )
        except (json.JSONDecodeError, KeyError):
            return None
