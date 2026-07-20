"""Default tokenizer (M5.3B-1, development/provider-neutral).

A deterministic word/character estimator — no vendor, no model. Estimates tokens as
`ceil(chars / chars_per_token)` bounded below by the word count, which tracks real
tokenizers closely enough for budgeting without a dependency. A model-exact tokenizer is a
future additive `Tokenizer` adapter (model-aware budgeting is M5.3B-2).
"""

from __future__ import annotations

import math

from ..ports import Tokenizer


class WordTokenizer(Tokenizer):
    name = "word_estimator"

    def __init__(self, *, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be > 0")
        self._cpt = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        by_chars = math.ceil(len(text) / self._cpt)
        by_words = len(text.split())
        return max(by_chars, by_words)
