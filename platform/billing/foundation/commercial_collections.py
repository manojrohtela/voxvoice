"""Dunning & Collections — the recovery **policy** domain (M9.17).

M9.2 declared a `DunningProcess`: one mutable row per failed payment, six states, a policy held as
a plain dataclass with no identity and no version. It is still there, still used, and nothing in
this module touches it. What it cannot express is the reason this milestone exists:

* it has **one row per payment**, so the recovery of an *obligation* that outlived three payment
  attempts is three unrelated rows and no readable history;
* its policy is a **value**, not a versioned fact, so a decision taken in March cannot be
  reproduced after the backoff schedule is edited in April;
* it conflates the decision with its execution — `attempts` is an integer, so "we chose to send a
  reminder" and "the reminder was delivered" are the same increment;
* it has **no notion of an ambiguous payment**, because M9.2 had none. After M9.16-R1 that is the
  single most dangerous gap: an ambiguous attempt that dunning treats as a failure produces a
  second charge for money that may already have moved.

So the richer aggregates land *beside* it, exactly as `JournalEntry` landed beside `LedgerEntry`
(M9.13), `CommercialInvoice` beside `Invoice` (M9.14), `TaxAssessment` beside `TaxDetermination`
(M9.15), and `PaymentIntent` beside `Payment` (M9.16). Extension is addition (§A12).

---

## What this domain owns, stated as a boundary

It owns **the decision to act and the record that acting happened**. It does not own the acting.

A collection case can decide that a payment should be retried. It cannot retry one: it has no
provider, no adapter, no instrument, and no reference to payment persistence. The decision becomes
a durable `CollectionAction` of kind `REQUEST_PAYMENT_RETRY`, and the Application hands that to the
Payment Engine's own command. Payments moves money; Collections records that it asked.

Equally, it computes no money. There is no `amount_due` field on a case, no `outstanding`, no
`balance`, and no copied invoice total — for the same reason M9.16 has no `collected` column. The
figure belongs to the Ledger, reaches Collections through the Invoice and Payment read boundaries,
and is *read* at the moment it is displayed rather than stored at the moment it was true.

## Why a case is not a payment attempt

The vocabulary is deliberately three-deep, and collapsing any two of them loses something:

* a **case** is the long-lived commercial recovery of one obligation. It outlives every attempt;
* an **action** is one decision taken inside that case — send a notice, ask Payments to retry, wait
  for reconciliation, escalate to a human. It is durable *before* anything external happens;
* an **attempt** is the evidence that an action was executed, and what came back.

A reminder is not a payment attempt. A reconciliation request is not a payment attempt. An operator
escalation is not a payment attempt. Only `REQUEST_PAYMENT_RETRY` causes money to be asked for, and
even that one only *asks*.

## The invariant this milestone inherits and must not break

M9.16-R1 established that an `AMBIGUOUS` payment attempt means *we do not know whether money
moved*, that it is never resolved by sending another one, and that only reconciliation resolves it.
Collections is the first consumer of payment state, and the first place that invariant could be
lost by accident — a policy engine that classified "not succeeded" as "failed" would turn every
ambiguous outcome into a second charge.

`PaymentSignal` therefore has six members, `AMBIGUOUS` is one of them, and the only action the
decision engine will ever select for it is `AWAIT_RECONCILIATION`. See `collection_rules`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_case_id(org_id: str, obligation_kind: str, obligation_id: str) -> str:
    """**One obligation, one case — forever.**

    Derived from the obligation rather than minted, so every one of the ways a second case could
    be opened for the same debt collapses onto a single primary key: a duplicated payment-failure
    event, a scheduler that ran twice, an invoice re-read, an application restart mid-command, two
    workers evaluating the same overdue document. They all compute this string and exactly one of
    them wins the insert.

    Note what is *not* in the derivation: no timestamp, no cycle, no policy. A case is the recovery
    of a debt, and the debt does not become a different debt because the strategy changed or
    because a second recovery cycle was opened months later. Cycles live *inside* the case."""
    return "ccs_" + _digest(org_id, obligation_kind, obligation_id)[:32]


def derive_action_id(case_id: str, cycle: int, sequence: int) -> str:
    """One action's identity: the case, the cycle, and its position in that cycle.

    Derived so two workers that both conclude "this case's next action is number 3" collide on a
    primary key instead of both sending a notice. Same shape as `derive_attempt_id` at M9.16, for
    the same reason and against the same race."""
    return "cac_" + _digest(case_id, str(cycle), str(sequence))[:32]


def derive_collection_attempt_id(action_id: str, sequence: int) -> str:
    """The evidence row for one execution of one action. Append-only; sequence never reused."""
    return "cat_" + _digest(action_id, str(sequence))[:32]


def derive_decision_id(case_id: str, cycle: int, stage_index: int, signal: str) -> str:
    """**A decision is identified by its inputs, not by when it was taken.**

    Re-evaluating an unchanged case in an unchanged stage against an unchanged payment signal is
    the *same* decision and records one row, not one per sweep. This is what keeps a scheduler that
    polls every minute from writing a decision log nobody can read — the same lesson M9.16 learned
    from `derive_reconciliation_id`.

    It also means a duplicate evaluation cannot produce a second action: the decision is already
    there, and the Application returns it rather than deciding again."""
    return "cdn_" + _digest(case_id, str(cycle), str(stage_index), signal)[:32]


def derive_policy_version_id(org_id: str, code: str, version: int) -> str:
    """A policy *version's* identity. `(code, version)` is the business key; the row is immutable
    once written, because a case decided under version 3 must still be explainable after version 4
    is published."""
    return "cpv_" + _digest(org_id, code, str(version))[:32]


# ── what is being collected ─────────────────────────────────────────────────────


class ObligationKind(StrEnum):
    """What a case is recovering.

    `INVOICE` is the ordinary path: the obligation identity is the immutable document identity, so
    Collections never invents a second definition of what is owed. `PAYMENT_INTENT` covers the
    non-document case — a failed collection with no invoice behind it, such as a deposit."""

    INVOICE = "invoice"
    PAYMENT_INTENT = "payment_intent"


class PaymentSignal(StrEnum):
    """**The payment state as Collections is allowed to understand it.**

    Six members, deliberately, because the review that produced M9.16-R1 turned on the difference
    between two of them. Anything that is not proven failed is not failed:

    * `FAILED` — the movement provably did not happen. Eligible for a further collection action;
    * `AMBIGUOUS` — we do not know whether money moved. **Never eligible for a retry.** The only
      action available is `AWAIT_RECONCILIATION`, and only the Payment Engine can clear it;
    * `SUCCEEDED` — money moved. The case recovers;
    * `CANCELLED` — the objective was withdrawn, not defeated;
    * `EXPIRED` — the window closed without the payer acting;
    * `NONE` — nothing has been attempted yet, or nothing is attemptable.

    There is no `PENDING`: an attempt still in flight is an *open* attempt, which is `AMBIGUOUS`
    from a collection standpoint in the only sense that matters — it is not a licence to send
    another one. Mapping lives in `collection_rules.signal_for`, in one place, tested by table."""

    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NONE = "none"


#: Signals that permit Collections to ask for money again — the ones where the previous movement
#: is **provably not in flight**. `AMBIGUOUS` is absent, and that absence is the M9.16-R1 invariant
#: expressed as data rather than as a comment. `SUCCEEDED` is absent for the opposite reason.
#:
#: `CANCELLED` and `EXPIRED` are present because they are *proof of non-movement*, which is the
#: property that matters here — not because they resemble failure. A cancelled objective and a
#: failed one are different commercial facts and are recorded differently; they are identical only
#: in the one respect this frozenset is about.
RETRYABLE_SIGNALS: frozenset[PaymentSignal] = frozenset(
    {
        PaymentSignal.FAILED,
        PaymentSignal.NONE,
        PaymentSignal.CANCELLED,
        PaymentSignal.EXPIRED,
    }
)

#: Signals that end a case successfully.
RECOVERING_SIGNALS: frozenset[PaymentSignal] = frozenset({PaymentSignal.SUCCEEDED})


class FailureClass(StrEnum):
    """Why the last movement failed, as far as policy is concerned.

    Coarse on purpose. Collections does not diagnose payment failures — the Payment Engine's
    `FailureCategory` does that — it only needs to know whether waiting could plausibly help.
    `PERMANENT` skips straight to the terminal stage rather than burning four retries on a card
    that was reported stolen."""

    TRANSIENT = "transient"  # insufficient funds, network, issuer timeout
    PERMANENT = "permanent"  # closed account, stolen instrument, blocked
    ACTION_REQUIRED = "action_required"  # the payer must do something (3DS, mandate)
    UNKNOWN = "unknown"


# ── the case lifecycle ──────────────────────────────────────────────────────────


class CaseState(StrEnum):
    """Where a recovery has got to.

    Nine states, every one reachable and every one asserted reachable by a test — the lesson of
    M9.16-R1 finding P-5, where a published state the engine could not enter read to integrators
    as a supported feature."""

    DRAFT = "draft"  # opened, no strategy attached yet
    SCHEDULED = "scheduled"  # a next action exists and is not yet due
    ACTION_DUE = "action_due"  # the clock has passed `next_action_at`
    IN_PROGRESS = "in_progress"  # an action is claimed and executing
    BLOCKED = "blocked"  # waiting on something Collections does not own
    RECOVERED = "recovered"  # the obligation was collected
    EXHAUSTED = "exhausted"  # the strategy reached its terminal boundary — **not** "nothing owed"
    CANCELLED = "cancelled"  # the obligation went away (voided, credited, withdrawn)
    ESCALATED = "escalated"  # handed to a human; automation has stopped


class CaseTrigger(StrEnum):
    OPEN = "open"
    SCHEDULE = "schedule"
    MATURE = "mature"
    CLAIM = "claim"
    BLOCK = "block"
    UNBLOCK = "unblock"
    RECOVER = "recover"
    EXHAUST = "exhaust"
    ESCALATE = "escalate"
    CANCEL = "cancel"


# The case lifecycle. What it **refuses** is the specification:
#
#   * **no edge out of `RECOVERED`.** A recovered case cannot be reopened by a stale worker, a
#     replayed event, or a scheduler that read the case before the payment landed. If money comes
#     back later that is a refund and a *new* obligation, never a rewrite of this one;
#   * **no edge out of `EXHAUSTED`.** `EXHAUSTED → SCHEDULED` is the transition that would let a
#     policy edit resurrect a closed recovery and re-dun a customer who was told the matter was
#     closed. A genuinely new commercial decision opens a **new cycle** — recorded as an increment
#     with its own actions and its own decision trail — and the FSM is not asked to lie about it;
#   * **no edge out of `CANCELLED`.** The debt was withdrawn; there is nothing to collect;
#   * **`ESCALATED` is terminal for automation and only for automation.** A human may cancel or
#     recover it, which are the two facts a human can actually establish, and nothing else;
#   * **`BLOCKED` leaves only by `UNBLOCK`, `RECOVER` or `CANCEL`.** There is no `BLOCKED →
#     ACTION_DUE`: a case waiting on an ambiguous payment does not become actionable because time
#     passed. It becomes actionable when the ambiguity is *resolved*, which is reconciliation's
#     news to deliver, not the clock's.
COLLECTION_CASE_LIFECYCLE: StateMachineDefinition = define(
    [
        (CaseState.DRAFT, CaseTrigger.SCHEDULE, CaseState.SCHEDULED),
        (CaseState.DRAFT, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.DRAFT, CaseTrigger.CANCEL, CaseState.CANCELLED),
        (CaseState.SCHEDULED, CaseTrigger.MATURE, CaseState.ACTION_DUE),
        (CaseState.SCHEDULED, CaseTrigger.BLOCK, CaseState.BLOCKED),
        (CaseState.SCHEDULED, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.SCHEDULED, CaseTrigger.EXHAUST, CaseState.EXHAUSTED),
        (CaseState.SCHEDULED, CaseTrigger.ESCALATE, CaseState.ESCALATED),
        (CaseState.SCHEDULED, CaseTrigger.CANCEL, CaseState.CANCELLED),
        (CaseState.ACTION_DUE, CaseTrigger.CLAIM, CaseState.IN_PROGRESS),
        (CaseState.ACTION_DUE, CaseTrigger.BLOCK, CaseState.BLOCKED),
        (CaseState.ACTION_DUE, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.ACTION_DUE, CaseTrigger.EXHAUST, CaseState.EXHAUSTED),
        (CaseState.ACTION_DUE, CaseTrigger.ESCALATE, CaseState.ESCALATED),
        (CaseState.ACTION_DUE, CaseTrigger.CANCEL, CaseState.CANCELLED),
        (CaseState.IN_PROGRESS, CaseTrigger.SCHEDULE, CaseState.SCHEDULED),
        (CaseState.IN_PROGRESS, CaseTrigger.BLOCK, CaseState.BLOCKED),
        (CaseState.IN_PROGRESS, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.IN_PROGRESS, CaseTrigger.EXHAUST, CaseState.EXHAUSTED),
        (CaseState.IN_PROGRESS, CaseTrigger.ESCALATE, CaseState.ESCALATED),
        (CaseState.IN_PROGRESS, CaseTrigger.CANCEL, CaseState.CANCELLED),
        (CaseState.BLOCKED, CaseTrigger.UNBLOCK, CaseState.SCHEDULED),
        (CaseState.BLOCKED, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.BLOCKED, CaseTrigger.ESCALATE, CaseState.ESCALATED),
        (CaseState.BLOCKED, CaseTrigger.CANCEL, CaseState.CANCELLED),
        (CaseState.ESCALATED, CaseTrigger.RECOVER, CaseState.RECOVERED),
        (CaseState.ESCALATED, CaseTrigger.CANCEL, CaseState.CANCELLED),
    ]
)

#: The state each trigger **establishes**.
#:
#: Used to tell "this transition is illegal" apart from "this transition has already happened".
#: Re-evaluating a case that is already `SCHEDULED` asks for `SCHEDULE` again; the FSM has no such
#: edge and should not — a self-loop would record the passage of a scheduler sweep as a business
#: fact. But refusing it outright would make a repeated evaluation an error, and a scheduler that
#: cannot safely run twice is a scheduler nobody can operate. So the caller consults this table:
#: already in the established state means *nothing to do*, and any other refusal stays loud.
TRIGGER_ESTABLISHES: dict[CaseTrigger, CaseState] = {
    CaseTrigger.OPEN: CaseState.DRAFT,
    CaseTrigger.SCHEDULE: CaseState.SCHEDULED,
    CaseTrigger.MATURE: CaseState.ACTION_DUE,
    CaseTrigger.CLAIM: CaseState.IN_PROGRESS,
    CaseTrigger.BLOCK: CaseState.BLOCKED,
    CaseTrigger.UNBLOCK: CaseState.SCHEDULED,
    CaseTrigger.RECOVER: CaseState.RECOVERED,
    CaseTrigger.EXHAUST: CaseState.EXHAUSTED,
    CaseTrigger.ESCALATE: CaseState.ESCALATED,
    CaseTrigger.CANCEL: CaseState.CANCELLED,
}

#: Cases automation will never touch again.
TERMINAL_CASE_STATES: frozenset[CaseState] = frozenset(
    {CaseState.RECOVERED, CaseState.EXHAUSTED, CaseState.CANCELLED}
)

#: Cases where a *recovery* has been established. Nothing may move these anywhere.
SETTLED_CASE_STATES: frozenset[CaseState] = frozenset({CaseState.RECOVERED})

#: Cases automation should stop selecting actions for, terminal or not.
QUIESCENT_CASE_STATES: frozenset[CaseState] = TERMINAL_CASE_STATES | {CaseState.ESCALATED}


# ── the action lifecycle ────────────────────────────────────────────────────────


class ActionKind(StrEnum):
    """What a collection decision can ask for.

    Exactly one of these causes money to be requested, and it does so by delegation. The rest are
    the reason this domain is a policy engine and not a payment engine."""

    NOTIFY = "notify"  # tell the customer something is outstanding
    FINAL_NOTICE = "final_notice"  # the last communication before the strategy ends
    REQUEST_PAYMENT_RETRY = "request_payment_retry"  # ask **Payments** to move money
    AWAIT_RECONCILIATION = "await_reconciliation"  # an ambiguous payment must be resolved first
    ESCALATE_TO_OPERATOR = "escalate_to_operator"  # a human decides what happens next
    STOP = "stop"  # the strategy has reached its boundary


#: The one kind that results in a request for money. Everything else is communication, waiting, or
#: handoff — and an architecture test asserts nothing else ever reaches the payment seam.
MONEY_REQUESTING_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.REQUEST_PAYMENT_RETRY})

#: The kinds that reach a customer. These are the ones the notice port may be asked to deliver.
COMMUNICATION_ACTIONS: frozenset[ActionKind] = frozenset(
    {ActionKind.NOTIFY, ActionKind.FINAL_NOTICE}
)


class ActionState(StrEnum):
    """The state of one decided action.

    `CLAIMED` exists because of the same discipline M9.16 applies to provider operations: the row
    is durable *before* anything external happens, so a crash between deciding and executing leaves
    a record naming exactly what was in flight."""

    PLANNED = "planned"  # decided and durable; nothing external has happened
    CLAIMED = "claimed"  # a worker holds it; the external effect may be in flight
    EXECUTED = "executed"  # it happened, and the attempt records what came back
    FAILED = "failed"  # it was attempted and did not happen
    OBSOLETE = "obsolete"  # overtaken by events — the debt was paid or withdrawn first
    SKIPPED = "skipped"  # policy no longer selects it (grace changed, stage withdrawn)


class ActionTrigger(StrEnum):
    CLAIM = "claim"
    EXECUTE = "execute"
    FAIL = "fail"
    OBSOLETE = "obsolete"
    SKIP = "skip"


# The action lifecycle. Its refusals:
#
#   * **no edge out of `EXECUTED`.** A notice that was sent was sent. A retry that was requested
#     was requested. Neither un-happens because a later sweep disagreed;
#   * **`CLAIMED → OBSOLETE` exists.** This is the payment-success-during-execution race: a worker
#     claims a retry, and before it completes the money lands by another route. The action is
#     recorded as overtaken rather than executed, and no second request is made;
#   * **`PLANNED → OBSOLETE` exists and `OBSOLETE → CLAIMED` does not.** An obsolete action is
#     never resurrected; a genuinely new decision is a new action with a new sequence.
COLLECTION_ACTION_LIFECYCLE: StateMachineDefinition = define(
    [
        (ActionState.PLANNED, ActionTrigger.CLAIM, ActionState.CLAIMED),
        (ActionState.PLANNED, ActionTrigger.OBSOLETE, ActionState.OBSOLETE),
        (ActionState.PLANNED, ActionTrigger.SKIP, ActionState.SKIPPED),
        (ActionState.CLAIMED, ActionTrigger.EXECUTE, ActionState.EXECUTED),
        (ActionState.CLAIMED, ActionTrigger.FAIL, ActionState.FAILED),
        (ActionState.CLAIMED, ActionTrigger.OBSOLETE, ActionState.OBSOLETE),
    ]
)

#: Actions no worker should pick up.
CLOSED_ACTION_STATES: frozenset[ActionState] = frozenset(
    {ActionState.EXECUTED, ActionState.FAILED, ActionState.OBSOLETE, ActionState.SKIPPED}
)


class AttemptOutcome(StrEnum):
    """What came back from executing an action.

    `NOT_SENT` is the communication analogue of M9.16's `ProviderNotSent`: the transport asserts
    that nothing left. Anything weaker than that assertion is `DEFERRED`, not `NOT_SENT`, because
    "we think the email probably did not go" is not evidence and a second send is a second email
    to a customer who is already unhappy."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEFERRED = "deferred"  # the transport could not say; do not repeat blindly
    NOT_SENT = "not_sent"  # the transport asserts nothing left


class CommandDisposition(StrEnum):
    """How the Application handled a command. Returned on every write result so a duplicate is
    never mistaken for a new collection action by the caller that issued it."""

    NEW = "new"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    DEFERRED = "deferred"


# ── policy ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionStage:
    """One rung of a recovery strategy. Immutable, and immutable *inside a policy version* — which
    is the whole point: a stage is only meaningful together with the version it belongs to."""

    sequence: int
    kind: ActionKind
    wait_seconds: int = 0  # from the previous stage's completion to this stage becoming due
    grace_seconds: int = 0  # additional forbearance before this stage may act
    label: str = ""

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("collection stage sequence must be >= 0")
        if self.wait_seconds < 0 or self.grace_seconds < 0:
            raise ValueError("collection stage waits must be non-negative")

    @property
    def delay_seconds(self) -> int:
        """Total forbearance before this stage may act. Grace is *additive* to the wait rather
        than a substitute for it, so extending grace never accidentally shortens a schedule."""
        return self.wait_seconds + self.grace_seconds

    @property
    def is_terminal(self) -> bool:
        return self.kind in (ActionKind.STOP, ActionKind.ESCALATE_TO_OPERATOR)


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    """**A versioned, immutable recovery strategy.**

    M9.2's `DunningPolicy` is a bare value with no identity — perfectly correct for a value, and
    unusable as the authority behind a historical decision, because there is nothing to point at
    when asked "under what rules was this customer dunned in March?". This one has an id, a code, a
    version, and no update path. Publishing a change appends version *n+1*; the case that was
    decided under version *n* still names version *n*, and its decisions replay exactly.

    A policy holds **no money and no amounts**. It holds waits, graces, and the order of stages."""

    id: str
    org_id: str
    code: str  # the stable business key; versions share it
    version: int
    stages: tuple[CollectionStage, ...] = ()
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    max_cycles: int = 1
    is_active: bool = True
    description: str = ""
    declared_at: float = 0.0
    declared_by: str = ""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("collection policy version must be >= 1")
        if self.max_cycles < 1:
            raise ValueError("collection policy max_cycles must be >= 1")

    @property
    def stage_count(self) -> int:
        return len(self.stages)

    def stage_at(self, index: int) -> CollectionStage | None:
        """The stage at `index`, or `None` past the end. Returning `None` rather than clamping is
        deliberate: running off the end of a strategy *is* exhaustion, and a clamp would silently
        repeat the last rung forever."""
        if 0 <= index < len(self.stages):
            return self.stages[index]
        return None

    @property
    def version_key(self) -> str:
        return f"{self.code}@{self.version}"


# ── the case ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionCase:
    """**The long-lived recovery of one obligation.**

    Its id is derived from the obligation, so it is unique by construction rather than by
    convention. Its `version` is the optimistic-concurrency token: every transition is a
    compare-and-set against the version the worker read, so a worker that decided from a stale view
    of the world fails loudly instead of overwriting a recovery that happened while it was thinking.

    Fields that deliberately do not exist:

    * **no amount, no balance, no `outstanding`.** Collections does not hold a figure another
      domain owns. The API computes it at read time from the Payment position;
    * **no policy *body*.** Only `policy_id` and `policy_version`, so the decision trail points at
      an immutable row instead of carrying a copy that could drift;
    * **no `next_action` payload.** The next action is a durable `CollectionAction` row, because a
      plan that lives only as a column is a plan no worker can claim exclusively."""

    id: str
    org_id: str
    account_id: str
    obligation_kind: ObligationKind = ObligationKind.INVOICE
    obligation_id: str = ""
    currency: str = ""  # carried for display/routing only; never used in arithmetic here
    state: CaseState = CaseState.DRAFT
    policy_id: str = ""
    policy_code: str = ""
    policy_version: int = 0
    cycle: int = 1
    stage_index: int = 0
    actions_planned: int = 0
    version: int = 0  # optimistic-concurrency token; incremented on every transition
    signal: PaymentSignal = PaymentSignal.NONE
    failure_class: FailureClass = FailureClass.UNKNOWN
    payment_intent_id: str = ""
    next_action_at: float | None = None
    grace_until: float | None = None
    blocked_reason: str = ""
    opened_at: float = 0.0
    opened_by: str = ""
    updated_at: float = 0.0
    closed_at: float | None = None
    closed_reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_CASE_STATES

    @property
    def is_settled(self) -> bool:
        return self.state in SETTLED_CASE_STATES

    @property
    def is_quiescent(self) -> bool:
        """True when automation must not select another action, whether or not the case is over.
        An escalated case is live commercially and closed to the scheduler."""
        return self.state in QUIESCENT_CASE_STATES

    @property
    def next_sequence(self) -> int:
        """The number the next action in this cycle takes. Dense, ascending, never reused."""
        return self.actions_planned + 1

    def action_id_for(self, sequence: int) -> str:
        return derive_action_id(self.id, self.cycle, sequence)

    def is_due(self, at: float) -> bool:
        """Whether the clock has reached this case's next action. Clock-free: `at` is passed in,
        never read, so a decision is reproducible by replaying the same instant."""
        if self.is_quiescent or self.next_action_at is None:
            return False
        return at >= self.next_action_at


# ── decisions and actions ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionDecision:
    """**Why an action was selected — reproducible after the policy changes.**

    Every input that fed the choice is recorded next to the choice: the policy *version* (not the
    policy), the stage index, the payment signal, the failure class, the instant, and the resulting
    due time. That is the difference between an audit trail and a log: given this row and the
    immutable policy version it names, the same decision recomputes exactly, in April, under a
    strategy that has been rewritten twice.

    It is a fact, not a plan. A decision that selected *nothing* is still recorded — "we looked and
    the answer was wait" is precisely the row missing when someone asks why a customer was not
    chased."""

    id: str
    org_id: str
    case_id: str
    cycle: int
    stage_index: int
    signal: PaymentSignal
    failure_class: FailureClass = FailureClass.UNKNOWN
    selected: ActionKind | None = None
    action_id: str = ""
    policy_id: str = ""
    policy_code: str = ""
    policy_version: int = 0
    reason: str = ""
    reason_path: tuple[str, ...] = ()
    decided_at: float = 0.0
    due_at: float | None = None

    @property
    def selected_anything(self) -> bool:
        return self.selected is not None


@dataclass(frozen=True, slots=True)
class CollectionAction:
    """**One durable decision inside a case**, claimable by exactly one worker.

    Durable before any external effect, for the reason M9.16 states about provider operations: a
    crash between "we decided to send a final notice" and "the notice was sent" must leave a record
    saying which, or the recovery from that crash is a guess.

    Carries no money. `REQUEST_PAYMENT_RETRY` names the intent to retry and nothing about how much
    — the amount is the Payment Engine's, read from the objective it already holds."""

    id: str
    org_id: str
    case_id: str
    cycle: int
    sequence: int
    kind: ActionKind
    state: ActionState = ActionState.PLANNED
    stage_index: int = 0
    decision_id: str = ""
    policy_id: str = ""
    policy_version: int = 0
    due_at: float = 0.0
    planned_at: float = 0.0
    claimed_at: float | None = None
    claimed_by: str = ""
    completed_at: float | None = None
    attempts_made: int = 0
    result_ref: str = ""  # e.g. the payment attempt id Payments returned; never an amount
    note: str = ""

    @property
    def is_closed(self) -> bool:
        return self.state in CLOSED_ACTION_STATES

    @property
    def requests_money(self) -> bool:
        return self.kind in MONEY_REQUESTING_ACTIONS

    @property
    def is_communication(self) -> bool:
        return self.kind in COMMUNICATION_ACTIONS

    @property
    def next_attempt_sequence(self) -> int:
        return self.attempts_made + 1

    def attempt_id_for(self, sequence: int) -> str:
        return derive_collection_attempt_id(self.id, sequence)

    def is_due(self, at: float) -> bool:
        return not self.is_closed and at >= self.due_at


@dataclass(frozen=True, slots=True)
class CollectionAttempt:
    """**Evidence that an action was executed, and what came back.** Append-only.

    Separate from the action for the reason M9.2's integer counter could not express: an action
    that was tried three times and deferred twice is one decision with three pieces of evidence,
    and collapsing them loses which of the three actually reached the customer."""

    id: str
    org_id: str
    case_id: str
    action_id: str
    sequence: int
    kind: ActionKind
    outcome: AttemptOutcome = AttemptOutcome.DEFERRED
    channel: str = ""  # provider-neutral: "email", "sms", "in_app" — never a vendor name
    reference: str = ""  # opaque transport/payment reference; never instrument data
    detail: str = ""
    executed_at: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.outcome is AttemptOutcome.SUCCEEDED


@dataclass(frozen=True, slots=True)
class CollectionActivity:
    """One audit line on a case. The narrative a support agent reads."""

    id: str
    org_id: str
    case_id: str
    at: float
    kind: str
    summary: str = ""
    actor_id: str = ""
    action_id: str = ""
    detail: dict[str, str] = field(default_factory=dict)


# ── the communication port's DTOs (provider-neutral) ────────────────────────────


@dataclass(frozen=True, slots=True)
class NoticeRequest:
    """What Collections asks a notifier to deliver.

    Note what it does not carry: no email address, no phone number, no template body, no vendor
    options, and **no amount**. It names the account, the obligation, the kind of notice, and an
    idempotency key. Resolving a recipient, rendering a template, and quoting a figure are all
    somebody else's job, and a port that accepted them would make Collections the owner of three
    things it does not own."""

    org_id: str
    case_id: str
    action_id: str
    account_id: str
    obligation_kind: ObligationKind
    obligation_id: str
    kind: ActionKind
    idempotency_key: str
    locale: str = ""


@dataclass(frozen=True, slots=True)
class NoticeOutcome:
    """What a notifier reports. Deliberately the same three-way shape as `ProviderOutcome`:
    delivered, refused, or *unknown* — and unknown is not a licence to send again."""

    outcome: AttemptOutcome
    channel: str = ""
    reference: str = ""
    detail: str = ""


# ── derived reads ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionExplanation:
    """**Why this case is where it is — answerable without re-running policy.**

    Mirrors the Entitlements resolution path: an ordered list of the checks that fired, ending in
    the conclusion. Observability, never authority — nothing reads this back."""

    case_id: str
    state: CaseState
    signal: PaymentSignal
    stage_index: int
    policy_code: str = ""
    policy_version: int = 0
    next_kind: ActionKind | None = None
    next_action_at: float | None = None
    grace_until: float | None = None
    reason: str = ""
    path: tuple[str, ...] = ()
    blocked_reason: str = ""


@dataclass(frozen=True, slots=True)
class CollectionInspection:
    """A case with its counts — the operator's single screen."""

    case: CollectionCase
    actions: int = 0
    open_actions: int = 0
    executed_actions: int = 0
    attempts: int = 0
    decisions: int = 0
    cycles: int = 1
    stage_count: int = 0
    is_due: bool = False


@dataclass(frozen=True, slots=True)
class CollectionSearchQuery:
    """Bounded search over cases. Every filter maps onto an indexed column — the access paths were
    chosen from the queries the dashboard actually issues, not from what looked complete."""

    org_id: str
    account_id: str = ""
    obligation_id: str = ""
    states: tuple[CaseState, ...] = ()
    signals: tuple[PaymentSignal, ...] = ()
    due_before: float | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CollectionSearchResult:
    cases: tuple[CollectionCase, ...] = ()
    total: int = 0
    truncated: bool = False


__all__ = [
    "CLOSED_ACTION_STATES",
    "COLLECTION_ACTION_LIFECYCLE",
    "COLLECTION_CASE_LIFECYCLE",
    "COMMUNICATION_ACTIONS",
    "MONEY_REQUESTING_ACTIONS",
    "QUIESCENT_CASE_STATES",
    "RECOVERING_SIGNALS",
    "RETRYABLE_SIGNALS",
    "SETTLED_CASE_STATES",
    "TERMINAL_CASE_STATES",
    "TRIGGER_ESTABLISHES",
    "ActionKind",
    "ActionState",
    "ActionTrigger",
    "AttemptOutcome",
    "CaseState",
    "CaseTrigger",
    "CollectionAction",
    "CollectionActivity",
    "CollectionAttempt",
    "CollectionCase",
    "CollectionDecision",
    "CollectionExplanation",
    "CollectionInspection",
    "CollectionPolicy",
    "CollectionSearchQuery",
    "CollectionSearchResult",
    "CollectionStage",
    "CommandDisposition",
    "FailureClass",
    "NoticeOutcome",
    "NoticeRequest",
    "ObligationKind",
    "PaymentSignal",
    "derive_action_id",
    "derive_case_id",
    "derive_collection_attempt_id",
    "derive_decision_id",
    "derive_policy_version_id",
]
