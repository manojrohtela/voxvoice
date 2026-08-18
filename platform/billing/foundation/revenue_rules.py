"""Revenue recognition rules — the scheduler, the allocator and the guards (M9.18).

Pure. No clock, no database, no provider, no scheduler thread, no transport, and **no payment**.
Every rule takes the instant it reasons about as an argument, which is what makes a recognition
taken in March reproducible in April by replaying the same instant against the same immutable basis.

## Four things this module refuses to do

**It does not compute what was billed.** The amount arrives on the basis, copied from an issued
`InvoiceSnapshot`. Nothing here prices, rates, totals, discounts or taxes — and there is no import
that could reach any of those.

**It does not record a financial fact.** It produces a *decision*: this much, in this period, on
this date. The Application hands that to M9.13's own `PostJournalEntry`. There is no ledger import
here, no balance, and no second bookkeeping system (ADR-0023 RS6).

**It does not know whether anyone paid.** Cash and revenue are different questions. There is no
payment import, no collection import, and no branch anywhere that reads either.

**It does not schedule itself.** `due_entries(entries, at)` answers *what has matured given this
instant* — a pure filter over values the caller supplies. There is no loop, no sleep, no thread.

## Conservation is structural, not careful

Every split goes through `Money.allocate`, which is M9.2's largest-remainder allocator: the sum of
the parts always equals the original to the last minor unit. This module does not divide money
anywhere else, does not use floating-point arithmetic on money at all, and does not implement a
second allocator — so "no paisa appears or disappears" is a property of the one function every path
goes through, and `test_conservation_holds_for_every_schedule_shape` asserts it by iteration rather
than by example.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_revenue import (
    REVENUE_ENTRY_LIFECYCLE,
    REVENUE_SCHEDULE_LIFECYCLE,
    AdjustmentKind,
    EntryState,
    EntryTrigger,
    PeriodGranularity,
    RecognitionAdjustment,
    RecognitionBasis,
    RecognitionEntry,
    RecognitionExplanation,
    RecognitionInspection,
    RecognitionMethod,
    RecognitionPolicy,
    RecognitionPosition,
    RemainderRule,
    RevenueSchedule,
    RevenueSearchQuery,
    RevenueSearchResult,
    ScheduleState,
    ScheduleTrigger,
    period_boundaries,
    period_key,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .domain import BillingPeriod
from .errors import BillingConflict, InvalidBillingTransition
from .ledger import RecognitionState
from .money import Money


class ScheduleStale(BillingConflict):
    """A worker tried to transition a schedule from a version that is no longer current.

    Raised by persistence when the compare-and-set finds no row, and declared here — in Foundation,
    beside the domain it protects — because the *Application* must react to it and may not import
    persistence. Same placement and same reason as `RefundSequenceConflict` (M9.16-R1) and
    `CaseStale` (M9.17).

    The scenario it exists for: worker A reads a schedule as `SCHEDULED`, worker B supersedes it
    because the document was revised, and worker A comes back to recognize a period against a basis
    that no longer describes anything."""


# ── the slice: one period, one amount ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecognitionSlice:
    """One planned piece of a schedule, before it is a row. A value, not a command."""

    sequence: int
    period: BillingPeriod
    amount: Money
    recognize_on: float
    weight: int
    label: str = ""


class RecognitionScheduler:
    """**The one place a billed amount is cut into dated pieces.**

    Deterministic: same basis, same policy version, same instant, same slices — byte-identical, in
    March and in April, before and after the policy is rewritten. That property is what the basis
    digest exists to preserve and what `test_the_same_basis_always_produces_the_same_schedule`
    asserts.

    All four methods reduce to the same two steps — *choose the periods*, then *weight them* — and
    then hand the weights to `Money.allocate`. That is deliberate: one allocator, one remainder
    rule, one conservation guarantee, and no method-specific arithmetic that could drift."""

    def build(
        self, basis: RecognitionBasis, *, at: float = 0.0
    ) -> tuple[RecognitionSlice, ...]:
        if basis.amount.is_zero:
            # A zero-value source is scheduled as nothing rather than as one zero slice. A zero
            # ledger posting is noise in a financial report, and an empty schedule says the same
            # thing more honestly.
            return ()
        method = basis.method
        if method is RecognitionMethod.POINT_IN_TIME:
            periods, weights = (basis.service_period,), (1,)
            dates = (basis.service_period.start,)
        elif method is RecognitionMethod.SCHEDULED:
            periods, weights, dates = self._milestones(basis)
        else:
            periods = period_boundaries(basis.service_period, basis.granularity)
            dates = tuple(p.start for p in periods)
            weights = (
                tuple(1 for _ in periods)
                if method is RecognitionMethod.STRAIGHT_LINE
                # **Proration is weighted by seconds in each clipped period.** That single choice
                # makes partial first and last periods, 28/29/30/31-day months and leap years all
                # correct with no special case and no "days in month" table.
                else tuple(max(1, int(round(p.duration))) for p in periods)
            )
        if not periods:
            return ()
        amounts = self._allocate(basis.amount, weights, basis.remainder)
        return tuple(
            RecognitionSlice(
                sequence=index + 1,
                period=period,
                amount=amount,
                recognize_on=date,
                weight=weight,
                label=period_key(period),
            )
            for index, (period, amount, date, weight) in enumerate(
                zip(periods, amounts, dates, weights, strict=True)
            )
        )

    @staticmethod
    def _milestones(basis: RecognitionBasis):
        """Explicit dated weights. Still *allocated*, so conservation is structural here
        too rather than the caller's problem."""
        ordered = sorted(basis.milestones, key=lambda m: (m[0], m[1]))
        if not ordered:
            return (), (), ()
        periods = tuple(
            BillingPeriod(start=date, end=max(date + 1.0, basis.service_period.end))
            for date, _ in ordered
        )
        return periods, tuple(max(1, weight) for _, weight in ordered), tuple(d for d, _ in ordered)

    @staticmethod
    def _allocate(
        total: Money, weights: tuple[int, ...], remainder: RemainderRule
    ) -> tuple[Money, ...]:
        """**Every division of money in this milestone goes through here.**

        `LARGEST_REMAINDER` is `Money.allocate` — M9.2's, reused rather than re-implemented, stable
        across process restart, worker ordering and database ordering because it sorts by
        descending fractional remainder and then by index.

        `FIRST_PERIOD` and `LAST_PERIOD` are the two policies an accountant may prefer instead, and
        they are built *on top of* the same allocator: floor every share, then hand the whole
        remainder to one end. They cannot lose a unit, because the remainder is computed as a
        difference from the original rather than accumulated."""
        if remainder is RemainderRule.LARGEST_REMAINDER:
            return total.allocate(weights)
        divisor = sum(weights)
        base = [total.minor * w // divisor for w in weights]
        leftover = total.minor - sum(base)
        target = 0 if remainder is RemainderRule.FIRST_PERIOD else len(base) - 1
        base[target] += leftover
        return tuple(Money(m, total.currency) for m in base)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class ScheduleLifecycleService:
    """Applies schedule triggers through the shared FSM, and bumps the concurrency token."""

    def __init__(self, definition: StateMachineDefinition = REVENUE_SCHEDULE_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: ScheduleState, trigger: ScheduleTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: ScheduleState, trigger: ScheduleTrigger) -> ScheduleState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a revenue schedule cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: ScheduleState) -> tuple[ScheduleTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        schedule: RevenueSchedule,
        trigger: ScheduleTrigger,
        *,
        at: float,
        reason: str = "",
    ) -> RevenueSchedule:
        target = self.next_state(schedule.state, trigger)
        changes: dict = {"state": target, "updated_at": at, "version": schedule.version + 1}
        if target in (
            ScheduleState.FULLY_RECOGNIZED,
            ScheduleState.CANCELLED,
            ScheduleState.SUPERSEDED,
        ):
            changes["closed_at"] = at
            changes["closed_reason"] = reason or trigger.value
        return replace(schedule, **changes)


class EntryLifecycleService:
    """Applies entry triggers. The machine's refusals are the specification."""

    def __init__(self, definition: StateMachineDefinition = REVENUE_ENTRY_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: EntryState, trigger: EntryTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: EntryState, trigger: EntryTrigger) -> EntryState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a recognition entry cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: EntryState) -> tuple[EntryTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        entry: RecognitionEntry,
        trigger: EntryTrigger,
        *,
        at: float,
        ledger_entry_id: str = "",
        period_key_value: str = "",
        disposition: str = "",
        note: str = "",
    ) -> RecognitionEntry:
        target = self.next_state(entry.state, trigger)
        changes: dict = {"state": target}
        if target is EntryState.RECOGNIZED:
            changes["recognized_at"] = at
            changes["recognition"] = RecognitionState.RECOGNIZED
        if target is EntryState.POSTED:
            changes["posted_at"] = at
            changes["recognition"] = RecognitionState.RECOGNIZED
        if ledger_entry_id:
            changes["ledger_entry_id"] = ledger_entry_id
        if period_key_value:
            changes["ledger_period_key"] = period_key_value
        if disposition:
            changes["ledger_disposition"] = disposition
        if note:
            changes["note"] = note
        return replace(entry, **changes)


class RecognitionGuardService:
    """The refusals that are not expressible as FSM edges."""

    @staticmethod
    def guard_not_earned(schedule: RevenueSchedule) -> None:
        """A schedule that has earned something cannot be cancelled into non-existence.

        The way back from a recognition is a **reversal** — a new financial fact with provenance to
        the original — never an erasure. Cancelling here would delete revenue that a period close
        may already have sealed and a statement may already have reported."""
        if schedule.has_earned:
            raise BillingConflict(
                f"revenue schedule {schedule.id!r} has already recognized revenue; correct it "
                "with a reversal, which is a new financial fact, rather than cancelling it"
            )

    @staticmethod
    def guard_recognizable(entry: RecognitionEntry, *, at: float) -> None:
        if entry.is_closed:
            raise BillingConflict(
                f"recognition entry {entry.id!r} is already {entry.state.value}"
            )
        if entry.is_recognized:
            raise BillingConflict(
                f"recognition entry {entry.id!r} was already recognized at {entry.recognized_at}; "
                "the same amount is never recognized twice"
            )
        if not entry.due_at(at):
            raise BillingConflict(
                f"recognition entry {entry.id!r} does not mature until {entry.recognize_on}"
            )

    @staticmethod
    def guard_schedulable(schedule: RevenueSchedule) -> None:
        if schedule.is_terminal:
            raise BillingConflict(
                f"revenue schedule {schedule.id!r} is {schedule.state.value} and accepts no "
                "further recognition"
            )

    @staticmethod
    def guard_reversible(
        entry: RecognitionEntry, existing: Sequence[RecognitionAdjustment]
    ) -> None:
        """One reversal per recognition. A second would double the correction.

        The already-reversed check runs **first**. A reversed entry is still a *recognized* one —
        that is what `RECOGNIZED_ENTRY_STATES` says and why the position stays conserved — so
        asking "is this recognizable?" first would refuse it with a message about the wrong
        problem, and an operator would go looking for a recognition that is sitting right there."""
        if entry.state is EntryState.REVERSED or any(
            a.kind is AdjustmentKind.REVERSAL for a in existing
        ):
            raise BillingConflict(
                f"recognition entry {entry.id!r} has already been reversed"
            )
        if not entry.is_recognized:
            raise BillingConflict(
                f"recognition entry {entry.id!r} is {entry.state.value}; only a recognized amount "
                "can be reversed"
            )


# ── derived reads ───────────────────────────────────────────────────────────────


class RecognitionPositionService:
    """Computes what a schedule has earned. **From the facts, every time.**

    Nothing is cached and nothing is stored. `deferred` is `total - recognized - adjusted`, and it
    is the only definition of deferred revenue in this codebase."""

    def position(
        self,
        schedule: RevenueSchedule,
        entries: Sequence[RecognitionEntry],
        adjustments: Sequence[RecognitionAdjustment] = (),
    ) -> RecognitionPosition:
        currency = schedule.currency
        zero = Money.zero(currency)
        recognized = zero
        for entry in entries:
            if entry.is_recognized:
                recognized = recognized + entry.amount
        adjusted = zero
        for adjustment in adjustments:
            adjusted = adjusted + adjustment.amount
        total = schedule.basis.amount
        deferred = Money(total.minor - recognized.minor - adjusted.minor, currency)
        return RecognitionPosition(
            schedule_id=schedule.id,
            currency=currency,
            total=total,
            recognized=recognized,
            adjusted=adjusted,
            deferred=deferred,
            entries=len(entries),
            recognized_entries=sum(1 for e in entries if e.is_recognized),
            posted_entries=sum(1 for e in entries if e.state is EntryState.POSTED),
            unposted_entries=sum(1 for e in entries if e.state is EntryState.UNPOSTED),
            reversed_entries=sum(1 for e in entries if e.state is EntryState.REVERSED),
        )


class RecognitionScheduleService:
    """Answers *what has matured*. It does not make anything happen.

    The whole of the scheduling surface in the domain, and a pure filter over values the caller
    passed in. Whoever calls it on a timer is outside the Foundation, as it has been since M9.9."""

    @staticmethod
    def due_entries(
        entries: Iterable[RecognitionEntry], *, at: float
    ) -> tuple[RecognitionEntry, ...]:
        due = (e for e in entries if e.state is EntryState.PENDING and e.due_at(at))
        return tuple(sorted(due, key=lambda e: (e.recognize_on, e.sequence)))

    @staticmethod
    def unposted(entries: Iterable[RecognitionEntry]) -> tuple[RecognitionEntry, ...]:
        """Recognized but not in the Ledger — the repair queue."""
        return tuple(e for e in entries if e.state is EntryState.UNPOSTED)


class RecognitionExplainer:
    """Turns a schedule and its position into a sentence and a path.

    Assembled entirely from the **basis** and the entries. It reaches no price book, no tax rate, no
    subscription and no payment — which is what makes it still answerable in five years."""

    def explain(
        self, schedule: RevenueSchedule, position: RecognitionPosition
    ) -> RecognitionExplanation:
        basis = schedule.basis
        path = [
            f"document={basis.document_id}@r{basis.revision}",
            f"snapshot={basis.snapshot_digest[:16]}",
            f"policy={basis.policy_code}@{basis.policy_version}",
            f"method={basis.method.value}",
            f"granularity={basis.granularity.value}",
            f"remainder={basis.remainder.value}",
            f"service=[{basis.service_period.start},{basis.service_period.end})",
            f"planned={schedule.entries_planned}",
            f"recognized={position.recognized_entries}",
        ]
        return RecognitionExplanation(
            schedule_id=schedule.id,
            state=schedule.state,
            document_id=basis.document_id,
            revision=basis.revision,
            snapshot_digest=basis.snapshot_digest,
            basis_digest=basis.digest,
            policy_code=basis.policy_code,
            policy_version=basis.policy_version,
            method=basis.method,
            granularity=basis.granularity,
            remainder=basis.remainder,
            service_start=basis.service_period.start,
            service_end=basis.service_period.end,
            total_minor=position.total.minor,
            recognized_minor=position.recognized.minor,
            deferred_minor=position.deferred.minor,
            currency=position.currency.value,
            reason=_reason(schedule, position),
            path=tuple(path),
        )


def _reason(schedule: RevenueSchedule, position: RecognitionPosition) -> str:
    if schedule.state is ScheduleState.FULLY_RECOGNIZED:
        return "every scheduled period has been recognized"
    if schedule.state is ScheduleState.SUPERSEDED:
        return (
            "the source document was revised; what was already recognized stands and the "
            "remainder moved to a new schedule"
        )
    if schedule.state is ScheduleState.CANCELLED:
        return "the source document was cancelled before any revenue was recognized"
    if position.has_unposted:
        return "revenue was recognized but the Ledger has not accepted the posting yet"
    return (
        f"{position.recognized_entries} of {schedule.entries_planned} periods recognized; "
        f"the remainder is deferred"
    )


class RecognitionInspector:
    """Counts, for the operator screen. Reads only what it is handed."""

    def inspect(
        self,
        schedule: RevenueSchedule,
        position: RecognitionPosition,
        *,
        entries: Sequence[RecognitionEntry] = (),
        adjustments: Sequence[RecognitionAdjustment] = (),
        at: float = 0.0,
    ) -> RecognitionInspection:
        pending = [e for e in entries if e.state is EntryState.PENDING]
        return RecognitionInspection(
            schedule=schedule,
            position=position,
            adjustments=len(adjustments),
            due_now=sum(1 for e in pending if e.due_at(at)),
            next_recognize_on=min((e.recognize_on for e in pending), default=None),
        )


class RecognitionValidator:
    """Structural validation. Findings, never exceptions — the factory decides what is fatal."""

    def validate_basis(self, basis: RecognitionBasis) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not basis.document_id:
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.ERROR,
                    message="a recognition basis must name the source document it derives from",
                )
            )
        if not basis.snapshot_digest:
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.UNPINNED_RECOGNITION_SOURCE,
                    severity=Severity.ERROR,
                    message=(
                        "a recognition basis must pin the issued snapshot digest; without it the "
                        "schedule cannot be reproduced after the document is revised"
                    ),
                )
            )
        if basis.amount.is_negative:
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.NEGATIVE_AMOUNT,
                    severity=Severity.ERROR,
                    message="a recognition basis amount must not be negative; a reduction is an "
                    "adjustment against an existing schedule",
                )
            )
        if basis.policy_version < 1:
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.UNPINNED_RECOGNITION_POLICY,
                    severity=Severity.ERROR,
                    message=(
                        "a recognition basis must pin a policy version; reading the current policy "
                        "at recognition time would let an edit restate history"
                    ),
                )
            )
        if basis.method is RecognitionMethod.SCHEDULED and not basis.milestones:
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.EMPTY_RECOGNITION_SCHEDULE,
                    severity=Severity.ERROR,
                    message="milestone recognition needs at least one dated weight",
                )
            )
        if (
            basis.method in (RecognitionMethod.STRAIGHT_LINE, RecognitionMethod.PRORATED)
            and basis.granularity is PeriodGranularity.NONE
        ):
            found.append(
                Diagnostic(
                    path=basis.schedule_id,
                    code=BillingDiagnosticCode.EMPTY_RECOGNITION_SCHEDULE,
                    severity=Severity.ERROR,
                    message=f"{basis.method.value} recognition needs a calendar granularity",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_conservation(
        self, basis: RecognitionBasis, slices: Sequence[RecognitionSlice]
    ) -> BillingDiagnostics:
        """**The last line of defence on the money.**

        Runs on every schedule the factory builds, so a future allocator change that lost a paisa
        would fail at construction rather than in a quarterly report."""
        if not slices:
            return BillingDiagnostics.empty()
        total = sum(s.amount.minor for s in slices)
        if total != basis.amount.minor:
            return BillingDiagnostics.of(
                [
                    Diagnostic(
                        path=basis.schedule_id,
                        code=BillingDiagnosticCode.RECOGNITION_NOT_CONSERVED,
                        severity=Severity.ERROR,
                        message=(
                            f"the schedule allocates {total} minor units but the basis states "
                            f"{basis.amount.minor}; revenue must be conserved exactly"
                        ),
                    )
                ]
            )
        return BillingDiagnostics.empty()

    def validate_policy(self, policy: RecognitionPolicy) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not policy.code:
            found.append(
                Diagnostic(
                    path=policy.id,
                    code=BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.ERROR,
                    message="a recognition policy needs a stable code",
                )
            )
        if (
            policy.method in (RecognitionMethod.STRAIGHT_LINE, RecognitionMethod.PRORATED)
            and policy.granularity is PeriodGranularity.NONE
        ):
            found.append(
                Diagnostic(
                    path=policy.id,
                    code=BillingDiagnosticCode.EMPTY_RECOGNITION_SCHEDULE,
                    severity=Severity.ERROR,
                    message=f"{policy.method.value} recognition needs a calendar granularity",
                )
            )
        return BillingDiagnostics.of(found)


class RevenueSearchService:
    """Filters schedules the repository already narrowed. Bounded, and honest about truncation."""

    def search(
        self, query: RevenueSearchQuery, schedules: Sequence[RevenueSchedule]
    ) -> RevenueSearchResult:
        matched = [s for s in schedules if self._matches(query, s)]
        limit = max(1, query.limit)
        return RevenueSearchResult(
            schedules=tuple(matched[:limit]),
            total=len(matched),
            truncated=len(matched) > limit,
        )

    @staticmethod
    def _matches(query: RevenueSearchQuery, schedule: RevenueSchedule) -> bool:
        if query.account_id and schedule.account_id != query.account_id:
            return False
        if query.document_id and schedule.basis.document_id != query.document_id:
            return False
        if query.states and schedule.state not in query.states:
            return False
        return True


__all__ = [
    "EntryLifecycleService",
    "RecognitionExplainer",
    "RecognitionGuardService",
    "RecognitionInspector",
    "RecognitionPositionService",
    "RecognitionScheduleService",
    "RecognitionScheduler",
    "RecognitionSlice",
    "RecognitionValidator",
    "RevenueSearchService",
    "ScheduleLifecycleService",
    "ScheduleStale",
]
