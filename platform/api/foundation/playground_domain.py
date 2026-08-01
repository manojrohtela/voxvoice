"""Conversation Playground domain value objects (M8.13) — additive.

A `ConversationTurn` is one debugging round-trip in the playground: a user input, the (mock)
adapter outputs it produced, plus token/timing/diagnostics. Turns are appended to the reused
`ConversationSession`'s `ConversationState.history` (no new persistence) — so a session's turns
persist as part of its state JSON. Frozen, slotted value object with round-trip serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One playground turn. `llm_output`/`tool_invocations`/`voice_output` are the deterministic
    **mock** adapter outputs — no real provider, no real execution, no audio."""

    index: int
    user_input: str
    llm_output: dict[str, Any] = field(default_factory=dict)
    tool_invocations: tuple[dict[str, Any], ...] = ()
    voice_output: dict[str, Any] | None = None
    token_estimate: int = 0
    timing: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    created_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user_input": self.user_input,
            "llm_output": self.llm_output,
            "tool_invocations": list(self.tool_invocations),
            "voice_output": self.voice_output,
            "token_estimate": self.token_estimate,
            "timing": self.timing,
            "diagnostics": list(self.diagnostics),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConversationTurn:
        return cls(
            index=d["index"], user_input=d.get("user_input", ""),
            llm_output=dict(d.get("llm_output", {})),
            tool_invocations=tuple(d.get("tool_invocations", ())),
            voice_output=d.get("voice_output"),
            token_estimate=int(d.get("token_estimate", 0)),
            timing=dict(d.get("timing", {})),
            diagnostics=tuple(d.get("diagnostics", ())),
            created_at=d.get("created_at", 0.0),
        )

    def messages(self) -> list[dict[str, Any]]:
        """The two chat messages this turn represents (user prompt + assistant mock reply)."""
        return [
            {"role": "user", "content": self.user_input, "turn": self.index},
            {
                "role": "assistant",
                "content": self.llm_output.get("text", ""),
                "turn": self.index,
            },
        ]
