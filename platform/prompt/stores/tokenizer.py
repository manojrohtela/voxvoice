"""Default tokenizer (M5.4B-1, deterministic/provider-neutral).

A word/character estimator — no vendor, no model. Estimates tokens as
`ceil(chars / chars_per_token)` bounded below by the word count. A model-exact tokenizer is
a future additive `Tokenizer` adapter (model-aware budgeting is M5.4B-2).
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
        return max(math.ceil(len(text) / self._cpt), len(text.split()))
