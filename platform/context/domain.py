"""Context domain model (M5.3B-1) — immutable value objects.

The vocabulary the Context Runtime assembles: normalized `ContextItem`s (each carrying
complete provenance) collected into an immutable, budgeted `ContextPackage`, plus the
`ContextWindow` (bounded rolling view) and `ContextSession` (per-conversation working
set) snapshots. Everything is frozen + slotted (deterministic, cheap, replay-friendly).
No behaviour beyond pure derivations; no storage, no ranking, no compression (ranking/
compression/budget intelligence is M5.3B-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class ContextKind(StrEnum):
    """What a context item *is* — its section in the assembled package."""

    CONVERSATION_HISTORY = "conversation_history"
    WORKING_MEMORY = "working_memory"
    LONG_TERM_MEMORY = "long_term_memory"
    KNOWLEDGE_PASSAGE = "knowledge_passage"
    TOOL_RESULT = "tool_result"
    WORKFLOW_RESULT = "workflow_result"
    AGENT_OUTPUT = "agent_output"
    SYSTEM_INSTRUCTION = "system_instruction"
    DEVELOPER_INSTRUCTION = "developer_instruction"
    USER_INSTRUCTION = "user_instruction"
    ENVIRONMENT = "environment"


class SourceRuntime(StrEnum):
    """Which producer an item came from (recorded in provenance, never interpreted)."""

    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    PROMPT = "prompt"
    INPUT = "input"
    TOOL = "tool"
    WORKFLOW = "workflow"
    AGENT = "agent"
    SYSTEM = "system"
    ENVIRONMENT = "environment"


class PackageState(StrEnum):
    REQUESTED = "requested"
    ASSEMBLED = "assembled"
    VALIDATED = "validated"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ContextScope:
    """Ownership/isolation keys carried through assembly (isolation is M5.3B-2; the core
    records the scope so downstream queries stay scoped)."""

    org_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where an item came from — preserved so no downstream runtime re-queries a source.
    `merged_from`/`compressed_from` are populated by the M5.3B-2 dedup/compression
    engines; the core leaves them empty."""

    source_runtime: SourceRuntime
    source_id: str
    original_score: float = 0.0
    content_hash: str = ""
    scope: ContextScope = field(default_factory=ContextScope)
    merged_from: tuple[str, ...] = ()
    compressed_from: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompressionMetadata:
    """Per-item compression record (core never compresses → defaults mean 'untouched')."""

    compressed: bool = False
    method: str | None = None
    original_tokens: int = 0
    final_tokens: int = 0


@dataclass(frozen=True, slots=True)
class BudgetMetadata:
    """Per-item budget record: the token estimate + how it was counted."""

    token_estimate: int = 0
    counted_by: str = "estimator"
    within_budget: bool = True


@dataclass(frozen=True, slots=True)
class RankingBreakdown:
    """The per-signal contribution behind an item's context-adjusted score — the ranking
    lineage. Deterministic + explainable; carried on every item so no downstream runtime
    loses ranking history. `reason` is a short human-readable summary."""

    signals: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One normalized, provenance-complete piece of context. Immutable.

    **Ranking lineage** is preserved end-to-end: `provenance.original_score` (original
    source score), `provenance.source_runtime` (original source runtime), `confidence`
    (original confidence at ingest — never mutated by ranking), `adjusted_score` (the
    context-adjusted score), and `ranking` (the full breakdown + reason)."""

    id: str
    kind: ContextKind
    text: str
    confidence: float
    confidence_source: str  # "memory" | "knowledge" | "prompt" | "input" | "default"
    provenance: Provenance
    order_index: int = 0
    adjusted_score: float = 0.0
    ranking: RankingBreakdown = field(default_factory=RankingBreakdown)
    budget: BudgetMetadata = field(default_factory=BudgetMetadata)
    compression: CompressionMetadata = field(default_factory=CompressionMetadata)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return self.budget.token_estimate

    def with_order(self, order_index: int) -> ContextItem:
        return replace(self, order_index=order_index)

    def with_budget(self, budget: BudgetMetadata) -> ContextItem:
        return replace(self, budget=budget)

    def with_ranking(self, adjusted_score: float, ranking: RankingBreakdown) -> ContextItem:
        return replace(self, adjusted_score=adjusted_score, ranking=ranking)

    def with_merged(self, merged_ids: tuple[str, ...]) -> ContextItem:
        """Record that duplicate items folded into this one (dedup provenance)."""
        merged = replace(self.provenance, merged_from=self.provenance.merged_from + merged_ids)
        return replace(self, provenance=merged)

    def with_compressed_text(
        self, text: str, *, method: str, new_tokens: int, sources: tuple[str, ...] = ()
    ) -> ContextItem:
        """Replace the text with a compressed form, preserving provenance + ranking lineage
        and recording what it was compressed from."""
        comp = CompressionMetadata(
            compressed=True, method=method,
            original_tokens=self.budget.token_estimate, final_tokens=new_tokens,
        )
        prov = replace(
            self.provenance, compressed_from=self.provenance.compressed_from + sources
        )
        return replace(
            self, text=text, compression=comp, provenance=prov,
            budget=replace(self.budget, token_estimate=new_tokens),
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Package-level budget accounting."""

    used_tokens: int = 0
    limit_tokens: int = 0  # 0 → unbounded
    per_source: dict[str, int] = field(default_factory=dict)

    @property
    def within_budget(self) -> bool:
        return self.limit_tokens <= 0 or self.used_tokens <= self.limit_tokens


@dataclass(frozen=True, slots=True)
class ContextMetadata:
    """Package-level descriptive envelope."""

    strategy: str = "insertion_order"
    source_counts: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """The assemble-once artifact: identity + scope + lifecycle state + the ordered,
    provenance-complete items + budget/compression/ordering metadata. Immutable; the raw
    text of every source lives here so consumers never re-query Memory or Knowledge."""

    id: str
    session_id: str
    scope: ContextScope
    state: PackageState
    items: tuple[ContextItem, ...] = ()
    budget: BudgetUsage = field(default_factory=BudgetUsage)
    metadata: ContextMetadata = field(default_factory=ContextMetadata)
    truncated: bool = False
    confidence: float = 1.0
    created_at: float = 0.0
    expires_at: float | None = None
    org_id: str | None = None
    conversation_id: str | None = None
    query: str | None = None

    # ── pure derivations (return a new package; never mutate) ─────────────────
    def with_state(self, state: PackageState) -> ContextPackage:
        return replace(self, state=state)

    def with_items(self, items: tuple[ContextItem, ...]) -> ContextPackage:
        return replace(self, items=items)

    def with_expiry(self, expires_at: float | None) -> ContextPackage:
        return replace(self, expires_at=expires_at)

    def section(self, kind: ContextKind) -> tuple[ContextItem, ...]:
        return tuple(i for i in self.items if i.kind is kind)

    @property
    def used_tokens(self) -> int:
        return self.budget.used_tokens

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class ContextResponse:
    """The rich result the runtime returns from the internal assembly path: the package
    plus a terminal status + reason. `assemble` (the port) narrows the package to a
    frozen `core.ports.AssembledContext`."""

    package: ContextPackage
    delivered: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """A bounded, ordered rolling view of recent conversation items (most-recent last).
    Immutable — `push` returns a new window, evicting the oldest beyond `max_items`."""

    max_items: int = 20
    items: tuple[ContextItem, ...] = ()

    def push(self, *new: ContextItem) -> ContextWindow:
        combined = (*self.items, *new)
        if self.max_items > 0 and len(combined) > self.max_items:
            combined = combined[-self.max_items :]
        return replace(self, items=combined)

    def clear(self) -> ContextWindow:
        return replace(self, items=())


@dataclass(frozen=True, slots=True)
class ContextSession:
    """A per-conversation working set: the rolling window + the ids of packages assembled
    this session (history) + session metadata. Immutable snapshot (`with_*` returns a new
    session); the packages themselves live in the temporary repository, not here."""

    session_id: str
    scope: ContextScope = field(default_factory=ContextScope)
    window: ContextWindow = field(default_factory=ContextWindow)
    package_ids: tuple[str, ...] = ()
    updated_at: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)

    def with_window(self, window: ContextWindow, *, now: float | None = None) -> ContextSession:
        return replace(self, window=window, updated_at=now if now is not None else self.updated_at)

    def record_package(self, package_id: str, *, now: float | None = None) -> ContextSession:
        ids = (*self.package_ids, package_id)
        ts = now if now is not None else self.updated_at
        return replace(self, package_ids=ids, updated_at=ts)
