"""Knowledge Policy Engine (M5.2B-2) — deterministic, AI-free.

Knowledge-specific policy value objects (built on generic `core.policies` where one fits)
+ a `KnowledgePolicyEngine` that turns them into **verdicts** the runtime/engine execute.
The engine performs no I/O and no side effects: it decides, the caller acts.

Policies (all eleven from the milestone): Retention, Version, Validation, Privacy,
Deduplication, Chunk, Embedding, Archive, Deletion, Retrieval, Citation. Privacy +
summarization/compression are represented as switches plus injected extension ports (no
implementation, no LLM here).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.policies import InvalidPolicy, ValidationPolicy

from .domain import Chunk, Document, KnowledgeType
from .extensions import Redactor
from .results import KnowledgeHit


# ── policy value objects ─────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """A protection floor: documents younger than `min_age_seconds`, or carrying a
    `pinned_tag`, are never archived/deleted by maintenance."""

    min_age_seconds: float = 0.0
    pinned_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionPolicy:
    """How many historical versions to keep. `max_versions=0` → keep all."""

    max_versions: int = 0
    supersede_on_update: bool = True

    def __post_init__(self) -> None:
        if self.max_versions < 0:
            raise InvalidPolicy("max_versions must be >= 0")


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    """Redact chunk content (via an injected `Redactor`) before it is returned/persisted."""

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class DeduplicationPolicy:
    """Content-hash dedup of identical chunks/documents (the pipeline already dedups; this
    formalizes the switch)."""

    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ChunkPolicy:
    """Bounds a chunk may not violate (defence against pathological inputs)."""

    max_chunk_chars: int = 100_000
    min_chunk_chars: int = 1


@dataclass(frozen=True, slots=True)
class EmbeddingPolicy:
    """Whether chunks are embedded (semantic index) and the max text embedded."""

    vectorize: bool = True
    max_embed_chars: int = 100_000


@dataclass(frozen=True, slots=True)
class ArchivePolicy:
    """When an updated document's prior version is archived (vs. deleted)."""

    archive_superseded: bool = True


@dataclass(frozen=True, slots=True)
class DeletionPolicy:
    """Hard vs. soft deletion, and whether deletion cascades to chunks/vectors."""

    cascade: bool = True
    hard_delete: bool = True


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """Guards applied to a retrieval result set."""

    max_results: int = 50
    min_score: float = 0.0
    allowed_types: tuple[KnowledgeType, ...] = ()  # empty → all types allowed


@dataclass(frozen=True, slots=True)
class CitationPolicy:
    """A hit must carry citeable provenance to be returned (compliance)."""

    require_source_uri: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgePolicies:
    """The full deterministic policy bundle for a runtime."""

    validation: ValidationPolicy = field(
        default_factory=lambda: ValidationPolicy(max_size=5_000_000)
    )
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    version: VersionPolicy = field(default_factory=VersionPolicy)
    privacy: PrivacyPolicy = field(default_factory=PrivacyPolicy)
    dedup: DeduplicationPolicy = field(default_factory=DeduplicationPolicy)
    chunk: ChunkPolicy = field(default_factory=ChunkPolicy)
    embedding: EmbeddingPolicy = field(default_factory=EmbeddingPolicy)
    archive: ArchivePolicy = field(default_factory=ArchivePolicy)
    deletion: DeletionPolicy = field(default_factory=DeletionPolicy)
    retrieval: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    citation: CitationPolicy = field(default_factory=CitationPolicy)


# ── verdicts ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reason: str = ""


# ── the engine ────────────────────────────────────────────────────────────────
class KnowledgePolicyEngine:
    def __init__(
        self, policies: KnowledgePolicies | None = None, *, redactor: Redactor | None = None
    ) -> None:
        self._p = policies or KnowledgePolicies()
        self._redactor = redactor

    @property
    def policies(self) -> KnowledgePolicies:
        return self._p

    # ── ingestion-time verdicts ──────────────────────────────────────────────
    def validate_document(self, text: str) -> Verdict:
        if self._p.validation.strict and not text.strip():
            return Verdict(False, "empty document content")
        ms = self._p.validation.max_size
        if ms and len(text) > ms:
            return Verdict(False, f"document exceeds max_size {ms}")
        return Verdict(True)

    def validate_chunk(self, chunk: Chunk) -> Verdict:
        n = len(chunk.content)
        if n < self._p.chunk.min_chunk_chars:
            return Verdict(False, "chunk below min size")
        if n > self._p.chunk.max_chunk_chars:
            return Verdict(False, "chunk above max size")
        return Verdict(True)

    def should_embed(self, chunk: Chunk) -> bool:
        e = self._p.embedding
        return e.vectorize and len(chunk.content) <= e.max_embed_chars

    def should_dedup(self) -> bool:
        return self._p.dedup.enabled

    # ── maintenance verdicts ─────────────────────────────────────────────────
    def is_protected(self, document: Document, now: float) -> bool:
        pinned = set(self._p.retention.pinned_tags)
        if pinned and pinned.intersection(document.metadata.tags):
            return True
        min_age = self._p.retention.min_age_seconds
        return min_age > 0 and (now - document.metadata.created_at) < min_age

    def prunable_versions(self, total_versions: int) -> int:
        """How many oldest versions exceed the retention cap (0 → keep all)."""
        cap = self._p.version.max_versions
        if cap <= 0:
            return 0
        return max(0, total_versions - cap)

    def archive_superseded(self) -> bool:
        return self._p.archive.archive_superseded

    def deletion_cascades(self) -> bool:
        return self._p.deletion.cascade

    # ── retrieval-time verdicts ──────────────────────────────────────────────
    def admit_type(self, kt: KnowledgeType) -> bool:
        allowed = self._p.retrieval.allowed_types
        return not allowed or kt in allowed

    def admit_hit(self, hit: KnowledgeHit) -> Verdict:
        """A single hit's admission verdict (score floor, type allow-list, citation)."""
        if hit.score < self._p.retrieval.min_score:
            return Verdict(False, "below min_score")
        if not self.admit_type(hit.knowledge_type):
            return Verdict(False, "knowledge type not allowed")
        if self._p.citation.require_source_uri and not hit.citation.source_uri:
            return Verdict(False, "missing citation source")
        return Verdict(True)

    def cap_results(self, n: int) -> int:
        return min(n, self._p.retrieval.max_results)

    def apply_privacy(self, content: str) -> str:
        if self._p.privacy.enabled and self._redactor is not None:
            return self._redactor.redact(content)
        return content
