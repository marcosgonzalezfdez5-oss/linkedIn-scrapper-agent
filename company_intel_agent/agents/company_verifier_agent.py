"""
AI-powered company verifier agent.

Replaces the old heuristic WebsiteVerifier. The LLM receives the company name,
optional candidate website, and optional raw actor data. It uses search_web to
find or confirm the official website and LinkedIn company page, then returns a
CompanyVerifierResult.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from company_intel_agent.utils.llm_client import LLMClient
from company_intel_agent.utils.search import ParallelSearchClient, SearchResult
from company_intel_agent.utils.verifier import WebsiteResult
from company_intel_agent.utils.logger import get_logger

logger = get_logger("CompanyVerifierAgent")

_MAX_ITERATIONS = 3

_SYSTEM_PROMPT = """\
You are a company intelligence verification agent. Your job is to find and confirm \
the official website and LinkedIn company page for a target company.

You will be given:
- The company name
- An optional candidate website URL (may be wrong or missing)
- Optional raw data scraped from LinkedIn by an actor (may be incomplete)

Use the search_web tool to gather evidence. Prefer results that come directly from \
the company's own domain or from their official LinkedIn page.

After gathering enough evidence, respond ONLY with a JSON object (no markdown, no explanation):
{
  "website": "<confirmed website URL or null>",
  "website_confidence": "high" | "medium" | "low",
  "linkedin": "<official LinkedIn company page URL or null>"
}

Confidence rules:
- "high": website confirmed by 2+ independent sources
- "medium": website found in 1 source or inferred from LinkedIn page
- "low": best guess, unconfirmed
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information about a company.",
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
]


@dataclass
class CompanyVerifierResult:
    website: Optional[str]
    website_confidence: str   # "high" | "medium" | "low"
    linkedin: Optional[str]


class CompanyVerifierAgent:
    def __init__(self, llm: LLMClient, search: ParallelSearchClient):
        self._llm = llm
        self._search = search

    async def verify(
        self,
        company_name: str,
        candidate_website: Optional[str],
        actor_data: Optional[dict] = None,
    ) -> CompanyVerifierResult:
        logger.info(
            f"AI company verification: company='{company_name}' "
            f"candidate_website={candidate_website}"
        )

        user_content = self._build_user_message(company_name, candidate_website, actor_data)
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
                    result = await self._dispatch(tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    })
                logger.info(f"[iter {iteration+1}] called search_web")
            else:
                parsed = self._parse_result(response.content or "")
                if parsed:
                    logger.info(
                        f"[CompanyVerifier] website='{parsed.website}' [{parsed.website_confidence}] "
                        f"linkedin={parsed.linkedin}"
                    )
                    return parsed
                logger.warning("CompanyVerifierAgent: could not parse LLM response, returning low-confidence result")
                break

        return CompanyVerifierResult(
            website=candidate_website,
            website_confidence="low",
            linkedin=None,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_user_message(
        self,
        company_name: str,
        candidate_website: Optional[str],
        actor_data: Optional[dict],
    ) -> str:
        parts = [
            f"Company name: {company_name}",
            f"Candidate website: {candidate_website or 'not provided'}",
        ]
        if actor_data:
            relevant = {k: actor_data.get(k) for k in (
                "name", "companyName", "company_name",
                "website", "companyWebsite", "websiteUrl",
                "linkedin", "linkedinUrl", "url",
                "description", "about",
            ) if actor_data.get(k)}
            if relevant:
                parts.append(f"Actor-scraped data: {json.dumps(relevant, ensure_ascii=False)}")
        return "\n".join(parts)

    async def _dispatch(self, args: dict) -> str:
        queries = args.get("queries", [])
        objective = args.get("objective", "")
        results: list[SearchResult] = await asyncio.to_thread(
            self._search.search, objective=objective, queries=queries
        )
        return "\n".join(
            f"[{r.url}] {r.title} — {r.excerpt[:200]}" for r in results
        ) or "No results found."

    def _parse_result(self, content: str) -> Optional[CompanyVerifierResult]:
        try:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            return CompanyVerifierResult(
                website=data.get("website"),
                website_confidence=data.get("website_confidence", "low"),
                linkedin=data.get("linkedin"),
            )
        except (json.JSONDecodeError, KeyError):
            return None
