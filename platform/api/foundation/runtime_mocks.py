"""Deterministic mock adapters for the Conversation Playground (M8.13).

Concrete, **deterministic**, **network-free** implementations of the M8.12 runtime adapter
interfaces (`LLMAdapter`/`VoiceAdapter`/`ToolExecutor`/`MemoryAdapter`). They are the *only*
adapters the playground binds — a testing/debugging substrate, never production. **No real
provider, no real LLM API, no real tool execution, no audio synthesis, no HTTP/DB/MCP.**

Every output is a pure function of the input (no wall clock, no randomness), so identical inputs
always yield byte-identical outputs — this is what makes the playground's execution deterministic
and its "adapter consistency" checkable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime_adapters import LLMAdapter, MemoryAdapter, ToolExecutor, VoiceAdapter


def _estimate_tokens(text: str) -> int:
    """A deterministic, provider-free token estimate (~4 chars/token)."""
    return max(1, (len(text) + 3) // 4)


class MockLLMAdapter(LLMAdapter):
    """Returns a deterministic response that echoes resolved-prompt metadata + token/timing
    estimates. Never calls a real provider."""

    provider = "mock"

    def complete(self, prompt: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        tokens = _estimate_tokens(prompt)
        sections = prompt.count("[")  # assembled prompt sections are bracketed, e.g. [SYSTEM]
        return {
            "provider": "mock",
            "model": options.get("model", "mock-model"),
            "text": f"[mock-llm] acknowledged {tokens} tokens across {sections} section(s)",
            "token_estimate": tokens,
            "timing_ms": tokens,  # deterministic pseudo-timing (no wall clock)
            "echo": {"prompt_chars": len(prompt), "sections": sections},
        }


class MockToolExecutor(ToolExecutor):
    """Returns a deterministic fake output for a tool step. No external system is contacted."""

    def invoke(
        self, tool_id: str, *, arguments: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = config or {}
        arguments = arguments or {}
        return {
            "tool_id": tool_id,
            "status": "mock",
            "output": f"mock-output::{tool_id}",
            "echo": {
                "config_keys": sorted(config),
                "argument_keys": sorted(arguments),
            },
        }


class MockVoiceAdapter(VoiceAdapter):
    """Returns synthesized **metadata only** — no audio is generated."""

    def synthesize(self, text: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        return {
            "provider": "mock",
            "voice_name": options.get("voice_name", ""),
            "chars": len(text),
            "estimated_duration_ms": len(text) * 10,  # deterministic
            "audio": None,  # never synthesized
        }

    def transcribe(self, audio: Any, *, options: dict[str, Any] | None = None) -> str:
        # Deterministic stub — the playground never sends real audio.
        return "[mock-transcript]"


class MockMemoryAdapter(MemoryAdapter):
    """A stateless recorder stub. The playground persists turns on the ConversationSession
    itself, so this adapter does not hold authoritative state (keeping the mocks stateless keeps
    the playground deterministic)."""

    def append(self, session_id: str, turn: dict[str, Any]) -> None:
        return None

    def recall(self, session_id: str, *, limit: int = 0) -> list[dict[str, Any]]:
        return []


@dataclass(frozen=True, slots=True)
class MockAdapterSet:
    """The bundle of deterministic mock adapters the playground binds. Stateless + reusable."""

    llm: MockLLMAdapter
    tools: MockToolExecutor
    voice: MockVoiceAdapter
    memory: MockMemoryAdapter

    @classmethod
    def default(cls) -> MockAdapterSet:
        return cls(
            llm=MockLLMAdapter(), tools=MockToolExecutor(),
            voice=MockVoiceAdapter(), memory=MockMemoryAdapter(),
        )

    def describe(self) -> dict[str, str]:
        """A deterministic descriptor of which mock adapters are bound (adapter consistency)."""
        return {
            "llm": "mock", "tool_executor": "mock", "voice": "mock", "memory": "mock",
        }
