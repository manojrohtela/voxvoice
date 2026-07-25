"""Loop Engine execution pipeline (M6.6B-1's own `loop/core/`, wired up in
M6.6B-2) — the actual propose→critique→repair→validate→stop-check→repeat
sequencing over Bundle 1's frozen model shapes and stopping-policy checks.

**A pure library entry point, not a class hierarchy** (design §8.1) — a
caller imports `run_loop` and calls it directly with an injected
`ReasoningPort` + `LoopValidator`, exactly the way `core.fsm.StateMachine`/
`core.policies.RetryPolicy` are used directly rather than through a
registry-backed runtime object.

**Propose/critique/repair are ordinary `ReasoningPort.run` calls,
deterministically parsed** — this module never generates content itself,
mirroring `agent.planning.decompose_goal`'s own discipline (§5 of the
M6.6A design) a second time. **Validation is entirely delegated** to the
injected `LoopValidator`; this module only reads the returned
`LoopValidation.passed`/`confidence`/`reason`, never computing correctness
itself. Loop Engine has no `ContextPort` seam (design §8.2 — the sole
external dependency is `ReasoningPort`), so the `ReasoningRequest.context_ref`
handed to Reasoning Runtime is a locally-derived content-hash over the loop's
own already-known state (task description + iteration + stage), never an
`AssembledContext` reference.

**The design's own optimization**: if the current proposal already
validates (before any critique/repair this iteration), the loop stops
immediately with `stop_reason="validation_success"` and
`LoopIteration.critique`/`.repair` are both `None` — a critique/repair pass
is never manufactured for a proposal that already passed.
"""

from __future__ import annotations

from collections.abc import Callable

from core.ports.reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest

from .cancellation import LoopCancellationToken
from .domain import (
    LoopCritique,
    LoopDefinition,
    LoopIteration,
    LoopProposal,
    LoopRepair,
    LoopResult,
    LoopValidation,
)
from .ids import content_hash, critique_id_for, proposal_id_for, repair_id_for
from .stopping import CONFIDENCE_THRESHOLD, CONVERGENCE, VALIDATION_SUCCESS, evaluate_stop
from .validator import LoopValidator

_CONVERGED_REASONS = frozenset({VALIDATION_SUCCESS, CONFIDENCE_THRESHOLD, CONVERGENCE})


def _parse_final_content(decisions: list[Decision]) -> str:
    """Deterministic parse of the last `Decision(kind=FINAL)`'s payload into
    a proposal/critique/repair's own content — never a bespoke generation
    algorithm, mirroring `agent.planning.decomposer.parse_plan_steps`."""
    finals = [d for d in decisions if d.kind is DecisionKind.FINAL]
    if not finals:
        return ""
    return str(finals[-1].payload.get("content", ""))


def _decisions_ref(decisions: list[Decision]) -> str | None:
    """A content-hash provenance reference over the raw decision sequence
    that produced a stage's content — distinct from `content_ref` (the
    content itself), the same "hash reference, never the payload" split
    `AgentDecision.payload_hash` already applies."""
    if not decisions:
        return None
    reprs = tuple(f"{d.kind.value}|{sorted(d.payload.items())}" for d in decisions)
    return content_hash(*reprs)


def _run_reasoning(
    *, reasoning_port: ReasoningPort, session_id: str, context_ref: str,
    model: str | None, org_id: str | None,
) -> list[Decision]:
    request = ReasoningRequest(
        session_id=session_id, context_ref=context_ref, model=model, org_id=org_id,
    )
    return list(reasoning_port.run(request))


def _propose(
    definition: LoopDefinition, *, iteration_number: int, reasoning_port: ReasoningPort,
    session_id: str, org_id: str | None, model: str | None, clock: Callable[[], float] | None,
) -> LoopProposal:
    context_ref = content_hash(
        definition.loop_id, iteration_number, "propose", definition.task_description,
    )
    decisions = _run_reasoning(
        reasoning_port=reasoning_port, session_id=session_id, context_ref=context_ref,
        model=model, org_id=org_id,
    )
    now = clock() if clock is not None else 0.0
    return LoopProposal(
        proposal_id=proposal_id_for(definition.loop_id, iteration_number, now),
        content_ref=content_hash(_parse_final_content(decisions)),
        reasoning_decision_ref=_decisions_ref(decisions),
    )


def _critique(
    definition: LoopDefinition, proposal: LoopProposal, validation: LoopValidation,
    *, iteration_number: int, reasoning_port: ReasoningPort, session_id: str,
    org_id: str | None, model: str | None, clock: Callable[[], float] | None,
) -> LoopCritique:
    context_ref = content_hash(
        definition.loop_id, iteration_number, "critique", proposal.content_ref, validation.reason,
    )
    decisions = _run_reasoning(
        reasoning_port=reasoning_port, session_id=session_id, context_ref=context_ref,
        model=model, org_id=org_id,
    )
    now = clock() if clock is not None else 0.0
    return LoopCritique(
        critique_id=critique_id_for(proposal.proposal_id, now),
        proposal_id=proposal.proposal_id,
        content_ref=content_hash(_parse_final_content(decisions)),
        reasoning_decision_ref=_decisions_ref(decisions),
    )


def _repair(
    definition: LoopDefinition, proposal: LoopProposal, critique: LoopCritique,
    *, iteration_number: int, reasoning_port: ReasoningPort, session_id: str,
    org_id: str | None, model: str | None, clock: Callable[[], float] | None,
) -> LoopRepair:
    context_ref = content_hash(
        definition.loop_id, iteration_number, "repair", proposal.content_ref, critique.content_ref,
    )
    decisions = _run_reasoning(
        reasoning_port=reasoning_port, session_id=session_id, context_ref=context_ref,
        model=model, org_id=org_id,
    )
    now = clock() if clock is not None else 0.0
    return LoopRepair(
        repair_id=repair_id_for(critique.critique_id, now),
        critique_id=critique.critique_id,
        content_ref=content_hash(_parse_final_content(decisions)),
        reasoning_decision_ref=_decisions_ref(decisions),
    )


def run_loop(
    definition: LoopDefinition,
    *,
    reasoning_port: ReasoningPort,
    validator: LoopValidator,
    session_id: str,
    org_id: str | None = None,
    model: str | None = None,
    clock: Callable[[], float] | None = None,
    cancellation_token: LoopCancellationToken | None = None,
) -> LoopResult:
    """Runs one full loop invocation to completion. Never persists anything
    — the returned `LoopResult` is the caller's own to keep or discard."""
    policy = definition.stopping_policy
    iterations: list[LoopIteration] = []
    iteration_number = 1
    proposal = _propose(
        definition, iteration_number=iteration_number, reasoning_port=reasoning_port,
        session_id=session_id, org_id=org_id, model=model, clock=clock,
    )

    while True:
        validation = validator.validate(proposal)
        stopped, reason = evaluate_stop(
            iteration_number=iteration_number, validation=validation,
            current_content_ref=proposal.content_ref, previous_content_ref=None,
            policy=policy, cancellation_token=cancellation_token,
        )
        if stopped:
            iterations.append(LoopIteration(
                iteration_number=iteration_number, proposal=proposal, critique=None, repair=None,
                validation=validation, stopped=True, stop_reason=reason,
            ))
            break

        critique = _critique(
            definition, proposal, validation, iteration_number=iteration_number,
            reasoning_port=reasoning_port, session_id=session_id, org_id=org_id, model=model,
            clock=clock,
        )
        repair = _repair(
            definition, proposal, critique, iteration_number=iteration_number,
            reasoning_port=reasoning_port, session_id=session_id, org_id=org_id, model=model,
            clock=clock,
        )
        now = clock() if clock is not None else 0.0
        repaired_proposal = LoopProposal(
            proposal_id=proposal_id_for(definition.loop_id, iteration_number + 1, now),
            content_ref=repair.content_ref, reasoning_decision_ref=repair.reasoning_decision_ref,
        )
        repaired_validation = validator.validate(repaired_proposal)
        stopped, reason = evaluate_stop(
            iteration_number=iteration_number, validation=repaired_validation,
            current_content_ref=repaired_proposal.content_ref,
            previous_content_ref=proposal.content_ref, policy=policy,
            cancellation_token=cancellation_token,
        )
        iterations.append(LoopIteration(
            iteration_number=iteration_number, proposal=proposal, critique=critique,
            repair=repair, validation=repaired_validation, stopped=stopped, stop_reason=reason,
        ))
        proposal = repaired_proposal
        if stopped:
            break
        iteration_number += 1

    completed_at = clock() if clock is not None else 0.0
    final_iteration = iterations[-1]
    return LoopResult(
        loop_id=definition.loop_id, final_proposal_id=proposal.proposal_id,
        converged=final_iteration.stop_reason in _CONVERGED_REASONS,
        iteration_count=len(iterations), stop_reason=final_iteration.stop_reason,
        iterations=tuple(iterations), completed_at=completed_at,
    )
