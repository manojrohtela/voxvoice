"""Context sources (M5.3B-1) — the uniform input adapters + selection.

Wrap the injected `MemoryPort`/`KnowledgePort`/`PromptPort` (and any static/immutable
input) into the single `ContextSource` seam the assembler pulls from. The runtime depends
only on the *ports* here — never the `memory`/`knowledge`/`prompt` packages — so the graph
stays acyclic. Future Tool/Workflow/Agent/Federated results land as additional
`ContextSource`s with **no assembler change**.

`SourceSelector` decides which registered sources are active for a request (the naive core
policy: honour `include_memory`/`include_knowledge`; always include the rest, in
registration order). Real source-selection intelligence is M5.3B-2 — this stays a fixed,
deterministic selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from core.ports.knowledge import KnowledgePort, KnowledgeQuery
from core.ports.memory import MemoryPort, MemoryQuery, MemoryScope, MemoryTier
from core.ports.prompt import PromptPort

from .domain import ContextKind, SourceRuntime
from .ports import AssemblyRequest, ContextSource, SourceContribution

# Deterministic MemoryTier → ContextKind mapping for normalization.
_TIER_KIND: Mapping[MemoryTier, ContextKind] = {
    MemoryTier.WORKING: ContextKind.WORKING_MEMORY,
    MemoryTier.CONVERSATION: ContextKind.CONVERSATION_HISTORY,
    MemoryTier.SESSION: ContextKind.CONVERSATION_HISTORY,
    MemoryTier.LONG_TERM: ContextKind.LONG_TERM_MEMORY,
    MemoryTier.SEMANTIC: ContextKind.LONG_TERM_MEMORY,
    MemoryTier.EPISODIC: ContextKind.LONG_TERM_MEMORY,
    MemoryTier.USER: ContextKind.LONG_TERM_MEMORY,
    MemoryTier.ORGANIZATION: ContextKind.LONG_TERM_MEMORY,
}


class MemorySource(ContextSource):
    """Pull ranked memory across tiers via the `MemoryPort` (Memory does its own ranking)."""

    name = "memory"

    def __init__(self, port: MemoryPort) -> None:
        self._port = port

    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]:
        if not request.include_memory:
            return ()
        scope = MemoryScope(
            session_id=request.scope.session_id,
            conversation_id=request.scope.conversation_id,
            user_id=request.scope.user_id,
            org_id=request.scope.org_id,
        )
        query = MemoryQuery(text=request.query, scope=scope, limit=request.limit)
        out: list[SourceContribution] = []
        for hit in self._port.retrieve(query):
            out.append(
                SourceContribution(
                    kind=_TIER_KIND.get(hit.tier, ContextKind.LONG_TERM_MEMORY),
                    text=hit.content,
                    source_runtime=SourceRuntime.MEMORY,
                    source_id=hit.id,
                    score=hit.score,
                    metadata={"tier": hit.tier.value},
                )
            )
        return out


class KnowledgeSource(ContextSource):
    """Pull knowledge passages via the `KnowledgePort` (needs query text)."""

    name = "knowledge"

    def __init__(self, port: KnowledgePort) -> None:
        self._port = port

    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]:
        if not request.include_knowledge or not request.query:
            return ()
        query = KnowledgeQuery(
            text=request.query, org_id=request.scope.org_id, limit=request.limit
        )
        out: list[SourceContribution] = []
        for p in self._port.retrieve(query):
            out.append(
                SourceContribution(
                    kind=ContextKind.KNOWLEDGE_PASSAGE,
                    text=p.text,
                    source_runtime=SourceRuntime.KNOWLEDGE,
                    source_id=p.id,
                    score=p.score,
                    metadata={"document_id": p.source_id},
                )
            )
        return out


@dataclass(frozen=True, slots=True)
class PromptDirective:
    """A template to render as an instruction item (template id + kind + variables)."""

    template_id: str
    kind: ContextKind = ContextKind.SYSTEM_INSTRUCTION
    variables: dict[str, str] = field(default_factory=dict)


class PromptSource(ContextSource):
    """Render configured templates into instruction items via the `PromptPort`."""

    name = "prompt"

    def __init__(self, port: PromptPort, directives: Sequence[PromptDirective] = ()) -> None:
        self._port = port
        self._directives = tuple(directives)

    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]:
        out: list[SourceContribution] = []
        for d in self._directives:
            fragment = self._port.render(d.template_id, d.variables)
            out.append(
                SourceContribution(
                    kind=d.kind,
                    text=fragment.text,
                    source_runtime=SourceRuntime.PROMPT,
                    source_id=f"{fragment.template_id}.v{fragment.version}",
                    score=1.0,
                    confidence=1.0,
                    metadata={"template_id": fragment.template_id},
                )
            )
        return out


class StaticContextSource(ContextSource):
    """A fixed set of contributions supplied as immutable data — the seam for user input,
    system/environment state, and (until their runtimes land) tool/workflow/agent outputs
    the composition root injects. No port, no coupling."""

    def __init__(self, name: str, contributions: Sequence[SourceContribution]) -> None:
        self.name = name
        self._contributions = tuple(contributions)

    def fetch(self, request: AssemblyRequest) -> Sequence[SourceContribution]:
        return self._contributions


class SourceSelector:
    """Selects the active sources for a request. Naive core policy: honour the memory/
    knowledge include flags; always include every other registered source, in registration
    order. Deterministic. (Source-selection intelligence is M5.3B-2.)"""

    def select(
        self, sources: Sequence[ContextSource], request: AssemblyRequest
    ) -> list[ContextSource]:
        active: list[ContextSource] = []
        for s in sources:
            if s.name == "memory" and not request.include_memory:
                continue
            if s.name == "knowledge" and not request.include_knowledge:
                continue
            active.append(s)
        return active
