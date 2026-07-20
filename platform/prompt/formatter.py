"""Prompt formatter (M5.4B-1) — deterministic section formatting + stable ordering.

Normalizes each section's content (trim trailing whitespace, collapse blank-line runs) and
orders the sections by the fixed section sequence (system → developer → conversation →
knowledge → tool → workflow → environment → user). Formatting is pure + deterministic — no
provider specifics (that is the renderer's `ProviderProfile`), no budgeting, no LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .domain import PromptSection, SectionKind
from .sections import section_order

_BLANK_RUN = re.compile(r"\n{3,}")


class PromptFormatter:
    def __init__(self, *, order: Sequence[SectionKind] | None = None) -> None:
        seq = order or section_order()
        self._order = {k: i for i, k in enumerate(seq)}

    def format(self, sections: Sequence[PromptSection]) -> list[PromptSection]:
        formatted = [s.with_content(_normalize(s.content)) for s in sections if s.content.strip()]
        fallback = len(self._order)
        formatted.sort(key=lambda s: (self._order.get(s.kind, fallback), s.id))
        return formatted


def _normalize(text: str) -> str:
    """Deterministic whitespace normalization: strip trailing spaces per line, collapse
    3+ blank lines to one, strip ends."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()
