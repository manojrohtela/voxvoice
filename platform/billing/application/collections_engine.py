"""Dunning & Collections application layer (M9.17) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.16: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 12 commands,
14 queries, their use cases, and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

Collections is the most *consuming* engine in Billing, so its boundaries are worth stating as a
picture rather than a paragraph:

    Invoice ─────read────▶ ┐
    Payments ────read────▶ ├── Collections ──command──▶ Payments  (StartPaymentAttempt)
    Ledger ──(via above)─▶ ┘         │
                                     └──port──▶ CollectionNotifier  (provider-neutral)

Every arrow into Collections is a **read**. The only arrow out that changes anything is a
*command* into the Payment Engine's own use cases — the same shape M9.16 uses to reach the Ledger,
and for the same reason. Collections writes to six tables, all its own, and an architecture test
scans the write targets of this module to prove it.

There is no arrow to the Ledger at all. Collections never posts an entry, never computes a balance,
and never learns what is owed except by asking Payments, which asks the document, which got the
figure from the Ledger. Three hops, one authority.

## The three phases, again — and why a notice needs them

M9.16 established that an external effect needs a durable record *before* it happens, because a
database transaction cannot un-send what left the building. That is as true of a dunning notice as
of a charge: a customer who receives the same final notice four times because a worker crashed
between sending and recording is a support incident and, in some jurisdictions, a complaint.

So `ExecuteCollectionAction` is three phases:

    ┌─ transaction 1 ─────────────────────────────────────────────────────────┐
    │ read the case · re-read the payment signal · **claim the action**       │
    │ (conditional UPDATE planned→claimed — one worker wins) · COMMIT         │
    └─────────────────────────────────────────────────────────────────────────┘
                                  ↓  no transaction is open
                notifier.deliver(request)  /  payment command dispatch
                                  ↓
    ┌─ transaction 2 ─────────────────────────────────────────────────────────┐
    │ append the attempt · settle the action · advance the case (CAS)         │
    │ plan the next action · COMMIT                                            │
    └─────────────────────────────────────────────────────────────────────────┘

Each failure mode rests somewhere defined: a crash before the effect leaves a `claimed` action
naming exactly what was in flight; a crash after it leaves the same row, and re-executing presents
the notifier with the same idempotency key, which a conforming notifier honours. Nothing is retried
blindly.

## The invariant inherited from M9.16-R1

An ambiguous payment is never retried. It is gated three times on the way here — in the decision
engine's first branch, in its money-request whitelist, and in `CollectionGuardService` at the
moment of execution — and the third is the one that catches the case the first two cannot see: an
action planned an hour ago against a failed payment, executed now, after a reconciliation moved
that attempt back to ambiguous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from billing.foundation import (
    ActionKind,
    ActionState,
    ActionTrigger,
    AttemptOutcome,
    CaseLifecycleService,
    CaseStale,
    CaseState,
    CaseTrigger,
    CollectionAction,
    CollectionCase,
    CollectionDecision,
    CollectionPolicy,
    CollectionSearchQuery,
    CollectionStage,
    CommandDisposition,
    FailureClass,
    NoticeNotSent,
    NoticeRequest,
    ObligationKind,
    PaymentSignal,
    derive_decision_id,
    notice_key,
)
from billing.foundation import BillingConflict as _Conflict
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

#: How many times a worker re-reads and re-decides after losing a compare-and-set. Small and
#: bounded: each round either wins or discovers the case moved somewhere it must not touch, so the
#: loop cannot spin. Losing five in a row means genuine contention that a human should see.
_CAS_ATTEMPTS = 5


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublishCollectionPolicy(_Command):
    """Publish one **immutable** strategy version.

    There is no `UpdateCollectionPolicy` and there never will be. A strategy is changed by
    publishing version *n+1*; a case decided under *n* keeps naming *n*, which is the only way its
    decisions still replay after the change."""

    code: str = ""
    version: int = 1
    stages: tuple[CollectionStage, ...] = ()
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    max_cycles: int = 1
    description: str = ""


@dataclass(frozen=True, slots=True)
class DeactivateCollectionPolicy(_Command):
    """Withdraw a version from *future* binding. The row stays readable forever."""

    policy_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OpenCollectionCase(_Command):
    """Open the recovery of one obligation. **Idempotent on the obligation, not on the request.**

    Unlike `CreatePaymentIntent`, there is no `request_key`: a second deliberate collection against
    one invoice is a second *payment objective*, but it is never a second recovery. One debt, one
    case, and the derived primary key makes that true under two workers rather than by convention.
    """

    account_id: str = ""
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    obligation_id: str = ""
    policy_code: str = ""
    payment_intent_id: str = ""
    currency: str = ""


@dataclass(frozen=True, slots=True)
class EvaluateCollectionCase(_Command):
    """Re-read the payment signal, decide, and plan at most one action.

    The command a scheduler calls. Safe to call repeatedly: the decision id is derived from its
    inputs and the action id from `(case, cycle, sequence)`, so an unchanged case evaluated a
    thousand times produces one decision and one action."""

    case_id: str = ""
    at: float | None = None  # explicit instant; `None` means the injected clock


@dataclass(frozen=True, slots=True)
class ClaimCollectionAction(_Command):
    """Take exclusive hold of a due action. Conditional UPDATE; exactly one worker wins."""

    action_id: str = ""
    at: float | None = None  # explicit instant; `None` means the injected clock


@dataclass(frozen=True, slots=True)
class ExecuteCollectionAction(_Command):
    """**The three-phase path.** Claim, do the external thing, record what happened.

    The only command that reaches outside Billing — and even then it reaches a *port* for notices
    and the Payment Engine's own *command* for money. It never reaches a provider."""

    action_id: str = ""
    notifier_code: str = ""
    correlation_id: str = ""
    # The instant this execution is judged against, so "is this action due?" is answered by a
    # value the caller supplies rather than by whatever the process clock happens to say. Same
    # reason `EvaluateCollectionCase` takes one: a decision that cannot be replayed at a chosen
    # instant is a decision that cannot be tested or audited.
    at: float | None = None


@dataclass(frozen=True, slots=True)
class RecordPaymentSignal(_Command):
    """Tell a case what the payment did. The path a payment event takes into Collections.

    Carries a *state string*, never a decision: a command that accepted `recovered=True` from its
    caller would let anyone who can reach it close a recovery. The signal is interpreted here, by
    `SignalReader`, in the one place that interpretation happens."""

    case_id: str = ""
    intent_state: str = ""
    attempt_state: str = ""
    failure_code: str = ""
    payment_intent_id: str = ""


@dataclass(frozen=True, slots=True)
class BlockCollectionCase(_Command):
    """Hold a case pending something Collections does not own — normally reconciliation."""

    case_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class EscalateCollectionCase(_Command):
    """Hand a case to a human. **Always attributed** — automation stops here."""

    case_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelCollectionCase(_Command):
    """The obligation went away — voided, credited, or withdrawn. Not a write-off.

    Collections cannot forgive a debt. This records that the *thing being recovered* no longer
    exists, which is a fact the Invoice or Credit domain established and this one is told about."""

    case_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OpenCollectionCycle(_Command):
    """Start a **new recovery cycle** on an exhausted case. An explicit commercial decision.

    This is the honest answer to "we want to chase them again". The FSM refuses
    `EXHAUSTED → SCHEDULED` because that rewrites the record of a recovery that ended; a new cycle
    leaves the old one intact and readable, with its own actions, attempts and decisions."""

    case_id: str = ""
    policy_code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ObsoleteCollectionAction(_Command):
    """Retire an action overtaken by events — normally because the money arrived first."""

    action_id: str = ""
    reason: str = ""


#: The command vocabulary this milestone adds, as a tuple so "one handler per command" is checkable
#: by iteration rather than asserted in prose.
ALL_COLLECTION_COMMANDS = (
    PublishCollectionPolicy,
    DeactivateCollectionPolicy,
    OpenCollectionCase,
    EvaluateCollectionCase,
    ClaimCollectionAction,
    ExecuteCollectionAction,
    RecordPaymentSignal,
    BlockCollectionCase,
    EscalateCollectionCase,
    CancelCollectionCase,
    OpenCollectionCycle,
    ObsoleteCollectionAction,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetCollectionCase(_Query):
    case_id: str


@dataclass(frozen=True, slots=True)
class ListCollectionCases(_Query):
    account_id: str = ""
    states: tuple[str, ...] = ()
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetCaseForObligation(_Query):
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    obligation_id: str = ""


@dataclass(frozen=True, slots=True)
class ListCollectionActions(_Query):
    case_id: str = ""


@dataclass(frozen=True, slots=True)
class GetCollectionAction(_Query):
    action_id: str


@dataclass(frozen=True, slots=True)
class ListCollectionAttempts(_Query):
    case_id: str = ""
    action_id: str = ""


@dataclass(frozen=True, slots=True)
class ListCollectionDecisions(_Query):
    case_id: str = ""


@dataclass(frozen=True, slots=True)
class ActionsDue(_Query):
    """**What is due now.** The read a scheduler outside this plane calls on a timer.

    A query, deliberately. There is no `RunCollections` command, no worker, and no loop anywhere in
    Billing: the engine answers what is due and something else decides when to ask."""

    at: float | None = None
    limit: int = 200


@dataclass(frozen=True, slots=True)
class CasesDue(_Query):
    at: float | None = None
    limit: int = 200


@dataclass(frozen=True, slots=True)
class ExplainCollectionCase(_Query):
    case_id: str


@dataclass(frozen=True, slots=True)
class InspectCollectionCase(_Query):
    case_id: str
    at: float | None = None


@dataclass(frozen=True, slots=True)
class ListCollectionPolicies(_Query):
    include_inactive: bool = False


@dataclass(frozen=True, slots=True)
class GetCollectionPolicyVersions(_Query):
    code: str = ""


@dataclass(frozen=True, slots=True)
class SearchCollectionCases(_Query):
    account_id: str = ""
    obligation_id: str = ""
    states: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    due_before: float | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CollectionTimeline(_Query):
    case_id: str
    limit: int = 200


ALL_COLLECTION_QUERIES = (
    GetCollectionCase,
    ListCollectionCases,
    GetCaseForObligation,
    ListCollectionActions,
    GetCollectionAction,
    ListCollectionAttempts,
    ListCollectionDecisions,
    ActionsDue,
    CasesDue,
    ExplainCollectionCase,
    InspectCollectionCase,
    ListCollectionPolicies,
    GetCollectionPolicyVersions,
    SearchCollectionCases,
    CollectionTimeline,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionStageView:
    sequence: int
    kind: str
    wait_seconds: int
    grace_seconds: int
    label: str
    is_terminal: bool


@dataclass(frozen=True, slots=True)
class CollectionPolicyView:
    id: str
    code: str
    version: int
    obligation_kind: str
    stages: tuple[CollectionStageView, ...]
    max_cycles: int
    is_active: bool
    description: str
    declared_at: float
    declared_by: str


@dataclass(frozen=True, slots=True)
class CollectionCaseView:
    """A case as the API and Studio see it.

    **No amount field.** The outstanding figure is a Payment/Ledger fact and is served by those
    engines' own reads; putting it here would make this view a second authority on it, and the one
    that goes stale first. `allowed_triggers` is server-computed so Studio never decides what is
    possible — the M9.16 lesson about clients re-deriving domain rules."""

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
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectionActionView:
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
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectionAttemptView:
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


@dataclass(frozen=True, slots=True)
class CollectionDecisionView:
    """A decision, with every input that produced it. Replayable after the policy changes."""

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
    reason_path: tuple[str, ...]
    decided_at: float
    due_at: float | None


@dataclass(frozen=True, slots=True)
class CollectionExplanationView:
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
    path: tuple[str, ...]
    blocked_reason: str


@dataclass(frozen=True, slots=True)
class CollectionInspectionView:
    case: CollectionCaseView
    actions: int
    open_actions: int
    executed_actions: int
    attempts: int
    decisions: int
    cycles: int
    stage_count: int
    is_due: bool


@dataclass(frozen=True, slots=True)
class CollectionActivityView:
    id: str
    case_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    action_id: str


@dataclass(frozen=True, slots=True)
class CollectionSearchView:
    cases: tuple[CollectionCaseView, ...]
    total: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class CollectionOutcomeView:
    """What a write command returns.

    `disposition` is why this type exists: a duplicate must never read to the caller as a new
    collection action. A scheduler that re-evaluated a case and saw `NEW` would count a chase that
    never happened."""

    case: CollectionCaseView
    disposition: str
    action: CollectionActionView | None = None
    decision: CollectionDecisionView | None = None
    note: str = ""


# ── view mapping ────────────────────────────────────────────────────────────────


def stage_view(stage: CollectionStage) -> CollectionStageView:
    return CollectionStageView(
        sequence=stage.sequence,
        kind=stage.kind.value,
        wait_seconds=stage.wait_seconds,
        grace_seconds=stage.grace_seconds,
        label=stage.label,
        is_terminal=stage.is_terminal,
    )


def collections_policy_view(policy: CollectionPolicy) -> CollectionPolicyView:
    return CollectionPolicyView(
        id=policy.id,
        code=policy.code,
        version=policy.version,
        obligation_kind=policy.obligation_kind.value,
        stages=tuple(stage_view(s) for s in policy.stages),
        max_cycles=policy.max_cycles,
        is_active=policy.is_active,
        description=policy.description,
        declared_at=policy.declared_at,
        declared_by=policy.declared_by,
    )


def case_view(case: CollectionCase, allowed: tuple = ()) -> CollectionCaseView:
    return CollectionCaseView(
        id=case.id,
        account_id=case.account_id,
        obligation_kind=case.obligation_kind.value,
        obligation_id=case.obligation_id,
        state=case.state.value,
        signal=case.signal.value,
        failure_class=case.failure_class.value,
        policy_code=case.policy_code,
        policy_version=case.policy_version,
        cycle=case.cycle,
        stage_index=case.stage_index,
        actions_planned=case.actions_planned,
        version=case.version,
        payment_intent_id=case.payment_intent_id,
        currency=case.currency,
        next_action_at=case.next_action_at,
        grace_until=case.grace_until,
        blocked_reason=case.blocked_reason,
        opened_at=case.opened_at,
        closed_at=case.closed_at,
        closed_reason=case.closed_reason,
        is_terminal=case.is_terminal,
        is_quiescent=case.is_quiescent,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def action_view(action: CollectionAction, allowed: tuple = ()) -> CollectionActionView:
    return CollectionActionView(
        id=action.id,
        case_id=action.case_id,
        cycle=action.cycle,
        sequence=action.sequence,
        kind=action.kind.value,
        state=action.state.value,
        stage_index=action.stage_index,
        decision_id=action.decision_id,
        policy_version=action.policy_version,
        due_at=action.due_at,
        planned_at=action.planned_at,
        claimed_at=action.claimed_at,
        claimed_by=action.claimed_by,
        completed_at=action.completed_at,
        attempts_made=action.attempts_made,
        result_ref=action.result_ref,
        note=action.note,
        requests_money=action.requests_money,
        is_closed=action.is_closed,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def collections_attempt_view(attempt) -> CollectionAttemptView:
    return CollectionAttemptView(
        id=attempt.id,
        case_id=attempt.case_id,
        action_id=attempt.action_id,
        sequence=attempt.sequence,
        kind=attempt.kind.value,
        outcome=attempt.outcome.value,
        channel=attempt.channel,
        reference=attempt.reference,
        detail=attempt.detail,
        executed_at=attempt.executed_at,
    )


def decision_view(decision: CollectionDecision) -> CollectionDecisionView:
    return CollectionDecisionView(
        id=decision.id,
        case_id=decision.case_id,
        cycle=decision.cycle,
        stage_index=decision.stage_index,
        signal=decision.signal.value,
        failure_class=decision.failure_class.value,
        selected=decision.selected.value if decision.selected is not None else "",
        action_id=decision.action_id,
        policy_code=decision.policy_code,
        policy_version=decision.policy_version,
        reason=decision.reason,
        reason_path=decision.reason_path,
        decided_at=decision.decided_at,
        due_at=decision.due_at,
    )


def collections_explanation_view(explanation) -> CollectionExplanationView:
    return CollectionExplanationView(
        case_id=explanation.case_id,
        state=explanation.state.value,
        signal=explanation.signal.value,
        stage_index=explanation.stage_index,
        policy_code=explanation.policy_code,
        policy_version=explanation.policy_version,
        next_kind=explanation.next_kind.value if explanation.next_kind is not None else "",
        next_action_at=explanation.next_action_at,
        grace_until=explanation.grace_until,
        reason=explanation.reason,
        path=explanation.path,
        blocked_reason=explanation.blocked_reason,
    )


def collections_activity_view(activity) -> CollectionActivityView:
    return CollectionActivityView(
        id=activity.id,
        case_id=activity.case_id,
        at=activity.at,
        kind=activity.kind,
        summary=activity.summary,
        actor_id=activity.actor_id,
        action_id=activity.action_id,
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionRepositories:
    """The six collection seams, injected. Interfaces only — implementations in persistence.

    Six, and only six. Nothing here reaches a payment, invoice, ledger, subscription, or account
    table; those arrive as *read* objects on the context or as commands dispatched into their own
    engines."""

    policies: object
    cases: object
    actions: object
    attempts: object
    decisions: object
    activity: object


@dataclass(frozen=True, slots=True)
class CollectionServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    case_lifecycle: CaseLifecycleService
    action_lifecycle: object
    guards: object
    decisions: object
    policy_rules: object
    signals: object
    schedule: object
    explainer: object
    inspector: object
    validator: object
    search: object
    notifiers: object


class _CollectionSupport:
    """Shared reads for the command and query sides.

    Both sides read the payment signal the same way, through the same helper, for the reason M9.16
    gives about positions: two paths deriving one fact from two sets of reads is how a dashboard
    and a scheduler come to disagree about whether a customer has paid."""

    def __init__(self, *, context, repos: CollectionRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services

    # ── the payment read boundary ───────────────────────────────────────────────

    def payment_signal(
        self, org_id: str, case: CollectionCase
    ) -> tuple[PaymentSignal, FailureClass]:
        """**Read** the Payment Engine's facts and translate them once.

        Reaches `context.payment_engine` — the same read-only repository group M9.16 owns — and
        touches nothing else. It reads the *latest* attempt rather than any aggregate the Payment
        Engine might expose, because an attempt describes a movement and that is the evidence this
        engine reasons about.

        Returns `(NONE, UNKNOWN)` when Payments is not wired, which is not an error: a Collections
        installation with no payment engine can still send notices, and pretending it had a failed
        payment would be an invention."""
        engine = getattr(self._ctx, "payment_engine", None)
        if engine is None or not case.payment_intent_id:
            return case.signal, case.failure_class
        intents = getattr(engine, "intents", None)
        attempts = getattr(engine, "attempts", None)
        if intents is None:
            return case.signal, case.failure_class
        intent = intents.get(org_id, case.payment_intent_id)
        if intent is None:
            return case.signal, case.failure_class
        intent_state = intent.state.value
        attempt_state = ""
        failure_code = ""
        if attempts is not None:
            history = attempts.for_intent(org_id, case.payment_intent_id)
            if history:
                latest = history[-1]
                attempt_state = latest.state.value
                failure_code = getattr(latest, "failure_code", "") or ""
        signal = self._svc.signals.signal_for(
            intent_state=intent_state, attempt_state=attempt_state
        )
        failure_class = self._svc.signals.failure_class_for(failure_code=failure_code)
        return signal, failure_class

    def case_or_404(self, org_id: str, case_id: str) -> CollectionCase:
        found = self._repos.cases.get(org_id, case_id)
        if found is None:
            raise _NotFound(f"collection case {case_id!r} not found")
        return found

    def action_or_404(self, org_id: str, action_id: str) -> CollectionAction:
        found = self._repos.actions.get(org_id, action_id)
        if found is None:
            raise _NotFound(f"collection action {action_id!r} not found")
        return found

    def policy_or_404(self, org_id: str, policy_id: str) -> CollectionPolicy:
        found = self._repos.policies.get(org_id, policy_id)
        if found is None:
            raise _NotFound(f"collection policy {policy_id!r} not found")
        return found

    def bound_policy(self, case: CollectionCase) -> CollectionPolicy:
        """**The version the case was bound to** — never today's.

        This single method is the whole of policy reproducibility. Reading the latest version here
        would mean a strategy edited on Tuesday silently re-judges a decision taken on Monday, and
        the decision trail would point at rules that were never applied."""
        return self.policy_or_404(case.org_id, case.policy_id)


class CollectionUseCases(_CollectionSupport):
    """Collection orchestration. One transaction everywhere except where something leaves."""

    def __init__(
        self,
        *,
        context,
        repos: CollectionRepositories,
        factory,
        services: CollectionServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
        payment_retry: Callable[..., object] | None = None,
        payment_retry_command: type | None = None,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        # **The payment boundary.** A callable into M9.16's own use cases, injected — not a
        # repository, not a provider, and not an adapter. When it is `None` the engine still plans
        # retries and records that it could not execute them, which is the honest behaviour for an
        # installation with no payment engine wired.
        self._payment_retry = payment_retry
        self._payment_retry_command = payment_retry_command

    # ── policy ──────────────────────────────────────────────────────────────────

    def publish_policy(self, command: PublishCollectionPolicy) -> CollectionPolicyView:
        """Append one immutable version. Republishing the same version is a duplicate, not an
        error — a deploy that runs its seed twice must not fail."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            built = self._factory.policy(
                org_id=command.org_id,
                code=command.code,
                version=command.version,
                stages=tuple(command.stages),
                obligation_kind=command.obligation_kind,
                max_cycles=command.max_cycles,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored, is_new = self._repos.policies.add(built)
            uow.stage(
                self._event(
                    command, stored.id, code=stored.code, version=str(stored.version),
                    disposition=CommandDisposition.NEW.value
                    if is_new
                    else CommandDisposition.DUPLICATE.value,
                )
            )
            return collections_policy_view(stored)

    def deactivate_policy(self, command: DeactivateCollectionPolicy) -> CollectionPolicyView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            policy = self.policy_or_404(command.org_id, command.policy_id)
            withdrawn = replace(policy, is_active=False)
            stored = self._repos.policies.deactivate(withdrawn)
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return collections_policy_view(stored)

    # ── the case ────────────────────────────────────────────────────────────────

    def open_case(self, command: OpenCollectionCase) -> CollectionOutcomeView:
        """Open a recovery. **One obligation, one case**, arbitrated by a derived primary key."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            policy = self._repos.policies.latest(command.org_id, command.policy_code)
            if policy is None:
                raise _Conflict(
                    f"no active collection policy {command.policy_code!r} is published; a "
                    "strategy must be declared before a recovery can be opened"
                )
            built = self._factory.case(
                org_id=command.org_id,
                account_id=command.account_id,
                obligation_kind=command.obligation_kind,
                obligation_id=command.obligation_id,
                policy=policy,
                currency=command.currency,
                payment_intent_id=command.payment_intent_id,
                actor_id=command.actor_id,
            )
            stored, is_new = self._repos.cases.open(built)
            disposition = CommandDisposition.NEW if is_new else CommandDisposition.DUPLICATE
            if is_new:
                self._log(command, stored, kind="opened", summary="recovery opened")
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    obligation=f"{stored.obligation_kind.value}:{stored.obligation_id}",
                    disposition=disposition.value,
                )
            )
            return CollectionOutcomeView(
                case=self._case_view(stored),
                disposition=disposition.value,
                note="" if is_new else "a recovery for this obligation already exists",
            )

    def evaluate_case(self, command: EvaluateCollectionCase) -> CollectionOutcomeView:
        """Decide what should happen next, and plan at most one action.

        Safe to call repeatedly and safe to call concurrently. Repetition is handled by derived
        ids — an unchanged case produces the same decision id and the same action id, and both
        repositories return the stored row rather than a second one. Concurrency is handled by the
        compare-and-set on the case: the loser re-reads and re-decides, and by then the winner's
        action already exists, so it plans nothing."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = command.at if command.at is not None else self._clock.now()
        with self._uow.new() as uow:
            for _ in range(_CAS_ATTEMPTS):
                case = self.case_or_404(command.org_id, command.case_id)
                if case.is_quiescent:
                    # Not an error. A scheduler sweeping a list that included a case someone
                    # closed a moment ago must be a no-op, not an exception.
                    return CollectionOutcomeView(
                        case=self._case_view(case),
                        disposition=CommandDisposition.REJECTED.value,
                        note=f"the case is {case.state.value} and is closed to automation",
                    )
                signal, failure_class = self.payment_signal(command.org_id, case)
                try:
                    settled = self._settle_or_hold(command, case, signal, failure_class, at, uow)
                    if settled is not None:
                        return settled
                    waiting = self._wait_or_mature(command, case, at, uow)
                    if waiting is not None:
                        return waiting
                    policy = self.bound_policy(case)
                    outcome = self._svc.decisions.decide(
                        case,
                        policy,
                        signal=signal,
                        at=at,
                        failure_class=failure_class,
                        since=case.updated_at or at,
                    )
                    return self._apply_decision(
                        command, case, policy, outcome, signal, failure_class, at, uow
                    )
                except CaseStale:
                    continue
            raise _Conflict(
                f"collection case {command.case_id!r} is under contention; "
                f"{_CAS_ATTEMPTS} evaluations were overtaken by other workers"
            )

    def _settle_or_hold(
        self, command, case: CollectionCase, signal, failure_class, at, uow
    ) -> CollectionOutcomeView | None:
        """Recovery and ambiguity are decided **before** anything about the strategy is consulted.

        A paid obligation is not a scheduling question, and an unknown payment outcome is not a
        stage the ladder can climb past. Both short-circuit here so that no arrangement of policy
        stages can produce a chase against money that already arrived or an outcome nobody knows —
        the M9.16-R1 invariant, expressed as control flow rather than as a check inside a loop."""
        if signal is PaymentSignal.SUCCEEDED and not case.is_settled:
            return self._settle(command, case, CaseTrigger.RECOVER, "the obligation was collected",
                                signal, failure_class, at, uow)
        if signal is PaymentSignal.AMBIGUOUS and case.state is not CaseState.BLOCKED:
            return self._settle(
                command,
                case,
                CaseTrigger.BLOCK,
                "the payment outcome is unknown; reconciliation must resolve it before any "
                "further collection action",
                signal,
                failure_class,
                at,
                uow,
            )
        return None

    def _settle(
        self, command, case: CollectionCase, trigger, reason, signal, failure_class, at, uow
    ) -> CollectionOutcomeView:
        moved = replace(case, signal=signal, failure_class=failure_class)
        if self._svc.case_lifecycle.can(moved.state, trigger):
            moved = self._svc.case_lifecycle.apply(moved, trigger, at=at, reason=reason)
        else:
            moved = replace(moved, version=moved.version + 1, updated_at=at)
        stored = self._repos.cases.advance(moved, expected_version=case.version)
        if stored.is_terminal or stored.state is CaseState.BLOCKED:
            self._retire_open_actions(command, stored, reason)
        self._factory.emit_case_state(stored, actor_id=command.actor_id, reason=reason)
        self._log(command, stored, kind=trigger.value, summary=reason)
        uow.stage(self._event(command, stored.id, state=stored.state.value, signal=signal.value))
        return CollectionOutcomeView(
            case=self._case_view(stored),
            disposition=CommandDisposition.NEW.value,
            note=reason,
        )

    def _wait_or_mature(
        self, command, case: CollectionCase, at: float, uow
    ) -> CollectionOutcomeView | None:
        """The clock's only job in this engine: turning a scheduled action into a due one.

        `SCHEDULED` with a `next_action_at` in the future is simply not ready — reported as
        `DEFERRED` rather than as an error, because a scheduler sweeping a page of cases must be
        able to call this on every one of them. `SCHEDULED` with no `next_action_at` means the
        previous action completed and the next stage has not been decided yet, which is the one
        case that falls through to the decision engine.

        `ACTION_DUE` and `IN_PROGRESS` are also `DEFERRED`: an action is already outstanding, and
        planning a second one is exactly the duplicate this engine exists to prevent."""
        if case.state is CaseState.ACTION_DUE:
            return self._deferred(case, "an action is already due on this case")
        if case.state is CaseState.IN_PROGRESS:
            return self._deferred(case, "an action is already being executed on this case")
        if case.state is CaseState.SCHEDULED and case.next_action_at is not None:
            if not case.is_due(at):
                return self._deferred(
                    case, f"the next action is not due until {case.next_action_at}"
                )
            moved = self._svc.case_lifecycle.apply(case, CaseTrigger.MATURE, at=at)
            stored = self._repos.cases.advance(moved, expected_version=case.version)
            self._log(command, stored, kind="matured", summary="the planned action is now due")
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return CollectionOutcomeView(
                case=self._case_view(stored),
                disposition=CommandDisposition.NEW.value,
                note="the planned action is now due",
            )
        return None

    def _deferred(self, case: CollectionCase, note: str) -> CollectionOutcomeView:
        return CollectionOutcomeView(
            case=self._case_view(case),
            disposition=CommandDisposition.DEFERRED.value,
            note=note,
        )

    def _apply_decision(
        self, command, case, policy, outcome, signal, failure_class, at, uow
    ) -> CollectionOutcomeView:
        """Write one decision, at most one action, and one case transition. Version-checked."""
        expected = case.version
        planned: CollectionAction | None = None
        moved = replace(case, signal=signal, failure_class=failure_class)

        # **The decision row is the memo.** Its id is derived from `(case, cycle, stage, signal)`,
        # so a repeated evaluation of an unchanged case finds it already there — and reuses the
        # action it names rather than planning a second one. Without this, a stage that cannot
        # complete (a notifier that keeps deferring) would plan a fresh action on every sweep and
        # chase a customer forever. Consulted *before* anything is written, so the check and the
        # write cannot disagree.
        decision_id = derive_decision_id(
            case.id, case.cycle, outcome.stage_index, signal.value
        )
        already = self._repos.decisions.get(command.org_id, decision_id)
        if already is not None:
            existing = (
                self._repos.actions.get(command.org_id, already.action_id)
                if already.action_id
                else None
            )
            if existing is not None and existing.is_closed:
                # The rung was attempted and did not complete, and the strategy has nothing else
                # to say. A human decides what happens next; automation stops rather than looping.
                return self._settle(
                    command,
                    case,
                    CaseTrigger.ESCALATE,
                    f"stage {outcome.stage_index} was attempted and could not complete",
                    signal,
                    failure_class,
                    at,
                    uow,
                )
            return CollectionOutcomeView(
                case=self._case_view(case),
                disposition=CommandDisposition.DUPLICATE.value,
                action=self._action_view(existing) if existing is not None else None,
                decision=decision_view(already),
                note="this case has already been evaluated in this stage against this signal",
            )

        if outcome.plans_action:
            sequence = case.next_sequence
            built = self._factory.action(
                case,
                kind=outcome.selected,
                sequence=sequence,
                stage_index=outcome.stage_index,
                due_at=outcome.due_at or at,
                policy=policy,
                at=at,
                actor_id=command.actor_id,
            )
            planned, is_new = self._repos.actions.plan(built)
            if is_new:
                moved = replace(moved, actions_planned=sequence)

        decision = self._factory.decision(
            case,
            outcome,
            policy=policy,
            signal=signal,
            failure_class=failure_class,
            at=at,
            action_id=planned.id if planned is not None else "",
        )
        stored_decision, _ = self._repos.decisions.append(decision)

        trigger = outcome.case_trigger
        if trigger is not None and self._svc.case_lifecycle.can(moved.state, trigger):
            moved = self._svc.case_lifecycle.apply(moved, trigger, at=at, reason=outcome.reason)
        elif trigger is not None and self._svc.case_lifecycle.establishes(moved.state, trigger):
            # Already where this trigger leads. A case that is scheduled and is being scheduled
            # again has not transitioned — what changed is *what* it is scheduled for. Recorded as
            # a version bump so a concurrent worker's read still goes stale, and not as an FSM
            # edge, because a self-loop here would publish a scheduler sweep as a business fact.
            moved = replace(moved, version=moved.version + 1, updated_at=at)
        elif trigger is not None:
            raise _Conflict(
                f"the collection strategy selected {trigger.value!r} for a case in state "
                f"{moved.state.value!r}, which the lifecycle does not permit"
            )
        else:
            moved = replace(moved, version=moved.version + 1, updated_at=at)
        if moved.state is CaseState.SCHEDULED:
            moved = replace(
                moved,
                stage_index=outcome.stage_index,
                next_action_at=outcome.due_at,
                grace_until=outcome.grace_until,
            )

        stored = self._repos.cases.advance(moved, expected_version=expected)
        self._factory.emit_case_state(stored, actor_id=command.actor_id, reason=outcome.reason)
        self._log(
            command,
            stored,
            kind="evaluated",
            summary=outcome.reason,
            action_id=planned.id if planned is not None else "",
        )
        uow.stage(
            self._event(
                command,
                stored.id,
                state=stored.state.value,
                signal=signal.value,
                selected=outcome.selected.value if outcome.selected is not None else "",
            )
        )
        return CollectionOutcomeView(
            case=self._case_view(stored),
            disposition=CommandDisposition.NEW.value,
            action=self._action_view(planned) if planned is not None else None,
            decision=decision_view(stored_decision),
            note=outcome.reason,
        )

    # ── executing an action: the three-phase path ───────────────────────────────

    def claim_action(self, command: ClaimCollectionAction) -> CollectionActionView:
        """Take exclusive hold. A conditional UPDATE, so exactly one worker wins."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            at = command.at if command.at is not None else self._clock.now()
            action = self.action_or_404(command.org_id, command.action_id)
            self._svc.guards.guard_claimable(action, at=at)
            held = self._svc.action_lifecycle.apply(
                action, ActionTrigger.CLAIM, at=at, actor_id=command.actor_id
            )
            stored = self._repos.actions.claim(held, expected_state=ActionState.PLANNED.value)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return self._action_view(stored)

    def execute_action(self, command: ExecuteCollectionAction) -> CollectionOutcomeView:
        """**Three phases.** Claim and commit; do the external thing; record and commit.

        Phase 1 ends with a committed `claimed` row, so a crash in phase 2 leaves a record naming
        exactly what was in flight. Phase 2 holds no transaction, because a notifier that blocks
        for thirty seconds must not hold a database lock for thirty seconds. Phase 3 records what
        came back and advances the case under a compare-and-set."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = command.at if command.at is not None else self._clock.now()

        # ── phase 1: claim, and re-check the world as it is *now* ───────────────
        with self._uow.new():
            action = self.action_or_404(command.org_id, command.action_id)
            case = self.case_or_404(command.org_id, action.case_id)
            signal, failure_class = self.payment_signal(command.org_id, case)
            # **Overtaken-by-events is checked first, and it is not an error.**
            #
            # This is the payment-success-versus-retry race as it actually arrives: a worker holds
            # a planned retry, and between planning and executing the money lands. Guarding the
            # case first would raise "this case is recovered" at a worker that did exactly the
            # right thing, and a scheduler cannot tell that exception apart from a real fault. The
            # action is retired instead, and the fact is emitted — a chase that did *not* happen is
            # invisible otherwise.
            if signal is PaymentSignal.SUCCEEDED or case.is_settled:
                return self._obsolete(
                    command, action, case, "the obligation was collected", at=at
                )
            self._svc.guards.guard_not_settled(case)
            # **The third gate.** The decision engine refused twice when this was planned; this
            # checks the signal as it is at the moment of execution, which is the only check that
            # sees a reconciliation that moved the attempt back to ambiguous in between.
            self._svc.guards.guard_money_request(action, signal)
            self._svc.guards.guard_claimable(action, at=at)
            held = self._svc.action_lifecycle.apply(
                action, ActionTrigger.CLAIM, at=at, actor_id=command.actor_id
            )
            claimed = self._repos.actions.claim(held, expected_state=ActionState.PLANNED.value)
            # The case moves with the action, in the same transaction. A `claimed` action under a
            # `scheduled` case would be two records disagreeing about whether anything is happening.
            #
            # A scheduled case matures on the way through rather than requiring a separate
            # evaluation first: the action's own due time has already been checked by
            # `guard_claimable`, so demanding a second call to establish the same fact would only
            # make the honest path longer without making it safer.
            case = self._mature_and_claim(case, at=at)

        # ── phase 2: the external effect. No transaction is open. ───────────────
        outcome, reference, detail = self._perform(command, claimed, case)

        # ── phase 3: record what happened ───────────────────────────────────────
        with self._uow.new() as uow:
            fresh = self.case_or_404(command.org_id, claimed.case_id)
            sequence = claimed.next_attempt_sequence
            evidence = self._factory.attempt(
                claimed,
                sequence=sequence,
                outcome=outcome,
                channel=detail.get("channel", ""),
                reference=reference,
                detail=detail.get("detail", ""),
                at=at,
            )
            self._repos.attempts.append(evidence)
            trigger = (
                ActionTrigger.EXECUTE
                if outcome in (AttemptOutcome.SUCCEEDED, AttemptOutcome.DEFERRED)
                else ActionTrigger.FAIL
            )
            settled = self._svc.action_lifecycle.apply(
                claimed,
                trigger,
                at=at,
                actor_id=command.actor_id,
                result_ref=reference,
                note=detail.get("detail", ""),
            )
            settled = replace(settled, attempts_made=sequence)
            stored_action = self._repos.actions.settle(settled)
            if stored_action.state is ActionState.EXECUTED:
                self._factory.emit_action_executed(
                    stored_action, actor_id=command.actor_id, detail=detail.get("detail", "")
                )
            advanced = self._advance_after_execution(
                command, fresh, stored_action, outcome, at=at
            )
            uow.stage(
                self._event(
                    command,
                    stored_action.id,
                    kind=stored_action.kind.value,
                    outcome=outcome.value,
                    state=advanced.state.value,
                )
            )
            return CollectionOutcomeView(
                case=self._case_view(advanced),
                disposition=CommandDisposition.NEW.value,
                action=self._action_view(stored_action),
                note=detail.get("detail", ""),
            )

    def _mature_and_claim(self, case: CollectionCase, *, at: float) -> CollectionCase:
        """Walk the case to `IN_PROGRESS`, one legal edge at a time, under compare-and-set.

        `SCHEDULED → ACTION_DUE → IN_PROGRESS` rather than a jump, so both intermediate states are
        real rather than decorative and an operator watching a case sees each of them."""
        for trigger in (CaseTrigger.MATURE, CaseTrigger.CLAIM):
            if not self._svc.case_lifecycle.can(case.state, trigger):
                continue
            moved = self._svc.case_lifecycle.apply(case, trigger, at=at)
            case = self._repos.cases.advance(moved, expected_version=case.version)
        return case

    def _perform(
        self, command: ExecuteCollectionAction, action: CollectionAction, case: CollectionCase
    ) -> tuple[AttemptOutcome, str, dict[str, str]]:
        """The single external edge. **Everything that leaves Billing goes through here.**

        Two destinations and no third: a provider-neutral notifier port, and the Payment Engine's
        own command. Both are injected; neither is reachable from the Foundation.

        A raised call becomes `DEFERRED`, never `FAILED`, for the reason M9.16 gives about
        transports: "we could not confirm the message went" is indistinguishable from "the
        acknowledgement was lost", and treating the second as the first is how a customer receives
        five copies of the same final notice. `NoticeNotSent` is the one assertion a notifier may
        make that nothing left, and only it produces a local failure."""
        if action.kind in (ActionKind.NOTIFY, ActionKind.FINAL_NOTICE):
            notifier = self._svc.notifiers.resolve(command.notifier_code)
            request = NoticeRequest(
                org_id=case.org_id,
                case_id=case.id,
                action_id=action.id,
                account_id=case.account_id,
                obligation_kind=case.obligation_kind,
                obligation_id=case.obligation_id,
                kind=action.kind,
                idempotency_key=notice_key(action),
            )
            try:
                answer = notifier.deliver(request)
            except NoticeNotSent as never_sent:
                return AttemptOutcome.NOT_SENT, "", {"detail": str(never_sent) or "not sent"}
            except Exception as transport:  # noqa: BLE001
                return (
                    AttemptOutcome.DEFERRED,
                    "",
                    {"detail": f"transport error: {type(transport).__name__}"},
                )
            return (
                answer.outcome,
                answer.reference,
                {"channel": answer.channel, "detail": answer.detail},
            )

        if action.kind is ActionKind.REQUEST_PAYMENT_RETRY:
            return self._request_payment_retry(command, action, case)

        if action.kind is ActionKind.AWAIT_RECONCILIATION:
            # Nothing leaves. The whole point of this action is that Collections waits for a
            # domain it does not own; recording the wait is the execution.
            return (
                AttemptOutcome.SUCCEEDED,
                case.payment_intent_id,
                {"detail": "waiting for the payment engine to resolve the outcome"},
            )

        if action.kind is ActionKind.ESCALATE_TO_OPERATOR:
            return AttemptOutcome.SUCCEEDED, "", {"detail": "handed to an operator"}

        return AttemptOutcome.SUCCEEDED, "", {"detail": action.kind.value}

    def _request_payment_retry(
        self, command: ExecuteCollectionAction, action: CollectionAction, case: CollectionCase
    ) -> tuple[AttemptOutcome, str, dict[str, str]]:
        """**Ask Payments to move money.** Through its command, never its repositories.

        Collections supplies an intent id and receives a receipt. It does not choose a provider,
        compute an amount, touch an attempt, or interpret a provider outcome — every one of those
        is M9.16's, and the next evaluation will read the result as a signal like any other."""
        if self._payment_retry is None or self._payment_retry_command is None:
            return (
                AttemptOutcome.DEFERRED,
                "",
                {"detail": "no payment engine is wired; the retry was recorded, not sent"},
            )
        if not case.payment_intent_id:
            return (
                AttemptOutcome.FAILED,
                "",
                {"detail": "this case names no payment objective to retry"},
            )
        try:
            receipt = self._payment_retry(
                self._payment_retry_command(
                    actor_id=command.actor_id,
                    org_id=command.org_id,
                    intent_id=case.payment_intent_id,
                    correlation_id=command.correlation_id or action.id,
                )
            )
        except Exception as refused:  # noqa: BLE001
            # A refusal from Payments is *information*, not a crash: the objective may be closed,
            # blocked by an unresolved attempt, or unroutable. Recorded as a failed collection
            # attempt so the next evaluation can move the strategy on.
            return AttemptOutcome.FAILED, "", {"detail": f"payments refused: {refused}"}
        reference = getattr(getattr(receipt, "attempt", None), "id", "") or getattr(
            receipt, "id", ""
        )
        return AttemptOutcome.SUCCEEDED, str(reference), {"detail": "payment retry requested"}

    def _advance_after_execution(
        self,
        command,
        case: CollectionCase,
        action: CollectionAction,
        outcome: AttemptOutcome,
        *,
        at: float,
    ) -> CollectionCase:
        """Move the case on, under compare-and-set. Never a blind write.

        An executed action does not itself advance the stage — the *next evaluation* does, after
        re-reading the payment. That ordering matters: advancing here would decide the next stage
        from the world as it was before the action, and the action is precisely the thing that may
        have changed it."""
        if action.kind is ActionKind.ESCALATE_TO_OPERATOR:
            trigger = CaseTrigger.ESCALATE
        elif action.kind is ActionKind.AWAIT_RECONCILIATION:
            trigger = CaseTrigger.BLOCK
        elif case.state is CaseState.IN_PROGRESS:
            trigger = CaseTrigger.SCHEDULE
        else:
            trigger = None

        moved = replace(case, stage_index=case.stage_index + 1) if _advances_stage(
            action, outcome
        ) else case
        if trigger is not None and self._svc.case_lifecycle.can(moved.state, trigger):
            moved = self._svc.case_lifecycle.apply(
                moved, trigger, at=at, reason=f"{action.kind.value} executed"
            )
        else:
            moved = replace(moved, version=moved.version + 1, updated_at=at)
        moved = replace(moved, next_action_at=None)
        try:
            stored = self._repos.cases.advance(moved, expected_version=case.version)
        except CaseStale:
            # Something more important happened to this case while the notice was in flight — a
            # payment landed, an operator cancelled it. The evidence of what we did is already
            # committed; the case belongs to whoever changed it.
            return self.case_or_404(command.org_id, case.id)
        self._factory.emit_case_state(stored, actor_id=command.actor_id)
        self._log(
            command, stored, kind="executed", summary=action.kind.value, action_id=action.id
        )
        return stored

    def _obsolete(
        self, command, action: CollectionAction, case: CollectionCase, reason: str, *, at: float
    ) -> CollectionOutcomeView:
        """Retire an action overtaken by events, and recover the case if the money landed.

        Idempotent: an action another worker already retired is returned as it stands. Two workers
        reaching this at once is the *normal* shape of the race it exists for — one records the
        payment success and retires the open actions, the other arrives holding one of them — and
        raising at the second would turn a correctly-handled race into an alert."""
        if action.is_closed:
            return CollectionOutcomeView(
                case=self._case_view(case),
                disposition=CommandDisposition.REJECTED.value,
                action=self._action_view(action),
                note=f"this action was already {action.state.value}: {reason}",
            )
        retired = self._svc.action_lifecycle.apply(
            action, ActionTrigger.OBSOLETE, at=at, actor_id=command.actor_id, note=reason
        )
        stored_action = self._repos.actions.settle(retired)
        self._factory.emit_action_obsolete(
            stored_action, actor_id=command.actor_id, reason=reason
        )
        recovered = case
        if self._svc.case_lifecycle.can(case.state, CaseTrigger.RECOVER):
            moved = self._svc.case_lifecycle.apply(
                replace(case, signal=PaymentSignal.SUCCEEDED),
                CaseTrigger.RECOVER,
                at=at,
                reason=reason,
            )
            try:
                recovered = self._repos.cases.advance(moved, expected_version=case.version)
                self._factory.emit_case_state(recovered, actor_id=command.actor_id, reason=reason)
            except CaseStale:
                recovered = self.case_or_404(command.org_id, case.id)
        return CollectionOutcomeView(
            case=self._case_view(recovered),
            disposition=CommandDisposition.REJECTED.value,
            action=self._action_view(stored_action),
            note=reason,
        )

    def obsolete_action(self, command: ObsoleteCollectionAction) -> CollectionActionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            action = self.action_or_404(command.org_id, command.action_id)
            retired = self._svc.action_lifecycle.apply(
                action,
                ActionTrigger.OBSOLETE,
                at=self._clock.now(),
                actor_id=command.actor_id,
                note=command.reason,
            )
            stored = self._repos.actions.settle(retired)
            self._factory.emit_action_obsolete(
                stored, actor_id=command.actor_id, reason=command.reason
            )
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return self._action_view(stored)

    # ── reacting to payment facts ───────────────────────────────────────────────

    def record_signal(self, command: RecordPaymentSignal) -> CollectionOutcomeView:
        """Take a payment state and let it move the case. **Interpreted here, not by the caller.**

        A payment success recovers the case *and* retires every open action, in one transaction.
        Doing only the first would leave a planned retry that a scheduler would happily execute
        against an invoice that has been paid."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            case = self.case_or_404(command.org_id, command.case_id)
            signal = self._svc.signals.signal_for(
                intent_state=command.intent_state, attempt_state=command.attempt_state
            )
            failure_class = self._svc.signals.failure_class_for(
                failure_code=command.failure_code
            )
            if case.is_settled and signal is not PaymentSignal.SUCCEEDED:
                raise _Conflict(
                    f"collection case {case.id!r} is recovered; a later payment signal does not "
                    "reopen a recovery"
                )
            moved = replace(
                case,
                signal=signal,
                failure_class=failure_class,
                payment_intent_id=command.payment_intent_id or case.payment_intent_id,
            )
            trigger = None
            reason = f"payment signal {signal.value}"
            if signal is PaymentSignal.SUCCEEDED and not case.is_settled:
                trigger = CaseTrigger.RECOVER
                reason = "the obligation was collected"
            elif signal is PaymentSignal.AMBIGUOUS and self._svc.case_lifecycle.can(
                case.state, CaseTrigger.BLOCK
            ):
                trigger = CaseTrigger.BLOCK
                reason = "the payment outcome is unknown; reconciliation must resolve it"
            elif (
                signal in (PaymentSignal.FAILED, PaymentSignal.CANCELLED, PaymentSignal.EXPIRED)
                and case.state is CaseState.BLOCKED
            ):
                trigger = CaseTrigger.UNBLOCK
                reason = "the payment outcome was resolved; collection may continue"

            if trigger is not None:
                moved = self._svc.case_lifecycle.apply(moved, trigger, at=at, reason=reason)
            else:
                moved = replace(moved, version=moved.version + 1, updated_at=at)
            stored = self._repos.cases.advance(moved, expected_version=case.version)
            if trigger is CaseTrigger.RECOVER:
                self._retire_open_actions(command, stored, reason)
            self._factory.emit_case_state(stored, actor_id=command.actor_id, reason=reason)
            self._log(command, stored, kind="signal", summary=reason)
            uow.stage(
                self._event(
                    command, stored.id, signal=signal.value, state=stored.state.value
                )
            )
            return CollectionOutcomeView(
                case=self._case_view(stored),
                disposition=CommandDisposition.NEW.value,
                note=reason,
            )

    def _retire_open_actions(self, command, case: CollectionCase, reason: str) -> None:
        """Every planned or claimed action on a recovered case becomes obsolete, in the same
        transaction as the recovery. **This is the payment-success-versus-retry race, closed.**"""
        for action in self._repos.actions.open_for_case(case.org_id, case.id):
            retired = self._svc.action_lifecycle.apply(
                action,
                ActionTrigger.OBSOLETE,
                at=self._clock.now(),
                actor_id=command.actor_id,
                note=reason,
            )
            self._repos.actions.settle(retired)
            self._factory.emit_action_obsolete(
                retired, actor_id=command.actor_id, reason=reason
            )

    # ── operator decisions ──────────────────────────────────────────────────────

    def block_case(self, command: BlockCollectionCase) -> CollectionOutcomeView:
        return self._transition(
            command, command.case_id, CaseTrigger.BLOCK, command.reason, P.BILLING_MANAGE
        )

    def escalate_case(self, command: EscalateCollectionCase) -> CollectionOutcomeView:
        return self._transition(
            command, command.case_id, CaseTrigger.ESCALATE, command.reason, P.BILLING_MANAGE
        )

    def cancel_case(self, command: CancelCollectionCase) -> CollectionOutcomeView:
        return self._transition(
            command, command.case_id, CaseTrigger.CANCEL, command.reason, P.BILLING_MANAGE
        )

    def _transition(
        self, command, case_id: str, trigger: CaseTrigger, reason: str, permission
    ) -> CollectionOutcomeView:
        """One attributed transition, version-checked.

        There is no `force_state` and no `admin_override`. An operator's authority is to fire a
        trigger the machine already allows; it is not to put a case into a state the machine says
        is unreachable, because that state would then have no explanation and no audit meaning."""
        self._authz.require(command.actor_id, command.org_id, permission)
        with self._uow.new() as uow:
            case = self.case_or_404(command.org_id, case_id)
            self._svc.guards.guard_not_settled(case)
            moved = self._svc.case_lifecycle.apply(
                case, trigger, at=self._clock.now(), reason=reason
            )
            stored = self._repos.cases.advance(moved, expected_version=case.version)
            if stored.is_terminal:
                self._retire_open_actions(command, stored, reason or trigger.value)
            self._factory.emit_case_state(stored, actor_id=command.actor_id, reason=reason)
            self._log(command, stored, kind=trigger.value, summary=reason)
            uow.stage(self._event(command, stored.id, state=stored.state.value, reason=reason))
            return CollectionOutcomeView(
                case=self._case_view(stored),
                disposition=CommandDisposition.NEW.value,
                note=reason,
            )

    def open_cycle(self, command: OpenCollectionCycle) -> CollectionOutcomeView:
        """Begin a second recovery on an exhausted case. **Never a rewrite of the first.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            case = self.case_or_404(command.org_id, command.case_id)
            self._svc.guards.guard_not_settled(case)
            if case.state is not CaseState.EXHAUSTED:
                raise _Conflict(
                    f"collection case {case.id!r} is {case.state.value}; a new cycle begins only "
                    "after the previous strategy is exhausted"
                )
            policy = self._repos.policies.latest(
                command.org_id, command.policy_code or case.policy_code
            )
            if policy is None:
                raise _Conflict("no active collection policy is published for this code")
            if case.cycle >= policy.max_cycles:
                raise _Conflict(
                    f"collection policy {policy.version_key} permits {policy.max_cycles} "
                    f"cycle(s); this case has completed {case.cycle}"
                )
            moved = self._factory.open_cycle(
                case, policy=policy, at=self._clock.now(), actor_id=command.actor_id
            )
            stored = self._repos.cases.advance(moved, expected_version=case.version)
            self._log(
                command,
                stored,
                kind="cycle_opened",
                summary=command.reason or f"cycle {stored.cycle}",
            )
            uow.stage(self._event(command, stored.id, cycle=str(stored.cycle)))
            return CollectionOutcomeView(
                case=self._case_view(stored),
                disposition=CommandDisposition.NEW.value,
                note=f"cycle {stored.cycle} opened under {policy.version_key}",
            )

    # ── internals ───────────────────────────────────────────────────────────────

    def _case_view(self, case: CollectionCase) -> CollectionCaseView:
        return case_view(case, self._svc.case_lifecycle.allowed(case.state))

    def _action_view(self, action: CollectionAction) -> CollectionActionView:
        return action_view(action, self._svc.action_lifecycle.allowed(action.state))

    def _log(self, command, case: CollectionCase, *, kind: str, summary: str = "",
             action_id: str = "") -> None:
        self._repos.activity.append(
            self._factory.activity(
                case,
                kind=kind,
                summary=summary,
                actor_id=command.actor_id,
                action_id=action_id,
                at=self._clock.now(),
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.COLLECTION_ORCHESTRATED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _advances_stage(action: CollectionAction, outcome: AttemptOutcome) -> bool:
    """Whether executing this action moves the strategy on.

    **A failed action still climbs the rung.** We tried the thing the stage asked for and it did
    not work; that is a completed step in a recovery, not a reason to try the same step forever.
    Treating failure as "stay here" is how a strategy re-plans stage 1 indefinitely and a customer
    is chased without end — which is exactly what the first end-to-end drain of this engine did
    before this line said `in`, and it is the reason the drain test exists.

    A **deferred** or **not-sent** outcome does not advance it: nothing reached anyone, so the rung
    genuinely has not been climbed and repeating it is correct. That is bounded elsewhere — a
    repeated evaluation derives the same decision id and plans no second action.

    Awaiting reconciliation and escalating never advance it either: the case is blocked or handed
    over, and the stage it resumes at is the one it was holding."""
    if action.kind in (ActionKind.AWAIT_RECONCILIATION, ActionKind.ESCALATE_TO_OPERATOR):
        return False
    return outcome in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED)


class CollectionQueries(_CollectionSupport):
    """Read side. Reaches no notifier, no provider, and no payment *command* — only reads."""

    def __init__(self, *, context, repos, services, authz, clock) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz
        self._clock = clock

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    def get_case(self, query: GetCollectionCase) -> CollectionCaseView:
        self._read(query)
        case = self.case_or_404(query.org_id, query.case_id)
        return case_view(case, self._svc.case_lifecycle.allowed(case.state))

    def list_cases(self, query: ListCollectionCases) -> tuple[CollectionCaseView, ...]:
        self._read(query)
        if query.account_id:
            found = self._repos.cases.for_account(query.org_id, query.account_id, query.limit)
        elif query.states:
            found = self._repos.cases.by_state(query.org_id, query.states, query.limit)
        else:
            found = self._repos.cases.all(query.org_id, query.limit)
        return tuple(
            case_view(c, self._svc.case_lifecycle.allowed(c.state)) for c in found
        )

    def case_for_obligation(self, query: GetCaseForObligation) -> CollectionCaseView | None:
        self._read(query)
        found = self._repos.cases.for_obligation(
            query.org_id, query.obligation_kind.value, query.obligation_id
        )
        if found is None:
            return None
        return case_view(found, self._svc.case_lifecycle.allowed(found.state))

    def list_actions(self, query: ListCollectionActions) -> tuple[CollectionActionView, ...]:
        self._read(query)
        found = self._repos.actions.for_case(query.org_id, query.case_id)
        return tuple(
            action_view(a, self._svc.action_lifecycle.allowed(a.state)) for a in found
        )

    def get_action(self, query: GetCollectionAction) -> CollectionActionView:
        self._read(query)
        action = self.action_or_404(query.org_id, query.action_id)
        return action_view(action, self._svc.action_lifecycle.allowed(action.state))

    def list_attempts(self, query: ListCollectionAttempts) -> tuple[CollectionAttemptView, ...]:
        self._read(query)
        if query.action_id:
            found = self._repos.attempts.for_action(query.org_id, query.action_id)
        else:
            found = self._repos.attempts.for_case(query.org_id, query.case_id)
        return tuple(collections_attempt_view(a) for a in found)

    def list_decisions(self, query: ListCollectionDecisions) -> tuple[CollectionDecisionView, ...]:
        self._read(query)
        found = self._repos.decisions.for_case(query.org_id, query.case_id)
        return tuple(decision_view(d) for d in found)

    def actions_due(self, query: ActionsDue) -> tuple[CollectionActionView, ...]:
        """What a scheduler outside this plane asks for. A read; nothing is claimed here."""
        self._read(query)
        at = query.at if query.at is not None else self._clock.now()
        found = self._repos.actions.due(query.org_id, at, query.limit)
        return tuple(
            action_view(a, self._svc.action_lifecycle.allowed(a.state)) for a in found
        )

    def cases_due(self, query: CasesDue) -> tuple[CollectionCaseView, ...]:
        self._read(query)
        at = query.at if query.at is not None else self._clock.now()
        found = self._repos.cases.due(query.org_id, at, query.limit)
        return tuple(
            case_view(c, self._svc.case_lifecycle.allowed(c.state)) for c in found
        )

    def explain(self, query: ExplainCollectionCase) -> CollectionExplanationView:
        self._read(query)
        case = self.case_or_404(query.org_id, query.case_id)
        policy = self._repos.policies.get(query.org_id, case.policy_id)
        open_actions = self._repos.actions.open_for_case(query.org_id, case.id)
        latest = self._repos.decisions.latest(query.org_id, case.id)
        return collections_explanation_view(
            self._svc.explainer.explain(
                case,
                policy=policy,
                next_action=open_actions[0] if open_actions else None,
                last_decision=latest,
            )
        )

    def inspect(self, query: InspectCollectionCase) -> CollectionInspectionView:
        self._read(query)
        at = query.at if query.at is not None else self._clock.now()
        case = self.case_or_404(query.org_id, query.case_id)
        policy = self._repos.policies.get(query.org_id, case.policy_id)
        inspection = self._svc.inspector.inspect(
            case,
            actions=self._repos.actions.for_case(query.org_id, case.id),
            attempts=self._repos.attempts.for_case(query.org_id, case.id),
            decisions=self._repos.decisions.for_case(query.org_id, case.id),
            policy=policy,
            at=at,
        )
        return CollectionInspectionView(
            case=case_view(case, self._svc.case_lifecycle.allowed(case.state)),
            actions=inspection.actions,
            open_actions=inspection.open_actions,
            executed_actions=inspection.executed_actions,
            attempts=inspection.attempts,
            decisions=inspection.decisions,
            cycles=inspection.cycles,
            stage_count=inspection.stage_count,
            is_due=inspection.is_due,
        )

    def list_policies(self, query: ListCollectionPolicies) -> tuple[CollectionPolicyView, ...]:
        self._read(query)
        found = self._repos.policies.all(query.org_id, query.include_inactive)
        return tuple(collections_policy_view(p) for p in found)

    def policy_versions(
        self, query: GetCollectionPolicyVersions
    ) -> tuple[CollectionPolicyView, ...]:
        self._read(query)
        found = self._repos.policies.versions(query.org_id, query.code)
        return tuple(collections_policy_view(p) for p in found)

    def search(self, query: SearchCollectionCases) -> CollectionSearchView:
        self._read(query)
        from billing.foundation import CaseState as _S
        from billing.foundation import PaymentSignal as _G

        states = tuple(_S(s) for s in query.states if s in {m.value for m in _S})
        signals = tuple(_G(s) for s in query.signals if s in {m.value for m in _G})
        candidates = (
            self._repos.cases.for_account(query.org_id, query.account_id, max(query.limit * 4, 100))
            if query.account_id
            else self._repos.cases.all(query.org_id, max(query.limit * 4, 100))
        )
        result = self._svc.search.search(
            CollectionSearchQuery(
                org_id=query.org_id,
                account_id=query.account_id,
                obligation_id=query.obligation_id,
                states=states,
                signals=signals,
                due_before=query.due_before,
                limit=query.limit,
            ),
            candidates,
        )
        return CollectionSearchView(
            cases=tuple(
                case_view(c, self._svc.case_lifecycle.allowed(c.state)) for c in result.cases
            ),
            total=result.total,
            truncated=result.truncated,
        )

    def timeline(self, query: CollectionTimeline) -> tuple[CollectionActivityView, ...]:
        self._read(query)
        found = self._repos.activity.for_case(query.org_id, query.case_id, query.limit)
        return tuple(collections_activity_view(a) for a in found)


def register_collection_use_cases(
    command_bus, query_bus, uc: CollectionUseCases, q: CollectionQueries
):
    """Wire the collections vocabulary onto the shared buses. One handler per command."""
    command_bus.register(PublishCollectionPolicy, uc.publish_policy)
    command_bus.register(DeactivateCollectionPolicy, uc.deactivate_policy)
    command_bus.register(OpenCollectionCase, uc.open_case)
    command_bus.register(EvaluateCollectionCase, uc.evaluate_case)
    command_bus.register(ClaimCollectionAction, uc.claim_action)
    command_bus.register(ExecuteCollectionAction, uc.execute_action)
    command_bus.register(RecordPaymentSignal, uc.record_signal)
    command_bus.register(BlockCollectionCase, uc.block_case)
    command_bus.register(EscalateCollectionCase, uc.escalate_case)
    command_bus.register(CancelCollectionCase, uc.cancel_case)
    command_bus.register(OpenCollectionCycle, uc.open_cycle)
    command_bus.register(ObsoleteCollectionAction, uc.obsolete_action)
    query_bus.register(GetCollectionCase, q.get_case)
    query_bus.register(ListCollectionCases, q.list_cases)
    query_bus.register(GetCaseForObligation, q.case_for_obligation)
    query_bus.register(ListCollectionActions, q.list_actions)
    query_bus.register(GetCollectionAction, q.get_action)
    query_bus.register(ListCollectionAttempts, q.list_attempts)
    query_bus.register(ListCollectionDecisions, q.list_decisions)
    query_bus.register(ActionsDue, q.actions_due)
    query_bus.register(CasesDue, q.cases_due)
    query_bus.register(ExplainCollectionCase, q.explain)
    query_bus.register(InspectCollectionCase, q.inspect)
    query_bus.register(ListCollectionPolicies, q.list_policies)
    query_bus.register(GetCollectionPolicyVersions, q.policy_versions)
    query_bus.register(SearchCollectionCases, q.search)
    query_bus.register(CollectionTimeline, q.timeline)


__all__ = [
    "ALL_COLLECTION_COMMANDS",
    "ALL_COLLECTION_QUERIES",
    "ActionsDue",
    "BlockCollectionCase",
    "CancelCollectionCase",
    "CasesDue",
    "ClaimCollectionAction",
    "CollectionActionView",
    "CollectionActivityView",
    "CollectionAttemptView",
    "CollectionCaseView",
    "CollectionDecisionView",
    "CollectionExplanationView",
    "CollectionInspectionView",
    "CollectionOutcomeView",
    "CollectionPolicyView",
    "CollectionQueries",
    "CollectionRepositories",
    "CollectionSearchView",
    "CollectionServices",
    "CollectionStageView",
    "CollectionTimeline",
    "CollectionUseCases",
    "DeactivateCollectionPolicy",
    "EscalateCollectionCase",
    "EvaluateCollectionCase",
    "ExecuteCollectionAction",
    "ExplainCollectionCase",
    "GetCaseForObligation",
    "GetCollectionAction",
    "GetCollectionCase",
    "GetCollectionPolicyVersions",
    "InspectCollectionCase",
    "ListCollectionActions",
    "ListCollectionAttempts",
    "ListCollectionCases",
    "ListCollectionDecisions",
    "ListCollectionPolicies",
    "ObsoleteCollectionAction",
    "OpenCollectionCase",
    "OpenCollectionCycle",
    "PublishCollectionPolicy",
    "RecordPaymentSignal",
    "SearchCollectionCases",
    "action_view",
    "collections_activity_view",
    "collections_attempt_view",
    "case_view",
    "decision_view",
    "collections_explanation_view",
    "collections_policy_view",
    "register_collection_use_cases",
    "stage_view",
]
