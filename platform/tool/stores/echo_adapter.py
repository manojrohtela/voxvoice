"""EchoToolAdapter (M6.1B-1) — the deterministic, offline default `ToolAdapter`.

Purpose: replay, tests, offline execution — validates the whole Tool Runtime
pipeline without any external SDK/service. It **never** calls an API or the
network — it deterministically echoes the validated arguments back as output.
Given the same request it always produces the same output, so orchestration is
byte-for-byte reproducible without a real tool integration.
"""

from __future__ import annotations

from ..domain import ToolRequest, ToolUsage
from ..ports import ToolAdapter, ToolExecutionOutcome


class EchoToolAdapter(ToolAdapter):
    name = "echo"
    capabilities = frozenset({"echo"})

    def execute(self, request: ToolRequest) -> ToolExecutionOutcome:
        output = {"echo": dict(request.arguments)}
        return ToolExecutionOutcome(output=output, usage=ToolUsage(duration_ms=0.0, units=1))
