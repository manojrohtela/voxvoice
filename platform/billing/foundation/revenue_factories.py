"""Revenue recognition factories + repository contracts (M9.18).

`RevenueFactory` is the one place a policy version, a basis, a schedule, a recognition entry, an
adjustment or an audit line is constructed. Same discipline as every milestone since M9.2: validate
structurally, fail fast on error-severity findings (`BillingValidationError`, §A2.6), compose an
immutable aggregate from the injected id and clock seams, emit the corresponding `billing.*` fact
through the injected emitter. No I/O, no persistence, no transport, no provider, no scheduler.

**Exactly one event per business fact**, and one is deliberately absent: there is no
`billing.revenue.posted`. A recognition that reaches the Ledger is the **Ledger's** fact and M9.13
already publishes `billing.ledger.entry_recorded` for it. A second type here would let one posting
be counted twice by anyone aggregating the stream.

Two emissions are easy to miss and deliberate:

* `billing.revenue.unposted` fires when the Ledger legitimately refuses a posting — no open
  accounting period. The recognition **decision** is still a fact and must not be lost, so it is
  published rather than swallowed. This is the M9.16 `UNPOSTED_CAPTURE` lesson applied to revenue;
* `billing.revenue.superseded` fires when a document revision invalidates a basis. What was already
  recognized stands; the schedule stops. A silent stop would look identical to a stalled worker.

---

## Why identities are derived rather than minted

* a **schedule** is derived from its **basis digest**, which covers every input that can change the
  answer — document, revision, snapshot content, amount, service window, method, policy version,
  remainder rule, granularity. Two workers scheduling the same recognition compute the same id and
  one loses a primary-key race; a re-run with unchanged inputs lands on the same row and writes
  nothing. Change any input and it is a *different* schedule rather than a mutated one;
* an **entry** is derived from `(schedule, sequence)`. **This is the double-recognition defence**:
  two workers that both decide "this is period 3" collide on a primary key, and a restart mid-run
  re-derives the same ids and adds nothing;
* an **adjustment** is derived from `(org, entry, kind, request key)`, so two concurrent "reverse
  this recognition" requests under one key are one correction rather than two.

## What no repository here can do

There is no `update` on `RecognitionPolicyRepository`, no `delete` anywhere, and no method that
amends a recognized entry. `RevenueScheduleRepository.advance` takes the version the caller read and
is a compare-and-set: a worker that decided from a stale view fails loudly rather than recognizing
revenue against a basis that has since been superseded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from .commercial_revenue import (
    AdjustmentKind,
    EntryState,
    PeriodGranularity,
    RecognitionActivity,
    RecognitionAdjustment,
    RecognitionBasis,
    RecognitionEntry,
    RecognitionMethod,
    RecognitionPolicy,
    RemainderRule,
    RevenueSchedule,
    ScheduleState,
    derive_adjustment_id,
    derive_entry_id,
    derive_recognition_policy_version_id,
)
from .domain import BillingPeriod
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .ledger import RecognitionState
from .money import Money
from .revenue_rules import RecognitionScheduler, RecognitionSlice, RecognitionValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_REVENUE_ACTIVITY = "rvl"

#: Which `billing.*` fact each schedule state publishes on entry. A table so "exactly one event per
#: business fact" is checkable by iteration rather than asserted in prose.
#:
#: `PARTIALLY_RECOGNIZED` is absent from this table and published per *entry* instead
#: (`REVENUE_RECOGNIZED`), because the news is "this period was earned", not "the schedule is still
#: in progress" — which would fire on every period and say nothing.
SCHEDULE_EVENTS: dict[ScheduleState, BillingEventType] = {
    ScheduleState.SCHEDULED: BillingEventType.REVENUE_SCHEDULED,
    ScheduleState.FULLY_RECOGNIZED: BillingEventType.REVENUE_SCHEDULE_COMPLETED,
    ScheduleState.SUPERSEDED: BillingEventType.REVENUE_SCHEDULE_SUPERSEDED,
    ScheduleState.CANCELLED: BillingEventType.REVENUE_SCHEDULE_CANCELLED,
}


@dataclass(frozen=True, slots=True)
class RevenueFactory:
    """Constructs every Recognition aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: RecognitionValidator = RecognitionValidator()
    scheduler: RecognitionScheduler = RecognitionScheduler()

    # ── policy ──────────────────────────────────────────────────────────────────

    def policy(
        self,
        *,
        org_id: str,
        code: str,
        version: int,
        method: RecognitionMethod = RecognitionMethod.STRAIGHT_LINE,
        granularity: PeriodGranularity = PeriodGranularity.MONTH,
        remainder: RemainderRule = RemainderRule.LARGEST_REMAINDER,
        description: str = "",
        actor_id: str = "",
    ) -> RecognitionPolicy:
        """Publish one immutable policy version.

        There is no `update_policy`. Changing a recognition strategy means publishing version
        *n+1*; a schedule built under *n* keeps naming *n* **inside its basis digest**, which is
        what makes replaying a March recognition after an April policy change land on the same
        answer rather than a restated one."""
        now = self.clock.now()
        built = RecognitionPolicy(
            id=derive_recognition_policy_version_id(org_id, code, version),
            org_id=org_id,
            code=code,
            version=version,
            method=method,
            granularity=granularity,
            remainder=remainder,
            description=description,
            declared_at=now,
            declared_by=actor_id,
        )
        self._fail_fast(self.validator.validate_policy(built))
        self._emit(
            BillingEventType.RECOGNITION_POLICY_PUBLISHED,
            org_id=org_id,
            subject_id=built.id,
            actor_id=actor_id,
            data={"code": code, "version": str(version), "method": method.value},
        )
        return built

    # ── the basis ───────────────────────────────────────────────────────────────

    def basis(
        self,
        *,
        org_id: str,
        account_id: str,
        document_id: str,
        revision: int,
        snapshot_digest: str,
        amount: Money,
        service_period: BillingPeriod,
        policy: RecognitionPolicy,
        method: RecognitionMethod | None = None,
        milestones: tuple[tuple[float, int], ...] = (),
        source_line_id: str = "",
        subscription_id: str = "",
    ) -> RecognitionBasis:
        """Pin the immutable evidence. **Every field is copied from a source that cannot change.**

        The amount arrives from the caller having read an issued `InvoiceSnapshot`; nothing here
        recomputes it, and nothing here asks Pricing, Rating or Tax anything. The policy's method
        may be overridden per basis — a one-off point-in-time line under a straight-line default —
        and the override is part of the digest, so it cannot be applied retroactively."""
        return RecognitionBasis(
            org_id=org_id,
            account_id=account_id,
            document_id=document_id,
            revision=revision,
            snapshot_digest=snapshot_digest,
            amount=amount,
            service_period=service_period,
            method=method or policy.method,
            granularity=policy.granularity,
            remainder=policy.remainder,
            policy_id=policy.id,
            policy_code=policy.code,
            policy_version=policy.version,
            milestones=milestones,
            source_line_id=source_line_id,
            subscription_id=subscription_id,
        )

    # ── the schedule ────────────────────────────────────────────────────────────

    def schedule(
        self, basis: RecognitionBasis, *, at: float = 0.0, actor_id: str = ""
    ) -> tuple[RevenueSchedule, tuple[RecognitionSlice, ...]]:
        """Build a schedule and its slices. **Conservation is checked here, at construction.**

        Both validations run before anything exists: the basis must be pinned (a snapshot digest
        and a policy version), and the slices must sum to the basis amount exactly. A future
        allocator change that lost a paisa fails here rather than in a quarterly report."""
        self._fail_fast(self.validator.validate_basis(basis))
        slices = self.scheduler.build(basis, at=at)
        self._fail_fast(self.validator.validate_conservation(basis, slices))
        now = at or self.clock.now()
        built = RevenueSchedule(
            id=basis.schedule_id,
            org_id=basis.org_id,
            account_id=basis.account_id,
            basis=basis,
            state=ScheduleState.DRAFT,
            entries_planned=len(slices),
            entries_recognized=0,
            version=0,
            created_at=now,
            created_by=actor_id,
            updated_at=now,
        )
        return built, slices

    def entry(
        self, schedule: RevenueSchedule, slice_: RecognitionSlice
    ) -> RecognitionEntry:
        """One planned slice as a durable row. Starts `DEFERRED` — nothing is earned yet."""
        return RecognitionEntry(
            id=derive_entry_id(schedule.id, slice_.sequence),
            org_id=schedule.org_id,
            schedule_id=schedule.id,
            account_id=schedule.account_id,
            sequence=slice_.sequence,
            period=slice_.period,
            amount=slice_.amount,
            recognize_on=slice_.recognize_on,
            state=EntryState.PENDING,
            recognition=RecognitionState.DEFERRED,
            note=slice_.label,
        )

    def emit_schedule_state(
        self, schedule: RevenueSchedule, *, actor_id: str = "", reason: str = ""
    ) -> None:
        event_type = SCHEDULE_EVENTS.get(schedule.state)
        if event_type is None:
            return
        self._emit(
            event_type,
            org_id=schedule.org_id,
            subject_id=schedule.id,
            actor_id=actor_id,
            data={
                "state": schedule.state.value,
                "document_id": schedule.basis.document_id,
                "revision": str(schedule.basis.revision),
                "policy": f"{schedule.basis.policy_code}@{schedule.basis.policy_version}",
                "reason": reason or schedule.closed_reason,
            },
        )

    def emit_recognized(self, entry: RecognitionEntry, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.REVENUE_RECOGNIZED,
            org_id=entry.org_id,
            subject_id=entry.id,
            actor_id=actor_id,
            data={
                "schedule_id": entry.schedule_id,
                "sequence": str(entry.sequence),
                "amount_minor": str(entry.amount.minor),
                "currency": entry.amount.currency.value,
                "ledger_entry_id": entry.ledger_entry_id,
            },
        )

    def emit_unposted(self, entry: RecognitionEntry, *, reason: str, actor_id: str = "") -> None:
        """**The refusal that must never be silent.**

        Revenue was recognized and the Ledger did not accept the fact — normally because no
        accounting period is open. Published so an operator sees a repair queue rather than a
        quietly divergent set of books."""
        self._emit(
            BillingEventType.REVENUE_UNPOSTED,
            org_id=entry.org_id,
            subject_id=entry.id,
            actor_id=actor_id,
            data={
                "schedule_id": entry.schedule_id,
                "amount_minor": str(entry.amount.minor),
                "reason": reason,
            },
        )

    def supersede(
        self, schedule: RevenueSchedule, *, successor_id: str, at: float
    ) -> RevenueSchedule:
        """Point a stopped schedule at the one that replaced it. Written with the transition."""
        return replace(schedule, superseded_by=successor_id, updated_at=at)

    # ── corrections ─────────────────────────────────────────────────────────────

    def adjustment(
        self,
        schedule: RevenueSchedule,
        entry: RecognitionEntry,
        *,
        kind: AdjustmentKind,
        amount: Money,
        reason: str,
        request_key: str,
        at: float,
        actor_id: str = "",
    ) -> RecognitionAdjustment:
        """**A correction as a new fact.** The original entry is never edited.

        Carries the full provenance the mandate requires: which recognition, why, from which
        document and revision, under which policy version, effective when, and for how much."""
        built = RecognitionAdjustment(
            id=derive_adjustment_id(schedule.org_id, entry.id, kind.value, request_key),
            org_id=schedule.org_id,
            account_id=schedule.account_id,
            schedule_id=schedule.id,
            entry_id=entry.id,
            kind=kind,
            amount=amount,
            reason=reason,
            document_id=schedule.basis.document_id,
            revision=schedule.basis.revision,
            policy_code=schedule.basis.policy_code,
            policy_version=schedule.basis.policy_version,
            effective_at=at,
            created_at=at,
            created_by=actor_id,
            request_key=request_key,
        )
        self._emit(
            BillingEventType.REVENUE_ADJUSTED,
            org_id=schedule.org_id,
            subject_id=built.id,
            actor_id=actor_id,
            data={
                "schedule_id": schedule.id,
                "entry_id": entry.id,
                "kind": kind.value,
                "amount_minor": str(amount.minor),
                "reason": reason,
            },
        )
        return built

    def reversal_amount(self, entry: RecognitionEntry) -> Money:
        """A reversal is the exact negative of what was recognized. No arithmetic choice here."""
        return Money(-entry.amount.minor, entry.amount.currency)

    # ── audit ───────────────────────────────────────────────────────────────────

    def activity(
        self,
        schedule: RevenueSchedule,
        *,
        kind: str,
        summary: str = "",
        actor_id: str = "",
        entry_id: str = "",
        detail: dict[str, str] | None = None,
        at: float = 0.0,
    ) -> RecognitionActivity:
        return RecognitionActivity(
            id=self.ids.new(PREFIX_REVENUE_ACTIVITY),
            org_id=schedule.org_id,
            schedule_id=schedule.id,
            at=at or self.clock.now(),
            kind=kind,
            summary=summary,
            actor_id=actor_id,
            entry_id=entry_id,
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


class RecognitionPolicyRepository(ABC):
    """Immutable policy versions. **No `update`, by design.**"""

    @abstractmethod
    def add(self, policy: RecognitionPolicy) -> tuple[RecognitionPolicy, bool]: ...

    @abstractmethod
    def get(self, org_id: str, policy_id: str) -> RecognitionPolicy | None: ...

    @abstractmethod
    def latest(self, org_id: str, code: str) -> RecognitionPolicy | None:
        """The highest active version of `code`. What a **new** basis binds to — and the only
        place `latest` is ever consulted, because a *historical* schedule reads the version pinned
        in its own basis."""

    @abstractmethod
    def versions(self, org_id: str, code: str) -> tuple[RecognitionPolicy, ...]: ...

    @abstractmethod
    def all(self, org_id: str, include_inactive: bool = False) -> tuple[RecognitionPolicy, ...]: ...

    @abstractmethod
    def deactivate(self, policy: RecognitionPolicy) -> RecognitionPolicy:
        """Withdraw from *future* binding. The row stays readable forever; a schedule already
        bound to it is unaffected, because its recognition must still replay."""


class RevenueScheduleRepository(ABC):
    """Schedules, arbitrated by the database.

    `create` is an insert on an id derived from the basis digest, so two workers scheduling the
    same recognition produce one schedule. `advance` is a compare-and-set on `version`, so a worker
    holding a stale read cannot recognize against a basis that has since been superseded."""

    @abstractmethod
    def create(self, schedule: RevenueSchedule) -> tuple[RevenueSchedule, bool]:
        """Insert if absent. Returns `(schedule, is_new)`; the stored one when it already exists."""

    @abstractmethod
    def advance(self, schedule: RevenueSchedule, *, expected_version: int) -> RevenueSchedule:
        """Conditional update. Raises `ScheduleStale` when the stored version has moved."""

    @abstractmethod
    def get(self, org_id: str, schedule_id: str) -> RevenueSchedule | None: ...

    @abstractmethod
    def for_document(self, org_id: str, document_id: str) -> tuple[RevenueSchedule, ...]:
        """Every schedule derived from one document, across revisions. The supersession trail."""

    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[RevenueSchedule, ...]: ...

    @abstractmethod
    def by_state(
        self, org_id: str, states: tuple[str, ...], limit: int = 200
    ) -> tuple[RevenueSchedule, ...]: ...

    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[RevenueSchedule, ...]: ...


class RecognitionEntryRepository(ABC):
    """Period slices. Append-only apart from the narrow lifecycle columns."""

    @abstractmethod
    def append(self, entry: RecognitionEntry) -> tuple[RecognitionEntry, bool]:
        """Insert on the derived id. **The double-recognition defence**: a duplicate returns the
        stored entry rather than a second one."""

    @abstractmethod
    def recognize(self, entry: RecognitionEntry, *, expected_state: str) -> RecognitionEntry:
        """Conditional state move. Two workers recognizing one period: the database picks one, and
        the loser raises `ScheduleStale` before any ledger posting is attempted."""

    @abstractmethod
    def settle(self, entry: RecognitionEntry) -> RecognitionEntry:
        """Record a posting outcome against an already-recognized entry."""

    @abstractmethod
    def get(self, org_id: str, entry_id: str) -> RecognitionEntry | None: ...

    @abstractmethod
    def for_schedule(self, org_id: str, schedule_id: str) -> tuple[RecognitionEntry, ...]: ...

    @abstractmethod
    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[RecognitionEntry, ...]:
        """Slices that have matured. **The read a scheduler outside this package calls.**"""

    @abstractmethod
    def unposted(self, org_id: str, limit: int = 200) -> tuple[RecognitionEntry, ...]:
        """Recognized but not in the Ledger — the repair queue."""


class RecognitionAdjustmentRepository(ABC):
    """Corrections. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(self, adjustment: RecognitionAdjustment) -> tuple[RecognitionAdjustment, bool]: ...

    @abstractmethod
    def get(self, org_id: str, adjustment_id: str) -> RecognitionAdjustment | None: ...

    @abstractmethod
    def for_entry(self, org_id: str, entry_id: str) -> tuple[RecognitionAdjustment, ...]: ...

    @abstractmethod
    def for_schedule(self, org_id: str, schedule_id: str) -> tuple[RecognitionAdjustment, ...]: ...


class RecognitionActivityRepository(ABC):
    """The audit trail an accountant reads."""

    @abstractmethod
    def append(self, activity: RecognitionActivity) -> RecognitionActivity: ...

    @abstractmethod
    def for_schedule(
        self, org_id: str, schedule_id: str, limit: int = 200
    ) -> tuple[RecognitionActivity, ...]: ...


__all__ = [
    "PREFIX_REVENUE_ACTIVITY",
    "SCHEDULE_EVENTS",
    "RecognitionActivityRepository",
    "RecognitionAdjustmentRepository",
    "RecognitionEntryRepository",
    "RecognitionPolicyRepository",
    "RecognitionPolicyRepository",
    "RevenueFactory",
    "RevenueScheduleRepository",
]
