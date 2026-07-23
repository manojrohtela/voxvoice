"""Unified Runtime Lineage (M5.7) — merges every stage's already-produced lineage.

**No lineage is recomputed or redesigned here.** Memory and Knowledge don't carry a
first-class "lineage" object of their own, so their contribution is the provenance the
Context Runtime already records for every item it pulled from them (`Provenance.source_id`
+ `content_hash` + `source_runtime` — exactly what "no downstream runtime re-queries a
source" means). Prompt's `prompt_hash`/`prompt_fingerprint`, Reasoning's full
`ReasoningLineage`, and Provider's `ProviderMetadata` + `ProviderExecutionTrace` are carried
through **unmodified, embedded as-is** — this module only assembles the container.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from provider.domain import ProviderExecutionTrace, ProviderMetadata
from reasoning.domain import ReasoningLineage


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """One Memory/Knowledge item's provenance, as already recorded by the Context Runtime —
    carried through unchanged, never re-derived."""

    source_runtime: str
    source_id: str
    content_hash: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class PipelineLineage:
    """The unified, replay-safe lineage container. Every field is either a plain summary of
    an upstream artifact or the upstream artifact itself, embedded verbatim."""

    memory_provenance: tuple[SourceProvenance, ...] = ()
    knowledge_provenance: tuple[SourceProvenance, ...] = ()
    context_package_id: str = ""
    context_used_tokens: int = 0
    prompt_hash: str = ""
    prompt_fingerprint: str = ""
    reasoning_lineage: ReasoningLineage | None = None  # embedded verbatim, never re-derived
    provider_metadata: ProviderMetadata | None = None  # embedded verbatim
    provider_trace: ProviderExecutionTrace | None = None  # embedded verbatim
    extra: dict[str, str] = field(default_factory=dict)
