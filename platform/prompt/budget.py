"""Prompt Budget Manager (M5.4B-2) — deterministic allocation.

Owns **allocation only** (it never renders, validates, or runs inference). Given the built,
formatted sections + a `ModelProfile` (window + completion reserve), it fits them into the
prompt budget honouring:

- **Global / prompt budget** — `min(policy.global_tokens or ∞, model_window − completion_reserve)`.
- **Completion reserve** — reserved for the model's output; the prompt ceiling excludes it.
- **Reserved tokens** — `reserved_kinds` (system/developer/user) are *always* kept.
- **Per-section budget** — optional per-kind fraction caps (enforced only when `adaptive` is
  off; **adaptive budgeting** lets unused capacity flow, so only the global cap binds).
- **Overflow strategy** — `drop_lowest` / `truncate_lowest` / `reject`.
- **Prompt truncation** — deterministic trimming of the lowest-priority context section.

Deterministic: stable partitions + section-id tie-break, no randomness. Model-aware
budgeting is the injected `ModelProfile` (exact window/counter); the allocation logic is
unchanged. Budget allocation only — never owns inference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain import BudgetUsage, PromptSection, SectionKind
from .policies import OverflowStrategy, PromptBudgetPolicy
from .ports import ModelProfile

# lowest-priority-first order for dropping/truncating context sections
_DROP_ORDER: tuple[SectionKind, ...] = (
    SectionKind.ENVIRONMENT_CONTEXT,
    SectionKind.WORKFLOW_CONTEXT,
    SectionKind.TOOL_CONTEXT,
    SectionKind.KNOWLEDGE_CONTEXT,
    SectionKind.CONVERSATION_CONTEXT,
)


@dataclass(frozen=True, slots=True)
class BudgetStats:
    limit_tokens: int = 0
    used_tokens: int = 0
    reserved_tokens: int = 0
    dropped_sections: int = 0
    truncated_sections: int = 0
    adaptive: bool = False


@dataclass(frozen=True, slots=True)
class BudgetResult:
    sections: tuple[PromptSection, ...]
    usage: BudgetUsage
    truncated: bool
    rejected: bool
    stats: BudgetStats


class PromptBudgetManager:
    def __init__(self, *, policy: PromptBudgetPolicy | None = None) -> None:
        self._p = policy or PromptBudgetPolicy()

    def allocate(
        self,
        sections: Sequence[PromptSection],
        *,
        limit: int,
        reserve: int,
        model: ModelProfile,
    ) -> BudgetResult:
        # global cap = min(policy global, model window − reserve); 0 → unbounded
        window_limit = max(0, model.context_window() - reserve)
        caps = [c for c in (self._p.global_tokens, limit, window_limit) if c > 0]
        budget = min(caps) if caps else 0

        reserved = [s for s in sections if s.kind in self._p.reserved_kinds]
        context = [s for s in sections if s.kind not in self._p.reserved_kinds]

        used = sum(s.token_estimate for s in reserved)
        reserved_tokens = used
        kept_ids = {s.id for s in reserved}
        per_caps = self._section_caps(budget)
        per_used: dict[str, int] = {}
        for s in reserved:
            per_used[s.kind.value] = per_used.get(s.kind.value, 0) + s.token_estimate

        truncated_out: dict[str, PromptSection] = {}
        dropped = 0
        truncated_count = 0
        rejected = False

        for s in context:
            est = s.token_estimate
            over_global = budget > 0 and used + est > budget
            over_section = (
                not self._p.adaptive
                and per_caps.get(s.kind.value) is not None
                and per_used.get(s.kind.value, 0) + est > per_caps[s.kind.value]
            )
            if over_global or over_section:
                if self._p.overflow is OverflowStrategy.REJECT:
                    rejected = True
                    dropped += 1
                    continue
                if self._p.overflow is OverflowStrategy.TRUNCATE_LOWEST and budget > 0:
                    room = max(0, budget - used)
                    if room > 0 and s.content:
                        trimmed = _truncate_section(s, room, model)
                        truncated_out[s.id] = trimmed
                        kept_ids.add(s.id)
                        used += trimmed.token_estimate
                        k = s.kind.value
                        per_used[k] = per_used.get(k, 0) + trimmed.token_estimate
                        truncated_count += 1
                        continue
                dropped += 1
                continue
            kept_ids.add(s.id)
            used += est
            per_used[s.kind.value] = per_used.get(s.kind.value, 0) + est

        # preserve input order; swap in any truncated variants
        kept = tuple(truncated_out.get(s.id, s) for s in sections if s.id in kept_ids)
        truncated_flag = dropped > 0 or truncated_count > 0
        usage = BudgetUsage(
            used_tokens=used, limit_tokens=budget, reserved_tokens=reserved_tokens,
            completion_reserve=reserve,
        )
        stats = BudgetStats(
            limit_tokens=budget, used_tokens=used, reserved_tokens=reserved_tokens,
            dropped_sections=dropped, truncated_sections=truncated_count, adaptive=self._p.adaptive,
        )
        return BudgetResult(kept, usage, truncated_flag, rejected, stats)

    def _section_caps(self, budget: int) -> dict[str, int]:
        return {k: int(budget * frac) for k, frac in self._p.per_section_fraction.items()}


def _truncate_section(
    section: PromptSection, room_tokens: int, model: ModelProfile
) -> PromptSection:
    """Deterministic word-boundary trim of a context section to ~`room_tokens`, marked."""
    words = section.content.split()
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if model.count(" ".join(words[:mid])) <= room_tokens:
            lo = mid
        else:
            hi = mid - 1
    text = (" ".join(words[:lo]) + " …") if lo < len(words) else " ".join(words[:lo])
    return section.with_content(text).with_tokens(model.count(text))


__all__ = ["PromptBudgetManager", "BudgetResult", "BudgetStats"]
