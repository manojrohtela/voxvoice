"""UserQuerySource (M5.7) — the pipeline's per-request user-input `ContextSource`.

The Context Runtime documents exactly this seam: "the seam for user input... the
composition root injects" (`context/sources.py`'s `StaticContextSource` docstring). A fixed
`StaticContextSource` cannot carry *per-request* text (its contributions are frozen at
construction), so the pipeline instead registers this tiny `ContextSource` **once** at
composition time; each call's text comes from `AssemblyRequest.query` — which the Context
Runtime already threads through from the frozen `core.ports.context.ContextRequest.query`
field (no new field, no redesign; §"Request propagation").
"""

from __future__ import annotations

from collections.abc import Sequence

from context.domain import ContextKind, SourceRuntime
from context.ports import AssemblyRequest, ContextSource, SourceContribution


class UserQuerySource(ContextSource):
    """Projects the per-request `query` into a `USER_INSTRUCTION` context item."""

    name = "pipeline_user_query"

    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]:
        if not request.query:
            return ()
        return (
            SourceContribution(
                kind=ContextKind.USER_INSTRUCTION,
                text=request.query,
                source_runtime=SourceRuntime.INPUT,
                source_id="pipeline:user_query",
                score=1.0,
                confidence=1.0,
            ),
        )
