"""Memory Policy Engine (M5.1B-2) — deterministic, AI-free.

Memory-specific policy value objects (built on the generic `core.policies` where one
fits) + a `PolicyEngine` that turns them into **verdicts** the runtime executes. The
engine performs no I/O and no side effects: it decides, the runtime acts.

Policies: Validation, Privacy, Expiration, Retention, Capacity, Merge, Conflict.
Extension seams: Compression + Summarization are represented as `core.policies`
switches plus injected `Compressor`/`Summarizer` ports (no implementation, no LLM).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.policies import (
    BudgetPolicy,
    CompressionPolicy,
    InvalidPolicy,
    ValidationPolicy,
)

from .domain import MemoryRecord, MemoryTier
from .extensions import Redactor


# ── memory-specific policy value objects ────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ExpirationPolicy:
    """Per-tier time-to-live. `ttl_seconds=None` → never expires."""

    ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.ttl_seconds is not None and self.ttl_seconds <= 0:
            raise InvalidPolicy("ttl_seconds must be > 0 or None")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """A protection floor: records younger than `min_age_seconds` are never evicted,
    and `pinned_tags` are never expired/evicted regardless of age."""

    min_age_seconds: float = 0.0
    pinned_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergePolicy:
    """Deduplicate identical content within the same scope+tier."""

    enabled: bool = True


class ConflictStrategy(StrEnum):
    KEEP_HIGHEST_IMPORTANCE = "keep_highest_importance"
    KEEP_LATEST = "keep_latest"


@dataclass(frozen=True, slots=True)
class ConflictPolicy:
    strategy: ConflictStrategy = ConflictStrategy.KEEP_HIGHEST_IMPORTANCE


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    """Redact content before persistence (via an injected `Redactor`)."""

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class MemoryPolicies:
    """The full deterministic policy bundle for a runtime."""

    validation: ValidationPolicy = field(default_factory=lambda: ValidationPolicy(max_size=100_000))
    privacy: PrivacyPolicy = field(default_factory=PrivacyPolicy)
    expiration: dict[MemoryTier, ExpirationPolicy] = field(default_factory=dict)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    capacity: dict[MemoryTier, BudgetPolicy] = field(default_factory=dict)
    merge: MergePolicy = field(default_factory=MergePolicy)
    conflict: ConflictPolicy = field(default_factory=ConflictPolicy)
    # extension switches (implementations are injected ports; off by default)
    compression: CompressionPolicy = field(default_factory=CompressionPolicy)


# ── verdicts ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ValidationVerdict:
    ok: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MergeVerdict:
    """`merge_into` is the existing record id to fold into, or None for a fresh store.
    `importance` is the resolved importance to apply on a merge."""

    merge_into: str | None = None
    importance: float = 0.0


# ── the engine ──────────────────────────────────────────────────────────────
class PolicyEngine:
    def __init__(self, policies: MemoryPolicies | None = None, *, redactor: Redactor | None = None):
        self._p = policies or MemoryPolicies()
        self._redactor = redactor

    @property
    def policies(self) -> MemoryPolicies:
        return self._p

    # ── store-time policies ──────────────────────────────────────────────────
    def validate(self, content: str) -> ValidationVerdict:
        if self._p.validation.strict and not content.strip():
            return ValidationVerdict(False, "empty content")
        ms = self._p.validation.max_size
        if ms and len(content) > ms:
            return ValidationVerdict(False, f"content exceeds max_size {ms}")
        return ValidationVerdict(True)

    def apply_privacy(self, content: str) -> str:
        if self._p.privacy.enabled and self._redactor is not None:
            return self._redactor.redact(content)
        return content

    def expiry_for(self, tier: MemoryTier, created_at: float) -> float | None:
        pol = self._p.expiration.get(tier)
        if pol is None or pol.ttl_seconds is None:
            return None
        return created_at + pol.ttl_seconds

    def resolve_merge(
        self, candidate: MemoryRecord, existing: list[MemoryRecord]
    ) -> MergeVerdict:
        """If merging is enabled and an identical-content record exists in the same
        scope+tier, fold into it, resolving importance by the conflict strategy."""
        if not self._p.merge.enabled:
            return MergeVerdict(merge_into=None, importance=candidate.metadata.importance)
        norm = candidate.content.strip()
        for r in existing:
            if r.content.strip() == norm and r.tier is candidate.tier:
                if self._p.conflict.strategy is ConflictStrategy.KEEP_HIGHEST_IMPORTANCE:
                    imp = max(r.metadata.importance, candidate.metadata.importance)
                else:  # KEEP_LATEST
                    imp = candidate.metadata.importance
                return MergeVerdict(merge_into=r.id.value, importance=imp)
        return MergeVerdict(merge_into=None, importance=candidate.metadata.importance)

    # ── maintenance policies ─────────────────────────────────────────────────
    def is_expired(self, record: MemoryRecord, now: float) -> bool:
        if self._is_pinned(record):
            return False
        exp = record.metadata.expires_at
        return exp is not None and exp <= now

    def evictions(
        self, records: list[MemoryRecord], tier: MemoryTier, now: float
    ) -> list[MemoryRecord]:
        """Which records to evict to satisfy the tier capacity budget. Evicts the
        lowest-value first (importance asc, then oldest), skipping protected records."""
        budget = self._p.capacity.get(tier)
        if budget is None or budget.unbounded:
            return []
        evictable = [r for r in records if not self._protected(r, now)]
        overflow = len(records) - budget.limit
        if overflow <= 0:
            return []
        evictable.sort(key=lambda r: (r.metadata.importance, r.metadata.created_at, r.id.value))
        return evictable[:overflow]

    # ── protection helpers ───────────────────────────────────────────────────
    def _is_pinned(self, record: MemoryRecord) -> bool:
        pinned = set(self._p.retention.pinned_tags)
        return bool(pinned and pinned.intersection(record.metadata.tags))

    def _protected(self, record: MemoryRecord, now: float) -> bool:
        if self._is_pinned(record):
            return True
        min_age = self._p.retention.min_age_seconds
        return min_age > 0 and (now - record.metadata.created_at) < min_age
