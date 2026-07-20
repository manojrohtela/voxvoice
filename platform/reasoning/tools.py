"""Tool Decision Coordinator (M5.5B-2) — coordination only.

Coordinates the tool loop: it reads `TOOL_CALL` chunks the model emitted, runs the approval
workflow (a deterministic policy verdict), invokes the tool **through the injected
`core.ports.ToolPort`** (Reasoning never executes a tool — the Tool Runtime does), collects
the typed `ToolResult`, and records the tool history + lineage. Retry/timeout are expressed as
**hooks** (delegated to the `RetryController` / a timeout verdict); no networking, no sandbox,
no execution here.

The coordinator is provider-neutral and deterministic for a given (calls, ToolPort) pair.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ports.tool import ToolCall, ToolPort, ToolResult

from .domain import ResponseChunk, ToolRecord
from .policies import ReasoningPolicyEngine


@dataclass(frozen=True, slots=True)
class ToolCoordination:
    """The deterministic record of one tool-loop pass."""

    results: tuple[ToolResult, ...] = ()
    history: tuple[ToolRecord, ...] = ()
    approved: int = 0
    rejected: int = 0


class ToolDecisionCoordinator:
    """Coordinates tool calls over an injected `ToolPort`. Executes nothing itself."""

    def __init__(
        self, tool_port: ToolPort, *, policy_engine: ReasoningPolicyEngine | None = None
    ) -> None:
        self._tools = tool_port
        self._pe = policy_engine or ReasoningPolicyEngine()

    def parse_calls(self, chunks: tuple[ResponseChunk, ...]) -> tuple[ToolCall, ...]:
        """Extract requested tool calls from the model's `TOOL_CALL` chunks (metadata carries
        `name`/`call_id`; arguments arrive as the chunk delta payload — opaque to Reasoning)."""
        calls: list[ToolCall] = []
        for c in chunks:
            if c.kind.value == "tool_call":
                calls.append(ToolCall(
                    call_id=c.metadata.get("call_id", c.id),
                    name=c.metadata.get("name", ""),
                    arguments={"raw": c.delta},
                ))
        return tuple(calls)

    async def coordinate(
        self, calls: tuple[ToolCall, ...], *, now: float = 0.0
    ) -> ToolCoordination:
        """Approval → invoke (via ToolPort) → collect results, recording history. Deterministic
        for a given (calls, ToolPort)."""
        results: list[ToolResult] = []
        history: list[ToolRecord] = []
        approved = rejected = 0
        for call in calls:
            history.append(ToolRecord(call_id=call.call_id, name=call.name,
                                      decision="requested", at=now))
            verdict = self._pe.admit_tool(call.name)
            if not verdict.ok:
                rejected += 1
                history.append(ToolRecord(call_id=call.call_id, name=call.name,
                                          decision="rejected", ok=False,
                                          detail=verdict.reason, at=now))
                continue
            approved += 1
            history.append(ToolRecord(call_id=call.call_id, name=call.name,
                                      decision="approved", at=now))
            result = await self._tools.invoke(call)  # injected port — Reasoning never executes
            results.append(result)
            history.append(ToolRecord(
                call_id=call.call_id, name=call.name,
                decision="completed" if result.ok else "failed",
                ok=result.ok, detail=result.error or "", at=now,
            ))
        return ToolCoordination(
            results=tuple(results), history=tuple(history),
            approved=approved, rejected=rejected,
        )
