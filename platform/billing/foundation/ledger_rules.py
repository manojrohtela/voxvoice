"""Ledger rules (M9.13) — the pure decisions that turn a commercial fact into a financial one.

Every rule the Ledger applies lives here. Nothing in this module reads a clock, touches persistence,
calculates a price, rates a charge, builds an invoice, moves money, or determines a tax: recording
is a **pure function of what it is given**, and that is the only way "replay reconstructs identical
financial state" can be asserted by a test rather than hoped for in production.

Three decisions carry the milestone.

## 1. Posting is a decision, not an action

`PostingService.decide` takes a request, the period it would land in, and whether an entry already
exists, and returns a verdict. It writes nothing. The Application acts on the verdict. That
separation is what lets the *same* pure function serve the live posting path and the replay path —
a replay that used different code would prove nothing about the original, which is the lesson M9.11
recorded and this milestone reuses.

Four outcomes, and three of them are successes:

* `POSTED` — a new financial fact;
* `DUPLICATE` — the cause was already recorded. **Not an error.** At-least-once delivery makes
  retries normal, and the recorded activity entry is the evidence exactly-once worked;
* `DEFERRED` — the period it belongs to is closed, so it lands in the next open one as an
  adjustment. A closed period is never reopened;
* `REJECTED` — no open period exists at all, or the request is malformed. The one failure.

## 2. A closed period is a one-way door, and lateness has somewhere to go

M9.11 learned this the hard way: sealing a period without deciding where a late fact lands produces
either a silent retroactive write or an unexplained rejection. Here the disposition is explicit and
recorded **on the entry**, so two periods reconcile against each other rather than appearing to
disagree.

## 3. Credit application is deterministic and ordered

Credits are applied **earliest-expiring first, then by id**. Declared rather than emergent, because
"which of my three credits did you spend?" has one right answer and an insertion-ordered one is not
it — and because a customer whose oldest credit silently expired unspent while a newer one was
consumed has a legitimate complaint.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from core.fsm import StateMachineDefinition

from .commercial_ledger import (
    ACCOUNTING_PERIOD_LIFECYCLE,
    CREDIT_LIFECYCLE,
    AccountBalance,
    AccountingPeriod,
    AccountingPeriodState,
    AccountingPeriodTrigger,
    BalanceCheckpoint,
    CommercialCredit,
    CreditApplication,
    CreditBalanceView,
    CreditPosition,
    CreditState,
    CreditTrigger,
    EntryInspection,
    EntryReferences,
    JournalEntry,
    LateEntryDisposition,
    LedgerDiscrepancy,
    LedgerDiscrepancyKind,
    LedgerInspection,
    LedgerReconciliation,
    LedgerReplayResult,
    LedgerSearchQuery,
    LedgerSearchResult,
    PostingSource,
    TrialBalance,
    TrialBalanceLine,
    _digest,
    derive_checkpoint_id,
    derive_journal_entry_id,
)
from .commercial_rating import ChargeKind
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .ledger import LedgerDirection, LedgerEntryType, RecognitionState
from .money import Currency, Money

# ── what a rated charge becomes ─────────────────────────────────────────────────

#: How an M9.12 `ChargeKind` becomes an M9.2 `LedgerEntryType`.
#:
#: A rule, and therefore declared here in one place rather than rebuilt by whichever orchestrator
#: happens to post. The mapping is deliberately almost trivial — nearly every rated charge is a
#: `CHARGE` — because the ledger records *that money is owed*, not *why*; the why lives in the
#: charge this entry references, and duplicating it here would make the ledger a second, weaker copy
#: of the rating breakdown.
#:
#: This is the one import from another domain's vocabulary in the ledger Foundation, and it runs in
#: the sanctioned direction: the Ledger consumes Rating's published types, never the reverse.
CHARGE_KIND_TO_ENTRY_TYPE: dict[ChargeKind, LedgerEntryType] = {
    ChargeKind.FLAT: LedgerEntryType.CHARGE,
    ChargeKind.SEAT: LedgerEntryType.CHARGE,
    ChargeKind.USAGE: LedgerEntryType.CHARGE,
    ChargeKind.OVERAGE: LedgerEntryType.CHARGE,
    ChargeKind.TIERED: LedgerEntryType.CHARGE,
    ChargeKind.VOLUME: LedgerEntryType.CHARGE,
    ChargeKind.PACKAGE: LedgerEntryType.CHARGE,
    ChargeKind.ONE_OFF: LedgerEntryType.CHARGE,
    ChargeKind.MINIMUM_COMMITMENT: LedgerEntryType.CHARGE,
    ChargeKind.ADJUSTMENT: LedgerEntryType.ADJUSTMENT,
}


class ChargePostingService:
    """Turns rated charges into posting requests. **Records; never recalculates.**

    Not one line here multiplies, folds, rounds, or re-derives an amount: `charge.amount` is taken
    exactly as M9.12 produced it, and `charge.digest` is carried as the source fingerprint so the
    ledger can later prove that what it recorded is what was decided. A test asserts this module
    performs no arithmetic on a charge.

    A **negative** charge — M9.12 permits one only for an adjustment — becomes a `CREDIT` entry of
    the absolute amount rather than a negative debit. Direction carries the sign in this domain, and
    a negative amount would make every report that sums a column silently wrong."""

    def requests(
        self,
        charges: Sequence,
        *,
        org_id: str,
        account_id: str,
        rating_run_id: str,
        period_key: str = "",
    ) -> tuple[PostingRequest, ...]:
        out: list[PostingRequest] = []
        for charge in charges:
            amount = charge.amount
            direction = LedgerDirection.CREDIT if amount.is_negative else LedgerDirection.DEBIT
            magnitude = Money(abs(amount.minor), amount.currency)
            if magnitude.is_zero:
                # A zero charge is a real commercial outcome (a met allowance) and not a financial
                # fact. Recording it would put a zero line on every statement and invite the
                # question "why am I being billed nothing for this?".
                continue
            out.append(
                PostingRequest(
                    org_id=org_id,
                    account_id=account_id,
                    entry_type=CHARGE_KIND_TO_ENTRY_TYPE.get(charge.kind, LedgerEntryType.CHARGE),
                    direction=direction,
                    amount=magnitude,
                    source=PostingSource.RATING_RUN,
                    source_id=charge.id,
                    source_digest=charge.digest,
                    references=EntryReferences(
                        subscription_id=charge.provenance.subscription_id,
                        rating_run_id=rating_run_id,
                        charge_id=charge.id,
                        usage_period_key=period_key,
                    ),
                    recognition=RecognitionState.RECOGNIZED,
                    target_period_key=period_key,
                    description=charge.description,
                )
            )
        return tuple(out)


# ── posting ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PostingRequest:
    """One financial fact somebody wants recorded.

    Carries what caused it, never a copy of the cause. `source_digest` is the fingerprint of the
    commercial artefact — an M9.12 charge digest, for instance — so the ledger can later prove that
    what it recorded is what was decided, without storing a second copy of the decision."""

    org_id: str
    account_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: Money
    source: PostingSource
    source_id: str = ""
    source_digest: str = ""
    references: EntryReferences = field(default_factory=EntryReferences)
    recognition: RecognitionState = RecognitionState.NOT_APPLICABLE
    reverses_entry_id: str = ""
    adjusts_entry_id: str = ""
    target_period_key: str = ""
    description: str = ""

    @property
    def entry_id(self) -> str:
        """The derived identity of the entry this request would produce.

        `target_period_key` is deliberately **excluded**: the same cause is one financial fact
        regardless of which period it ends up recorded in. Including it would mean a charge
        deferred into February had a different identity from the same charge posted in January,
        and re-running the January job after the close would double-post."""
        return derive_journal_entry_id(
            self.org_id,
            self.account_id,
            self.source.value,
            self.source_id or f"{self.reverses_entry_id}{self.adjusts_entry_id}",
            self.entry_type.value,
        )


class PostingOutcome(StrEnum):
    POSTED = "posted"
    DUPLICATE = "duplicate"
    DEFERRED = "deferred"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PostingDecision:
    """What should happen, decided without doing it.

    `landing_period_key` is the period the entry actually lands in, which is not always the period
    it belongs to — a late fact lands in the next open one. Recording both is what lets an auditor
    see that a January charge was recorded in February *and why*."""

    outcome: PostingOutcome
    entry_id: str = ""
    landing_period_key: str = ""
    disposition: LateEntryDisposition = LateEntryDisposition.IN_PERIOD
    reason: str = ""
    diagnostics: BillingDiagnostics = field(default_factory=BillingDiagnostics.empty)

    @property
    def should_write(self) -> bool:
        return self.outcome in (PostingOutcome.POSTED, PostingOutcome.DEFERRED)

    @property
    def is_success(self) -> bool:
        """Three of the four outcomes are successes. Only `REJECTED` is a failure."""
        return self.outcome is not PostingOutcome.REJECTED


class PostingService:
    """The pure correctness core: *should this become a financial fact, and where?*

    Reads no clock, opens no transaction, and writes nothing. The same function serves the live path
    and replay, which is what makes a replay's agreement meaningful."""

    def decide(
        self,
        request: PostingRequest,
        *,
        target_period: AccountingPeriod | None,
        open_period: AccountingPeriod | None,
        already_posted: bool,
    ) -> PostingDecision:
        entry_id = request.entry_id

        # 1. Exactly-once first among the *successful* outcomes, but after validity: reporting a
        #    malformed request as a duplicate would hide the malformation.
        findings = self._validate(request)
        if findings.has_errors:
            return PostingDecision(
                outcome=PostingOutcome.REJECTED,
                entry_id=entry_id,
                reason="the posting request is invalid",
                diagnostics=findings,
            )

        if already_posted:
            return PostingDecision(
                outcome=PostingOutcome.DUPLICATE,
                entry_id=entry_id,
                landing_period_key=target_period.period_key if target_period else "",
                reason="this cause is already recorded; no second entry exists",
            )

        # 2. The period it belongs to, if that period still accepts postings.
        if target_period is not None and target_period.accepts_postings:
            return PostingDecision(
                outcome=PostingOutcome.POSTED,
                entry_id=entry_id,
                landing_period_key=target_period.period_key,
                disposition=LateEntryDisposition.IN_PERIOD,
            )

        # 3. The period is closed (or absent). A closed period is **never** reopened, so the entry
        #    lands in the next open one and says so.
        if open_period is not None:
            return PostingDecision(
                outcome=PostingOutcome.DEFERRED,
                entry_id=entry_id,
                landing_period_key=open_period.period_key,
                disposition=LateEntryDisposition.NEXT_OPEN_PERIOD,
                reason=(
                    f"the period this belongs to is "
                    f"{target_period.state.value if target_period else 'absent'}; "
                    f"recorded in {open_period.period_key} as an adjustment"
                ),
            )

        # 4. Nowhere to put it. Rejected loudly rather than dropped — an unrecordable financial
        #    fact is an operational incident, and silence is how revenue disappears.
        return PostingDecision(
            outcome=PostingOutcome.REJECTED,
            entry_id=entry_id,
            disposition=LateEntryDisposition.REJECTED_NO_OPEN_PERIOD,
            reason="no open accounting period exists for this account",
            diagnostics=BillingDiagnostics.of(
                [
                    _d(
                        "ledger.period",
                        BillingDiagnosticCode.NO_OPEN_ACCOUNTING_PERIOD,
                        "no open accounting period exists to record this entry",
                        remediation="open the next period; a closed period is never reopened",
                    )
                ]
            ),
        )

    @staticmethod
    def _validate(request: PostingRequest) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        if request.amount.is_negative:
            findings.append(
                _d(
                    "posting.amount",
                    BillingDiagnosticCode.INVALID_LEDGER_AMOUNT,
                    "a journal entry amount is never negative; direction carries the sign",
                    remediation="post a CREDIT entry rather than a negative DEBIT",
                )
            )
        if not request.account_id:
            findings.append(
                _d(
                    "posting.account_id",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "every entry belongs to exactly one account",
                )
            )
        if request.reverses_entry_id and request.adjusts_entry_id:
            findings.append(
                _d(
                    "posting.correction",
                    BillingDiagnosticCode.INVALID_LEDGER_CORRECTION,
                    "an entry either reverses or adjusts another; never both",
                    remediation="a reversal withdraws a figure; an adjustment changes it",
                )
            )
        return BillingDiagnostics.of(findings)


class EntryComposer:
    """Turns an accepted decision into the immutable entry. Composition only — the deciding
    happened in `PostingService`, and keeping them apart is what lets the decision be replayed."""

    def compose(
        self,
        request: PostingRequest,
        decision: PostingDecision,
        *,
        sequence: int,
        posted_at: float,
        posted_by: str = "",
    ) -> JournalEntry:
        return JournalEntry(
            id=decision.entry_id,
            org_id=request.org_id,
            account_id=request.account_id,
            period_key=decision.landing_period_key,
            sequence=sequence,
            entry_type=request.entry_type,
            direction=request.direction,
            amount=request.amount,
            source=request.source,
            source_id=request.source_id,
            source_digest=request.source_digest,
            references=request.references,
            recognition=request.recognition,
            reverses_entry_id=request.reverses_entry_id,
            adjusts_entry_id=request.adjusts_entry_id,
            disposition=decision.disposition,
            description=request.description,
            posted_at=posted_at,
            posted_by=posted_by,
        )


# ── corrections ─────────────────────────────────────────────────────────────────


class ReversalService:
    """Builds the entry that withdraws another.

    A reversal is a **new posting in the opposite direction for the same amount**, never an edit and
    never a deletion. Two guards, both refusing loudly: a reversal cannot itself be reversed (that
    is a re-posting, and calling it a reversal makes the chain unreadable), and an entry cannot be
    reversed twice (which would credit the customer the amount twice over)."""

    def guard_reversible(self, entry: JournalEntry, existing: Sequence[JournalEntry]) -> None:
        if entry.is_reversal:
            raise BillingConflict(
                f"entry {entry.id} is itself a reversal; reversing a reversal is a new posting, "
                "and naming it a reversal makes the correction chain unreadable"
            )
        already = [e for e in existing if e.reverses_entry_id == entry.id]
        if already:
            raise BillingConflict(
                f"entry {entry.id} is already reversed by {already[0].id}; "
                "reversing it again would credit the amount twice"
            )

    def request(
        self, entry: JournalEntry, *, reason: str = "", actor_id: str = ""
    ) -> PostingRequest:
        opposite = (
            LedgerDirection.CREDIT
            if entry.direction is LedgerDirection.DEBIT
            else LedgerDirection.DEBIT
        )
        return PostingRequest(
            org_id=entry.org_id,
            account_id=entry.account_id,
            entry_type=entry.entry_type,
            direction=opposite,
            amount=entry.amount,
            source=PostingSource.REVERSAL,
            source_id=entry.id,
            source_digest=entry.digest,
            references=entry.references,
            recognition=entry.recognition,
            reverses_entry_id=entry.id,
            # A correction belongs to the period of the entry it corrects. If that period has
            # closed, the posting defers into the next open one and says so on the entry.
            target_period_key=entry.period_key,
            description=reason or f"Reversal of {entry.id}",
        )


class AdjustmentService:
    """Builds the entry that changes a figure without withdrawing it.

    The distinction from a reversal has teeth: a reversal says "this should not have been
    recorded"; an adjustment says "this was recorded, and the amount has moved by this much". An
    auditor needs to tell them apart, and a single `correction` type would make that impossible."""

    def request(
        self,
        entry: JournalEntry,
        *,
        delta: Money,
        entry_type: LedgerEntryType | None = None,
        reason: str = "",
    ) -> PostingRequest:
        """`delta` is signed by *intent*: a positive delta increases what the customer owes and
        posts in the original entry's direction; a negative one posts against it."""
        magnitude = Money(abs(delta.minor), delta.currency)
        same_way = delta.minor >= 0
        direction = (
            entry.direction
            if same_way
            else (
                LedgerDirection.CREDIT
                if entry.direction is LedgerDirection.DEBIT
                else LedgerDirection.DEBIT
            )
        )
        return PostingRequest(
            org_id=entry.org_id,
            account_id=entry.account_id,
            entry_type=entry_type or LedgerEntryType.ADJUSTMENT,
            direction=direction,
            amount=magnitude,
            source=PostingSource.ADJUSTMENT,
            source_id=entry.id,
            source_digest=entry.digest,
            references=entry.references,
            adjusts_entry_id=entry.id,
            target_period_key=entry.period_key,
            description=reason or f"Adjustment to {entry.id}",
        )


# ── balances ────────────────────────────────────────────────────────────────────


class BalanceService:
    """Derives balances and the checkpoints that make reading one affordable.

    A checkpoint is a **cache with a proof**. `verify` recomputes it from the entries it claims to
    summarise, so drift is detectable. Nothing here ever *stores* a balance as truth, and there is
    no method that would let a caller assert one."""

    def balance(
        self,
        account_id: str,
        currency: Currency,
        entries: Sequence[JournalEntry],
        *,
        as_of: float = 0.0,
    ) -> AccountBalance:
        return AccountBalance.of(account_id, currency, tuple(entries), as_of=as_of)

    def checkpoint(
        self,
        *,
        org_id: str,
        account_id: str,
        period_key: str,
        currency: Currency,
        entries: Sequence[JournalEntry],
        computed_at: float = 0.0,
    ) -> BalanceCheckpoint:
        derived = self.balance(account_id, currency, entries)
        return BalanceCheckpoint(
            id=derive_checkpoint_id(org_id, account_id, period_key),
            org_id=org_id,
            account_id=account_id,
            period_key=period_key,
            currency=currency,
            outstanding_minor=derived.outstanding.minor,
            debit_minor=derived.debits.minor,
            credit_minor=derived.credits.minor,
            entry_count=derived.entry_count,
            through_sequence=derived.through_sequence,
            digest=derived.digest,
            computed_at=computed_at,
        )

    def verify(self, checkpoint: BalanceCheckpoint, entries: Sequence[JournalEntry]) -> bool:
        return checkpoint.verify(tuple(entries))

    def sequence_gaps(self, entries: Sequence[JournalEntry]) -> tuple[int, ...]:
        """Missing positions in an account's total order.

        A gap means an entry was allocated a sequence and never committed, or was removed — and the
        second is supposed to be impossible. Surfacing it is how the impossible gets noticed."""
        numbers = sorted(e.sequence for e in entries)
        if not numbers:
            return ()
        expected = set(range(numbers[0], numbers[-1] + 1))
        return tuple(sorted(expected - set(numbers)))


class TrialBalanceService:
    """The subledger control report. Totals by entry type, debits and credits kept separate.

    Separate rather than netted, deliberately: an accountant reconciling against a general ledger
    needs both sides, and a net figure cannot be mapped onto two GL accounts."""

    def compute(
        self,
        entries: Sequence[JournalEntry],
        *,
        org_id: str,
        account_id: str,
        period_key: str,
        currency: Currency,
        opening: Money | None = None,
        at: float = 0.0,
    ) -> TrialBalance:
        by_type: dict[LedgerEntryType, list[JournalEntry]] = {}
        for entry in entries:
            by_type.setdefault(entry.entry_type, []).append(entry)
        lines = []
        for entry_type in sorted(by_type, key=lambda t: t.value):
            group = by_type[entry_type]
            debits = Money(
                sum(e.amount.minor for e in group if e.direction is LedgerDirection.DEBIT),
                currency,
            )
            credits = Money(
                sum(e.amount.minor for e in group if e.direction is LedgerDirection.CREDIT),
                currency,
            )
            lines.append(
                TrialBalanceLine(
                    entry_type=entry_type,
                    debits=debits,
                    credits=credits,
                    entry_count=len(group),
                )
            )
        movement = Money(sum(e.signed_minor for e in entries), currency)
        opening_balance = opening if opening is not None else Money.zero(currency)
        return TrialBalance(
            org_id=org_id,
            account_id=account_id,
            period_key=period_key,
            currency=currency,
            lines=tuple(lines),
            opening_balance=opening_balance,
            closing_balance=opening_balance + movement,
            at=at,
        )


# ── accounting periods ──────────────────────────────────────────────────────────


class AccountingPeriodService:
    """Applies period lifecycle triggers through the shared FSM, and computes the close digest.

    Illegal edges are rejected by the table, not by an `if` — which is what makes "a closed period
    is never reopened" a property of the machine rather than of whoever wrote the last branch."""

    def __init__(self, definition: StateMachineDefinition = ACCOUNTING_PERIOD_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: AccountingPeriodState, trigger: AccountingPeriodTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(
        self, current: AccountingPeriodState, trigger: AccountingPeriodTrigger
    ) -> AccountingPeriodState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"an accounting period cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: AccountingPeriodState) -> tuple[AccountingPeriodTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def close_digest(self, entries: Sequence[JournalEntry]) -> str:
        """A deterministic fingerprint of everything the period contained at close.

        Order-independent by construction — the entry digests are sorted before hashing — so two
        readers who fetched the entries in different orders compute the same digest. Excludes
        posting timestamps, which legitimately differ between an original posting and a replay."""
        return _digest(*sorted(e.digest for e in entries))

    def apply(
        self,
        period: AccountingPeriod,
        trigger: AccountingPeriodTrigger,
        *,
        at: float,
        actor_id: str = "",
        entries: Sequence[JournalEntry] = (),
    ) -> AccountingPeriod:
        target = self.next_state(period.state, trigger)
        changes: dict = {"state": target, "updated_at": at}
        if target is AccountingPeriodState.CLOSING:
            changes["closing_at"] = at
        if target is AccountingPeriodState.CLOSED:
            balance = AccountBalance.of(period.account_id, period.currency, tuple(entries))
            changes.update(
                closed_at=at,
                closed_by=actor_id,
                entry_count=balance.entry_count,
                debit_minor=balance.debits.minor,
                credit_minor=balance.credits.minor,
                closing_balance_minor=balance.outstanding.minor,
                close_digest=self.close_digest(entries),
            )
        return replace(period, **changes)

    def guard_open(self, period: AccountingPeriod) -> None:
        """Fail fast when something would post into a period that no longer accepts postings.

        Belt and braces beside the FSM: the machine refuses the transition, and this refuses the
        *intent* with a message a caller trying to correct a filed month actually needs to read."""
        if not period.accepts_postings:
            raise BillingConflict(
                f"accounting period {period.period_key} is {period.state.value}; a closed period "
                "is never reopened — a late fact is recorded in the next open period"
            )


# ── credits ─────────────────────────────────────────────────────────────────────


class CreditLifecycleService:
    """Applies credit lifecycle triggers through the shared FSM."""

    def __init__(self, definition: StateMachineDefinition = CREDIT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: CreditState, trigger: CreditTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: CreditState, trigger: CreditTrigger) -> CreditState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a credit cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: CreditState) -> tuple[CreditTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        credit: CommercialCredit,
        trigger: CreditTrigger,
        *,
        at: float,
        actor_id: str = "",
        reason: str = "",
    ) -> CommercialCredit:
        target = self.next_state(credit.state, trigger)
        return replace(
            credit,
            state=target,
            closed_at=at,
            closed_by=actor_id,
            reason=reason or credit.reason,
        )


@dataclass(frozen=True, slots=True)
class CreditAllocation:
    """One credit's share of an amount being covered."""

    credit_id: str
    amount: Money


@dataclass(frozen=True, slots=True)
class CreditPlan:
    """How an amount would be covered by the credits available. A *read* — planning it has no
    effect, so a caller can show a customer what would happen before it does."""

    requested: Money
    allocations: tuple[CreditAllocation, ...] = ()
    covered: Money | None = None
    shortfall: Money | None = None

    @property
    def is_fully_covered(self) -> bool:
        return self.shortfall is not None and self.shortfall.is_zero


class CreditApplicationService:
    """Decides which credits cover an amount, and by how much.

    **Earliest-expiring first, then by id.** Declared rather than emergent: a customer whose oldest
    credit lapsed unspent while a newer one was consumed has a legitimate complaint, and an
    insertion-ordered answer would make "which credit did you spend?" depend on how a repository
    happened to sort its rows.

    An expired credit is excluded at *resolution time* as well as by state, so a credit whose window
    has passed stops being spendable immediately rather than when a job gets round to closing it."""

    def applicable(
        self,
        credits: Sequence[CommercialCredit],
        positions: dict[str, CreditPosition],
        *,
        at: float,
        currency: Currency,
    ) -> tuple[CommercialCredit, ...]:
        live = [
            c
            for c in credits
            if c.is_live_at(at)
            and c.currency is currency
            and positions.get(c.id, None) is not None
            and positions[c.id].remaining.minor > 0
        ]
        return tuple(sorted(live, key=lambda c: (c.expires_at or float("inf"), c.id)))

    def plan(
        self,
        amount: Money,
        credits: Sequence[CommercialCredit],
        positions: dict[str, CreditPosition],
        *,
        at: float,
    ) -> CreditPlan:
        remaining = amount
        allocations: list[CreditAllocation] = []
        for credit in self.applicable(credits, positions, at=at, currency=amount.currency):
            if remaining.minor <= 0:
                break
            available = positions[credit.id].remaining
            take = available if available.minor < remaining.minor else remaining
            if take.minor <= 0:
                continue
            allocations.append(CreditAllocation(credit_id=credit.id, amount=take))
            remaining = remaining - take
        covered = amount - remaining
        return CreditPlan(
            requested=amount,
            allocations=tuple(allocations),
            covered=covered,
            shortfall=remaining,
        )

    def position(
        self, credit: CommercialCredit, applications: Sequence[CreditApplication]
    ) -> CreditPosition:
        return CreditPosition.of(credit, tuple(applications))

    def balance(
        self,
        account_id: str,
        currency: Currency,
        credits: Sequence[CommercialCredit],
        applications: Sequence[CreditApplication],
    ) -> CreditBalanceView:
        positions = tuple(self.position(c, applications) for c in credits)
        live = [p for p in positions if p.state is CreditState.ACTIVE]
        granted = Money.zero(currency)
        applied = Money.zero(currency)
        available = Money.zero(currency)
        for position in positions:
            granted = granted + position.granted
            applied = applied + position.applied
        for position in live:
            if position.remaining.minor > 0:
                available = available + position.remaining
        return CreditBalanceView(
            account_id=account_id,
            currency=currency,
            available=available,
            granted=granted,
            applied=applied,
            credit_count=len(positions),
            positions=positions,
        )


# ── replay, reconciliation, inspection ──────────────────────────────────────────


class LedgerReplayService:
    """Reconstructs a period's financial state from its entries and compares it to what was closed.

    Uses the **same** `AccountingPeriodService.close_digest` the close used — not a second
    implementation that could drift from it. That identity is the point: a replay computed
    differently would prove nothing about the original."""

    def __init__(self, periods: AccountingPeriodService | None = None) -> None:
        self._periods = periods or AccountingPeriodService()

    def replay(
        self,
        entries: Sequence[JournalEntry],
        *,
        org_id: str,
        account_id: str,
        period: AccountingPeriod | None,
        currency: Currency,
        at: float = 0.0,
    ) -> LedgerReplayResult:
        rebuilt = AccountBalance.of(account_id, currency, tuple(entries))
        return LedgerReplayResult(
            org_id=org_id,
            account_id=account_id,
            period_key=period.period_key if period else "",
            entries_replayed=len(entries),
            rebuilt_balance_minor=rebuilt.outstanding.minor,
            recorded_balance_minor=(
                period.closing_balance_minor if period else rebuilt.outstanding.minor
            ),
            replay_digest=self._periods.close_digest(entries),
            close_digest=period.close_digest if period else "",
            at=at,
        )


class LedgerReconciliationService:
    """Does the ledger say what the commercial layer says?

    Compares posted entries against the charges that should have produced them. Asked continuously
    rather than at close, because a gap found at close is a gap that has already reached a
    statement."""

    def reconcile(
        self,
        *,
        org_id: str,
        account_id: str,
        period_key: str,
        expected: Sequence[tuple[str, int]],  # (charge id, minor amount)
        entries: Sequence[JournalEntry],
        checkpoint: BalanceCheckpoint | None = None,
        at: float = 0.0,
    ) -> LedgerReconciliation:
        # Only *original* postings map to a charge. A reversal and an adjustment both carry the
        # original's `charge_id` in their references — that lineage is the point — so keying on it
        # without filtering let a correction shadow the entry it corrected, and every corrected
        # charge then read as an `AMOUNT_MISMATCH`. Found by the end-to-end smoke at M9.13.
        posted = {
            e.references.charge_id: e
            for e in entries
            if e.references.charge_id and not e.is_correction
        }
        seen: dict[str, int] = {}
        discrepancies: list[LedgerDiscrepancy] = []

        for charge_id, minor in expected:
            seen[charge_id] = seen.get(charge_id, 0) + 1
            entry = posted.get(charge_id)
            if entry is None:
                discrepancies.append(
                    LedgerDiscrepancy(
                        kind=LedgerDiscrepancyKind.UNPOSTED_CHARGE,
                        charge_id=charge_id,
                        expected_minor=minor,
                        detail="rating produced this charge; the ledger never recorded it",
                    )
                )
            elif entry.amount.minor != minor:
                discrepancies.append(
                    LedgerDiscrepancy(
                        kind=LedgerDiscrepancyKind.AMOUNT_MISMATCH,
                        charge_id=charge_id,
                        entry_id=entry.id,
                        expected_minor=minor,
                        actual_minor=entry.amount.minor,
                        detail="the recorded amount differs from the rated charge",
                    )
                )

        expected_ids = {c for c, _ in expected}
        for entry in entries:
            charge_id = entry.references.charge_id
            if entry.is_correction:
                continue  # a correction is explained by the entry it corrects, not by a charge
            if charge_id and charge_id not in expected_ids:
                discrepancies.append(
                    LedgerDiscrepancy(
                        kind=LedgerDiscrepancyKind.ORPHAN_ENTRY,
                        entry_id=entry.id,
                        charge_id=charge_id,
                        actual_minor=entry.amount.minor,
                        detail="the ledger recorded this; no rated charge produced it",
                    )
                )

        gaps = BalanceService().sequence_gaps(entries)
        for gap in gaps:
            discrepancies.append(
                LedgerDiscrepancy(
                    kind=LedgerDiscrepancyKind.SEQUENCE_GAP,
                    detail=f"no entry occupies sequence {gap}",
                )
            )

        if checkpoint is not None and not checkpoint.verify(tuple(entries)):
            discrepancies.append(
                LedgerDiscrepancy(
                    kind=LedgerDiscrepancyKind.CHECKPOINT_DRIFT,
                    expected_minor=checkpoint.outstanding_minor,
                    detail="the balance checkpoint no longer matches the entries it summarises",
                )
            )

        return LedgerReconciliation(
            org_id=org_id,
            account_id=account_id,
            period_key=period_key,
            charges_examined=len(expected),
            entries_examined=len(entries),
            discrepancies=tuple(discrepancies),
            at=at,
        )


class LedgerInspector:
    """A structural read of a period: is every entry traced, sequenced, and accounted for?

    Used by the diagnostics surface and by closing — a period whose entries cannot be walked back to
    a cause must not become a statement, and `is_sound` is how closing asks."""

    def inspect(
        self,
        entries: Sequence[JournalEntry],
        *,
        org_id: str,
        account_id: str,
        period_key: str,
        state: AccountingPeriodState,
        currency: Currency,
        checkpoint: BalanceCheckpoint | None = None,
    ) -> LedgerInspection:
        balances = BalanceService()
        reversed_by: dict[str, list[str]] = {}
        for entry in entries:
            if entry.reverses_entry_id:
                reversed_by.setdefault(entry.reverses_entry_id, []).append(entry.id)
            if entry.adjusts_entry_id:
                reversed_by.setdefault(entry.adjusts_entry_id, []).append(entry.id)

        inspections = []
        for entry in sorted(entries, key=lambda e: (e.sequence, e.id)):
            notes: list[str] = []
            if not entry.references.is_traced and entry.source is not PostingSource.MANUAL:
                notes.append("no reference back to what caused this entry")
            if entry.was_late:
                notes.append(f"recorded late: {entry.disposition.value}")
            inspections.append(
                EntryInspection(
                    entry_id=entry.id,
                    entry_type=entry.entry_type,
                    direction=entry.direction,
                    amount=entry.amount,
                    sequence=entry.sequence,
                    is_traced=entry.references.is_traced or entry.source is PostingSource.MANUAL,
                    is_corrected=entry.id in reversed_by,
                    corrected_by=tuple(reversed_by.get(entry.id, ())),
                    notes=tuple(notes),
                )
            )

        consistent = checkpoint is None or checkpoint.verify(tuple(entries))
        notes = [] if consistent else ["the balance checkpoint has drifted from the entries"]
        return LedgerInspection(
            org_id=org_id,
            account_id=account_id,
            period_key=period_key,
            state=state,
            balance=balances.balance(account_id, currency, entries),
            entries=tuple(inspections),
            sequence_gaps=balances.sequence_gaps(entries),
            checkpoint_consistent=consistent,
            notes=tuple(notes),
        )


# ── validation ──────────────────────────────────────────────────────────────────


class LedgerValidator:
    """Structural validation. **Never raises** — it reports, and the factory decides (§A2.6)."""

    def validate_credit(self, credit: CommercialCredit) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        if credit.amount.is_negative or credit.amount.is_zero:
            findings.append(
                _d(
                    "credit.amount",
                    BillingDiagnosticCode.INVALID_CREDIT_AMOUNT,
                    "a credit grant must be positive",
                    remediation="withdrawing a credit is a revocation, not a negative grant",
                )
            )
        if not credit.code:
            findings.append(
                _d(
                    "credit.code",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "a code is required",
                )
            )
        if credit.expires_at is not None and credit.expires_at <= credit.granted_at:
            findings.append(
                _d(
                    "credit.expires_at",
                    BillingDiagnosticCode.INVALID_CREDIT_AMOUNT,
                    "a credit expires before it was granted",
                )
            )
        return BillingDiagnostics.of(findings)

    def validate_application(
        self, credit: CommercialCredit, position: CreditPosition, amount: Money, *, at: float
    ) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        if amount.currency is not credit.currency:
            findings.append(
                _d(
                    "application.amount",
                    BillingDiagnosticCode.LEDGER_CURRENCY_MISMATCH,
                    f"applying {amount.currency.value} against a {credit.currency.value} credit",
                )
            )
        elif amount.minor > position.remaining.minor:
            findings.append(
                _d(
                    "application.amount",
                    BillingDiagnosticCode.CREDIT_OVERDRAWN,
                    f"applying {amount} against {position.remaining} remaining",
                    remediation="a credit cannot be spent past its grant",
                )
            )
        if not credit.is_live_at(at):
            findings.append(
                _d(
                    "application.credit",
                    BillingDiagnosticCode.EXPIRED_CREDIT_APPLIED,
                    f"credit {credit.code} is {credit.state.value} or past its expiry",
                )
            )
        return BillingDiagnostics.of(findings)

    def validate_period_close(
        self, period: AccountingPeriod, entries: Sequence[JournalEntry]
    ) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        gaps = BalanceService().sequence_gaps(entries)
        if gaps:
            findings.append(
                _d(
                    "period.sequence",
                    BillingDiagnosticCode.LEDGER_SEQUENCE_GAP,
                    f"{len(gaps)} gap(s) in the entry sequence: {gaps[:5]}",
                    remediation="an allocated sequence with no committed entry is unexplained",
                )
            )
        untraced = [
            e
            for e in entries
            if not e.references.is_traced and e.source is not PostingSource.MANUAL
        ]
        if untraced:
            findings.append(
                Diagnostic(
                    "period.entries",
                    BillingDiagnosticCode.UNTRACED_LEDGER_ENTRY,
                    Severity.WARNING,
                    f"{len(untraced)} entr(ies) cannot be walked back to a cause",
                    remediation="an amount nobody can trace cannot be defended in an audit",
                )
            )
        mismatched = [e for e in entries if e.amount.currency is not period.currency]
        if mismatched:
            findings.append(
                _d(
                    "period.currency",
                    BillingDiagnosticCode.LEDGER_CURRENCY_MISMATCH,
                    f"{len(mismatched)} entr(ies) in a currency other than {period.currency.value}",
                )
            )
        return BillingDiagnostics.of(findings)

    def validate_entries(self, entries: Sequence[JournalEntry]) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        seen: dict[str, int] = {}
        for entry in entries:
            seen[entry.id] = seen.get(entry.id, 0) + 1
            if entry.amount.is_negative:
                findings.append(
                    _d(
                        f"entry.{entry.id}",
                        BillingDiagnosticCode.INVALID_LEDGER_AMOUNT,
                        "a journal entry amount is never negative",
                    )
                )
        for entry_id, count in sorted(seen.items()):
            if count > 1:
                findings.append(
                    _d(
                        f"entry.{entry_id}",
                        BillingDiagnosticCode.DUPLICATE_LEDGER_POSTING,
                        f"{count} entries share one derived id",
                        remediation="the exactly-once constraint should have made this impossible",
                    )
                )
        return BillingDiagnostics.of(findings)


# ── search + history ────────────────────────────────────────────────────────────


class LedgerSearchService:
    """A deterministic filter over entries. No index, no scoring."""

    def search(
        self, entries: Sequence[JournalEntry], query: LedgerSearchQuery
    ) -> LedgerSearchResult:
        matches = [e for e in entries if self._matches(e, query)]
        ordered = sorted(matches, key=lambda e: (-e.sequence, e.id))
        window = ordered[query.offset : query.offset + max(query.limit, 0)]
        return LedgerSearchResult(entries=tuple(window), total=len(ordered))

    def _matches(self, entry: JournalEntry, query: LedgerSearchQuery) -> bool:
        if query.account_id and entry.account_id != query.account_id:
            return False
        if query.period_key and entry.period_key != query.period_key:
            return False
        if query.entry_types and entry.entry_type not in query.entry_types:
            return False
        if query.directions and entry.direction not in query.directions:
            return False
        if query.sources and entry.source not in query.sources:
            return False
        if query.corrections_only and not entry.is_correction:
            return False
        if query.min_amount_minor is not None and entry.amount.minor < query.min_amount_minor:
            return False
        if query.max_amount_minor is not None and entry.amount.minor > query.max_amount_minor:
            return False
        if query.posted_from is not None and entry.posted_at < query.posted_from:
            return False
        if query.posted_until is not None and entry.posted_at >= query.posted_until:
            return False
        return not (
            query.text
            and not self._text_matches(
                query.text, entry.id, entry.description, entry.source_id, entry.period_key
            )
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        needle = text.lower()
        return any(needle in (f or "").lower() for f in fields)


class LedgerHistoryService:
    """The ledger's own ordered history. Newest first, deterministic on ties."""

    def timeline(self, entries: Sequence, *, limit: int = 100) -> tuple:
        ordered = sorted(entries, key=lambda a: (-a.at, a.id))
        return tuple(ordered[: max(limit, 0)])

    def for_entry(self, entries: Sequence, journal_entry_id: str) -> tuple:
        return tuple(
            sorted(
                (a for a in entries if a.journal_entry_id == journal_entry_id),
                key=lambda a: (a.at, a.id),
            )
        )


# ── diagnostics helpers ─────────────────────────────────────────────────────────


def _d(
    path: str, code: BillingDiagnosticCode, message: str, *, remediation: str = ""
) -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


__all__ = [
    "CHARGE_KIND_TO_ENTRY_TYPE",
    "AccountingPeriodService",
    "AdjustmentService",
    "ChargePostingService",
    "BalanceService",
    "CreditAllocation",
    "CreditApplicationService",
    "CreditLifecycleService",
    "CreditPlan",
    "EntryComposer",
    "LedgerHistoryService",
    "LedgerInspector",
    "LedgerReconciliationService",
    "LedgerReplayService",
    "LedgerSearchService",
    "LedgerValidator",
    "PostingDecision",
    "PostingOutcome",
    "PostingRequest",
    "PostingService",
    "ReversalService",
    "TrialBalanceService",
]
