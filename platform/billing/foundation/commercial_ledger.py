"""Credit & Revenue Ledger domain (M9.13) — financial recording, and nothing else.

Seven milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**. None of them recorded money as owed. This one does.

**The Ledger owns** journal entries, account balances, accounting periods and their close, credits,
credit applications, adjustments, reversals, balance checkpoints, reconciliation, and append-only
ledger history.

**The Ledger owns nothing else.** It calculates no price, resolves no pricing, meters no usage,
evaluates no entitlement, authorizes no request, creates no invoice, executes no payment, and
determines no tax. It reads M9.12's charges and records them; a test asserts this module names none
of those domains' operations.

## What kind of ledger this is

An **accounts-receivable subledger**, deliberately and explicitly. A `JournalEntry` is single-sided:
one account, one direction, one amount. It is not a general ledger and does not enforce ΣDr = ΣCr
across a balanced journal.

That is a decision, not an omission, and it is recorded here rather than left to be discovered. A
general ledger requires a chart of accounts, contra accounts, and a fiscal structure that belongs to
an accounting system VoxVoice does not own and should not reimplement (ADR-0023 §5 — Billing owns
*revenue*, not bookkeeping). `TrialBalance` is the control report that makes this subledger
reconcilable against whatever GL consumes it: totals by entry type, debits and credits separately,
per period. Mapping those totals onto GL accounts is an integration concern at the boundary, and
keeping it at the boundary is what stops a chart of accounts leaking into this plane.

## Four properties that are structural rather than conventional

* **Balances are derived, never stored.** `AccountBalance.of()` is the only way to obtain one, and
  there is no balance field on any stored aggregate. A `BalanceCheckpoint` exists for scale, carries
  a digest, and is *recomputable* — `verify()` proves it still matches the entries it summarises, so
  a stale checkpoint is detectable rather than authoritative.
* **Posting is exactly-once by derived identity.** An entry's id is a hash of what caused it, so
  posting the same charge twice is one row. This is the M9.11/M9.12 device applied where a duplicate
  is *unrecoverable*: a double-billed customer needs a credit note and a conversation, not a re-run.
* **History is never mutated.** There is no update path on an entry, no delete state anywhere, and
  no edge out of a closed period. A correction is a **new** entry — a reversal or an adjustment —
  that references the original, so the original figure and the change to it are separately readable.
* **Entries are totally ordered per account.** A monotonic sequence from a stored counter, not
  `max + 1` and not a timestamp. "What was the balance immediately after entry N?" is an audit
  question, and a float timestamp with ties cannot answer it.

M9.2's `LedgerEntryType`, `LedgerDirection`, `RecognitionState`, and `CreditKind` are **reused
unchanged**, not restated: two answers to "which way does this move the balance?" is exactly the
drift §A2.5 prevents.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .domain import BillingPeriod
from .ledger import CreditKind, LedgerDirection, LedgerEntryType, RecognitionState
from .money import Currency, Money

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_journal_entry_id(
    org_id: str, account_id: str, source: str, source_id: str, entry_type: str
) -> str:
    """The deterministic identity of one financial posting.

    Derived from **what caused it** rather than minted, so posting the same cause twice is one
    entry. Every other Billing aggregate could tolerate a minted id and a de-duplicating read; the
    ledger cannot, because a duplicated posting is the one error that cannot be corrected by
    re-running anything — it needs a reversal, a credit note, and an explanation.

    The corollary matters as much: a *different* cause produces a different id, so a correction is
    structurally incapable of overwriting the entry it corrects."""
    return "jrn_" + _digest(org_id, account_id, source, source_id, entry_type)[:32]


def derive_credit_application_id(credit_id: str, journal_entry_id: str, sequence: int) -> str:
    """Credit applications are replay-stable for the same reason: applying the same credit to the
    same posting twice is one movement, not two."""
    return "cap_" + _digest(credit_id, journal_entry_id, str(sequence))[:32]


def derive_checkpoint_id(org_id: str, account_id: str, period_key: str) -> str:
    """A checkpoint is *derived*, so its id is too — a rebuild replaces rather than accumulating
    beside the figure it supersedes."""
    return "bck_" + _digest(org_id, account_id, period_key)[:32]


def period_key_for(period: BillingPeriod) -> str:
    """The stable name of an accounting period.

    Derived from the window itself rather than from a calendar, because Billing's periods are
    subscription-anchored and need not align with calendar months — and a key that depended on a
    timezone would make the same period resolve differently in two regions."""
    return f"{int(period.start)}-{int(period.end)}"


# ── accounting periods ──────────────────────────────────────────────────────────


class AccountingPeriodState(StrEnum):
    """The lifecycle of one account's accounting period."""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class AccountingPeriodTrigger(StrEnum):
    BEGIN_CLOSE = "begin_close"
    CLOSE = "close"


# The period lifecycle. **No edge out of CLOSED** — a closed period is what a statement was issued
# from, what an auditor examined, and what a statutory filing reported. Reopening it is not a
# recovery procedure; it is how a filed number silently changes. A late fact lands in the next open
# period as an adjustment, which is what `LateEntryDisposition` exists to say.
#
# `CLOSING` exists so that a close can be *begun* — halting new postings — before the totals are
# computed and sealed, without either step being able to happen twice.
ACCOUNTING_PERIOD_LIFECYCLE: StateMachineDefinition = define(
    [
        (
            AccountingPeriodState.OPEN,
            AccountingPeriodTrigger.BEGIN_CLOSE,
            AccountingPeriodState.CLOSING,
        ),
        (AccountingPeriodState.OPEN, AccountingPeriodTrigger.CLOSE, AccountingPeriodState.CLOSED),
        (
            AccountingPeriodState.CLOSING,
            AccountingPeriodTrigger.CLOSE,
            AccountingPeriodState.CLOSED,
        ),
    ]
)

#: The only state in which a period accepts a new posting.
POSTABLE_PERIOD_STATES: frozenset[AccountingPeriodState] = frozenset({AccountingPeriodState.OPEN})


class LateEntryDisposition(StrEnum):
    """Where a fact that arrives after its period closed is recorded.

    `IN_PERIOD` is the normal case. `NEXT_OPEN_PERIOD` is the one that matters: a closed period is
    never reopened, so a late charge becomes an adjustment in the next period that will accept it —
    and the entry records that it did, so the two months reconcile against each other rather than
    appearing to disagree."""

    IN_PERIOD = "in_period"
    NEXT_OPEN_PERIOD = "next_open_period"
    REJECTED_NO_OPEN_PERIOD = "rejected_no_open_period"


@dataclass(frozen=True, slots=True)
class AccountingPeriod:
    """One account's accounting period and what it sealed.

    `close_digest` is the load-bearing field, exactly as M9.11's seal digest is: a deterministic
    hash over the entries the period contained at close, so a replay months later can **prove** it
    reconstructed the same financial state instead of asserting it. Without it, "the ledger is
    deterministically reconstructible" is untestable in production, and a property nobody can test
    in production is a property that decays."""

    id: str
    org_id: str
    account_id: str
    period_key: str
    period: BillingPeriod
    currency: Currency
    state: AccountingPeriodState = AccountingPeriodState.OPEN
    entry_count: int = 0
    debit_minor: int = 0
    credit_minor: int = 0
    closing_balance_minor: int = 0
    close_digest: str = ""
    opened_at: float = 0.0
    closing_at: float | None = None
    closed_at: float | None = None
    closed_by: str = ""
    updated_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.state is AccountingPeriodState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state is AccountingPeriodState.CLOSED

    @property
    def accepts_postings(self) -> bool:
        return self.state in POSTABLE_PERIOD_STATES

    @property
    def closing_balance(self) -> Money:
        return Money(self.closing_balance_minor, self.currency)

    @property
    def debits(self) -> Money:
        return Money(self.debit_minor, self.currency)

    @property
    def credits(self) -> Money:
        return Money(self.credit_minor, self.currency)


# ── postings ────────────────────────────────────────────────────────────────────


class PostingSource(StrEnum):
    """What caused a financial entry. A closed vocabulary, because "why does this line exist?" is
    the first question of every audit and free text would let the interesting answers hide.

    Note what is absent: there is no `PRICE`, no `USAGE`, and no `TAX` source. The ledger records
    what another domain decided; it never records something it decided itself."""

    RATING_RUN = "rating_run"  # a finalized M9.12 charge
    CREDIT_GRANT = "credit_grant"
    CREDIT_APPLICATION = "credit_application"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"
    MANUAL = "manual"  # an operator posting, always actor-attributed
    # M9.16 (additive; recorded as an amendment in the milestone report and DL-0126). Payment
    # execution needs to be a *nameable cause*, and the two available alternatives were both
    # wrong: reusing `MANUAL` would make every captured payment read as operator-entered in the
    # one report where that distinction is the question, and letting Payments post under its own
    # source vocabulary would put a second poster beside this one. These stay consistent with the
    # rule above — the ledger records what another domain decided — because a payment provider
    # moving money is exactly that, and M9.16 owns the evidence while M9.13 keeps owning the fact.
    PAYMENT = "payment"  # a captured external money movement (M9.16)
    REFUND = "refund"  # a completed return of one (M9.16)
    # M9.18 (additive; recorded as an amendment in the milestone report and DL-0129). Same
    # reasoning as the M9.16 members above and the same two rejected alternatives: reusing
    # `MANUAL` would make every recognized period read as operator-entered in the one report
    # where that distinction is the entire question, and letting Recognition post under its own
    # source vocabulary would put a second poster beside this one. It stays consistent with the
    # rule above — the ledger records what another domain decided — because M9.18 decides *when*
    # an amount is earned and M9.13 keeps owning the fact that it was recorded. ADR-0023 RS6 is
    # unchanged: recognition remains a projection over this ledger, not a second ledger.
    REVENUE_RECOGNITION = "revenue_recognition"  # a matured recognition period (M9.18)


@dataclass(frozen=True, slots=True)
class EntryReferences:
    """The lineage that makes an entry explainable down to its cause.

    Every field is a *reference to a fact another domain owns*, never a copy of it. The ledger
    stores no charge breakdown, no usage record, and no price — it stores the ids that let a reader
    walk back to them, which is what keeps this domain a recorder rather than a second copy of the
    revenue chain."""

    subscription_id: str = ""
    rating_run_id: str = ""
    charge_id: str = ""
    invoice_id: str = ""
    payment_id: str = ""
    credit_id: str = ""
    usage_period_key: str = ""

    @property
    def is_traced(self) -> bool:
        """Whether this entry can be walked back to something that caused it."""
        return bool(
            self.rating_run_id
            or self.charge_id
            or self.invoice_id
            or self.payment_id
            or self.credit_id
        )


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """**The** financial fact the Ledger owns: one immutable, sequenced, attributed posting.

    Named `JournalEntry` beside M9.2's `LedgerEntry`, which stays exactly as it is and which the
    minimal M9.5 surface still writes — the same call M9.6 made for `Product` beside `Plan`. The
    difference is real rather than cosmetic: an M9.2 `LedgerEntry` is a bare revenue fact, and this
    one carries the period it belongs to, its position in a total order, the digest of what caused
    it, and the lineage to walk back.

    Immutable by construction. No field here is ever amended, and no repository method exists that
    could. A correction is a new entry pointing at this one.

    `LedgerEntryType` and `LedgerDirection` are M9.2's, reused unchanged: direction is explicit
    rather than inferred from a sign, so a report can never mis-read an amount."""

    id: str
    org_id: str
    account_id: str
    period_key: str
    sequence: int
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
    disposition: LateEntryDisposition = LateEntryDisposition.IN_PERIOD
    description: str = ""
    posted_at: float = 0.0
    posted_by: str = ""

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def signed_minor(self) -> int:
        """This entry's contribution to the balance. Debits increase what the customer owes."""
        return self.amount.minor if self.direction is LedgerDirection.DEBIT else -self.amount.minor

    @property
    def is_reversal(self) -> bool:
        return bool(self.reverses_entry_id)

    @property
    def is_adjustment(self) -> bool:
        return bool(self.adjusts_entry_id)

    @property
    def is_correction(self) -> bool:
        return self.is_reversal or self.is_adjustment

    @property
    def was_late(self) -> bool:
        return self.disposition is not LateEntryDisposition.IN_PERIOD

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of this entry's **financial content**.

        Excludes `posted_at` and `posted_by`, which legitimately differ between an original posting
        and a replay of it, and includes everything a balance depends on. Two runs producing equal
        digests recorded the same money, which is checkable by comparing two strings rather than by
        trusting a narrative."""
        return _digest(
            self.id,
            self.org_id,
            self.account_id,
            self.period_key,
            str(self.sequence),
            self.entry_type.value,
            self.direction.value,
            str(self.amount.minor),
            self.amount.currency.value,
            self.source.value,
            self.source_id,
            self.reverses_entry_id,
            self.adjusts_entry_id,
        )


# ── balances ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """A derived, read-only balance. **Never stored as truth.**

    `of()` is the only constructor, and there is no balance field on any stored aggregate in this
    domain — which is what makes the ledger verifiable rather than merely consistent-looking. A
    stored balance is a second copy of a fact the entries already carry, and the two eventually
    disagree in a way nobody notices until an auditor does.

    Named `AccountBalance` beside M9.2's `LedgerBalance`, which stays exactly as it is."""

    account_id: str
    currency: Currency
    outstanding: Money
    debits: Money
    credits: Money
    entry_count: int
    through_sequence: int = 0
    as_of: float = 0.0

    @classmethod
    def of(
        cls,
        account_id: str,
        currency: Currency,
        entries: tuple[JournalEntry, ...],
        *,
        as_of: float = 0.0,
    ) -> AccountBalance:
        """Fold entries into a balance. Raises on a mixed-currency fold rather than producing a
        meaningless number — ADR-0023 RS5 fixes currency per account, so a mixed set is a data
        integrity failure, not an arithmetic problem to be papered over."""
        mismatched = [e for e in entries if e.amount.currency is not currency]
        if mismatched:
            raise ValueError(
                f"journal entries in {mismatched[0].amount.currency}, expected {currency}"
            )
        debit = sum(e.amount.minor for e in entries if e.direction is LedgerDirection.DEBIT)
        credit = sum(e.amount.minor for e in entries if e.direction is LedgerDirection.CREDIT)
        return cls(
            account_id=account_id,
            currency=currency,
            outstanding=Money(debit - credit, currency),
            debits=Money(debit, currency),
            credits=Money(credit, currency),
            entry_count=len(entries),
            through_sequence=max((e.sequence for e in entries), default=0),
            as_of=as_of,
        )

    @property
    def is_settled(self) -> bool:
        return self.outstanding.is_zero

    @property
    def is_in_credit(self) -> bool:
        """The customer is owed money — a real state, and one a naive unsigned balance hides."""
        return self.outstanding.is_negative

    @property
    def digest(self) -> str:
        return _digest(
            self.account_id,
            self.currency.value,
            str(self.outstanding.minor),
            str(self.debits.minor),
            str(self.credits.minor),
            str(self.entry_count),
            str(self.through_sequence),
        )


@dataclass(frozen=True, slots=True)
class BalanceCheckpoint:
    """A **derived** balance snapshot, kept so a balance read need not fold every entry ever.

    The distinction that keeps this safe: a checkpoint is a *cache with a proof*, never a source of
    truth. `verify()` recomputes from the entries it claims to summarise and says whether it still
    agrees. A checkpoint that has drifted is therefore **detectable**, which is the property that
    makes it acceptable to have at all — the moment a checkpoint becomes authoritative, the ledger
    stops being verifiable."""

    id: str
    org_id: str
    account_id: str
    period_key: str
    currency: Currency
    outstanding_minor: int = 0
    debit_minor: int = 0
    credit_minor: int = 0
    entry_count: int = 0
    through_sequence: int = 0
    digest: str = ""
    computed_at: float = 0.0

    @property
    def outstanding(self) -> Money:
        return Money(self.outstanding_minor, self.currency)

    def verify(self, entries: tuple[JournalEntry, ...]) -> bool:
        """Whether this checkpoint still matches the entries. A `False` here is a financial
        integrity incident, and the reason the digest exists at all."""
        rebuilt = AccountBalance.of(self.account_id, self.currency, entries)
        return rebuilt.digest == self.digest


@dataclass(frozen=True, slots=True)
class TrialBalanceLine:
    """One entry type's totals within a period."""

    entry_type: LedgerEntryType
    debits: Money
    credits: Money
    entry_count: int

    @property
    def net(self) -> Money:
        return self.debits - self.credits


@dataclass(frozen=True, slots=True)
class TrialBalance:
    """The subledger control report: totals by entry type, debits and credits separately.

    This is what makes an AR subledger reconcilable against whatever general ledger consumes it.
    Mapping these totals onto GL accounts is an **integration concern at the boundary** — keeping it
    there is what stops a chart of accounts leaking into a plane that does not own one."""

    org_id: str
    account_id: str
    period_key: str
    currency: Currency
    lines: tuple[TrialBalanceLine, ...] = ()
    opening_balance: Money | None = None
    closing_balance: Money | None = None
    at: float = 0.0

    @property
    def total_debits(self) -> Money:
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + line.debits
        return total

    @property
    def total_credits(self) -> Money:
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + line.credits
        return total

    @property
    def movement(self) -> Money:
        return self.total_debits - self.total_credits

    @property
    def reconciles(self) -> bool:
        """Whether opening + movement equals closing. The one arithmetic identity a subledger must
        satisfy, and the first thing an accountant checks."""
        if self.opening_balance is None or self.closing_balance is None:
            return True
        return self.opening_balance + self.movement == self.closing_balance


# ── credits ─────────────────────────────────────────────────────────────────────


class CreditState(StrEnum):
    """The lifecycle of one credit grant."""

    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CreditTrigger(StrEnum):
    CONSUME = "consume"
    EXPIRE = "expire"
    REVOKE = "revoke"


# The credit lifecycle. Three absences, each a decision:
#
#   * **no edge out of CONSUMED, EXPIRED, or REVOKED.** A credit that has been spent, lapsed, or
#     withdrawn is a historical fact; re-activating one would make "was this credit ever honoured?"
#     unanswerable — precisely the question a disputed balance turns on;
#   * no `EXPIRED → ACTIVE`. Extending a credit is a **new** grant, because collapsing the two makes
#     the original expiry invisible;
#   * **no deletion state at all** (ADR-0023 RS1). Revocation is a state, and the movements that
#     were already applied stay applied — withdrawing a credit does not un-spend it.
CREDIT_LIFECYCLE: StateMachineDefinition = define(
    [
        (CreditState.ACTIVE, CreditTrigger.CONSUME, CreditState.CONSUMED),
        (CreditState.ACTIVE, CreditTrigger.EXPIRE, CreditState.EXPIRED),
        (CreditState.ACTIVE, CreditTrigger.REVOKE, CreditState.REVOKED),
    ]
)

#: States in which a credit may still be applied to a posting.
APPLICABLE_CREDIT_STATES: frozenset[CreditState] = frozenset({CreditState.ACTIVE})


@dataclass(frozen=True, slots=True)
class CommercialCredit:
    """An immutable credit issuance.

    **Carries no balance.** What remains is derived from its applications, so the grant and its
    consumption can never disagree — the same reason `AccountBalance` is derived. Named
    `CommercialCredit` beside M9.2's `CreditGrant`, which stays exactly as it is.

    `CreditKind` is M9.2's, reused unchanged."""

    id: str
    org_id: str
    account_id: str
    code: str
    kind: CreditKind
    amount: Money
    state: CreditState = CreditState.ACTIVE
    granted_at: float = 0.0
    granted_by: str = ""
    expires_at: float | None = None
    source_ref: str = ""
    reason: str = ""
    closed_at: float | None = None
    closed_by: str = ""

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_applicable(self) -> bool:
        return self.state in APPLICABLE_CREDIT_STATES

    def is_live_at(self, ts: float) -> bool:
        """Whether this credit may be applied at an instant. Expiry is a **resolution-time
        property** as well as a state: a credit whose window has passed stops being applicable
        immediately, and the `EXPIRED` state records the administrative fact afterwards. Relying on
        the state alone would let a lapsed credit be spent until a job got round to closing it."""
        return self.is_applicable and (self.expires_at is None or ts < self.expires_at)


@dataclass(frozen=True, slots=True)
class CreditApplication:
    """One append-only application of a credit to a posting.

    There is no `set_balance` and no way to un-apply: reversing a credit application is a **new**
    application of the opposite sign, referencing the one it reverses. That is what keeps "how much
    of this credit is left?" answerable by folding, rather than by trusting a number someone wrote
    down."""

    id: str
    org_id: str
    account_id: str
    credit_id: str
    amount: Money  # positive = applied (consumes credit); negative = released back
    applied_at: float = 0.0
    applied_by: str = ""
    journal_entry_id: str = ""
    invoice_id: str = ""
    reverses_application_id: str = ""
    reason: str = ""

    @property
    def is_release(self) -> bool:
        return self.amount.is_negative


@dataclass(frozen=True, slots=True)
class CreditPosition:
    """A derived view of one credit: granted, applied, remaining. `of()` is the only constructor."""

    credit_id: str
    account_id: str
    currency: Currency
    granted: Money
    applied: Money
    remaining: Money
    application_count: int
    state: CreditState = CreditState.ACTIVE

    @classmethod
    def of(
        cls, credit: CommercialCredit, applications: tuple[CreditApplication, ...]
    ) -> CreditPosition:
        applied = Money.zero(credit.currency)
        for application in applications:
            if application.credit_id != credit.id:
                continue
            if application.amount.currency is not credit.currency:
                raise ValueError(
                    f"credit application in {application.amount.currency}, "
                    f"expected {credit.currency}"
                )
            applied = applied + application.amount
        return cls(
            credit_id=credit.id,
            account_id=credit.account_id,
            currency=credit.currency,
            granted=credit.amount,
            applied=applied,
            remaining=credit.amount - applied,
            application_count=len([a for a in applications if a.credit_id == credit.id]),
            state=credit.state,
        )

    @property
    def is_exhausted(self) -> bool:
        return self.remaining.minor <= 0

    @property
    def is_overdrawn(self) -> bool:
        """More applied than granted — an invariant violation, surfaced rather than clamped."""
        return self.remaining.is_negative


@dataclass(frozen=True, slots=True)
class CreditBalanceView:
    """An account's total available credit, derived from every position it holds."""

    account_id: str
    currency: Currency
    available: Money
    granted: Money
    applied: Money
    credit_count: int
    positions: tuple[CreditPosition, ...] = ()

    @property
    def has_credit(self) -> bool:
        return self.available.minor > 0


# ── replay, reconciliation, inspection ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LedgerReplayResult:
    """The outcome of reconstructing a period's financial state from its entries.

    `matches_close_digest` is the whole point: a closed period recorded a digest over what it
    contained, so a replay months later **proves** it reconstructed identical financial state. A
    replay that could only assert this would be a ritual."""

    org_id: str
    account_id: str
    period_key: str
    entries_replayed: int
    rebuilt_balance_minor: int
    recorded_balance_minor: int
    replay_digest: str = ""
    close_digest: str = ""
    at: float = 0.0

    @property
    def matches_close_digest(self) -> bool:
        """`True` when the period was never closed — there is nothing to contradict. A closed
        period with a differing digest is a financial integrity incident."""
        return not self.close_digest or self.replay_digest == self.close_digest

    @property
    def balances_agree(self) -> bool:
        return self.rebuilt_balance_minor == self.recorded_balance_minor

    @property
    def is_clean(self) -> bool:
        return self.matches_close_digest and self.balances_agree


class LedgerDiscrepancyKind(StrEnum):
    """How the ledger and its sources can disagree. Each is a distinct failure with a distinct
    remedy, which is why they are separate members rather than one `MISMATCH`."""

    UNPOSTED_CHARGE = "unposted_charge"  # rating produced it; the ledger never recorded it
    ORPHAN_ENTRY = "orphan_entry"  # the ledger recorded it; nothing produced it
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE_POSTING = "duplicate_posting"
    SEQUENCE_GAP = "sequence_gap"
    CHECKPOINT_DRIFT = "checkpoint_drift"
    CURRENCY_MISMATCH = "currency_mismatch"


@dataclass(frozen=True, slots=True)
class LedgerDiscrepancy:
    kind: LedgerDiscrepancyKind
    entry_id: str = ""
    charge_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LedgerReconciliation:
    """Does the ledger say what the rating runs say? Asked continuously rather than at close,
    because a gap found at close is a gap that has already reached a statement."""

    org_id: str
    account_id: str
    period_key: str
    charges_examined: int
    entries_examined: int
    discrepancies: tuple[LedgerDiscrepancy, ...] = ()
    at: float = 0.0

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies

    @property
    def unposted_count(self) -> int:
        return len(
            [d for d in self.discrepancies if d.kind is LedgerDiscrepancyKind.UNPOSTED_CHARGE]
        )


@dataclass(frozen=True, slots=True)
class EntryInspection:
    """A structural read of one entry: is it traced, sequenced, and in a period that existed?"""

    entry_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: Money
    sequence: int
    is_traced: bool = True
    is_corrected: bool = False
    corrected_by: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        return self.is_traced


@dataclass(frozen=True, slots=True)
class LedgerInspection:
    """A whole period, inspected. `is_sound` is what closing requires: a period whose entries
    cannot be traced to a cause must not become a statement."""

    org_id: str
    account_id: str
    period_key: str
    state: AccountingPeriodState
    balance: AccountBalance | None = None
    entries: tuple[EntryInspection, ...] = ()
    sequence_gaps: tuple[int, ...] = ()
    checkpoint_consistent: bool = True
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        return (
            all(e.is_sound for e in self.entries)
            and not self.sequence_gaps
            and self.checkpoint_consistent
        )

    @property
    def untraced(self) -> tuple[str, ...]:
        return tuple(e.entry_id for e in self.entries if not e.is_traced)


# ── history + search ────────────────────────────────────────────────────────────


class LedgerAction(StrEnum):
    """What happened in the ledger. The activity log's closed vocabulary."""

    PERIOD_OPENED = "period.opened"
    PERIOD_CLOSING = "period.closing"
    PERIOD_CLOSED = "period.closed"
    ENTRY_POSTED = "entry.posted"
    ENTRY_DUPLICATE_IGNORED = "entry.duplicate_ignored"
    ENTRY_REVERSED = "entry.reversed"
    ENTRY_ADJUSTED = "entry.adjusted"
    RUN_POSTED = "run.posted"
    CREDIT_GRANTED = "credit.granted"
    CREDIT_APPLIED = "credit.applied"
    CREDIT_EXPIRED = "credit.expired"
    CREDIT_REVOKED = "credit.revoked"
    CHECKPOINT_REBUILT = "checkpoint.rebuilt"


@dataclass(frozen=True, slots=True)
class LedgerActivity:
    """One append-only entry in the ledger's own history — including the postings that changed
    nothing. "We saw this charge again and recorded no second entry" is the evidence exactly-once
    worked, and it is exactly what an auditor asks for when a total looks too round."""

    id: str
    org_id: str
    action: LedgerAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    period_key: str = ""
    journal_entry_id: str = ""
    credit_id: str = ""
    amount_minor: int = 0
    currency: Currency | None = None
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LedgerSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same entries and the same
    query always come back in the same order, which is what an accounting screen and a reproducible
    test both need."""

    text: str = ""
    account_id: str = ""
    period_key: str = ""
    entry_types: tuple[LedgerEntryType, ...] = ()
    directions: tuple[LedgerDirection, ...] = ()
    sources: tuple[PostingSource, ...] = ()
    corrections_only: bool = False
    min_amount_minor: int | None = None
    max_amount_minor: int | None = None
    posted_from: float | None = None
    posted_until: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class LedgerSearchResult:
    entries: tuple[JournalEntry, ...] = ()
    total: int = 0


__all__ = [
    "ACCOUNTING_PERIOD_LIFECYCLE",
    "APPLICABLE_CREDIT_STATES",
    "CREDIT_LIFECYCLE",
    "POSTABLE_PERIOD_STATES",
    "AccountBalance",
    "AccountingPeriod",
    "AccountingPeriodState",
    "AccountingPeriodTrigger",
    "BalanceCheckpoint",
    "CommercialCredit",
    "CreditApplication",
    "CreditBalanceView",
    "CreditPosition",
    "CreditState",
    "CreditTrigger",
    "EntryInspection",
    "EntryReferences",
    "JournalEntry",
    "LateEntryDisposition",
    "LedgerAction",
    "LedgerActivity",
    "LedgerDiscrepancy",
    "LedgerDiscrepancyKind",
    "LedgerInspection",
    "LedgerReconciliation",
    "LedgerReplayResult",
    "LedgerSearchQuery",
    "LedgerSearchResult",
    "PostingSource",
    "TrialBalance",
    "TrialBalanceLine",
    "derive_checkpoint_id",
    "derive_credit_application_id",
    "derive_journal_entry_id",
    "period_key_for",
]
