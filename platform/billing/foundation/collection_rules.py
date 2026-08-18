"""Collections rules — the decision engine and its ports (M9.17).

Pure. No clock, no database, no provider, no scheduler, no transport. Every rule here takes the
instant it is reasoning about as an argument, which is the property that makes a decision taken in
March reproducible in April by replaying the same instant against the same immutable policy
version.

## Three things this module refuses to do

**It does not move money.** `CollectionDecisionEngine` can select `REQUEST_PAYMENT_RETRY`. That is
a durable request, handed to the Payment Engine's own command by the Application. There is no
provider here, no adapter, no instrument, and no import that could reach one.

**It does not calculate money.** No amount, no balance, no outstanding, no proration, no tax.
Ask this module *what should happen* and it answers; ask it *how much* and it has no opinion,
because the figure belongs to the Ledger and reaches the screen through Invoice and Payments.

**It does not schedule.** There is `actions_due(cases, at)`, which answers "given this instant,
which of these is due" — a pure filter over values the caller supplies. There is no `run()`, no
loop, no sleep, and no thread. Execution is somebody else's layer, as it has been since M9.9.

## The gate that matters most

An ambiguous payment must never become a retry. That is enforced twice, independently:

1. `CollectionDecisionEngine.decide` branches on `AMBIGUOUS` **before** it looks at the policy at
   all, and the only action it can produce from that branch is `AWAIT_RECONCILIATION`;
2. even if a policy stage says `REQUEST_PAYMENT_RETRY`, `_gate_money_request` downgrades it unless
   the signal is in `RETRYABLE_SIGNALS`.

Two gates rather than one, because M9.16-R1 finding P-1 was a single-gate failure: one table
interpreted every outcome, and when a caller reached it with the wrong context there was nothing
behind it. A second, differently-shaped check is what makes a future editing mistake produce a
declined action instead of a duplicate charge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_collections import (
    COLLECTION_ACTION_LIFECYCLE,
    COLLECTION_CASE_LIFECYCLE,
    RETRYABLE_SIGNALS,
    TRIGGER_ESTABLISHES,
    ActionKind,
    ActionState,
    ActionTrigger,
    AttemptOutcome,
    CaseState,
    CaseTrigger,
    CollectionAction,
    CollectionAttempt,
    CollectionCase,
    CollectionDecision,
    CollectionExplanation,
    CollectionInspection,
    CollectionPolicy,
    CollectionSearchQuery,
    CollectionSearchResult,
    CollectionStage,
    FailureClass,
    NoticeOutcome,
    NoticeRequest,
    ObligationKind,
    PaymentSignal,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition

# ── reading payment state, in exactly one place ─────────────────────────────────

#: How a payment **intent** state reads as a collection signal. Strings rather than the imported
#: enum so this module stays free of any payment import at all: Collections consumes payment facts
#: through the Application read boundary, and a Foundation-to-Foundation dependency would make the
#: two domains compile together whether or not they are wired together.
#:
#: The mapping is a table so it can be asserted by iteration. Note `requires_action` → `FAILED` is
#: **absent**: an intent waiting on the payer is not a failure and chasing it with a second payment
#: request would race the payer's own 3DS approval.
INTENT_SIGNALS: dict[str, PaymentSignal] = {
    "draft": PaymentSignal.NONE,
    "pending": PaymentSignal.AMBIGUOUS,
    "requires_action": PaymentSignal.AMBIGUOUS,
    "authorized": PaymentSignal.AMBIGUOUS,
    "paid": PaymentSignal.SUCCEEDED,
    "failed": PaymentSignal.FAILED,
    "cancelled": PaymentSignal.CANCELLED,
    "expired": PaymentSignal.EXPIRED,
}

#: How a payment **attempt** state reads. The attempt is the stronger evidence when both are
#: available, because it describes a movement rather than an objective.
#:
#: Five states map to `AMBIGUOUS`, and only one of them is literally called ambiguous. That is the
#: point: `started`, `pending`, `requires_action` and `authorized` all mean *a movement may be in
#: flight*, and from a collection standpoint the honest reading of all four is the same as the
#: honest reading of `ambiguous` — **not a licence to send another one**.
ATTEMPT_SIGNALS: dict[str, PaymentSignal] = {
    "started": PaymentSignal.AMBIGUOUS,
    "pending": PaymentSignal.AMBIGUOUS,
    "requires_action": PaymentSignal.AMBIGUOUS,
    "authorized": PaymentSignal.AMBIGUOUS,
    "ambiguous": PaymentSignal.AMBIGUOUS,
    "succeeded": PaymentSignal.SUCCEEDED,
    "failed": PaymentSignal.FAILED,
    "cancelled": PaymentSignal.CANCELLED,
    "abandoned": PaymentSignal.EXPIRED,
}

#: How the Payment Engine's failure categories read as policy inputs. Anything unmapped is
#: `UNKNOWN`, never `TRANSIENT` — guessing "probably worth retrying" is how a blocked card is
#: retried four times and the account is flagged for abuse.
FAILURE_CLASSES: dict[str, FailureClass] = {
    "insufficient_funds": FailureClass.TRANSIENT,
    "network": FailureClass.TRANSIENT,
    "provider_error": FailureClass.TRANSIENT,
    "timeout": FailureClass.TRANSIENT,
    "rate_limited": FailureClass.TRANSIENT,
    "declined": FailureClass.TRANSIENT,
    "expired_instrument": FailureClass.PERMANENT,
    "invalid_instrument": FailureClass.PERMANENT,
    "blocked": FailureClass.PERMANENT,
    "fraud": FailureClass.PERMANENT,
    "revoked": FailureClass.PERMANENT,
    "authentication_required": FailureClass.ACTION_REQUIRED,
    "action_required": FailureClass.ACTION_REQUIRED,
}


class SignalReader:
    """Reads payment facts into the one vocabulary Collections is allowed to reason about.

    A single translation point on purpose. The M9.16-R1 review found that the failure it cared
    about — an ambiguous outcome treated as a failure — becomes unpreventable the moment two places
    interpret payment state, because only one of them gets fixed."""

    @staticmethod
    def signal_for(intent_state: str = "", attempt_state: str = "") -> PaymentSignal:
        """The strongest available reading. The attempt wins when both are present: it describes a
        movement of money, and the intent only describes an intention to move some.

        Unknown strings read as `AMBIGUOUS`, not as `FAILED`. A state this engine does not
        recognise is by definition a state whose safety it cannot vouch for, and the safe reading
        of "I do not know" is the one that refuses to ask for money."""
        if attempt_state:
            return ATTEMPT_SIGNALS.get(attempt_state, PaymentSignal.AMBIGUOUS)
        if intent_state:
            return INTENT_SIGNALS.get(intent_state, PaymentSignal.AMBIGUOUS)
        return PaymentSignal.NONE

    @staticmethod
    def failure_class_for(failure_code: str = "", failure_category: str = "") -> FailureClass:
        for token in (failure_category, failure_code):
            if token and token in FAILURE_CLASSES:
                return FAILURE_CLASSES[token]
        return FailureClass.UNKNOWN

    @staticmethod
    def is_retryable(signal: PaymentSignal) -> bool:
        """Whether asking for money again is permissible. One predicate, used everywhere, so the
        rule cannot be re-derived slightly differently in a second place."""
        return signal in RETRYABLE_SIGNALS


# ── lifecycle ───────────────────────────────────────────────────────────────────


class CaseStale(BillingConflict):
    """A worker tried to transition a case from a version that is no longer current.

    Raised by persistence when the compare-and-set finds no row, and declared here — in Foundation,
    beside the domain it protects — because the *Application* must react to it and may not import
    persistence. Same placement and same reason as `RefundSequenceConflict` at M9.16-R1.

    This is the stale-worker defence made explicit: worker A reads a case as `SCHEDULED`, worker B
    records a payment success and closes it as `RECOVERED`, and worker A comes back to schedule
    another retry. Without the version it would succeed, and a customer who has paid would be
    chased for money they no longer owe."""


class CaseLifecycleService:
    """Applies case triggers through the shared FSM, and bumps the concurrency token.

    `apply` always increments `version`. That is what makes the token meaningful: a transition the
    database accepted is a transition every other worker's read is now stale against."""

    def __init__(self, definition: StateMachineDefinition = COLLECTION_CASE_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: CaseState, trigger: CaseTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: CaseState, trigger: CaseTrigger) -> CaseState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a collection case cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: CaseState) -> tuple[CaseTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    @staticmethod
    def establishes(current: CaseState, trigger: CaseTrigger) -> bool:
        """Whether `current` is already the state `trigger` would establish.

        The difference between "this transition is illegal" and "this transition has already
        happened". A scheduler re-evaluating a case that is already scheduled asks for `SCHEDULE`
        again; there is no such edge and there should not be one — a self-loop would record a
        sweep as a business fact. Refusing it outright, though, would make a repeated evaluation an
        error, and a scheduler that cannot safely run twice is one nobody can operate."""
        return TRIGGER_ESTABLISHES.get(trigger) is current

    def apply(
        self,
        case: CollectionCase,
        trigger: CaseTrigger,
        *,
        at: float,
        reason: str = "",
    ) -> CollectionCase:
        target = self.next_state(case.state, trigger)
        changes: dict = {
            "state": target,
            "updated_at": at,
            "version": case.version + 1,
        }
        if target in (CaseState.RECOVERED, CaseState.EXHAUSTED, CaseState.CANCELLED):
            changes["closed_at"] = at
            changes["closed_reason"] = reason or trigger.value
            changes["next_action_at"] = None
        if target is CaseState.BLOCKED:
            changes["blocked_reason"] = reason or "awaiting_reconciliation"
            changes["next_action_at"] = None
        elif target is CaseState.SCHEDULED:
            changes["blocked_reason"] = ""
        return replace(case, **changes)


class ActionLifecycleService:
    """Applies action triggers. The action FSM is small and its refusals do the work."""

    def __init__(self, definition: StateMachineDefinition = COLLECTION_ACTION_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: ActionState, trigger: ActionTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: ActionState, trigger: ActionTrigger) -> ActionState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a collection action cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: ActionState) -> tuple[ActionTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        action: CollectionAction,
        trigger: ActionTrigger,
        *,
        at: float,
        actor_id: str = "",
        note: str = "",
        result_ref: str = "",
    ) -> CollectionAction:
        target = self.next_state(action.state, trigger)
        changes: dict = {"state": target}
        if target is ActionState.CLAIMED:
            changes["claimed_at"] = at
            changes["claimed_by"] = actor_id
        else:
            changes["completed_at"] = at
        if note:
            changes["note"] = note
        if result_ref:
            changes["result_ref"] = result_ref
        return replace(action, **changes)


class CollectionGuardService:
    """The refusals that are not expressible as FSM edges.

    Everything an edge *can* express lives in the edge table, because a missing edge is a
    specification and a scattered `if` is a hope. These are the checks that need more than the
    current state to decide."""

    @staticmethod
    def guard_not_settled(case: CollectionCase) -> None:
        """A recovered case is a financial conclusion. Nothing rewrites it."""
        if case.is_settled:
            raise BillingConflict(
                f"collection case {case.id!r} is recovered; a recovered case is never reopened, "
                "and a fresh obligation is a fresh case"
            )

    @staticmethod
    def guard_actionable(case: CollectionCase) -> None:
        """Automation may not select an action for a quiescent case — terminal *or* escalated."""
        CollectionGuardService.guard_not_settled(case)
        if case.is_quiescent:
            raise BillingConflict(
                f"collection case {case.id!r} is {case.state.value} and is closed to automation; "
                "an operator decision is required to change that"
            )

    @staticmethod
    def guard_claimable(action: CollectionAction, *, at: float) -> None:
        if action.is_closed:
            raise BillingConflict(
                f"collection action {action.id!r} is already {action.state.value}"
            )
        if action.state is ActionState.CLAIMED:
            raise BillingConflict(
                f"collection action {action.id!r} is already claimed by "
                f"{action.claimed_by or 'another worker'}"
            )
        if not action.is_due(at):
            raise BillingConflict(
                f"collection action {action.id!r} is not due until {action.due_at}"
            )

    @staticmethod
    def guard_money_request(action: CollectionAction, signal: PaymentSignal) -> None:
        """**The last gate before Payments is asked for anything.**

        Sits at the Application's edge, after the decision engine has already refused twice, and
        checks the signal as it is *now* rather than as it was when the action was planned. That is
        the case the first two gates cannot see: an action planned an hour ago against a failed
        payment, claimed now, while a reconciliation has since moved that attempt to ambiguous."""
        if not action.requests_money:
            return
        if not SignalReader.is_retryable(signal):
            raise BillingConflict(
                f"collection action {action.id!r} would request a payment while the payment "
                f"signal is {signal.value}; only a provably-settled non-movement may be retried, "
                "and an ambiguous outcome is resolved by reconciliation, never by a second request"
            )


# ── policy ──────────────────────────────────────────────────────────────────────


#: The strategy a fresh installation gets if an operator declares none: notify, wait a day and
#: retry, wait three days and retry, wait a week, final notice, stop. Declared as data so the
#: shipped default is auditable rather than buried in a constructor. It is *published* like any
#: other policy — there is no privileged code path for it.
DEFAULT_STAGES: tuple[CollectionStage, ...] = (
    CollectionStage(sequence=0, kind=ActionKind.NOTIFY, wait_seconds=0, label="first notice"),
    CollectionStage(
        sequence=1, kind=ActionKind.REQUEST_PAYMENT_RETRY, wait_seconds=86400, label="retry 1"
    ),
    CollectionStage(
        sequence=2, kind=ActionKind.REQUEST_PAYMENT_RETRY, wait_seconds=259200, label="retry 2"
    ),
    CollectionStage(
        sequence=3, kind=ActionKind.REQUEST_PAYMENT_RETRY, wait_seconds=604800, label="retry 3"
    ),
    CollectionStage(
        sequence=4, kind=ActionKind.FINAL_NOTICE, wait_seconds=604800, label="final notice"
    ),
    CollectionStage(sequence=5, kind=ActionKind.STOP, wait_seconds=259200, label="stop"),
)


class CollectionPolicyService:
    """Reads a policy version. It never writes one — a policy version is immutable once declared,
    and the only way to change a strategy is to publish the next version."""

    @staticmethod
    def stage_for(policy: CollectionPolicy, index: int) -> CollectionStage | None:
        return policy.stage_at(index)

    @staticmethod
    def due_at(stage: CollectionStage, *, since: float) -> float:
        """When a stage becomes actionable. Pure arithmetic over seconds — the one kind of
        arithmetic this domain does, and it is about time, never about money."""
        return since + float(stage.delay_seconds)

    @staticmethod
    def terminal_index(policy: CollectionPolicy) -> int:
        """The index of the first terminal stage, or the end of the ladder.

        Used when a permanent failure means the intervening retries are pointless: skipping to the
        terminal rung is a *policy* decision recorded in the decision trail, not a shortcut."""
        for stage in policy.stages:
            if stage.is_terminal:
                return stage.sequence
        return policy.stage_count

    @staticmethod
    def is_exhausted(policy: CollectionPolicy, index: int) -> bool:
        return index >= policy.stage_count


# ── the decision engine ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """What the engine concluded, before anything is written.

    A value, not a command: it names the action kind to plan (or `None`), the case trigger to fire
    (or `None`), when the action becomes due, and the ordered path of checks that produced it. The
    Application turns it into rows; the engine touches nothing."""

    selected: ActionKind | None = None
    case_trigger: CaseTrigger | None = None
    stage_index: int = 0
    due_at: float | None = None
    grace_until: float | None = None
    reason: str = ""
    path: tuple[str, ...] = ()

    @property
    def plans_action(self) -> bool:
        return self.selected is not None


class CollectionDecisionEngine:
    """**The one place a collection decision is made.**

    Deterministic and clock-free: same case, same policy version, same signal, same instant, same
    answer — in March and in April, before and after the strategy is rewritten. That property is
    what `CollectionDecision` records exist to preserve, and it is why the policy arrives here as
    an immutable version rather than as "the current settings".

    The order of the branches is the specification, and the first two are the ones that matter:
    recovery is checked before anything, and ambiguity is checked before the policy is consulted at
    all — so no arrangement of stages can produce a retry against an unknown outcome."""

    def decide(
        self,
        case: CollectionCase,
        policy: CollectionPolicy,
        *,
        signal: PaymentSignal,
        at: float,
        failure_class: FailureClass = FailureClass.UNKNOWN,
        since: float | None = None,
    ) -> DecisionOutcome:
        path: list[str] = [f"signal={signal.value}", f"policy={policy.version_key}"]

        # 1. Money landed. Nothing else is relevant, and no stage can override it.
        if signal is PaymentSignal.SUCCEEDED:
            path.append("payment_succeeded")
            return DecisionOutcome(
                case_trigger=CaseTrigger.RECOVER,
                stage_index=case.stage_index,
                reason="the obligation was collected",
                path=tuple(path),
            )

        # 2. **Before the policy is consulted at all.** An unknown outcome is not a failure, and
        #    the only thing that resolves it is the Payment Engine asking the provider about the
        #    same operation. Collections waits; it does not act. (M9.16-R1 invariant.)
        if signal is PaymentSignal.AMBIGUOUS:
            path.append("payment_ambiguous_blocks_all_action")
            return DecisionOutcome(
                selected=ActionKind.AWAIT_RECONCILIATION,
                case_trigger=CaseTrigger.BLOCK,
                stage_index=case.stage_index,
                due_at=at,
                reason=(
                    "the payment outcome is unknown; reconciliation must resolve it before any "
                    "further collection action"
                ),
                path=tuple(path),
            )

        # 3. Automation has no business here.
        if case.is_quiescent:
            path.append(f"case_{case.state.value}")
            return DecisionOutcome(
                stage_index=case.stage_index,
                reason=f"the case is {case.state.value}",
                path=tuple(path),
            )

        index = case.stage_index
        if failure_class is FailureClass.PERMANENT:
            terminal = CollectionPolicyService.terminal_index(policy)
            if terminal > index:
                path.append(f"permanent_failure_skips_to_stage_{terminal}")
                index = terminal

        stage = policy.stage_at(index)

        # 4. Off the end of the ladder. Exhausted means the *strategy* ended, never that the debt
        #    did — Collections has no authority to write off anything.
        if stage is None:
            path.append("stages_exhausted")
            return DecisionOutcome(
                case_trigger=CaseTrigger.EXHAUST,
                stage_index=index,
                reason=(
                    "the configured collection strategy reached its terminal boundary; the "
                    "obligation may still be outstanding"
                ),
                path=tuple(path),
            )

        path.append(f"stage={index}:{stage.kind.value}")
        kind = self._gate_money_request(stage.kind, signal, path)

        if kind is ActionKind.STOP:
            path.append("stage_is_stop")
            return DecisionOutcome(
                case_trigger=CaseTrigger.EXHAUST,
                stage_index=index,
                reason="the strategy's terminal stage was reached",
                path=tuple(path),
            )

        base = case.updated_at if since is None else since
        due = CollectionPolicyService.due_at(stage, since=base)
        grace = (base + float(stage.grace_seconds)) if stage.grace_seconds else None
        if grace is not None:
            path.append(f"grace_until={grace}")
        path.append(f"due_at={due}")
        return DecisionOutcome(
            selected=kind,
            case_trigger=CaseTrigger.SCHEDULE,
            stage_index=index,
            due_at=due,
            grace_until=grace,
            reason=f"stage {index} of {policy.version_key} selects {kind.value}",
            path=tuple(path),
        )

    @staticmethod
    def _gate_money_request(
        kind: ActionKind, signal: PaymentSignal, path: list[str]
    ) -> ActionKind:
        """**The second gate.** Independent of the branch above and differently shaped.

        Branch 2 asks "is the signal ambiguous?". This asks "is the signal one of the few we are
        willing to ask for money against?" — a whitelist rather than a blacklist, so a signal added
        later defaults to *refusing* rather than to permitting. That asymmetry is the whole reason
        both exist: M9.16-R1's S0 was a single interpretation table with nothing behind it."""
        if kind not in (ActionKind.REQUEST_PAYMENT_RETRY,):
            return kind
        if SignalReader.is_retryable(signal):
            return kind
        path.append(f"retry_refused_signal_{signal.value}")
        return ActionKind.AWAIT_RECONCILIATION


class CollectionScheduleService:
    """Answers *what is due*. It does not make anything happen.

    This is the whole of the scheduling surface in the domain, and it is a pure filter over values
    the caller passed in. Whoever calls it — a cron entry, an operator clicking a button, a test —
    is outside the Foundation, exactly as it has been for every engine since M9.9."""

    @staticmethod
    def due_cases(
        cases: Iterable[CollectionCase], *, at: float
    ) -> tuple[CollectionCase, ...]:
        due = (c for c in cases if c.is_due(at))
        return tuple(sorted(due, key=lambda c: c.next_action_at or 0))

    @staticmethod
    def due_actions(
        actions: Iterable[CollectionAction], *, at: float
    ) -> tuple[CollectionAction, ...]:
        return tuple(sorted((a for a in actions if a.is_due(at)), key=lambda a: a.due_at))


# ── the communication port (provider-neutral) ───────────────────────────────────


class NoticeNotSent(Exception):
    """The one exception a notifier may raise to assert **nothing left the building**.

    Deliberately the same contract as M9.16's `ProviderNotSent`, and for the same reason: every
    other failure mode is indistinguishable from a delivered message whose acknowledgement was
    lost, and treating those as non-delivery is how a customer receives the same dunning notice
    five times. Anything a notifier cannot make this assertion about is `DEFERRED`."""


class CollectionNotifier(ABC):
    """The outbound communication seam. One verb, provider-neutral.

    There is no `send_email`, no `send_sms`, no template, no recipient, and no vendor anywhere in
    this signature — an architecture test asserts as much. Collections decides that a customer
    should be told something; resolving who they are, in what language, over what transport, and
    with what wording is a different domain's job, and a port that named any of it would have
    quietly moved that ownership here."""

    @property
    def code(self) -> str:
        return "null"

    @abstractmethod
    def deliver(self, request: NoticeRequest) -> NoticeOutcome: ...


class NullNotifier(CollectionNotifier):
    """The default when nothing is wired: every notice is `DEFERRED`, never `FAILED`.

    Deferred is the honest answer — no transport was configured, so nothing was attempted, and
    reporting a failure would put "we tried to tell the customer and could not" into the audit
    trail of an installation that never tried. Same shape as `NullPaymentProvider` returning
    `UNAVAILABLE` rather than `FAILED` at M9.16."""

    def deliver(self, request: NoticeRequest) -> NoticeOutcome:
        return NoticeOutcome(
            outcome=AttemptOutcome.DEFERRED,
            detail="no collection notifier is configured; nothing was sent",
        )


class RecordingNotifier(CollectionNotifier):
    """A deterministic in-memory notifier for tests and dry runs.

    Honours the idempotency key: delivering the same key twice returns the first outcome and does
    **not** record a second delivery, which is what lets a test prove that a duplicated action
    cannot produce a duplicated notice rather than merely asserting the code looks careful."""

    def __init__(self, code: str = "recording", scripted: NoticeOutcome | None = None) -> None:
        self._code = code
        self._scripted = scripted
        self._sent: dict[str, NoticeOutcome] = {}
        self._log: list[NoticeRequest] = []

    @property
    def code(self) -> str:
        return self._code

    def deliver(self, request: NoticeRequest) -> NoticeOutcome:
        existing = self._sent.get(request.idempotency_key)
        if existing is not None:
            return existing
        outcome = self._scripted or NoticeOutcome(
            outcome=AttemptOutcome.SUCCEEDED,
            channel="recording",
            reference=request.idempotency_key,
        )
        self._sent[request.idempotency_key] = outcome
        self._log.append(request)
        return outcome

    def deliveries(self) -> tuple[NoticeRequest, ...]:
        """How many notices actually left. The counter a duplicate-suppression test asserts on."""
        return tuple(self._log)


class NotifierRegistry:
    """Operator-declared notifiers by code. Empty by default: an installation that registers none
    communicates nothing at all, which is the only safe default for outbound messaging."""

    def __init__(self, notifiers: Sequence[CollectionNotifier] = ()) -> None:
        self._by_code = {n.code: n for n in notifiers}

    def register(self, notifier: CollectionNotifier) -> None:
        self._by_code[notifier.code] = notifier

    def get(self, code: str) -> CollectionNotifier | None:
        return self._by_code.get(code)

    def resolve(self, code: str = "") -> CollectionNotifier:
        """The notifier for `code`, or the single registered one, or a null. Never raises: a
        missing transport must degrade to "nothing was sent", not to a failed collection case."""
        if code and code in self._by_code:
            return self._by_code[code]
        if len(self._by_code) == 1:
            return next(iter(self._by_code.values()))
        return NullNotifier()

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_code))


# ── validation, explanation, inspection, search ─────────────────────────────────


class CollectionValidator:
    """Structural validation. Findings, never exceptions — the factory decides what is fatal."""

    def validate_policy(self, policy: CollectionPolicy) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not policy.code:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.ERROR,
                    message="a collection policy needs a stable code",
                    path=policy.id,
                )
            )
        if not policy.stages:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.EMPTY_COLLECTION_STRATEGY,
                    severity=Severity.ERROR,
                    message="a collection policy with no stages can never act",
                    path=policy.id,
                )
            )
        expected = list(range(len(policy.stages)))
        actual = [s.sequence for s in policy.stages]
        if actual != expected:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.COLLECTION_STAGE_GAP,
                    severity=Severity.ERROR,
                    message=(
                        f"collection stages must be dense and ascending from 0; got {actual}"
                    ),
                    path=policy.id,
                )
            )
        if policy.stages and not any(s.is_terminal for s in policy.stages):
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.UNBOUNDED_COLLECTION_STRATEGY,
                    severity=Severity.WARNING,
                    message=(
                        "this strategy has no terminal stage; it will exhaust by running off the "
                        "end rather than by an explicit decision"
                    ),
                    path=policy.id,
                )
            )
        return BillingDiagnostics.of(found)

    def validate_case(self, case: CollectionCase) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not case.obligation_id:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.ERROR,
                    message="a collection case must name the obligation it recovers",
                    path=case.id,
                )
            )
        if not case.account_id:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.ERROR,
                    message="a collection case must name the account it is against",
                    path=case.id,
                )
            )
        if case.signal is PaymentSignal.AMBIGUOUS and case.state is CaseState.ACTION_DUE:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.AMBIGUOUS_PAYMENT_IN_COLLECTION,
                    severity=Severity.ERROR,
                    message=(
                        "this case is actionable while its payment outcome is unknown; it must "
                        "be blocked until reconciliation resolves the attempt"
                    ),
                    path=case.id,
                )
            )
        return BillingDiagnostics.of(found)

    def validate_action(
        self, action: CollectionAction, signal: PaymentSignal
    ) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if action.requests_money and not SignalReader.is_retryable(signal):
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.RETRY_AGAINST_UNKNOWN_OUTCOME,
                    severity=Severity.ERROR,
                    message=(
                        f"a payment retry is planned while the signal is {signal.value}; only a "
                        "proven non-movement may be retried"
                    ),
                    path=action.id,
                )
            )
        return BillingDiagnostics.of(found)


class CollectionExplainer:
    """Turns a case plus its next action into a sentence and a path. Observability only."""

    def explain(
        self,
        case: CollectionCase,
        *,
        policy: CollectionPolicy | None = None,
        next_action: CollectionAction | None = None,
        last_decision: CollectionDecision | None = None,
    ) -> CollectionExplanation:
        path: list[str] = [
            f"case={case.state.value}",
            f"signal={case.signal.value}",
            f"cycle={case.cycle}",
            f"stage={case.stage_index}",
        ]
        if policy is not None:
            path.append(f"policy={policy.version_key}")
        if last_decision is not None:
            path.extend(last_decision.reason_path)
        reason = last_decision.reason if last_decision is not None else ""
        if not reason:
            reason = _default_reason(case)
        return CollectionExplanation(
            case_id=case.id,
            state=case.state,
            signal=case.signal,
            stage_index=case.stage_index,
            policy_code=case.policy_code,
            policy_version=case.policy_version,
            next_kind=next_action.kind if next_action is not None else None,
            next_action_at=case.next_action_at,
            grace_until=case.grace_until,
            reason=reason,
            path=tuple(path),
            blocked_reason=case.blocked_reason,
        )


def _default_reason(case: CollectionCase) -> str:
    if case.state is CaseState.RECOVERED:
        return "the obligation was collected"
    if case.state is CaseState.EXHAUSTED:
        return "the collection strategy reached its terminal boundary; the debt may still stand"
    if case.state is CaseState.CANCELLED:
        return "the obligation was withdrawn"
    if case.state is CaseState.ESCALATED:
        return "an operator holds this case; automation has stopped"
    if case.state is CaseState.BLOCKED:
        return case.blocked_reason or "waiting on something Collections does not own"
    return f"the case is {case.state.value}"


class CollectionInspector:
    """Counts, for the operator screen. Reads only what it is handed."""

    def inspect(
        self,
        case: CollectionCase,
        *,
        actions: Sequence[CollectionAction] = (),
        attempts: Sequence[CollectionAttempt] = (),
        decisions: Sequence[CollectionDecision] = (),
        policy: CollectionPolicy | None = None,
        at: float = 0.0,
    ) -> CollectionInspection:
        return CollectionInspection(
            case=case,
            actions=len(actions),
            open_actions=sum(1 for a in actions if not a.is_closed),
            executed_actions=sum(1 for a in actions if a.state is ActionState.EXECUTED),
            attempts=len(attempts),
            decisions=len(decisions),
            cycles=case.cycle,
            stage_count=policy.stage_count if policy is not None else 0,
            is_due=case.is_due(at),
        )


class CollectionSearchService:
    """Filters cases the repository already narrowed. Bounded, and honest about truncation.

    The repository does the indexed work; this applies the residual predicates the SQL layer does
    not express. It never loads a whole org — `limit` is applied by the caller before this is
    reached, and `truncated` says so rather than letting a short list read as a complete one."""

    def search(
        self, query: CollectionSearchQuery, cases: Sequence[CollectionCase]
    ) -> CollectionSearchResult:
        matched = [c for c in cases if self._matches(query, c)]
        limit = max(1, query.limit)
        return CollectionSearchResult(
            cases=tuple(matched[:limit]),
            total=len(matched),
            truncated=len(matched) > limit,
        )

    @staticmethod
    def _matches(query: CollectionSearchQuery, case: CollectionCase) -> bool:
        if query.account_id and case.account_id != query.account_id:
            return False
        if query.obligation_id and case.obligation_id != query.obligation_id:
            return False
        if query.states and case.state not in query.states:
            return False
        if query.signals and case.signal not in query.signals:
            return False
        if query.due_before is not None:
            if case.next_action_at is None or case.next_action_at > query.due_before:
                return False
        return True


def notice_key(action: CollectionAction) -> str:
    """The idempotency key a notice carries.

    Derived from the action, so re-executing a claimed action after a crash presents the transport
    with the same key and a notifier that honours it sends nothing. The action id is already
    derived from `(case, cycle, sequence)`, so this is unique per decision without a second
    derivation to keep in step."""
    return f"{action.id}:{action.kind.value}"


def obligation_ref(kind: ObligationKind, obligation_id: str) -> str:
    return f"{kind.value}:{obligation_id}"


__all__ = [
    "ATTEMPT_SIGNALS",
    "DEFAULT_STAGES",
    "FAILURE_CLASSES",
    "INTENT_SIGNALS",
    "ActionLifecycleService",
    "CaseLifecycleService",
    "CaseStale",
    "CollectionDecisionEngine",
    "CollectionExplainer",
    "CollectionGuardService",
    "CollectionInspector",
    "CollectionNotifier",
    "CollectionPolicyService",
    "CollectionScheduleService",
    "CollectionSearchService",
    "CollectionValidator",
    "DecisionOutcome",
    "NoticeNotSent",
    "NotifierRegistry",
    "NullNotifier",
    "RecordingNotifier",
    "SignalReader",
    "notice_key",
    "obligation_ref",
]
