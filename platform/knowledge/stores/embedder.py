"""Deterministic local embedder (M5.2B-1, development only).

Text → fixed-dimension unit vector with no ML model, no randomness, no network — a
stand-in behind the `Embedder` contract so the semantic path is exercisable offline.
SHA-256 hashing (never salted `hash()`) makes it stable across processes.

A hashing bag-of-words projection: adequate for wiring/tests, not semantic quality. A
production embedder implements the same contract and swaps in via the registry.
Self-contained (Knowledge never imports Memory).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


class DeterministicLocalEmbedder:
    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.name = "deterministic_local"
        self.dimensions = dimensions

    def embed(self, text: str) -> Sequence[float]:
        vec = [0.0] * self.dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return tuple(vec)
        return tuple(v / norm for v in vec)
