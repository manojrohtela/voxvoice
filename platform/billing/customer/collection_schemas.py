"""Dunning & Collections wire DTOs (M9.17) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary rather than from a second list that will drift.

Named distinctly from the frozen surfaces they sit beside: `CollectionCaseResponse` rather than a
second `DunningResponse`, which no earlier milestone declared but which would collide with M9.2's
vocabulary the moment one did.

## What no schema here can carry

There is **no amount, no balance, no `amount_due`, and no `outstanding`** — not on the way in and
not on the way out. Collections does not own a figure; a response field that carried one would
become a second authority on a number the Ledger owns, and a client that read it would eventually
render a stale total next to a live one. What a customer owes is served by the Payment and Invoice
surfaces, which is where it is computed from facts each time it is asked for.

There is also no recipient, no email address, no phone number, no template, and no message body.
Collections decides that a customer should be told something; who they are and how they are reached
belongs to whatever implements the notifier port.

`RecordPaymentSignalBody` deliberately takes **payment state strings**, never a verdict. A body
carrying `{"recovered": true}` would let anyone who can reach the endpoint close a recovery;
interpretation happens in `SignalReader`, once, on the server.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    ActionKind,
    CaseState,
    ObligationKind,
    PaymentSignal,
)


def _fields(view) -> dict:
    """An Application view as a plain mapping.

    `vars()` is not available: every view is a slotted frozen dataclass, deliberately, and a slotted
    object has no `__dict__`."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


# ── inbound ─────────────────────────────────────────────────────────────────────


class CollectionStageBody(BaseModel):
    """One rung of a strategy. Waits and graces are in **seconds** and are the only numbers this
    domain accepts — there is no amount field, and there is no threshold in money anywhere."""

    sequence: int = Field(ge=0)
    kind: ActionKind
    wait_seconds: int = Field(default=0, ge=0)
    grace_seconds: int = Field(default=0, ge=0)
    label: str = ""


class PublishPolicyBody(BaseModel):
    """Publish one **immutable** strategy version.

    There is no PUT and no PATCH on a policy anywhere in this surface. Changing a strategy is
    publishing version *n+1*; a case decided under version *n* keeps naming it, which is the only
    way its decisions still replay after the change."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "standard",
                "version": 1,
                "stages": [
                    {"sequence": 0, "kind": "notify", "wait_seconds": 0, "label": "first notice"},
                    {"sequence": 1, "kind": "request_payment_retry", "wait_seconds": 86400},
                    {"sequence": 2, "kind": "final_notice", "wait_seconds": 604800},
                    {"sequence": 3, "kind": "stop", "wait_seconds": 259200},
                ],
                "max_cycles": 1,
            }
        }
    )

    code: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    stages: list[CollectionStageBody] = Field(default_factory=list)
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    max_cycles: int = Field(default=1, ge=1)
    description: str = ""


class OpenCaseBody(BaseModel):
    """Open the recovery of one obligation.

    There is no `request_key`. A second deliberate collection against one invoice is a second
    *payment objective*, but it is never a second recovery — so this is idempotent on the
    obligation itself, and asking twice returns the same case."""

    account_id: str = Field(min_length=1)
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    obligation_id: str = Field(min_length=1)
    policy_code: str = Field(min_length=1)
    payment_intent_id: str = ""
    currency: str = ""


class EvaluateCaseBody(BaseModel):
    """Re-read the payment, decide, plan at most one action.

    `at` is explicit so a decision is reproducible: replaying the same instant against the same
    immutable policy version yields the same answer, in March and in April."""

    at: float | None = None


class ExecuteActionBody(BaseModel):
    """`at` is explicit for the same reason `EvaluateCaseBody` carries one: whether an action is
    due is answered against an instant the caller names, not against whatever the process clock
    happens to say."""

    notifier_code: str = ""
    correlation_id: str = ""
    at: float | None = None


class RecordPaymentSignalBody(BaseModel):
    """Tell a case what the payment did. **States, never verdicts.**

    `attempt_state` wins over `intent_state` when both are present, because an attempt describes a
    movement of money and an intent only describes an intention to move some. Anything the server
    does not recognise reads as *ambiguous*, never as failed."""

    intent_state: str = ""
    attempt_state: str = ""
    failure_code: str = ""
    payment_intent_id: str = ""


class CollectionReasonBody(BaseModel):
    """The attribution every operator decision carries. There is no unattributed override."""

    reason: str = Field(default="", max_length=500)


class OpenCycleBody(BaseModel):
    """Begin a second recovery on an exhausted case. **Never a rewrite of the first.**"""

    policy_code: str = ""
    reason: str = Field(default="", max_length=500)


# ── outbound ────────────────────────────────────────────────────────────────────


class CollectionStageResponse(BaseModel):
    sequence: int
    kind: str
    wait_seconds: int
    grace_seconds: int
    label: str
    is_terminal: bool

    @classmethod
    def of(cls, view) -> CollectionStageResponse:
        return cls(**_fields(view))


class CollectionPolicyResponse(BaseModel):
    id: str
    code: str
    version: int
    obligation_kind: str
    stages: list[CollectionStageResponse]
    max_cycles: int
    is_active: bool
    description: str
    declared_at: float
    declared_by: str

    @classmethod
    def of(cls, view) -> CollectionPolicyResponse:
        data = _fields(view)
        data["stages"] = [CollectionStageResponse.of(s) for s in view.stages]
        return cls(**data)


class CollectionCaseResponse(BaseModel):
    """A case on the wire.

    `allowed_triggers` is **server-computed**. A client that derived what is possible from the
    state would be re-implementing the lifecycle, and it would be the copy that goes stale — the
    same reason M9.16 sends allowed triggers rather than letting Studio infer them."""

    id: str
    account_id: str
    obligation_kind: str
    obligation_id: str
    state: str
    signal: str
    failure_class: str
    policy_code: str
    policy_version: int
    cycle: int
    stage_index: int
    actions_planned: int
    version: int
    payment_intent_id: str
    currency: str
    next_action_at: float | None
    grace_until: float | None
    blocked_reason: str
    opened_at: float
    closed_at: float | None
    closed_reason: str
    is_terminal: bool
    is_quiescent: bool
    allowed_triggers: list[str]

    @classmethod
    def of(cls, view) -> CollectionCaseResponse:
        data = _fields(view)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class CollectionActionResponse(BaseModel):
    id: str
    case_id: str
    cycle: int
    sequence: int
    kind: str
    state: str
    stage_index: int
    decision_id: str
    policy_version: int
    due_at: float
    planned_at: float
    claimed_at: float | None
    claimed_by: str
    completed_at: float | None
    attempts_made: int
    result_ref: str
    note: str
    requests_money: bool
    is_closed: bool
    allowed_triggers: list[str]

    @classmethod
    def of(cls, view) -> CollectionActionResponse:
        data = _fields(view)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class CollectionAttemptResponse(BaseModel):
    id: str
    case_id: str
    action_id: str
    sequence: int
    kind: str
    outcome: str
    channel: str
    reference: str
    detail: str
    executed_at: float

    @classmethod
    def of(cls, view) -> CollectionAttemptResponse:
        return cls(**_fields(view))


class CollectionDecisionResponse(BaseModel):
    """A decision with every input that produced it — the reproducibility contract, on the wire."""

    id: str
    case_id: str
    cycle: int
    stage_index: int
    signal: str
    failure_class: str
    selected: str
    action_id: str
    policy_code: str
    policy_version: int
    reason: str
    reason_path: list[str]
    decided_at: float
    due_at: float | None

    @classmethod
    def of(cls, view) -> CollectionDecisionResponse:
        data = _fields(view)
        data["reason_path"] = list(view.reason_path)
        return cls(**data)


class CollectionExplanationResponse(BaseModel):
    case_id: str
    state: str
    signal: str
    stage_index: int
    policy_code: str
    policy_version: int
    next_kind: str
    next_action_at: float | None
    grace_until: float | None
    reason: str
    path: list[str]
    blocked_reason: str

    @classmethod
    def of(cls, view) -> CollectionExplanationResponse:
        data = _fields(view)
        data["path"] = list(view.path)
        return cls(**data)


class CollectionInspectionResponse(BaseModel):
    case: CollectionCaseResponse
    actions: int
    open_actions: int
    executed_actions: int
    attempts: int
    decisions: int
    cycles: int
    stage_count: int
    is_due: bool

    @classmethod
    def of(cls, view) -> CollectionInspectionResponse:
        data = _fields(view)
        data["case"] = CollectionCaseResponse.of(view.case)
        return cls(**data)


class CollectionActivityResponse(BaseModel):
    id: str
    case_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    action_id: str

    @classmethod
    def of(cls, view) -> CollectionActivityResponse:
        return cls(**_fields(view))


class CollectionOutcomeResponse(BaseModel):
    """What a write returns.

    `disposition` is the field that matters: `new`, `duplicate`, `rejected` or `deferred`. A
    scheduler that could not tell a duplicate from a new action would count chases that never
    happened, and a duplicate must never read as one."""

    case: CollectionCaseResponse
    disposition: str
    action: CollectionActionResponse | None = None
    decision: CollectionDecisionResponse | None = None
    note: str = ""

    @classmethod
    def of(cls, view) -> CollectionOutcomeResponse:
        return cls(
            case=CollectionCaseResponse.of(view.case),
            disposition=view.disposition,
            action=CollectionActionResponse.of(view.action) if view.action else None,
            decision=CollectionDecisionResponse.of(view.decision) if view.decision else None,
            note=view.note,
        )


class CollectionSearchResponse(BaseModel):
    cases: list[CollectionCaseResponse]
    total: int
    truncated: bool

    @classmethod
    def of(cls, view) -> CollectionSearchResponse:
        return cls(
            cases=[CollectionCaseResponse.of(c) for c in view.cases],
            total=view.total,
            truncated=view.truncated,
        )


__all__ = [
    "CollectionActionResponse",
    "CollectionActivityResponse",
    "CollectionAttemptResponse",
    "CollectionCaseResponse",
    "CollectionDecisionResponse",
    "CollectionExplanationResponse",
    "CollectionInspectionResponse",
    "CollectionOutcomeResponse",
    "CollectionPolicyResponse",
    "CollectionReasonBody",
    "CollectionSearchResponse",
    "CollectionStageBody",
    "CollectionStageResponse",
    "EvaluateCaseBody",
    "ExecuteActionBody",
    "OpenCaseBody",
    "OpenCycleBody",
    "PublishPolicyBody",
    "RecordPaymentSignalBody",
    "CaseState",
    "PaymentSignal",
]
