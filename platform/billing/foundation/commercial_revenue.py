"""Revenue Recognition & Deferred Revenue — the recognition **decision** domain (M9.18).

M9.2 declared `RecognitionState` — `DEFERRED` / `RECOGNIZED` / `NOT_APPLICABLE` — as a *posture tag
on a ledger entry*, and ADR-0023 **RS6** ratified what it means:

> Revenue-recognition state is a **projection over the ledger owned by Billing** — not a second
> bookkeeping system, and not something an external accounting system writes back into.

That tag is correct and this milestone **reuses it unchanged**. What it cannot express is the
question M9.18 exists to answer:

* it says *this entry is deferred* but not **when it stops being deferred**, on what basis, or in
  how many pieces;
* it has no schedule, so "₹120,000 billed for a twelve-month term" is one tag rather than twelve
  dated decisions;
* it has no immutable **basis**, so a recognition taken in March cannot be replayed after the
  policy, the price book, or the subscription terms change in April.

So the schedule lands *beside* it, exactly as `JournalEntry` landed beside `LedgerEntry` (M9.13),
`CommercialInvoice` beside `Invoice` (M9.14), `TaxAssessment` beside `TaxDetermination` (M9.15),
`PaymentIntent` beside `Payment` (M9.16) and `CollectionCase` beside `DunningProcess` (M9.17).
Extension is addition (§A12).

---

## The word "deferred" already means something else here

M9.13's `PostingOutcome.DEFERRED` means *the accounting period was closed, so this entry landed in
the next open one*. That is **period deferral** and it has nothing to do with deferred revenue. The
two are never conflated in this package: period disposition is `LateEntryDisposition`, which is
M9.13's and is consumed rather than restated, and deferred *revenue* is never a state at all — it
is `basis.amount - recognized to date`, derived at read time and stored nowhere.

## What this domain owns, stated as a boundary

It owns **when an eligible billed amount becomes recognized revenue, and the record that it did**.

> **Invoice states what was billed. Recognition decides when it is earned. Ledger records the
> fact.**

It computes no invoice total, no tax, no price, no balance. It posts nothing itself: a recognition
becomes a financial fact only by dispatching M9.13's own `PostJournalEntry`, with the new
`PostingSource.REVENUE_RECOGNITION` and `RecognitionState.RECOGNIZED`, and the Ledger decides the
period, the entry id, and whether the posting is a duplicate.

## Paid is not earned

Nothing in this package reads a payment state, a payment attempt, a collection case, or a dunning
stage. A paid invoice may sit fully deferred for eleven months; an unpaid one may be fully
recognized on the day it was issued. Cash and revenue are different questions, and a recognition
engine that inferred one from the other would be wrong in both directions — which is why there is
no payment import here at all, asserted by test rather than by intention.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .domain import BillingPeriod
from .ledger import RecognitionState
from .money import Currency, Money

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_basis_digest(
    *,
    org_id: str,
    document_id: str,
    revision: int,
    snapshot_digest: str,
    account_id: str,
    amount_minor: int,
    currency: str,
    service_start: float,
    service_end: float,
    method: str,
    policy_code: str,
    policy_version: int,
    remainder: str,
    granularity: str,
) -> str:
    """**Every input that can change the answer, and nothing that cannot.**

    This is the reproducibility contract expressed as a hash. If the document is revised, the
    snapshot content changes, the service window moves, the method changes, the policy version
    changes, the remainder rule changes, or the granularity changes — the digest changes, and so
    does the schedule's identity. If none of them changes, re-deriving is idempotent and lands on
    the same row.

    Note what is deliberately **absent**: no timestamp, no "recognized at", no run id, no actor. A
    volatile value in here would make every re-derivation a new schedule and destroy the very
    idempotency the digest exists to provide. The instant a recognition is *taken* is a fact about
    the entry, not about the basis."""
    return _digest(
        org_id,
        document_id,
        str(revision),
        snapshot_digest,
        account_id,
        str(amount_minor),
        currency,
        f"{service_start:.6f}",
        f"{service_end:.6f}",
        method,
        policy_code,
        str(policy_version),
        remainder,
        granularity,
    )


def derive_schedule_id(org_id: str, basis_digest: str) -> str:
    """A schedule **is** its basis. Two workers deriving the same recognition compute the same id
    and one loses a primary-key race instead of both scheduling the same revenue."""
    return "rvs_" + _digest(org_id, basis_digest)[:32]


def derive_entry_id(schedule_id: str, sequence: int) -> str:
    """One period slice's identity: the schedule plus its position.

    **This is the double-recognition defence.** Two workers that both decide "this is period 3"
    compute one id and collide on a primary key; a restart mid-run re-derives the same ids and
    writes nothing new. The sequence is dense, ascending, and never reused."""
    return "rve_" + _digest(schedule_id, str(sequence))[:32]


def derive_adjustment_id(org_id: str, entry_id: str, kind: str, request_key: str) -> str:
    """A correction's identity, derived from what it corrects and the request that asked for it.
    Two concurrent "reverse this recognition" requests under one key are one adjustment."""
    return "rva_" + _digest(org_id, entry_id, kind, request_key)[:32]


def derive_recognition_policy_version_id(org_id: str, code: str, version: int) -> str:
    """A policy *version's* identity. The row is immutable once written, because a schedule built
    under version 3 must still replay after version 4 is published."""
    return "rvp_" + _digest(org_id, code, str(version))[:32]


# ── vocabulary ──────────────────────────────────────────────────────────────────


class RecognitionMethod(StrEnum):
    """How a billed amount becomes earned. Four, and no more without a documented reason.

    `SCHEDULED` is the escape hatch for milestone-based contracts, and it is deliberately *not* a
    calculation: the milestones arrive on the basis as explicit dated weights, so the engine still
    allocates rather than inventing an accounting method of its own."""

    POINT_IN_TIME = "point_in_time"  # earned when delivered; one entry
    STRAIGHT_LINE = "straight_line"  # equal across whole periods
    PRORATED = "prorated"  # weighted by actual time in each period
    SCHEDULED = "scheduled"  # explicit milestone weights supplied on the basis


class PeriodGranularity(StrEnum):
    """The calendar step a schedule is cut on. **UTC, always** — see `period_boundaries`."""

    MONTH = "month"
    DAY = "day"
    NONE = "none"  # point-in-time and milestone schedules have no calendar step


class RemainderRule(StrEnum):
    """Where the indivisible last minor unit goes.

    Declared rather than assumed, because "the remainder goes somewhere sensible" is how one paisa
    disappears between two reports. `LARGEST_REMAINDER` is `Money.allocate`'s rule — M9.2's, reused
    rather than re-implemented — which distributes by descending fractional remainder and then by
    index, so it is stable across process restart, worker ordering and database ordering."""

    LARGEST_REMAINDER = "largest_remainder"
    FIRST_PERIOD = "first_period"
    LAST_PERIOD = "last_period"


class ScheduleState(StrEnum):
    """Where a recognition schedule has got to."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PARTIALLY_RECOGNIZED = "partially_recognized"
    FULLY_RECOGNIZED = "fully_recognized"
    CANCELLED = "cancelled"  # the source document was cancelled before anything was recognized
    SUPERSEDED = "superseded"  # a revised document produced a new basis and a new schedule


class ScheduleTrigger(StrEnum):
    SCHEDULE = "schedule"
    RECOGNIZE = "recognize"
    COMPLETE = "complete"
    CANCEL = "cancel"
    SUPERSEDE = "supersede"


# The schedule lifecycle. What it **refuses** is the specification:
#
#   * **no edge out of `FULLY_RECOGNIZED`.** Not to `SCHEDULED`, not to `PARTIALLY_RECOGNIZED`.
#     Revenue that was recognized stays recognized; a change is a new adjustment fact carrying
#     provenance to the original, never a rewind. `FULLY_RECOGNIZED → SCHEDULED` is the transition
#     that would let a policy edit silently restate a filed number;
#   * **no edge out of `CANCELLED` or `SUPERSEDED`.** A superseded schedule is the record of what
#     was decided under the old document revision, and it must stay exactly that;
#   * **`PARTIALLY_RECOGNIZED` cannot be cancelled.** Once revenue has been recognized against a
#     basis, the way back is a **reversal**, which is a new financial fact with its own provenance.
#     Cancelling would erase the recognition rather than correct it;
#   * **`PARTIALLY_RECOGNIZED → SUPERSEDED` exists.** A document revised mid-term does not undo
#     what was already earned: the old schedule stops, the already-recognized entries stand, and
#     the new basis produces a new schedule for what remains.
REVENUE_SCHEDULE_LIFECYCLE: StateMachineDefinition = define(
    [
        (ScheduleState.DRAFT, ScheduleTrigger.SCHEDULE, ScheduleState.SCHEDULED),
        (ScheduleState.DRAFT, ScheduleTrigger.CANCEL, ScheduleState.CANCELLED),
        (ScheduleState.SCHEDULED, ScheduleTrigger.RECOGNIZE, ScheduleState.PARTIALLY_RECOGNIZED),
        (ScheduleState.SCHEDULED, ScheduleTrigger.COMPLETE, ScheduleState.FULLY_RECOGNIZED),
        (ScheduleState.SCHEDULED, ScheduleTrigger.CANCEL, ScheduleState.CANCELLED),
        (ScheduleState.SCHEDULED, ScheduleTrigger.SUPERSEDE, ScheduleState.SUPERSEDED),
        (
            ScheduleState.PARTIALLY_RECOGNIZED,
            ScheduleTrigger.RECOGNIZE,
            ScheduleState.PARTIALLY_RECOGNIZED,
        ),
        (
            ScheduleState.PARTIALLY_RECOGNIZED,
            ScheduleTrigger.COMPLETE,
            ScheduleState.FULLY_RECOGNIZED,
        ),
        (ScheduleState.PARTIALLY_RECOGNIZED, ScheduleTrigger.SUPERSEDE, ScheduleState.SUPERSEDED),
    ]
)

#: Schedules automation will never advance again.
TERMINAL_SCHEDULE_STATES: frozenset[ScheduleState] = frozenset(
    {ScheduleState.FULLY_RECOGNIZED, ScheduleState.CANCELLED, ScheduleState.SUPERSEDED}
)

#: Schedules that have earned something. Nothing may cancel these into non-existence.
EARNED_SCHEDULE_STATES: frozenset[ScheduleState] = frozenset(
    {ScheduleState.PARTIALLY_RECOGNIZED, ScheduleState.FULLY_RECOGNIZED}
)


class EntryState(StrEnum):
    """One period slice's state.

    `RECOGNIZED` and `POSTED` are separate on purpose, and the gap between them is where the
    M9.16 lesson lives: the Ledger can legitimately refuse a posting (no open accounting period),
    and when it does, the recognition **decision** is still a fact that must not be lost. An
    `UNPOSTED` entry names exactly what needs repairing."""

    PENDING = "pending"  # scheduled, not yet due or not yet taken
    RECOGNIZED = "recognized"  # the decision is recorded
    POSTED = "posted"  # and the Ledger accepted the financial fact
    UNPOSTED = "unposted"  # recognized, but the Ledger refused; awaiting repair
    REVERSED = "reversed"  # corrected by a later adjustment; the row itself is unchanged
    SKIPPED = "skipped"  # the schedule was superseded before this slice came due


class EntryTrigger(StrEnum):
    RECOGNIZE = "recognize"
    POST = "post"
    REFUSE = "refuse"
    REVERSE = "reverse"
    SKIP = "skip"


# The entry lifecycle. Its refusals:
#
#   * **no edge out of `REVERSED`.** A reversed recognition is not re-recognizable; a further
#     change is a further adjustment;
#   * **no `POSTED → RECOGNIZED`, and no `POSTED → PENDING`.** A financial fact that reached the
#     Ledger is the Ledger's; withdrawing it is a reversal it records, not a state this domain
#     rewinds;
#   * **`UNPOSTED → POSTED` exists.** That is the repair path, and it is the whole reason
#     `UNPOSTED` is a state rather than an exception.
REVENUE_ENTRY_LIFECYCLE: StateMachineDefinition = define(
    [
        (EntryState.PENDING, EntryTrigger.RECOGNIZE, EntryState.RECOGNIZED),
        (EntryState.PENDING, EntryTrigger.SKIP, EntryState.SKIPPED),
        (EntryState.RECOGNIZED, EntryTrigger.POST, EntryState.POSTED),
        (EntryState.RECOGNIZED, EntryTrigger.REFUSE, EntryState.UNPOSTED),
        (EntryState.RECOGNIZED, EntryTrigger.REVERSE, EntryState.REVERSED),
        (EntryState.UNPOSTED, EntryTrigger.POST, EntryState.POSTED),
        (EntryState.UNPOSTED, EntryTrigger.REVERSE, EntryState.REVERSED),
        (EntryState.POSTED, EntryTrigger.REVERSE, EntryState.REVERSED),
    ]
)

#: Entries that count toward "recognized to date" — **gross of corrections**.
#:
#: `REVERSED` is deliberately *included*, and getting this wrong is how a position double-counts a
#: correction. The revenue **was** recognized: a ledger credit exists for it, a period may have
#: closed over it, and a statement may have reported it. The reversal is a *separate* fact carrying
#: its own negative amount and its own ledger debit. Excluding the entry here as well would subtract
#: the same correction twice and report `deferred` greater than the basis — which is what the first
#: conservation test over a reversed schedule caught.
#:
#: So the position is `recognized + adjusted + deferred == total`, always, where `recognized` is
#: gross and `adjusted` carries the signed corrections. `SKIPPED` and `PENDING` are absent because
#: nothing was ever earned for them.
RECOGNIZED_ENTRY_STATES: frozenset[EntryState] = frozenset(
    {EntryState.RECOGNIZED, EntryState.POSTED, EntryState.UNPOSTED, EntryState.REVERSED}
)

#: Entries no worker should pick up again.
CLOSED_ENTRY_STATES: frozenset[EntryState] = frozenset(
    {EntryState.POSTED, EntryState.REVERSED, EntryState.SKIPPED}
)


class AdjustmentKind(StrEnum):
    """Why a recognized amount is being corrected. Always a **new fact**, never an edit."""

    REVERSAL = "reversal"  # the whole recognition was wrong
    CORRECTION = "correction"  # the amount was wrong; a signed difference
    WRITE_DOWN = "write_down"  # the revenue will not be earned after all


# ── calendar ────────────────────────────────────────────────────────────────────


SECONDS_PER_DAY = 86_400


def civil_from_days(days: int) -> tuple[int, int, int]:
    """A day number since the Unix epoch → `(year, month, day)` in the proleptic Gregorian calendar.

    **Pure integer arithmetic, no table, no leap-year branch, no `datetime`.**

    `datetime` is not importable in this layer, and deliberately so: the Foundation reads no
    ambient clock, and the architecture test enforces that by banning the module outright rather
    than by enumerating the ways it could be misused. Rather than weaken that assertion for the
    sake of a calendar, the calendar is computed here — this is Howard Hinnant's well-known
    `civil_from_days`, which is exact for every date in range and is *proven against the stdlib*
    in the test suite (tests may import `datetime` freely, so the algorithm is checked rather than
    trusted).

    It is also the answer to "do not create another days-in-month table": there is no table. The
    month length falls out of the arithmetic, so February is 28 or 29 days without anything in this
    file knowing what a leap year is."""
    z = days + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    day_of_era = z - era * 146_097  # [0, 146096]
    year_of_era = (
        day_of_era - day_of_era // 1_460 + day_of_era // 36_524 - day_of_era // 146_096
    ) // 365  # [0, 399]
    year = year_of_era + era * 400
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    mp = (5 * day_of_year + 2) // 153  # [0, 11], March-based
    day = day_of_year - (153 * mp + 2) // 5 + 1  # [1, 31]
    month = mp + (3 if mp < 10 else -9)  # [1, 12]
    return year + (1 if month <= 2 else 0), month, day


def days_from_civil(year: int, month: int, day: int) -> int:
    """`(year, month, day)` → a day number since the Unix epoch. The exact inverse of the above."""
    year -= 1 if month <= 2 else 0
    era = (year if year >= 0 else year - 399) // 400
    year_of_era = year - era * 400  # [0, 399]
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146_097 + day_of_era - 719_468


def period_boundaries(
    window: BillingPeriod, granularity: PeriodGranularity
) -> tuple[BillingPeriod, ...]:
    """Cut a service window into calendar periods. **Pure, UTC, half-open.**

    Every boundary decision this milestone must make is made here, once:

    * **timezone is UTC**, always. A recognition schedule that shifted when a server moved would be
      a schedule nobody could reproduce, and "local time" has no meaning for a document billed
      across jurisdictions;
    * **intervals are half-open `[start, end)`**, matching M9.2's `BillingPeriod` — so a boundary
      instant belongs to exactly one period and no amount is counted twice or lost;
    * **the first and last periods are clipped to the window**, which is what makes a mid-month
      start a genuinely partial period rather than a whole one with a fudged weight;
    * **month arithmetic is exact integer calendar arithmetic** (`civil_from_days`), so February
      has 28 or 29 days with no table and no special case;
    * **leap seconds do not exist** in POSIX time, so there is nothing to handle.

    Weighting by the *seconds* in each clipped period is what makes proration correct across month
    lengths, leap years and partial periods without a single special case."""
    if granularity is PeriodGranularity.NONE:
        return (window,)
    step = _next_month if granularity is PeriodGranularity.MONTH else _next_day
    out: list[BillingPeriod] = []
    cursor = _floor(window.start, granularity)
    while cursor < window.end:
        nxt = step(cursor)
        lo = max(cursor, window.start)
        hi = min(nxt, window.end)
        if hi > lo:
            out.append(BillingPeriod(start=lo, end=hi))
        cursor = nxt
    return tuple(out) or (window,)


def _day_number(ts: float) -> int:
    """Floor-divide to a day number. Floor, not truncate, so pre-epoch instants behave."""
    return int(ts // SECONDS_PER_DAY)


def _floor(ts: float, granularity: PeriodGranularity) -> float:
    days = _day_number(ts)
    if granularity is PeriodGranularity.MONTH:
        year, month, _ = civil_from_days(days)
        return float(days_from_civil(year, month, 1) * SECONDS_PER_DAY)
    return float(days * SECONDS_PER_DAY)


def _next_month(ts: float) -> float:
    year, month, _ = civil_from_days(_day_number(ts))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return float(days_from_civil(year, month, 1) * SECONDS_PER_DAY)


def _next_day(ts: float) -> float:
    return _floor(ts, PeriodGranularity.DAY) + float(SECONDS_PER_DAY)


def period_key(period: BillingPeriod) -> str:
    """A stable, sortable label. Presentation only — nothing decides anything from it."""
    year, month, day = civil_from_days(_day_number(period.start))
    return f"{year:04d}-{month:02d}-{day:02d}"


# ── policy ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecognitionPolicy:
    """**A versioned, immutable recognition strategy.**

    There is no update path anywhere: publishing a change appends version *n+1*, and a schedule
    built under *n* keeps naming *n*. That is what makes "re-run the March recognition after the
    April policy change and get the same answer" a property of addressing rather than of care —
    and it is why the policy *version* is inside the basis digest.

    A policy holds **no money**. It holds a method, a calendar step, and a remainder rule."""

    id: str
    org_id: str
    code: str
    version: int
    method: RecognitionMethod = RecognitionMethod.STRAIGHT_LINE
    granularity: PeriodGranularity = PeriodGranularity.MONTH
    remainder: RemainderRule = RemainderRule.LARGEST_REMAINDER
    is_active: bool = True
    description: str = ""
    declared_at: float = 0.0
    declared_by: str = ""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("recognition policy version must be >= 1")

    @property
    def version_key(self) -> str:
        return f"{self.code}@{self.version}"


# ── the basis ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecognitionBasis:
    """**The immutable evidence a schedule was derived from.**

    Every field here is copied from a source that cannot change: the document id and *revision*, the
    snapshot digest that addresses its content, the amount that snapshot stated, the service window
    it stated, and the policy version in force at the time. Nothing is a lookup, for the same reason
    `InvoiceSnapshot` is self-contained (ADR-0023 IN5): a basis that had to query live state to be
    understood would be a basis that changes when that state does.

    `milestones` carries explicit weights for `SCHEDULED` recognition, so even a milestone contract
    is *allocated* rather than hand-entered — which is what keeps conservation structural."""

    org_id: str
    account_id: str
    document_id: str
    revision: int
    snapshot_digest: str
    amount: Money
    service_period: BillingPeriod
    method: RecognitionMethod = RecognitionMethod.STRAIGHT_LINE
    granularity: PeriodGranularity = PeriodGranularity.MONTH
    remainder: RemainderRule = RemainderRule.LARGEST_REMAINDER
    policy_id: str = ""
    policy_code: str = ""
    policy_version: int = 0
    milestones: tuple[tuple[float, int], ...] = ()  # (recognize_on, weight) for SCHEDULED
    source_line_id: str = ""
    subscription_id: str = ""

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def digest(self) -> str:
        return derive_basis_digest(
            org_id=self.org_id,
            document_id=self.document_id,
            revision=self.revision,
            snapshot_digest=self.snapshot_digest,
            account_id=self.account_id,
            amount_minor=self.amount.minor,
            currency=self.amount.currency.value,
            service_start=self.service_period.start,
            service_end=self.service_period.end,
            method=self.method.value,
            policy_code=self.policy_code,
            policy_version=self.policy_version,
            remainder=self.remainder.value,
            granularity=self.granularity.value,
        )

    @property
    def schedule_id(self) -> str:
        return derive_schedule_id(self.org_id, self.digest)


# ── the schedule ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RevenueSchedule:
    """**The plan for turning one billed amount into recognized revenue.**

    Its id *is* its basis digest, so any change to any input that could affect the answer produces
    a different schedule rather than a mutated one.

    Fields that deliberately do not exist:

    * **no `recognized_minor` and no `deferred_minor`.** Deferred revenue is not a stored balance —
      it is `basis.amount - sum(recognized entries)`, derived at read time from the entries that
      actually happened. A stored balance is a second authority on a figure the Ledger owns, and it
      is the one that goes stale (the same rule M9.13 applies to `AccountBalance` and M9.16 to a
      payment position);
    * **no payment state, no collection state.** Paid is not earned;
    * **no invoice total.** The amount was consumed from the snapshot, never recomputed."""

    id: str
    org_id: str
    account_id: str
    basis: RecognitionBasis
    state: ScheduleState = ScheduleState.DRAFT
    entries_planned: int = 0
    entries_recognized: int = 0  # a **count**, never a money balance
    version: int = 0  # optimistic-concurrency token
    superseded_by: str = ""
    created_at: float = 0.0
    created_by: str = ""
    updated_at: float = 0.0
    closed_at: float | None = None
    closed_reason: str = ""

    @property
    def currency(self) -> Currency:
        return self.basis.currency

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_SCHEDULE_STATES

    @property
    def has_earned(self) -> bool:
        return self.state in EARNED_SCHEDULE_STATES

    def entry_id_for(self, sequence: int) -> str:
        return derive_entry_id(self.id, sequence)


@dataclass(frozen=True, slots=True)
class RecognitionEntry:
    """**One dated slice of revenue.** Append-only in every sense that matters.

    Its id is derived from `(schedule, sequence)`, so two workers recognizing "period 3" write one
    row. Its `recognition` field is M9.2's `RecognitionState`, carried through to the ledger entry
    it causes — this domain declares no second posture vocabulary."""

    id: str
    org_id: str
    schedule_id: str
    account_id: str
    sequence: int
    period: BillingPeriod
    amount: Money
    recognize_on: float
    state: EntryState = EntryState.PENDING
    recognition: RecognitionState = RecognitionState.DEFERRED
    ledger_entry_id: str = ""
    ledger_period_key: str = ""
    ledger_disposition: str = ""
    recognized_at: float | None = None
    posted_at: float | None = None
    reversed_by: str = ""
    note: str = ""

    def due_at(self, at: float) -> bool:
        """Whether this slice has matured. Clock-free: `at` is passed, never read."""
        return at >= self.recognize_on

    @property
    def is_recognized(self) -> bool:
        return self.state in RECOGNIZED_ENTRY_STATES

    @property
    def is_closed(self) -> bool:
        return self.state in CLOSED_ENTRY_STATES


@dataclass(frozen=True, slots=True)
class RecognitionAdjustment:
    """**A correction, as a new fact.** The original entry is read and left exactly as it is.

    Carries the full provenance the mandate requires: which recognition, why, from which source
    document and revision, under which policy version, effective when, and for how much."""

    id: str
    org_id: str
    account_id: str
    schedule_id: str
    entry_id: str
    kind: AdjustmentKind
    amount: Money  # signed: a reversal is the negative of what was recognized
    reason: str = ""
    document_id: str = ""
    revision: int = 0
    policy_code: str = ""
    policy_version: int = 0
    effective_at: float = 0.0
    ledger_entry_id: str = ""
    created_at: float = 0.0
    created_by: str = ""
    request_key: str = ""


@dataclass(frozen=True, slots=True)
class RecognitionActivity:
    """One audit line on a schedule."""

    id: str
    org_id: str
    schedule_id: str
    at: float
    kind: str
    summary: str = ""
    actor_id: str = ""
    entry_id: str = ""
    detail: dict[str, str] = field(default_factory=dict)


# ── derived reads ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecognitionPosition:
    """**Derived**, never stored: what a schedule has actually earned.

    Nothing in persistence holds `recognized` or `deferred`. Both are computed from the entries and
    adjustments that happened, because the moment a revenue total is a column it is a second
    authority on a number the Ledger owns."""

    schedule_id: str
    currency: Currency
    total: Money
    recognized: Money
    adjusted: Money
    deferred: Money
    entries: int = 0
    recognized_entries: int = 0
    posted_entries: int = 0
    unposted_entries: int = 0
    reversed_entries: int = 0

    @property
    def is_fully_recognized(self) -> bool:
        return self.deferred.is_zero and not self.total.is_zero

    @property
    def has_unposted(self) -> bool:
        """Recognized but not in the Ledger. The one number an operator must never ignore."""
        return self.unposted_entries > 0


@dataclass(frozen=True, slots=True)
class RecognitionExplanation:
    """**Why this amount was recognized, answerable without current pricing, tax or payment state.**

    Assembled entirely from the basis and the entries — which is what makes it still answerable in
    five years, after the price book, the tax rates and the subscription have all changed."""

    schedule_id: str
    state: ScheduleState
    document_id: str
    revision: int
    snapshot_digest: str
    basis_digest: str
    policy_code: str
    policy_version: int
    method: RecognitionMethod
    granularity: PeriodGranularity
    remainder: RemainderRule
    service_start: float
    service_end: float
    total_minor: int
    recognized_minor: int
    deferred_minor: int
    currency: str
    reason: str = ""
    path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecognitionInspection:
    schedule: RevenueSchedule
    position: RecognitionPosition
    adjustments: int = 0
    due_now: int = 0
    next_recognize_on: float | None = None


@dataclass(frozen=True, slots=True)
class RevenueSearchQuery:
    org_id: str
    account_id: str = ""
    document_id: str = ""
    states: tuple[ScheduleState, ...] = ()
    due_before: float | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class RevenueSearchResult:
    schedules: tuple[RevenueSchedule, ...] = ()
    total: int = 0
    truncated: bool = False


__all__ = [
    "CLOSED_ENTRY_STATES",
    "EARNED_SCHEDULE_STATES",
    "RECOGNIZED_ENTRY_STATES",
    "REVENUE_ENTRY_LIFECYCLE",
    "REVENUE_SCHEDULE_LIFECYCLE",
    "TERMINAL_SCHEDULE_STATES",
    "AdjustmentKind",
    "EntryState",
    "EntryTrigger",
    "PeriodGranularity",
    "RecognitionActivity",
    "RecognitionAdjustment",
    "RecognitionBasis",
    "RecognitionEntry",
    "RecognitionExplanation",
    "RecognitionInspection",
    "RecognitionMethod",
    "RecognitionPolicy",
    "RecognitionPosition",
    "RemainderRule",
    "RevenueSchedule",
    "RevenueSearchQuery",
    "RevenueSearchResult",
    "ScheduleState",
    "ScheduleTrigger",
    "derive_adjustment_id",
    "derive_basis_digest",
    "derive_entry_id",
    "derive_recognition_policy_version_id",
    "civil_from_days",
    "days_from_civil",
    "derive_schedule_id",
    "period_boundaries",
    "period_key",
]
