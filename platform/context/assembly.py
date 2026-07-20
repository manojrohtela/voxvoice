"""Context assembler (M5.3B-1) — the pipeline orchestrator.

Owns orchestration only: **source selection → retrieval (delegated to the registered
`ContextSource`s) → merge/normalize/order/budget (via the `ContextBuilder`) → immutable
`ContextPackage`**. It never touches a store or a provider — sources wrap the injected
ports, the builder is pure. Deterministic; the caller supplies the clock + package id.

The M5.3B-2 stages (dedup, validation, ranking, compression, adaptive budget, ordering
intelligence, tenancy) slot into this same flow additively — the core wires the fixed,
deterministic path.
"""

from __future__ import annotations

from collections.abc import Sequence

from .builder import ContextBuilder
from .domain import ContextPackage, ContextScope
from .ports import AssemblyRequest, ContextSource
from .sources import SourceSelector


class ContextAssembler:
    def __init__(
        self,
        *,
        sources: Sequence[ContextSource],
        selector: SourceSelector,
        builder: ContextBuilder,
    ) -> None:
        self._sources = tuple(sources)
        self._selector = selector
        self._builder = builder

    @property
    def sources(self) -> tuple[ContextSource, ...]:
        return self._sources

    async def assemble(
        self,
        request: AssemblyRequest,
        *,
        package_id: str,
        scope: ContextScope,
        now: float,
        budget_tokens: int,
        expires_at: float | None,
    ) -> ContextPackage:
        # 1. source selection (deterministic; honours include flags)
        active = self._selector.select(self._sources, request)
        # 2. retrieval — delegated to each source (which wraps an injected port)
        contributions = []
        for source in active:
            contributions.extend(source.fetch(request))
        # 3. merge → normalize → order → naive budget → immutable package
        return self._builder.build(
            package_id=package_id,
            session_id=request.session_id,
            scope=scope,
            contributions=contributions,
            budget_tokens=budget_tokens,
            now=now,
            query=request.query,
            expires_at=expires_at,
        )
