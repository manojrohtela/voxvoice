"""Mechanical stopping-policy checks (M6.6B-1) — per `docs/architecture/
epic6/loop-engine-design.md` §6. Every check here is a plain read-and-compare
over a fact some other party already produced (the injected `LoopValidator`,
the caller's own `LoopStoppingPolicy`, an injected cancellation token, or a
previously-computed content-hash). Loop Engine never itself judges quality,
correctness, or convergence "closeness" here — only exact, deterministic
comparisons.
"""

from __future__ import annotations

from .cancellation import LoopCancellationToken
from .domain import LoopStoppingPolicy, LoopValidation

# Checked in this order: an external stop request always wins first (the
# caller's explicit signal should never be overridden by an in-flight
# computation), then a genuinely successful validation, then a
# confidence-threshold pass, then structural convergence, and finally the
# budget backstop (`max_iterations`) once every other condition has been
# ruled out.
EXTERNAL_STOP = "external_stop"
VALIDATION_SUCCESS = "validation_success"
CONFIDENCE_THRESHOLD = "confidence_threshold"
CONVERGENCE = "convergence"
MAX_ITERATIONS = "max_iterations"
NOT_STOPPED = ""


def is_externally_stopped(token: LoopCancellationToken | None) -> bool:
    return token is not None and token.is_cancelled()


def is_max_iterations_reached(iteration_number: int, policy: LoopStoppingPolicy) -> bool:
    return iteration_number >= policy.max_iterations


def is_validation_success(validation: LoopValidation, policy: LoopStoppingPolicy) -> bool:
    return policy.stop_on_validation_success and validation.passed


def is_confidence_threshold_met(validation: LoopValidation, policy: LoopStoppingPolicy) -> bool:
    if policy.confidence_threshold is None or validation.confidence is None:
        return False
    return validation.confidence >= policy.confidence_threshold


def is_converged(
    current_content_ref: str, previous_content_ref: str | None, policy: LoopStoppingPolicy,
) -> bool:
    if not policy.stop_on_convergence or previous_content_ref is None:
        return False
    return current_content_ref == previous_content_ref


def evaluate_stop(
    *,
    iteration_number: int,
    validation: LoopValidation,
    current_content_ref: str,
    previous_content_ref: str | None,
    policy: LoopStoppingPolicy,
    cancellation_token: LoopCancellationToken | None = None,
) -> tuple[bool, str]:
    """Returns `(stopped, stop_reason)`. `stop_reason` is `""` (`NOT_STOPPED`)
    only when `stopped` is `False`."""
    if is_externally_stopped(cancellation_token):
        return True, EXTERNAL_STOP
    if is_validation_success(validation, policy):
        return True, VALIDATION_SUCCESS
    if is_confidence_threshold_met(validation, policy):
        return True, CONFIDENCE_THRESHOLD
    if is_converged(current_content_ref, previous_content_ref, policy):
        return True, CONVERGENCE
    if is_max_iterations_reached(iteration_number, policy):
        return True, MAX_ITERATIONS
    return False, NOT_STOPPED
