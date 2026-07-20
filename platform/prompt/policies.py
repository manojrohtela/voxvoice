"""Prompt Policy Engine (M5.4B-2) — deterministic, AI-free.

Prompt-specific policy value objects + a `PromptPolicyEngine` that turns them into
**verdicts** the runtime/engines execute. The engine performs no I/O and no side effects: it
decides, the caller acts.

Policies (all seven from the milestone): Budget, Rendering, Provider, Validation, Citation,
Safety, Template. The `PromptPolicies` bundle is the single config the factory threads into
the engines; each engine reads its own sub-policy so the surface stays in one place and is
validated once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.policies import InvalidPolicy

from .domain import SectionKind

# instruction sections never dropped by budgeting (reserved)
RESERVED_KINDS: frozenset[SectionKind] = frozenset(
    {SectionKind.SYSTEM, SectionKind.DEVELOPER, SectionKind.USER}
)

# sections requiring a citeable context source (citation policy)
CITED_KINDS: frozenset[SectionKind] = frozenset(
    {SectionKind.KNOWLEDGE_CONTEXT, SectionKind.TOOL_CONTEXT, SectionKind.WORKFLOW_CONTEXT}
)


class OverflowStrategy(StrEnum):
    DROP_LOWEST = "drop_lowest"
    TRUNCATE_LOWEST = "truncate_lowest"
    REJECT = "reject"


# ── policy value objects ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class PromptBudgetPolicy:
    """Global/prompt token budgeting to the model window − completion reserve."""

    global_tokens: int = 0  # 0 → use the model window − reserve
    per_section_fraction: dict[str, float] = field(default_factory=dict)  # kind → [0,1] cap
    reserved_kinds: frozenset[SectionKind] = RESERVED_KINDS
    overflow: OverflowStrategy = OverflowStrategy.DROP_LOWEST
    adaptive: bool = True

    def __post_init__(self) -> None:
        if self.global_tokens < 0:
            raise InvalidPolicy("global_tokens must be >= 0")
        for k, v in self.per_section_fraction.items():
            if not (0.0 <= v <= 1.0):
                raise InvalidPolicy(f"per_section_fraction[{k}] must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class PromptRenderingPolicy:
    rendering_version: str = "1"
    drop_empty_sections: bool = True


@dataclass(frozen=True, slots=True)
class PromptProviderPolicy:
    """Which provider profiles are allowed (empty → all)."""

    allowed_providers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PromptValidationPolicy:
    require_non_empty: bool = True
    enforce_budget: bool = True
    enforce_roles: bool = True
    enforce_placeholders: bool = True


@dataclass(frozen=True, slots=True)
class PromptCitationPolicy:
    require_source_for_cited_kinds: bool = False


@dataclass(frozen=True, slots=True)
class PromptSafetyPolicy:
    enabled: bool = False  # a SafetyValidator hook gates content when on (interface only)


@dataclass(frozen=True, slots=True)
class PromptTemplatePolicy:
    """Allowed template levels / names (empty → all). Guards template resolution."""

    allowed_names: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PromptPolicies:
    """The full deterministic policy bundle for a runtime."""

    budget: PromptBudgetPolicy = field(default_factory=PromptBudgetPolicy)
    rendering: PromptRenderingPolicy = field(default_factory=PromptRenderingPolicy)
    provider: PromptProviderPolicy = field(default_factory=PromptProviderPolicy)
    validation: PromptValidationPolicy = field(default_factory=PromptValidationPolicy)
    citation: PromptCitationPolicy = field(default_factory=PromptCitationPolicy)
    safety: PromptSafetyPolicy = field(default_factory=PromptSafetyPolicy)
    template: PromptTemplatePolicy = field(default_factory=PromptTemplatePolicy)


# ── verdicts ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reason: str = ""


# ── the engine ────────────────────────────────────────────────────────────────
class PromptPolicyEngine:
    """Holds the policy bundle and produces **verdicts** (no I/O, no side effects). The
    budget engine reads `budget`; the validation engine calls the verdict helpers here."""

    def __init__(self, policies: PromptPolicies | None = None) -> None:
        self._p = policies or PromptPolicies()

    @property
    def policies(self) -> PromptPolicies:
        return self._p

    def admit_provider(self, name: str) -> Verdict:
        allowed = self._p.provider.allowed_providers
        if allowed and name not in allowed:
            return Verdict(False, f"provider {name!r} not allowed")
        return Verdict(True)

    def admit_template(self, name: str) -> Verdict:
        allowed = self._p.template.allowed_names
        if allowed and name not in allowed:
            return Verdict(False, f"template {name!r} not allowed")
        return Verdict(True)

    def budget_ok(self, used_tokens: int, limit_tokens: int) -> Verdict:
        if limit_tokens > 0 and used_tokens > limit_tokens:
            return Verdict(False, f"used {used_tokens} exceeds budget {limit_tokens}")
        return Verdict(True)

    def citation_required(self) -> bool:
        return self._p.citation.require_source_for_cited_kinds

    def safety_enabled(self) -> bool:
        return self._p.safety.enabled
