"""Semantic turn-evaluation capability contract (Constitution §A6, M3.5).

The **semantic extension interface** for the Smart Turn Intelligence Engine. A
future semantic reasoning module (an EOT classifier, an intent model, an LLM-backed
service) implements this one contract and registers via the Provider Registry
(`CapabilityKind.SEMANTIC_TURN`); the runtime then blends its hint into the
deterministic Smart Turn decision — **no runtime changes required** and full vendor
neutrality preserved.

Neutrality is enforced by the signature: the evaluator receives a plain mapping of
**named float features** (never runtime enums or vendor objects) and returns a
`SemanticHint` of plain floats. The runtime never calls an LLM — it calls this
contract, and whatever the provider does behind it (heuristics, a local model, a
remote LLM) stays behind the contract. `evaluate` is synchronous and must be
side-effect-free and deterministic for a given input (any async/remote reasoning is
computed out-of-band and surfaced as features/hints).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SemanticCapabilities:
    """Declared capability flags for a semantic turn evaluator (discovery only)."""

    end_of_turn: bool = False
    interrupt_intent: bool = False
    languages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SemanticHint:
    """A neutral, additive hint blended into the deterministic decision. All fields
    default to the identity (no influence), so a null/absent evaluator changes
    nothing. Values are plain floats — no vendor types, no runtime enums."""

    end_of_turn_confidence: float = 0.0  # added to the end-of-turn/finish confidence
    interrupt_intent: float = 0.0  # added to the barge-in confirmation confidence
    delay_scale: float = 1.0  # multiplies the adaptive response delay


class SemanticTurnEvaluator(ABC):
    """Provider contract for semantic turn evaluation. Implemented outside the
    runtime and resolved via the registry; the Smart Turn engine calls `evaluate`
    with a neutral feature map and blends the returned hint per policy."""

    name: str
    capabilities: SemanticCapabilities

    @abstractmethod
    def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
        """Return a neutral hint for the given named-float features. Must be
        deterministic and side-effect-free for a given input."""
