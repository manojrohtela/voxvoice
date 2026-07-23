"""Provider Runtime lifecycles (M5.6B-1) — built on `core.fsm`.

Two independent deterministic state machines, dogfooding the shared FSM framework (no
hand-written transition logic). Illegal moves raise typed `FSMError`s.

**Request execution** (per `ProviderResponse`):
REQUESTED → ROUTED → EXECUTING → STREAMING → COMPLETED
Any active state → FAILED / CANCELLED; ROUTED/EXECUTING/STREAMING → FAILED_OVER (router-driven
retry-with-next-candidate); COMPLETED → EXPIRED. The M5.6B-1 core drives only
REQUESTED→ROUTED→EXECUTING→STREAMING→COMPLETED (+ FAILED on an adapter exception, + EXPIRED via
the TTL sweep); CANCELLED/FAILED_OVER are defined now (the FSM is complete) but exercised by
the M5.6B-2 router/cancellation intelligence.

**Adapter health** (per `ProviderRegistryEntry`):
HEALTHY ⇄ DEGRADED ⇄ UNAVAILABLE (conservative: recovery from UNAVAILABLE must pass back
through DEGRADED — no single-success snap-back). The M5.6B-1 core exposes only deterministic,
caller-driven transitions (`ProviderRegistry.mark_healthy/mark_degraded/mark_unavailable`); the
M5.6B-2 `ProviderHealthMonitor` drives these from observed call-outcome feedback.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import (
    ProviderHealthState,
    ProviderRegistryEntry,
    ProviderRequestState,
    ProviderResponse,
)


class ProviderTrigger(StrEnum):
    ROUTE = "route"
    EXECUTE = "execute"
    STREAM = "stream"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    FAILOVER = "failover"
    EXPIRE = "expire"


class HealthTrigger(StrEnum):
    OUTCOME_SUCCESS = "outcome_success"
    OUTCOME_FAILURE = "outcome_failure"
    OUTCOME_TIMEOUT = "outcome_timeout"
    FORCE_UNAVAILABLE = "force_unavailable"
    RECOVER = "recover"


def _execution_def() -> StateMachineDefinition[ProviderRequestState, ProviderTrigger]:
    d: StateMachineDefinition[ProviderRequestState, ProviderTrigger] = StateMachineDefinition()
    d.add(ProviderRequestState.REQUESTED, ProviderTrigger.ROUTE, ProviderRequestState.ROUTED)
    d.add(ProviderRequestState.ROUTED, ProviderTrigger.EXECUTE, ProviderRequestState.EXECUTING)
    d.add(ProviderRequestState.EXECUTING, ProviderTrigger.STREAM, ProviderRequestState.STREAMING)
    d.add(ProviderRequestState.STREAMING, ProviderTrigger.COMPLETE, ProviderRequestState.COMPLETED)
    # single-shot (no streaming) completes directly from EXECUTING
    d.add(ProviderRequestState.EXECUTING, ProviderTrigger.COMPLETE, ProviderRequestState.COMPLETED)
    # failure / cancellation / failover from any active state (M5.6B-2 exercises FAILOVER/CANCEL)
    for st in (ProviderRequestState.ROUTED, ProviderRequestState.EXECUTING,
               ProviderRequestState.STREAMING):
        d.add(st, ProviderTrigger.FAIL, ProviderRequestState.FAILED)
        d.add(st, ProviderTrigger.CANCEL, ProviderRequestState.CANCELLED)
        d.add(st, ProviderTrigger.FAILOVER, ProviderRequestState.FAILED_OVER)
    d.add(ProviderRequestState.REQUESTED, ProviderTrigger.FAIL, ProviderRequestState.FAILED)
    d.add(ProviderRequestState.REQUESTED, ProviderTrigger.CANCEL, ProviderRequestState.CANCELLED)
    d.add(ProviderRequestState.FAILED_OVER, ProviderTrigger.ROUTE, ProviderRequestState.ROUTED)
    d.add(ProviderRequestState.COMPLETED, ProviderTrigger.EXPIRE, ProviderRequestState.EXPIRED)
    return d


PROVIDER_EXECUTION_LIFECYCLE = _execution_def()


def advance(
    response: ProviderResponse, trigger: ProviderTrigger,
    *, clock: Callable[[], float] | None = None,
) -> ProviderResponse:
    """Validate `trigger` against the response's state; return it in the new state."""
    fsm: StateMachine[ProviderRequestState, ProviderTrigger] = StateMachine(
        PROVIDER_EXECUTION_LIFECYCLE, response.state, machine_id=response.id, clock=clock
    )
    return response.with_state(fsm.fire(trigger))


def _health_def() -> StateMachineDefinition[ProviderHealthState, HealthTrigger]:
    d: StateMachineDefinition[ProviderHealthState, HealthTrigger] = StateMachineDefinition()
    # a success while already healthy is a deterministic no-op (self-loop), not an error
    d.add(ProviderHealthState.HEALTHY, HealthTrigger.OUTCOME_SUCCESS, ProviderHealthState.HEALTHY)
    d.add(ProviderHealthState.HEALTHY, HealthTrigger.OUTCOME_FAILURE, ProviderHealthState.DEGRADED)
    d.add(ProviderHealthState.HEALTHY, HealthTrigger.OUTCOME_TIMEOUT, ProviderHealthState.DEGRADED)
    d.add(ProviderHealthState.HEALTHY, HealthTrigger.FORCE_UNAVAILABLE,
          ProviderHealthState.UNAVAILABLE)
    d.add(ProviderHealthState.DEGRADED, HealthTrigger.OUTCOME_FAILURE,
          ProviderHealthState.UNAVAILABLE)
    d.add(ProviderHealthState.DEGRADED, HealthTrigger.OUTCOME_TIMEOUT,
          ProviderHealthState.UNAVAILABLE)
    d.add(ProviderHealthState.DEGRADED, HealthTrigger.OUTCOME_SUCCESS, ProviderHealthState.HEALTHY)
    d.add(ProviderHealthState.DEGRADED, HealthTrigger.FORCE_UNAVAILABLE,
          ProviderHealthState.UNAVAILABLE)
    # conservative recovery: UNAVAILABLE -> DEGRADED -> HEALTHY (never a single-success snap-back)
    d.add(ProviderHealthState.UNAVAILABLE, HealthTrigger.RECOVER, ProviderHealthState.DEGRADED)
    return d


PROVIDER_HEALTH_LIFECYCLE = _health_def()


def advance_health(
    entry: ProviderRegistryEntry, trigger: HealthTrigger,
    *, clock: Callable[[], float] | None = None,
) -> ProviderRegistryEntry:
    """Validate `trigger` against the entry's health; return it in the new health state."""
    fsm: StateMachine[ProviderHealthState, HealthTrigger] = StateMachine(
        PROVIDER_HEALTH_LIFECYCLE, entry.health, machine_id=entry.descriptor.name, clock=clock
    )
    return entry.with_health(fsm.fire(trigger))
