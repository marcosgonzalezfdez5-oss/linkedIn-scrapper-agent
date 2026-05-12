"""
Provider-agnostic LLM adapter.

Set LLM_PROVIDER in key.env to "gemini" or "azure".
Each concrete client exposes a single async `chat` method that accepts
OpenAI-style messages + tool definitions and returns an LLMResponse.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from company_intel_agent.config import settings
from company_intel_agent.utils.logger import get_logger

logger = get_logger("LLMClient")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        ...


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiLLMClient(LLMClient):
    def __init__(self):
        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=settings.LLM_API_KEY)
        model_name = settings.LLM_MODEL or "gemini-1.5-flash"
        self._model_name = model_name
        self._genai = genai

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        return await asyncio.to_thread(self._chat_sync, messages, tools)

    def _chat_sync(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        from google.generativeai.types import content_types  # type: ignore

        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            tools=self._convert_tools(tools) if tools else None,
        )

        # Extract system instruction from messages
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        history_messages = [m for m in messages if m["role"] != "system"]

        if system_parts:
            model = self._genai.GenerativeModel(
                model_name=self._model_name,
                tools=self._convert_tools(tools) if tools else None,
                system_instruction="\n".join(system_parts),
            )

        # Convert to Gemini content format
        gemini_history = []
        last_user = None
        for msg in history_messages:
            role = "user" if msg["role"] == "user" else "model"
            if role == "user":
                last_user = {"role": "user", "parts": [msg["content"]]}
                gemini_history.append(last_user)
            elif role == "model":
                gemini_history.append({"role": "model", "parts": [msg["content"]]})
            elif msg["role"] == "tool":
                # Inject tool result as a user-role function response
                gemini_history.append({
                    "role": "user",
                    "parts": [{"function_response": {"name": msg.get("name", "tool"), "response": {"result": msg["content"]}}}],
                })

        if not gemini_history:
            gemini_history = [{"role": "user", "parts": ["Start."]}]

        # Use the last user message as the prompt, history as prior context
        chat = model.start_chat(history=gemini_history[:-1] if len(gemini_history) > 1 else [])
        last_content = gemini_history[-1]["parts"][0] if gemini_history else ""
        response = chat.send_message(last_content)

        return self._parse_response(response)

    def _parse_response(self, response) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        id=fc.name,
                        name=fc.name,
                        arguments=dict(fc.args),
                    ))
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

        return LLMResponse(
            content="\n".join(text_parts) or None,
            tool_calls=tool_calls,
        )

    def _convert_tools(self, tools: list[dict]) -> list:
        """Convert OpenAI-style tool defs to Gemini function declarations."""
        from google.generativeai import protos  # type: ignore

        declarations = []
        for t in tools:
            fn = t.get("function", t)
            params = fn.get("parameters", {})
            declarations.append(self._genai.protos.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=self._genai.protos.Schema(
                    type=self._genai.protos.Type.OBJECT,
                    properties={
                        k: self._genai.protos.Schema(
                            type=self._gemini_type(v.get("type", "string")),
                            description=v.get("description", ""),
                            items=(
                                self._genai.protos.Schema(type=self._gemini_type(v["items"]["type"]))
                                if v.get("type") == "array" and "items" in v else None
                            ),
                        )
                        for k, v in params.get("properties", {}).items()
                    },
                    required=params.get("required", []),
                ),
            ))
        return [self._genai.protos.Tool(function_declarations=declarations)]

    def _gemini_type(self, t: str):
        mapping = {
            "string": self._genai.protos.Type.STRING,
            "number": self._genai.protos.Type.NUMBER,
            "integer": self._genai.protos.Type.INTEGER,
            "boolean": self._genai.protos.Type.BOOLEAN,
            "array": self._genai.protos.Type.ARRAY,
            "object": self._genai.protos.Type.OBJECT,
        }
        return mapping.get(t, self._genai.protos.Type.STRING)


# ── Azure OpenAI ──────────────────────────────────────────────────────────────

class AzureLLMClient(LLMClient):
    def __init__(self):
        from openai import AsyncAzureOpenAI  # type: ignore

        self._client = AsyncAzureOpenAI(
            api_key=settings.LLM_API_KEY,
            azure_endpoint=settings.LLM_ENDPOINT,
            api_version="2024-02-01",
        )
        self._model = settings.LLM_MODEL or "gpt-4o"

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return LLMResponse(content=msg.content, tool_calls=tool_calls)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "gemini":
        logger.info("LLM provider: Gemini")
        return GeminiLLMClient()
    if provider == "azure":
        logger.info("LLM provider: Azure OpenAI")
        return AzureLLMClient()
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}' — expected 'gemini' or 'azure'")
