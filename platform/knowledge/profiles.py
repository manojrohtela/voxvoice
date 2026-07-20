"""Retrieval profiles (M5.2B-2) — immutable, configuration-driven tuning.

A profile is a *named bundle of configuration* that biases the planner (which strategy)
and the ranking engine (signal weights) and caps results — **without any runtime
branching**. The planner and engine read the profile's config; they never special-case a
profile by name. New profiles are additive data, not code.

Profiles:
- **Fast**          — keyword-first, cheap, few results (skip semantic when text is short).
- **Balanced**      — hybrid lexical+semantic, the default.
- **High Precision** — semantic-dominant, high score floor, tight result set.
- **Compliance**    — reference/version-priority dominant, citation required, current-only.
- **Research**      — broad hybrid, freshness+popularity weighted, larger result set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class RetrievalProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH_PRECISION = "high_precision"
    COMPLIANCE = "compliance"
    RESEARCH = "research"


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """The immutable knobs a profile sets. `weights` seed the ranking engine; the planner
    reads `prefer_semantic`/`prefer_keyword` and the caps."""

    weights: Mapping[str, float]
    prefer_semantic: bool = True
    prefer_keyword: bool = True
    min_score: float = 0.0
    max_results: int = 10
    vector_k: int = 100  # semantic candidate breadth
    require_citation: bool = False
    current_version_only: bool = True

    def weight_map(self) -> dict[str, float]:
        return dict(self.weights)


# Signal weights per profile (all >= 0; the ranker weights the named signals). Ordering
# emphasis is what distinguishes the profiles — never a code branch.
_FAST = ProfileConfig(
    weights=MappingProxyType({
        "semantic": 0.0, "keyword": 3.0, "freshness": 1.0, "popularity": 0.5,
        "version_priority": 0.5, "org_priority": 1.0, "document_priority": 0.5,
        "chunk_priority": 0.5,
    }),
    prefer_semantic=False, prefer_keyword=True, min_score=0.0, max_results=5, vector_k=0,
)
_BALANCED = ProfileConfig(
    weights=MappingProxyType({
        "semantic": 3.0, "keyword": 2.0, "freshness": 1.0, "popularity": 0.5,
        "version_priority": 1.0, "org_priority": 1.0, "document_priority": 1.0,
        "chunk_priority": 0.5,
    }),
    prefer_semantic=True, prefer_keyword=True, min_score=0.0, max_results=10, vector_k=100,
)
_HIGH_PRECISION = ProfileConfig(
    weights=MappingProxyType({
        "semantic": 4.0, "keyword": 1.5, "freshness": 0.5, "popularity": 0.25,
        "version_priority": 1.0, "org_priority": 1.0, "document_priority": 2.0,
        "chunk_priority": 0.5,
    }),
    prefer_semantic=True, prefer_keyword=True, min_score=0.15, max_results=5, vector_k=200,
)
_COMPLIANCE = ProfileConfig(
    weights=MappingProxyType({
        "semantic": 2.0, "keyword": 2.0, "freshness": 0.25, "popularity": 0.0,
        "version_priority": 3.0, "org_priority": 3.0, "document_priority": 2.0,
        "chunk_priority": 0.25,
    }),
    prefer_semantic=True, prefer_keyword=True, min_score=0.1, max_results=8, vector_k=100,
    require_citation=True, current_version_only=True,
)
_RESEARCH = ProfileConfig(
    weights=MappingProxyType({
        "semantic": 3.0, "keyword": 2.0, "freshness": 2.0, "popularity": 1.5,
        "version_priority": 0.5, "org_priority": 0.5, "document_priority": 1.0,
        "chunk_priority": 0.5,
    }),
    prefer_semantic=True, prefer_keyword=True, min_score=0.0, max_results=20, vector_k=300,
)

PROFILES: Mapping[RetrievalProfile, ProfileConfig] = MappingProxyType({
    RetrievalProfile.FAST: _FAST,
    RetrievalProfile.BALANCED: _BALANCED,
    RetrievalProfile.HIGH_PRECISION: _HIGH_PRECISION,
    RetrievalProfile.COMPLIANCE: _COMPLIANCE,
    RetrievalProfile.RESEARCH: _RESEARCH,
})


def config_for(profile: RetrievalProfile) -> ProfileConfig:
    return PROFILES[profile]
