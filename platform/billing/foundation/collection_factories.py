"""Collection factories + repository contracts (M9.17).

`CollectionFactory` is the one place a policy version, a case, a decision, an action, an attempt or
an audit line is constructed. Same discipline as every milestone since M9.2: validate structurally,
fail fast on error-severity findings (`BillingValidationError`, §A2.6), compose an immutable
aggregate from the injected id and clock seams, emit the corresponding `billing.*` fact through the
injected emitter. No I/O, no persistence, no transport, no provider, no scheduler.

**Exactly one event per business fact**, and three of the facts this milestone produces already had
names at M9.2 — `billing.dunning.started`, `billing.dunning.recovered`, `billing.dunning.abandoned`
— so they are reused rather than renamed. A case opening *is* dunning starting. Giving it a second
type would leave every consumer with two ways to count the same thing.

Two emissions are easy to miss and deliberate:

* planning an action emits `collection.action_planned` **before** anything external happens, and
  executing it emits `collection.action_executed` afterwards. Two facts, because "we decided to
  chase this customer" and "the customer was chased" are different, and a crash between them is
  exactly the state an operator needs to see;
* an action overtaken by a payment emits `collection.action_obsolete` rather than nothing. A
  planned retry that was never sent because the money arrived first is a *good* outcome and an
  invisible one unless it is published.

---

## Why identities are derived rather than minted

Each derivation answers a specific race that a real deployment produces:

* a **case** is derived from `(org, obligation kind, obligation id)`, so a duplicated
  payment-failure event, a scheduler that ran twice, a re-read invoice and an application restart
  all compute one id and one of them loses the insert. There is no world in which one debt has two
  recoveries;
* an **action** is derived from `(case, cycle, sequence)`, so two workers that both conclude "the
  next action is number 3" collide on a primary key rather than both sending a notice;
* a **decision** is derived from its *inputs* — case, cycle, stage, signal — so re-evaluating an
  unchanged case records the same row. A minute-by-minute scheduler produces one decision per real
  decision, not one per sweep;
* a **policy version** is derived from `(org, code, version)`, so publishing the same version twice
  is idempotent and publishing a *different* body under the same version is a primary-key
  collision rather than a silent rewrite of history.

## What no repository here can do

There is no `update` on `CollectionPolicyRepository`, no `delete` anywhere, and no method that
takes a case and writes it unconditionally. `CollectionCaseRepository.advance` takes the version
the caller read and is a compare-and-set: a worker that decided from a stale view fails loudly
rather than overwriting a recovery that happened while it was thinking. That signature is the
stale-worker defence, and it is a contract rather than a convention because a convention is what
the next caller does not know about.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from .collection_rules import CollectionValidator, DecisionOutcome
from .commercial_collections import (
    ActionKind,
    ActionState,
    AttemptOutcome,
    CaseState,
    CollectionAction,
    CollectionActivity,
    CollectionAttempt,
    CollectionCase,
    CollectionDecision,
    CollectionPolicy,
    CollectionStage,
    FailureClass,
    NoticeOutcome,
    ObligationKind,
    PaymentSignal,
    derive_action_id,
    derive_case_id,
    derive_collection_attempt_id,
    derive_decision_id,
    derive_policy_version_id,
)
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_COLLECTION_ACTIVITY = "cal"

#: Which `billing.*` fact each case state publishes when a case enters it. A table so "exactly one
#: event per business fact" is checkable by iteration rather than asserted in prose.
#:
#: `SCHEDULED`, `ACTION_DUE` and `IN_PROGRESS` are absent, and that is the interesting part: they
#: are *positions in a workflow*, not commercial facts. A case moving from scheduled to due is the
#: clock passing, and publishing it would fill the audit stream with the passage of time.
CASE_EVENTS: dict[CaseState, BillingEventType] = {
    CaseState.RECOVERED: BillingEventType.DUNNING_RECOVERED,
    CaseState.EXHAUSTED: BillingEventType.DUNNING_ABANDONED,
    CaseState.BLOCKED: BillingEventType.COLLECTION_CASE_BLOCKED,
    CaseState.ESCALATED: BillingEventType.COLLECTION_CASE_ESCALATED,
    CaseState.CANCELLED: BillingEventType.COLLECTION_CASE_CANCELLED,
}

#: And for actions, once executed. The kind decides the fact: a notice that went out and a retry
#: that was requested are not the same news, and an integration that had to parse a payload to tell
#: them apart would eventually tell them apart wrongly.
EXECUTED_ACTION_EVENTS: dict[ActionKind, BillingEventType] = {
    ActionKind.NOTIFY: BillingEventType.COLLECTION_NOTICE_SENT,
    ActionKind.FINAL_NOTICE: BillingEventType.COLLECTION_NOTICE_SENT,
    ActionKind.REQUEST_PAYMENT_RETRY: BillingEventType.COLLECTION_RETRY_REQUESTED,
    ActionKind.AWAIT_RECONCILIATION: BillingEventType.COLLECTION_ACTION_EXECUTED,
    ActionKind.ESCALATE_TO_OPERATOR: BillingEventType.COLLECTION_CASE_ESCALATED,
    ActionKind.STOP: BillingEventType.COLLECTION_ACTION_EXECUTED,
}


@dataclass(frozen=True, slots=True)
class CollectionFactory:
    """Constructs every Collections aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: CollectionValidator = CollectionValidator()

    # ── policy ──────────────────────────────────────────────────────────────────

    def policy(
        self,
        *,
        org_id: str,
        code: str,
        version: int,
        stages: tuple[CollectionStage, ...],
        obligation_kind: ObligationKind = ObligationKind.INVOICE,
        max_cycles: int = 1,
        description: str = "",
        actor_id: str = "",
    ) -> CollectionPolicy:
        """Publish one immutable policy version.

        There is no `update_policy`. Changing a strategy means publishing version *n+1*, and every
        case decided under *n* keeps naming *n* — which is the only way a decision taken before the
        change stays reproducible after it."""
        now = self.clock.now()
        built = CollectionPolicy(
            id=derive_policy_version_id(org_id, code, version),
            org_id=org_id,
            code=code,
            version=version,
            stages=stages,
            obligation_kind=obligation_kind,
            max_cycles=max_cycles,
            description=description,
            declared_at=now,
            declared_by=actor_id,
        )
        self._fail_fast(self.validator.validate_policy(built))
        self._emit(
            BillingEventType.COLLECTION_POLICY_PUBLISHED,
            org_id=org_id,
            subject_id=built.id,
            actor_id=actor_id,
            data={"code": code, "version": str(version), "stages": str(len(stages))},
        )
        return built

    # ── the case ────────────────────────────────────────────────────────────────

    def case(
        self,
        *,
        org_id: str,
        account_id: str,
        obligation_kind: ObligationKind,
        obligation_id: str,
        policy: CollectionPolicy,
        currency: str = "",
        payment_intent_id: str = "",
        signal: PaymentSignal = PaymentSignal.NONE,
        failure_class: FailureClass = FailureClass.UNKNOWN,
        actor_id: str = "",
    ) -> CollectionCase:
        """Open a recovery. The id is the obligation's, so opening it twice is one row.

        The case starts at `DRAFT` with `version=0`; the first transition is the Application's, and
        it is a compare-and-set from 0. A case that was opened and never scheduled is visible as
        exactly that rather than as a case that mysteriously has no action."""
        now = self.clock.now()
        built = CollectionCase(
            id=derive_case_id(org_id, obligation_kind.value, obligation_id),
            org_id=org_id,
            account_id=account_id,
            obligation_kind=obligation_kind,
            obligation_id=obligation_id,
            currency=currency,
            state=CaseState.DRAFT,
            policy_id=policy.id,
            policy_code=policy.code,
            policy_version=policy.version,
            cycle=1,
            stage_index=0,
            version=0,
            signal=signal,
            failure_class=failure_class,
            payment_intent_id=payment_intent_id,
            opened_at=now,
            opened_by=actor_id,
            updated_at=now,
        )
        self._fail_fast(self.validator.validate_case(built))
        self._emit(
            BillingEventType.DUNNING_STARTED,
            org_id=org_id,
            subject_id=built.id,
            actor_id=actor_id,
            data={
                "obligation": f"{obligation_kind.value}:{obligation_id}",
                "account_id": account_id,
                "policy": policy.version_key,
            },
        )
        return built

    def emit_case_state(
        self, case: CollectionCase, *, actor_id: str = "", reason: str = ""
    ) -> None:
        """Publish the fact a case's new state represents, if it represents one."""
        event_type = CASE_EVENTS.get(case.state)
        if event_type is None:
            return
        self._emit(
            event_type,
            org_id=case.org_id,
            subject_id=case.id,
            actor_id=actor_id,
            data={
                "state": case.state.value,
                "obligation": f"{case.obligation_kind.value}:{case.obligation_id}",
                "cycle": str(case.cycle),
                "reason": reason or case.closed_reason or case.blocked_reason,
            },
        )

    def open_cycle(
        self, case: CollectionCase, *, policy: CollectionPolicy, at: float, actor_id: str = ""
    ) -> CollectionCase:
        """Start a **new recovery cycle** on an existing case.

        This is the honest answer to "the business wants to try again after exhaustion". The FSM
        refuses `EXHAUSTED → SCHEDULED` because that would rewrite the record of a recovery that
        ended; a new cycle instead increments `cycle`, resets the stage, and re-derives every
        action id under the new cycle number — so the previous cycle's actions, attempts and
        decisions remain readable exactly as they were.

        It is a deliberate commercial decision, so it carries an actor and emits its own fact."""
        moved = replace(
            case,
            cycle=case.cycle + 1,
            stage_index=0,
            actions_planned=0,
            state=CaseState.DRAFT,
            policy_id=policy.id,
            policy_code=policy.code,
            policy_version=policy.version,
            version=case.version + 1,
            closed_at=None,
            closed_reason="",
            blocked_reason="",
            next_action_at=None,
            grace_until=None,
            updated_at=at,
        )
        self._emit(
            BillingEventType.COLLECTION_CYCLE_OPENED,
            org_id=case.org_id,
            subject_id=case.id,
            actor_id=actor_id,
            data={"cycle": str(moved.cycle), "policy": policy.version_key},
        )
        return moved

    # ── decisions and actions ───────────────────────────────────────────────────

    def decision(
        self,
        case: CollectionCase,
        outcome: DecisionOutcome,
        *,
        policy: CollectionPolicy,
        signal: PaymentSignal,
        failure_class: FailureClass,
        at: float,
        action_id: str = "",
    ) -> CollectionDecision:
        """Record why. Derived from the inputs, so a repeated evaluation is one row.

        A decision that selected nothing is still recorded — "we looked and the answer was wait" is
        precisely the row that is missing when someone asks why a customer was not chased."""
        return CollectionDecision(
            id=derive_decision_id(case.id, case.cycle, outcome.stage_index, signal.value),
            org_id=case.org_id,
            case_id=case.id,
            cycle=case.cycle,
            stage_index=outcome.stage_index,
            signal=signal,
            failure_class=failure_class,
            selected=outcome.selected,
            action_id=action_id,
            policy_id=policy.id,
            policy_code=policy.code,
            policy_version=policy.version,
            reason=outcome.reason,
            reason_path=outcome.path,
            decided_at=at,
            due_at=outcome.due_at,
        )

    def action(
        self,
        case: CollectionCase,
        *,
        kind: ActionKind,
        sequence: int,
        stage_index: int,
        due_at: float,
        policy: CollectionPolicy,
        decision_id: str = "",
        at: float = 0.0,
        actor_id: str = "",
    ) -> CollectionAction:
        """Plan one action. **Durable before anything external happens.**

        Emitted as `action_planned`, not as the thing it will eventually do. The gap between this
        fact and `action_executed` is where a crash lives, and an operator who can see the gap can
        recover from it."""
        built = CollectionAction(
            id=derive_action_id(case.id, case.cycle, sequence),
            org_id=case.org_id,
            case_id=case.id,
            cycle=case.cycle,
            sequence=sequence,
            kind=kind,
            state=ActionState.PLANNED,
            stage_index=stage_index,
            decision_id=decision_id,
            policy_id=policy.id,
            policy_version=policy.version,
            due_at=due_at,
            planned_at=at or self.clock.now(),
        )
        self._fail_fast(self.validator.validate_action(built, case.signal))
        self._emit(
            BillingEventType.COLLECTION_ACTION_PLANNED,
            org_id=case.org_id,
            subject_id=built.id,
            actor_id=actor_id,
            data={
                "case_id": case.id,
                "kind": kind.value,
                "cycle": str(case.cycle),
                "sequence": str(sequence),
                "due_at": str(due_at),
            },
        )
        return built

    def emit_action_executed(
        self, action: CollectionAction, *, actor_id: str = "", detail: str = ""
    ) -> None:
        event_type = EXECUTED_ACTION_EVENTS.get(
            action.kind, BillingEventType.COLLECTION_ACTION_EXECUTED
        )
        self._emit(
            event_type,
            org_id=action.org_id,
            subject_id=action.id,
            actor_id=actor_id,
            data={
                "case_id": action.case_id,
                "kind": action.kind.value,
                "result_ref": action.result_ref,
                "detail": detail,
            },
        )

    def emit_action_obsolete(
        self, action: CollectionAction, *, actor_id: str = "", reason: str = ""
    ) -> None:
        """A planned action overtaken by events. Published because a retry that was *not* sent
        because the money arrived first is a good outcome, and an invisible one otherwise."""
        self._emit(
            BillingEventType.COLLECTION_ACTION_OBSOLETE,
            org_id=action.org_id,
            subject_id=action.id,
            actor_id=actor_id,
            data={"case_id": action.case_id, "kind": action.kind.value, "reason": reason},
        )

    def attempt(
        self,
        action: CollectionAction,
        *,
        sequence: int,
        outcome: AttemptOutcome,
        channel: str = "",
        reference: str = "",
        detail: str = "",
        at: float = 0.0,
    ) -> CollectionAttempt:
        """Record the evidence of one execution. Append-only; sequence never reused."""
        return CollectionAttempt(
            id=derive_collection_attempt_id(action.id, sequence),
            org_id=action.org_id,
            case_id=action.case_id,
            action_id=action.id,
            sequence=sequence,
            kind=action.kind,
            outcome=outcome,
            channel=channel,
            reference=reference,
            detail=detail,
            executed_at=at or self.clock.now(),
        )

    def attempt_from_notice(
        self, action: CollectionAction, notice: NoticeOutcome, *, sequence: int, at: float = 0.0
    ) -> CollectionAttempt:
        """The notifier's answer, recorded verbatim. No interpretation: a `DEFERRED` outcome stays
        deferred rather than becoming a failure that a later sweep would feel entitled to retry."""
        return self.attempt(
            action,
            sequence=sequence,
            outcome=notice.outcome,
            channel=notice.channel,
            reference=notice.reference,
            detail=notice.detail,
            at=at,
        )

    # ── audit ───────────────────────────────────────────────────────────────────

    def activity(
        self,
        case: CollectionCase,
        *,
        kind: str,
        summary: str = "",
        actor_id: str = "",
        action_id: str = "",
        detail: dict[str, str] | None = None,
        at: float = 0.0,
    ) -> CollectionActivity:
        return CollectionActivity(
            id=self.ids.new(PREFIX_COLLECTION_ACTIVITY),
            org_id=case.org_id,
            case_id=case.id,
            at=at or self.clock.now(),
            kind=kind,
            summary=summary,
            actor_id=actor_id,
            action_id=action_id,
            detail=dict(detail or {}),
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        *,
        org_id: str,
        subject_id: str,
        actor_id: str = "",
        data: dict[str, str] | None = None,
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data or {}),
            )
        )


# ── repository contracts ────────────────────────────────────────────────────────


class CollectionPolicyRepository(ABC):
    """Immutable policy versions. **No `update`, by design.**

    A strategy is changed by publishing the next version. If this interface had an update method,
    a case decided under version 3 could no longer be replayed, and the decision trail would be
    pointing at a row that has since become something else."""

    @abstractmethod
    def add(self, policy: CollectionPolicy) -> tuple[CollectionPolicy, bool]:
        """Append a version. Returns `(policy, is_new)`; a replayed publish returns the stored
        one and `False` rather than raising, because publishing the same version twice is a
        duplicate command and not an error."""

    @abstractmethod
    def get(self, org_id: str, policy_id: str) -> CollectionPolicy | None: ...

    @abstractmethod
    def latest(self, org_id: str, code: str) -> CollectionPolicy | None:
        """The highest active version of `code`, or `None`. What a *new* case binds to."""

    @abstractmethod
    def versions(self, org_id: str, code: str) -> tuple[CollectionPolicy, ...]: ...

    @abstractmethod
    def all(self, org_id: str, include_inactive: bool = False) -> tuple[CollectionPolicy, ...]: ...

    @abstractmethod
    def deactivate(self, policy: CollectionPolicy) -> CollectionPolicy:
        """Withdraw a version from *future* binding. It stays readable forever; a case already
        bound to it is unaffected, because its decisions must still replay."""


class CollectionCaseRepository(ABC):
    """Cases, arbitrated by the database.

    Two methods carry the whole concurrency story. `open` is an insert on a derived primary key, so
    two workers opening the same obligation produce one case. `advance` is a compare-and-set on
    `version`, so a worker holding a stale read cannot overwrite what happened while it was
    thinking. Neither is an application-level check, because between a check and a write there is
    a window, and the window is the incident."""

    @abstractmethod
    def open(self, case: CollectionCase) -> tuple[CollectionCase, bool]:
        """Insert if absent. Returns `(case, is_new)`; the stored case when it already exists."""

    @abstractmethod
    def advance(self, case: CollectionCase, *, expected_version: int) -> CollectionCase:
        """Conditional update: write only if the stored version is still `expected_version`.

        Raises `CaseStale` otherwise. The caller's job is to re-read and re-decide — never to
        retry the same write, which would be the stale decision all over again."""

    @abstractmethod
    def get(self, org_id: str, case_id: str) -> CollectionCase | None: ...

    @abstractmethod
    def for_obligation(
        self, org_id: str, obligation_kind: str, obligation_id: str
    ) -> CollectionCase | None: ...

    @abstractmethod
    def for_account(self, org_id: str, account_id: str, limit: int = 200
                    ) -> tuple[CollectionCase, ...]: ...

    @abstractmethod
    def by_state(
        self, org_id: str, states: tuple[str, ...], limit: int = 200
    ) -> tuple[CollectionCase, ...]: ...

    @abstractmethod
    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[CollectionCase, ...]:
        """Cases whose `next_action_at` has passed. **The read a scheduler outside this package
        calls.** There is no writer here and no loop — answering "what is due" is a query."""

    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[CollectionCase, ...]: ...


class CollectionActionRepository(ABC):
    """Planned actions. Append-only apart from the narrow lifecycle column."""

    @abstractmethod
    def plan(self, action: CollectionAction) -> tuple[CollectionAction, bool]:
        """Insert on the derived id. Returns `(action, is_new)` — a duplicate plan returns the
        stored action rather than a second one, which is what makes a re-run of a scheduler sweep
        harmless instead of a second notice."""

    @abstractmethod
    def claim(self, action: CollectionAction, *, expected_state: str) -> CollectionAction:
        """Conditional state move: `PLANNED → CLAIMED` only if still `expected_state`.

        The second concurrency arbitrator. Two workers that both see a due action both try this;
        the database picks one, and the loser raises `CaseStale` and does nothing external."""

    @abstractmethod
    def settle(self, action: CollectionAction) -> CollectionAction:
        """Record a terminal action state and its result reference."""

    @abstractmethod
    def get(self, org_id: str, action_id: str) -> CollectionAction | None: ...

    @abstractmethod
    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionAction, ...]: ...

    @abstractmethod
    def open_for_case(self, org_id: str, case_id: str) -> tuple[CollectionAction, ...]: ...

    @abstractmethod
    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[CollectionAction, ...]: ...


class CollectionAttemptRepository(ABC):
    """Execution evidence. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(self, attempt: CollectionAttempt) -> tuple[CollectionAttempt, bool]: ...

    @abstractmethod
    def for_action(self, org_id: str, action_id: str) -> tuple[CollectionAttempt, ...]: ...

    @abstractmethod
    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionAttempt, ...]: ...


class CollectionDecisionRepository(ABC):
    """The decision trail. Append-only, and idempotent on the derived id — re-evaluating an
    unchanged case writes nothing new."""

    @abstractmethod
    def append(self, decision: CollectionDecision) -> tuple[CollectionDecision, bool]: ...

    @abstractmethod
    def get(self, org_id: str, decision_id: str) -> CollectionDecision | None: ...

    @abstractmethod
    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionDecision, ...]: ...

    @abstractmethod
    def latest(self, org_id: str, case_id: str) -> CollectionDecision | None: ...


class CollectionActivityRepository(ABC):
    """The audit trail an operator reads."""

    @abstractmethod
    def append(self, activity: CollectionActivity) -> CollectionActivity: ...

    @abstractmethod
    def for_case(self, org_id: str, case_id: str, limit: int = 200
                 ) -> tuple[CollectionActivity, ...]: ...


__all__ = [
    "CASE_EVENTS",
    "EXECUTED_ACTION_EVENTS",
    "PREFIX_COLLECTION_ACTIVITY",
    "CollectionActionRepository",
    "CollectionActivityRepository",
    "CollectionAttemptRepository",
    "CollectionCaseRepository",
    "CollectionDecisionRepository",
    "CollectionFactory",
    "CollectionPolicyRepository",
]
