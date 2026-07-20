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
from .ids import package_id_for, prompt_hash_for
from .ports import ModelProfile
from .renderer import PromptRenderer
from .request import PromptRequest
from .sections import SectionGenerator

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
    ) -> PromptPackage:
        # 1. template selection + section generation (from the frozen AssembledContext only)
        sections = self._generator.generate(context, request)
        # 2. formatting (deterministic; stable order)
        sections = self._formatter.format(sections)
        # 3. naive budget (truncation only)
        reserve = self._model.completion_reserve()
        window_limit = max(0, self._model.context_window() - reserve)
        limit = budget_tokens if budget_tokens > 0 else window_limit
        kept, used, truncated = self._apply_budget(sections, limit)
        # 4. rendering (provider-neutral messages via the injected ProviderProfile)
        messages = self._renderer.render(kept)

        prompt_hash = prompt_hash_for(tuple(s.id for s in kept))
        package_id = package_id_for(session_id, prompt_hash, seq)
        templates_resolved = {
            s.kind.value: (s.lineage.template_version or "") for s in kept
        }
        return PromptPackage(
            id=package_id,
            session_id=session_id,
            scope=scope,
            state=PackageState.REQUESTED,
            sections=tuple(kept),
            messages=messages,
            generation=request.generation,
            budget=BudgetUsage(
                used_tokens=used, limit_tokens=limit,
                reserved_tokens=sum(s.token_estimate for s in kept if s.kind in _RESERVED),
                completion_reserve=reserve,
            ),
            metadata=PromptMetadata(
                provider=self._renderer.provider, model=self._model.name,
                templates_resolved=templates_resolved,
            ),
            prompt_hash=prompt_hash,
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
