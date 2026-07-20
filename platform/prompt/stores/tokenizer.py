"""Default tokenizer (M5.4B-1, deterministic/provider-neutral).

A word/character estimator — no vendor, no model. Estimates tokens as
`ceil(chars / chars_per_token)` bounded below by the word count. A model-exact tokenizer is
a future additive `Tokenizer` adapter (model-aware budgeting is M5.4B-2).
"""

from __future__ import annotations

import math

from ..ports import Tokenizer


class WordTokenizer(Tokenizer):
    """Word-count-dominant estimate, bounded below by chars/cpt. Deterministic."""

    name = "word_estimator"

    def __init__(self, *, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be > 0")
        self._cpt = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(math.ceil(len(text) / self._cpt), len(text.split()))


class EstimatedTokenizer(Tokenizer):
    """Character-ratio estimate (`ceil(chars / chars_per_token)`) — a coarser, purely
    character-based estimator. Deterministic; still vendor-neutral. A model-exact tokenizer
    (tiktoken/HF) is a future additive `Tokenizer` adapter."""

    name = "char_estimator"

    def __init__(self, *, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be > 0")
        self._cpt = chars_per_token

    def count(self, text: str) -> int:
        return math.ceil(len(text) / self._cpt) if text else 0
