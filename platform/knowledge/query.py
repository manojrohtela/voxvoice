"""Knowledge retrieval query (M5.2B-2) — the rich internal request.

The frozen `core.ports.knowledge.KnowledgeQuery` (text/source_ids/org_id/limit/min_score)
is the public port shape; this is the runtime-internal query the planner/engine reason
over. Additive fields only, all with defaults, so it is trivially built from the port
query. Immutable + deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from core.ports.knowledge import KnowledgeQuery as PortQuery

from .domain import KnowledgeScope, KnowledgeType
from .profiles import RetrievalProfile


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalQuery:
    """A fully-specified retrieval request. The runtime builds it from the port query;
    richer callers may construct it directly."""

    text: str = ""
    scope: KnowledgeScope = field(default_factory=KnowledgeScope)
    profile: RetrievalProfile = RetrievalProfile.BALANCED
    source_ids: tuple[str, ...] = ()  # restrict to these document ids (empty → all in scope)
    tags: tuple[str, ...] = ()
    knowledge_types: tuple[KnowledgeType, ...] = ()  # restrict to these types (empty → all)
    metadata_filters: dict[str, str] = field(default_factory=dict)  # exact-match doc metadata
    version: int | None = None  # pin a specific document version (None → current)
    limit: int = 5
    min_score: float = 0.0

    def with_scope(self, scope: KnowledgeScope) -> KnowledgeRetrievalQuery:
        return replace(self, scope=scope)

    @classmethod
    def from_port(
        cls,
        q: PortQuery,
        *,
        scope: KnowledgeScope | None = None,
        profile: RetrievalProfile = RetrievalProfile.BALANCED,
    ) -> KnowledgeRetrievalQuery:
        return cls(
            text=q.text,
            scope=scope or KnowledgeScope(org_id=q.org_id),
            profile=profile,
            source_ids=tuple(q.source_ids),
            limit=q.limit,
            min_score=q.min_score,
        )
