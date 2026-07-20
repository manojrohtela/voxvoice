"""Prompt builder (M5.4B-1) — the deterministic construction pipeline.

Orchestrates: **section generation → formatting → naive budget → rendering → PromptPackage**.
Section generation consumes only the frozen `AssembledContext`; formatting + rendering are
pure; the budget is **naive truncation** (drop the lowest-priority *context* sections that
overflow the model window − completion reserve; instructions system/developer/user are never
dropped). Intelligent budgeting/validation/policies are M5.4B-2.

Deterministic throughout: content-hash ids, stable ordering, injected clock supplied by the
caller. The builder owns orchestration only — it never touches a store or a provider (the
renderer delegates to the injected `ProviderProfile`; the section generator to the resolver).
"""

from __future__ import annotations

from typing import Any

from core.ports.context import AssembledContext

from .domain import (
    BudgetUsage,
    PackageState,
    PromptMetadata,
    PromptPackage,
    PromptScope,
    PromptSection,
    SectionKind,
)
from .formatter import PromptFormatter
from .ids import content_hash, package_id_for, prompt_fingerprint_for
from .ports import ModelProfile, ProviderProfile
from .renderer import PromptRenderer
from .request import PromptRequest
from .sections import SectionGenerator


def _stamp(section: PromptSection, provider: str, model: str) -> PromptSection:
    """Record the provider/model the section was constructed for (prompt lineage)."""
    from dataclasses import replace

    return replace(section, lineage=section.lineage.with_provider_model(provider, model))

# instruction sections never dropped by the naive budget (reserved)
_RESERVED: frozenset[SectionKind] = frozenset(
    {SectionKind.SYSTEM, SectionKind.DEVELOPER, SectionKind.USER}
)


class PromptBuilder:
    def __init__(
        self,
        *,
        generator: SectionGenerator,
        formatter: PromptFormatter,
        renderer: PromptRenderer,
        model_profile: ModelProfile,
    ) -> None:
        self._generator = generator
        self._formatter = formatter
        self._renderer = renderer
        self._model = model_profile

    def build(
        self,
        request: PromptRequest,
        context: AssembledContext,
        *,
        session_id: str,
        scope: PromptScope,
        now: float,
        seq: int = 0,
        budget_tokens: int = 0,
        expires_at: float | None = None,
        provider_profile: ProviderProfile | None = None,
        model_profile: ModelProfile | None = None,
        budget_manager: Any | None = None,
    ) -> PromptPackage:
        """Deterministic construction. The M5.4B-2 intelligence path passes a resolved
        `provider_profile`/`model_profile` (per request) + a `budget_manager`; when none are
        given the M5.4B-1 defaults + naive truncation apply — the core behaviour is
        unchanged."""
        renderer = (
            PromptRenderer(provider_profile) if provider_profile is not None else self._renderer
        )
        model = model_profile or self._model
        provider_name, model_name = renderer.provider, model.name

        # 1. template selection + section generation (from the frozen AssembledContext only)
        sections = self._generator.generate(context, request)
        # 2. formatting (deterministic; stable order)
        sections = self._formatter.format(sections)
        # stamp provider/model lineage onto every section (prompt history)
        sections = [_stamp(s, provider_name, model_name) for s in sections]
        # 3. budget — intelligent manager if injected, else naive truncation
        reserve = model.completion_reserve()
        window_limit = max(0, model.context_window() - reserve)
        limit = budget_tokens if budget_tokens > 0 else window_limit
        if budget_manager is not None:
            result = budget_manager.allocate(sections, limit=limit, reserve=reserve, model=model)
            kept = list(result.sections)
            used, truncated = result.usage.used_tokens, result.truncated
            usage = result.usage
        else:
            kept, used, truncated = self._apply_budget(sections, limit)
            usage = BudgetUsage(
                used_tokens=used, limit_tokens=limit,
                reserved_tokens=sum(s.token_estimate for s in kept if s.kind in _RESERVED),
                completion_reserve=reserve,
            )
        # 4. rendering (provider-neutral messages via the ProviderProfile)
        messages = renderer.render(kept)

        # provider-sensitive hash + provider-independent fingerprint
        prompt_hash = content_hash(provider_name, model_name, *[s.id for s in kept])
        fingerprint = prompt_fingerprint_for((s.kind.value, s.role.value, s.content) for s in kept)
        package_id = package_id_for(session_id, prompt_hash, seq)
        templates_resolved = {s.kind.value: (s.lineage.template_version or "") for s in kept}
        return PromptPackage(
            id=package_id,
            session_id=session_id,
            scope=scope,
            state=PackageState.REQUESTED,
            sections=tuple(kept),
            messages=messages,
            generation=request.generation,
            budget=usage,
            metadata=PromptMetadata(
                provider=provider_name, model=model_name, templates_resolved=templates_resolved,
            ),
            prompt_hash=prompt_hash,
            prompt_fingerprint=fingerprint,
            truncated=truncated,
            created_at=now,
            expires_at=expires_at,
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
        )

    def _apply_budget(
        self, sections: list[PromptSection], limit: int
    ) -> tuple[list[PromptSection], int, bool]:
        kept: list[PromptSection] = []
        used = 0
        truncated = False
        for s in sections:
            est = s.token_estimate
            if s.kind in _RESERVED:  # instructions are always kept
                kept.append(s)
                used += est
                continue
            if limit > 0 and used + est > limit:  # naive: drop overflowing context sections
                truncated = True
                continue
            kept.append(s)
            used += est
        return kept, used, truncated
