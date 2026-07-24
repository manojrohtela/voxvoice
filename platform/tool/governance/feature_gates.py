"""Feature gates (M6.1B-2) — deterministic, in-memory rollout switches.

No remote config, no polling: a `FeatureGateRegistry` is a plain, caller-supplied
mapping evaluated once per request. `PREVIEW`/`INTERNAL` are opt-in per caller
(`allow_preview`/`allow_internal`) so a gate can ship disabled-by-default for most
callers while a specific caller (e.g. an internal test harness) opts in.
"""

from __future__ import annotations

from collections.abc import Mapping

from .domain import FeatureGateDecision, FeatureGateState


class FeatureGateRegistry:
    def __init__(self, gates: Mapping[str, FeatureGateState] | None = None) -> None:
        self._gates: dict[str, FeatureGateState] = dict(gates or {})

    def set(self, name: str, state: FeatureGateState) -> None:
        self._gates[name] = state

    def evaluate(
        self, name: str, *, allow_preview: bool = False, allow_internal: bool = False
    ) -> FeatureGateDecision:
        state = self._gates.get(name, FeatureGateState.ENABLED)  # ungated -> enabled
        if state is FeatureGateState.ENABLED:
            return FeatureGateDecision(name=name, state=state, allowed=True)
        if state is FeatureGateState.DISABLED:
            return FeatureGateDecision(name=name, state=state, allowed=False,
                                       reason=f"feature {name!r} disabled")
        if state is FeatureGateState.PREVIEW:
            reason = "" if allow_preview else f"feature {name!r} preview-gated"
            return FeatureGateDecision(name=name, state=state, allowed=allow_preview, reason=reason)
        # INTERNAL
        reason = "" if allow_internal else f"feature {name!r} internal-only"
        return FeatureGateDecision(name=name, state=state, allowed=allow_internal, reason=reason)
