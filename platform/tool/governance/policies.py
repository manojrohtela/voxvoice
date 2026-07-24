"""ToolPolicyEngine (M6.1B-2) — deterministic, AI-free policy verdicts.

Mirrors `provider.policies.ProviderPolicyEngine`'s "verdicts only, no I/O, no side
effects" shape, generalized to the six-level precedence chain the M6.1B-2 prompt
specifies. Implements the frozen `tool.extensions.PolicyEngine` seam directly, so a
`ToolRuntime` (a future, separate wiring milestone) can inject this without any
change to `runtime.py`.

**Deterministic conflict resolution** (documented once, here, per the prompt's
explicit requirement):

1. A `DENY` at *any* precedence level is absolute — it can never be overridden by
   an `ALLOW`/`PENDING` at a more specific level. Fail-safe: a broad system-wide
   restriction always holds.
2. Absent any `DENY`, the *most specific applicable rule* wins — precedence runs
   SYSTEM → ORGANIZATION → WORKSPACE → AGENT → SKILL → TOOL, and a later
   (narrower) match overrides an earlier (broader) one exactly like Python
   `dict` update semantics: last-applicable-write-wins.
3. Absent any applicable rule at any level, the default verdict is `ALLOW` —
   open-unless-restricted, the same convention `ProviderAccessPolicy`'s empty
   allow-list already uses ("empty -> all").
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import PlatformError

from ..domain import ToolRequest
from ..extensions import PolicyEngine, PolicyVerdict
from .domain import POLICY_PRECEDENCE, PolicyDecision, PolicyLevel, PolicyRule


class InvalidPolicy(PlatformError):
    """A governance policy rule was constructed with an invalid combination."""


@dataclass(frozen=True, slots=True)
class ToolPolicies:
    """The full deterministic rule set for one `ToolPolicyEngine`."""

    rules: tuple[PolicyRule, ...] = ()

    def __post_init__(self) -> None:
        for r in self.rules:
            if r.level not in POLICY_PRECEDENCE:
                raise InvalidPolicy(f"unknown policy level {r.level!r}")

    def for_level(self, level: PolicyLevel) -> tuple[PolicyRule, ...]:
        return tuple(r for r in self.rules if r.level is level)


class ToolPolicyEngine(PolicyEngine):
    """Holds a `ToolPolicies` bundle and produces `PolicyDecision`s. The richer
    `evaluate()` returns the full decision + trail; `authorize()` (the frozen
    `PolicyEngine` ABC method) returns just the verdict."""

    def __init__(self, policies: ToolPolicies | None = None) -> None:
        self._p = policies or ToolPolicies()

    @property
    def policies(self) -> ToolPolicies:
        return self._p

    def authorize(self, request: ToolRequest) -> PolicyVerdict:
        return self.evaluate(request).verdict

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        qualified = f"{request.namespace}.{request.tool}"
        trail: list[PolicyRule] = []
        winner: PolicyRule | None = None
        deny_hit: PolicyRule | None = None

        for level in POLICY_PRECEDENCE:
            for rule in self._p.for_level(level):
                if rule.tool is not None and rule.tool != qualified:
                    continue
                trail.append(rule)
                if rule.verdict is PolicyVerdict.DENY and deny_hit is None:
                    deny_hit = rule
                winner = rule  # last applicable match in precedence order wins

        if deny_hit is not None:
            return PolicyDecision(
                verdict=PolicyVerdict.DENY, level=deny_hit.level,
                reason=deny_hit.reason or f"denied at {deny_hit.level.value}",
                trail=tuple(trail),
            )
        if winner is not None:
            return PolicyDecision(
                verdict=winner.verdict, level=winner.level, reason=winner.reason,
                trail=tuple(trail),
            )
        return PolicyDecision(verdict=PolicyVerdict.ALLOW, level=None, trail=())
