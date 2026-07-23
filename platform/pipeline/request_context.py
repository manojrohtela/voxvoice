"""RequestContext (M5.7) — the one propagated context every stage receives.

A single immutable value carrying request id, session id, organization, conversation,
user, a trace id, and free-form metadata through Memory → Knowledge → Context → Prompt →
Reasoning → Provider. No stage constructs its own competing identity fields — the pipeline
derives each stage's own frozen request shape (`MemoryQuery`'s scope, `ContextRequest`,
`PromptRequest`, `InferenceRequest`'s scope, `ProviderRequest`'s scope) from this single
source, so identifiers never drift between stages (no duplication).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable. `trace_id` identifies one pipeline execution (distinct from any single
    stage's own internal ids) — every `PipelineTraceEvent` this run produces carries it."""

    request_id: str
    session_id: str
    trace_id: str
    org_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    query: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
