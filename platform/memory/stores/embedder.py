"""Deterministic local embedder (M5.1B-1, development only).

Turns text into a fixed-dimension unit vector with **no ML model, no randomness, and
no network** — a stand-in behind the `Embedder` contract so the semantic path is
exercisable offline. Determinism across processes matters, so tokens are hashed with
`hashlib` (SHA-256), never Python's per-process-salted `hash()`.

It is a hashing bag-of-words projection: adequate for wiring/tests, not for real
semantic quality. A production embedder (a vendor model) implements the same contract
and swaps in via the registry.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


class DeterministicLocalEmbedder:
    """Text → L2-normalized vector of `dimensions` floats, deterministic and pure."""

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
            sign = 1.0 if digest[4] & 1 else -1.0  # stable per-token sign
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return tuple(vec)  # empty/blank text → zero vector (deterministic)
        return tuple(v / norm for v in vec)
