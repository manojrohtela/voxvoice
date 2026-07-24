"""HookEngine (M6.3B) — runs the already-ordered, matching hook set for one
point, collects verdicts, applies the point's `HookExecutionPolicy`
aggregation rule. Never executes a Tool, never plans a Skill, never mutates
runtime state — the only side effect of `run` is invoking each registered
`Hook.evaluate` and collecting its return value.

**Isolation is mandatory**: every hook runs in its own try/except; one hook's
exception never prevents sibling hooks at the same point from running
(collected, not first-wins). Every isolated failure is recorded in
`HookRunResult.failures` regardless of `failure_mode` — a `FAIL_OPEN` point
never silently swallows a real integration bug.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .domain import HookDescriptor, HookEvent, HookFailure, HookOutcome, HookRunResult, HookVerdict
from .extensions import Hook
from .policy import HookExecutionPolicy, HookFailureMode, aggregate_verdicts


class HookEngine:
    def run(
        self,
        descriptors: Sequence[HookDescriptor],
        hooks: Mapping[str, Hook],
        event: HookEvent,
        policy: HookExecutionPolicy,
        *,
        now: float = 0.0,
    ) -> HookRunResult:
        verdicts: list[HookVerdict] = []
        failures: list[HookFailure] = []

        for descriptor in descriptors:
            hook = hooks.get(descriptor.qualified_name)
            if hook is None:
                continue  # no concrete callback supplied for this call; caller's choice

            try:
                verdict = hook.evaluate(event)
            except Exception as exc:  # isolation: one hook's failure never blocks siblings
                failures.append(
                    HookFailure(
                        hook=descriptor.qualified_name, error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                if policy.failure_mode is HookFailureMode.FAIL_CLOSED:
                    verdicts.append(
                        HookVerdict(
                            outcome=HookOutcome.DENY, hook=descriptor.qualified_name,
                            reason=f"hook raised: {type(exc).__name__}",
                        )
                    )
                continue

            verdicts.append(verdict)

        aggregated = aggregate_verdicts(verdicts, policy.aggregation)
        return HookRunResult(
            point=event.point, scope=event.scope, verdicts=tuple(verdicts),
            failures=tuple(failures), aggregated_outcome=aggregated,
            aggregation=policy.aggregation.value, created_at=now,
        )
