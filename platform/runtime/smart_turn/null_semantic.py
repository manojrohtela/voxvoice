"""NullSemanticEvaluator — the default (no-op) semantic evaluator (M3.5).

Used when no `SEMANTIC_TURN` provider is registered. Returns the identity hint, so
the Smart Turn engine's decision is purely deterministic/policy-driven. A real
semantic module (EOT classifier, intent model, LLM-backed service) implements
`core.providers.semantic_turn.SemanticTurnEvaluator` and registers via the registry
— no runtime change required (Constitution §A6/§A12).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.providers.semantic_turn import (
    SemanticCapabilities,
    SemanticHint,
    SemanticTurnEvaluator,
)


class NullSemanticEvaluator(SemanticTurnEvaluator):
    name = "null"
    capabilities = SemanticCapabilities()

    def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
        return SemanticHint()  # identity: no influence
