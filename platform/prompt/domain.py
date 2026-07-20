"""Prompt domain model (M5.4B-1) — immutable value objects.

The vocabulary the Prompt Runtime constructs: typed `PromptSection`s (each carrying full
`PromptLineage`) assembled into an immutable, self-sufficient `PromptPackage` whose rendered
`PromptMessage`s + carried `GenerationParameters` the future Reasoning Runtime sends to a
model **without reconstructing anything**. Everything is frozen + slotted (deterministic,
cheap, replay-friendly). No behaviour beyond pure derivations; no rendering logic, no
budgeting, no provider specifics (those are the engines/ports).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from .request import GenerationParameters


class PromptRole(StrEnum):
    """The message role a section renders into (provider-neutral)."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"


class SectionKind(StrEnum):
    """What a section *is* — its slot in the constructed prompt."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    CONVERSATION_CONTEXT = "conversation_context"
    KNOWLEDGE_CONTEXT = "knowledge_context"
    TOOL_CONTEXT = "tool_context"
    WORKFLOW_CONTEXT = "workflow_context"
    ENVIRONMENT_CONTEXT = "environment_context"


class PackageState(StrEnum):
    REQUESTED = "requested"
    BUILT = "built"
    VALIDATED = "validated"
    RENDERED = "rendered"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PromptScope:
    """Ownership/isolation keys carried through construction (isolation is M5.4B-2; the
    core records the scope for template resolution + downstream)."""

    org_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromptLineage:
    """Where a rendered section came from — preserved so no downstream runtime loses prompt
    history. `context_sources` are the `AssembledContext` item ids the section was built
    from (with their provenance carried in `context_provenance`)."""

    template_id: str | None = None
    template_version: str | None = None
    context_sources: tuple[str, ...] = ()
    context_provenance: tuple[dict[str, str], ...] = ()
    rendering_version: str = "1"
    section_version: str = "1"
    transformation: str = "render"  # what formatting/rendering was applied
    hash: str = ""


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One typed, lineage-complete piece of the prompt. Immutable."""

    id: str
    role: PromptRole
    kind: SectionKind
    content: str
    lineage: PromptLineage
    token_estimate: int = 0
    version: str = "1"
    metadata: dict[str, str] = field(default_factory=dict)

    def with_tokens(self, token_estimate: int) -> PromptSection:
        return replace(self, token_estimate=token_estimate)

    def with_content(self, content: str) -> PromptSection:
        return replace(self, content=content)


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """A provider-neutral rendered message — the exact shape Reasoning sends to a model."""

    role: PromptRole
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Prompt-level token accounting (naive in the core; intelligence is M5.4B-2)."""

    used_tokens: int = 0
    limit_tokens: int = 0  # 0 → unbounded
    reserved_tokens: int = 0
    completion_reserve: int = 0

    @property
    def within_budget(self) -> bool:
        return self.limit_tokens <= 0 or self.used_tokens <= self.limit_tokens


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    """Package-level descriptive envelope."""

    provider: str = "neutral"
    model: str = "default"
    rendering_version: str = "1"
    templates_resolved: dict[str, str] = field(default_factory=dict)  # kind → template version
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptPackage:
    """The construct-once artifact: identity + lifecycle state + the ordered, lineage-complete
    sections + the rendered provider-neutral messages + carried generation parameters +
    provider/model refs + budget/metadata. Immutable and **self-sufficient** — Reasoning reads
    `messages` + `generation` and calls the model; it never reconstructs a prompt."""

    id: str
    session_id: str
    scope: PromptScope
    state: PackageState
    sections: tuple[PromptSection, ...] = ()
    messages: tuple[PromptMessage, ...] = ()
    generation: GenerationParameters = field(default_factory=GenerationParameters)
    budget: BudgetUsage = field(default_factory=BudgetUsage)
    metadata: PromptMetadata = field(default_factory=PromptMetadata)
    prompt_hash: str = ""
    prompt_version: str = "1"
    truncated: bool = False
    created_at: float = 0.0
    expires_at: float | None = None
    org_id: str | None = None
    conversation_id: str | None = None

    # ── pure derivations (return a new package; never mutate) ─────────────────
    def with_state(self, state: PackageState) -> PromptPackage:
        return replace(self, state=state)

    def with_messages(self, messages: tuple[PromptMessage, ...]) -> PromptPackage:
        return replace(self, messages=messages)

    def with_expiry(self, expires_at: float | None) -> PromptPackage:
        return replace(self, expires_at=expires_at)

    def section(self, kind: SectionKind) -> tuple[PromptSection, ...]:
        return tuple(s for s in self.sections if s.kind is kind)

    @property
    def used_tokens(self) -> int:
        return self.budget.used_tokens

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class PromptResponse:
    """The rich result the runtime returns from the internal build path: the package plus a
    terminal status + reason. `render` (the port) narrows to a frozen `PromptFragment`."""

    package: PromptPackage
    delivered: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PromptStatistics:
    """A point-in-time snapshot: health + counters + temporary-storage size + templates."""

    name: str
    state: str
    healthy: bool
    packages: int = 0
    templates: int = 0
    provider: str = "neutral"
    metrics: dict[str, object] = field(default_factory=dict)
