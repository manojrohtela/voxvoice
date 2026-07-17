"""EchoLLM — deterministic in-process LLM for local dev + tests (M2C.2).

Implements the `LLMProvider` contract. Streams a deterministic response derived
from the last user message, token by token — enough to exercise the full
streaming pipeline without a network or credentials. A real LLM vendor is a
drop-in replacement behind the contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from core.providers.llm import LLMCapabilities, LLMProvider, TokenDelta, ToolCallDelta


class EchoLLM(LLMProvider):
    name = "echo"
    capabilities = LLMCapabilities(tools=False, structured_output=False, context_window=8192)

    async def stream(
        self, *, context: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[TokenDelta | ToolCallDelta]:
        user = ""
        for message in reversed(context):
            if message.get("role") == "user":
                user = str(message.get("content", "")).strip()
                break
        response = f"You said: {user}" if user else "Hello, how can I help?"
        for word in response.split(" "):
            yield TokenDelta(text=word + " ")
