"""Ledger factories + repository contracts (M9.13).

`LedgerFactory` is the one place an accounting period, a journal entry, a credit, a credit
application, a balance checkpoint, or an activity entry is constructed, following the M9.2 factory
discipline exactly: validate structurally, **fail fast** on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and clock
seams, and emit the corresponding `billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all. Two are easy to
miss and both are deliberate:

* a **duplicate** posting emits `billing.ledger.entry_duplicate_ignored`, not `entry_posted` — "we
  saw this cause again and recorded no second entry" is a distinct fact, and it is the evidence
  exactly-once posting worked;
* a **deferred** posting emits `billing.ledger.entry_deferred` — a January charge recorded in
  February is not the same fact as one recorded in January, and an auditor comparing two months
  needs to see which it was.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

---

## Why journal entry ids are derived rather than minted

Every other Billing aggregate before M9.11 drew its id from the `IdGenerator` seam. A journal entry
derives it from `(org, account, source, source id, entry type)` — the cause, not a counter. This is
the M9.11/M9.12 device applied where it matters most.

* **A retry cannot double-post.** Same cause, same id, unique constraint, one row. Every other
  domain in this plane can recover from a duplicate by re-running; the ledger cannot. A duplicated
  posting needs a reversal, a credit note, and a conversation with a customer who has already paid.
* **Replay is a no-op rather than a rewrite.** Reprocessing a period recomputes the same ids and
  writes nothing new, which is what makes replay safe to run in production rather than only in a
  drill.
* **A correction cannot overwrite what it corrects.** A reversal's source is the original's id, so
  it derives a *different* entry id by construction. There is no code path that could edit the
  entry it is correcting, because the id it would need is never the id it computes.

## Why the sequence comes from a stored counter

`JournalEntryRepository.next_sequence` allocates from a durable per-account counter, not from
`max(sequence) + 1`. A derived maximum hands a number back after a rollback, and two entries that
have both legitimately occupied position 47 make "what was the balance after entry 47?" a question
with two answers. `BalanceService.sequence_gaps` surfaces an allocated-but-uncommitted number rather
than hiding it — a gap is unexplained, and unexplained is what an audit looks for.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_ledger import (
    AccountingPeriod,
    AccountingPeriodState,
    BalanceCheckpoint,
    CommercialCredit,
    CreditApplication,
    CreditState,
    JournalEntry,
    LedgerAction,
    LedgerActivity,
    derive_credit_application_id,
    period_key_for,
)
from .domain import BillingPeriod
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .ledger import CreditKind
from .ledger_rules import (
    EntryComposer,
    LedgerValidator,
    PostingDecision,
    PostingOutcome,
    PostingRequest,
)
from .money import Currency, Money
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_ACCOUNTING_PERIOD = "acp"
PREFIX_COMMERCIAL_CREDIT = "ccr"
PREFIX_LEDGER_ACTIVITY = "lac"

#: Which `billing.*` fact each posting outcome publishes. A table so "exactly one event per business
#: fact" is checkable by iteration rather than asserted in prose.
POSTING_EVENTS: dict[PostingOutcome, BillingEventType] = {
    PostingOutcome.POSTED: BillingEventType.JOURNAL_ENTRY_POSTED,
    PostingOutcome.DUPLICATE: BillingEventType.JOURNAL_ENTRY_DUPLICATE_IGNORED,
    PostingOutcome.DEFERRED: BillingEventType.JOURNAL_ENTRY_DEFERRED,
}


@dataclass(frozen=True, slots=True)
class LedgerFactory:
    """Constructs ledger aggregates from injected seams. Stateless; holds references only."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: LedgerValidator = LedgerValidator()
    composer: EntryComposer = EntryComposer()

    # ── accounting periods ──────────────────────────────────────────────────────

    def open_period(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        currency: Currency,
        actor_id: str = "",
    ) -> AccountingPeriod:
        now = self.clock.now()
        opened = AccountingPeriod(
            id=self.ids.new(PREFIX_ACCOUNTING_PERIOD),
            org_id=org_id,
            account_id=account_id,
            period_key=period_key_for(period),
            period=period,
            currency=currency,
            state=AccountingPeriodState.OPEN,
            opened_at=now,
            updated_at=now,
        )
        self._emit(
            BillingEventType.ACCOUNTING_PERIOD_OPENED,
            org_id,
            opened.id,
            actor_id,
            account_id=account_id,
            period_key=opened.period_key,
        )
        return opened

    def emit_period_transition(
        self, period: AccountingPeriod, *, event_type: BillingEventType, actor_id: str = "", **data
    ) -> None:
        """The public emission path for a lifecycle move an orchestrator performed.

        Public deliberately: at M9.10 the Application was found reaching into a factory's private
        emitter, and a seam that must be reached around is not a seam."""
        self._emit(
            event_type,
            period.org_id,
            period.id,
            actor_id,
            account_id=period.account_id,
            period_key=period.period_key,
            state=period.state.value,
            **data,
        )

    # ── postings ────────────────────────────────────────────────────────────────

    def compose_entry(
        self,
        request: PostingRequest,
        decision: PostingDecision,
        *,
        sequence: int,
        actor_id: str = "",
    ) -> JournalEntry:
        """Compose the immutable entry an accepted decision produced. The *deciding* happened in
        `PostingService`; this only builds what it decided."""
        return self.composer.compose(
            request,
            decision,
            sequence=sequence,
            posted_at=self.clock.now(),
            posted_by=actor_id,
        )

    def emit_posting(
        self,
        decision: PostingDecision,
        entry: JournalEntry | None,
        *,
        org_id: str,
        actor_id: str = "",
    ) -> None:
        """Emit exactly the one fact this outcome represents.

        A rejected posting emits nothing: there is no business fact in "we declined to record
        something", only an error the caller must handle."""
        event_type = POSTING_EVENTS.get(decision.outcome)
        if event_type is None:
            return
        self._emit(
            event_type,
            org_id,
            decision.entry_id,
            actor_id,
            account_id=entry.account_id if entry else "",
            period_key=decision.landing_period_key,
            amount_minor=entry.amount.minor if entry else 0,
            currency=entry.amount.currency.value if entry else "",
            direction=entry.direction.value if entry else "",
            disposition=decision.disposition.value,
        )

    def emit_correction(
        self, entry: JournalEntry, *, event_type: BillingEventType, actor_id: str = "", **data
    ) -> None:
        self._emit(
            event_type,
            entry.org_id,
            entry.id,
            actor_id,
            account_id=entry.account_id,
            corrects=entry.reverses_entry_id or entry.adjusts_entry_id,
            amount_minor=entry.amount.minor,
            **data,
        )

    # ── credits ─────────────────────────────────────────────────────────────────

    def grant_credit(
        self,
        *,
        org_id: str,
        account_id: str,
        code: str,
        kind: CreditKind,
        amount: Money,
        expires_at: float | None = None,
        source_ref: str = "",
        reason: str = "",
        actor_id: str = "",
    ) -> CommercialCredit:
        now = self.clock.now()
        credit = CommercialCredit(
            id=self.ids.new(PREFIX_COMMERCIAL_CREDIT),
            org_id=org_id,
            account_id=account_id,
            code=code,
            kind=kind,
            amount=amount,
            state=CreditState.ACTIVE,
            granted_at=now,
            granted_by=actor_id,
            expires_at=expires_at,
            source_ref=source_ref,
            reason=reason,
        )
        self._fail_fast(self.validator.validate_credit(credit))
        self._emit(
            BillingEventType.CREDIT_GRANTED,
            org_id,
            credit.id,
            actor_id,
            account_id=account_id,
            code=code,
            amount_minor=amount.minor,
            currency=amount.currency.value,
        )
        return credit

    def apply_credit(
        self,
        credit: CommercialCredit,
        *,
        amount: Money,
        journal_entry_id: str = "",
        invoice_id: str = "",
        sequence: int = 1,
        reason: str = "",
        actor_id: str = "",
    ) -> CreditApplication:
        """One append-only application. The id is derived, so applying the same credit to the same
        posting twice is one movement rather than two."""
        application = CreditApplication(
            id=derive_credit_application_id(credit.id, journal_entry_id, sequence),
            org_id=credit.org_id,
            account_id=credit.account_id,
            credit_id=credit.id,
            amount=amount,
            applied_at=self.clock.now(),
            applied_by=actor_id,
            journal_entry_id=journal_entry_id,
            invoice_id=invoice_id,
            reason=reason,
        )
        self._emit(
            BillingEventType.CREDIT_CONSUMED,
            credit.org_id,
            application.id,
            actor_id,
            account_id=credit.account_id,
            credit_id=credit.id,
            amount_minor=amount.minor,
            journal_entry_id=journal_entry_id,
        )
        return application

    def emit_credit_transition(
        self, credit: CommercialCredit, *, event_type: BillingEventType, actor_id: str = "", **data
    ) -> None:
        self._emit(
            event_type,
            credit.org_id,
            credit.id,
            actor_id,
            account_id=credit.account_id,
            code=credit.code,
            state=credit.state.value,
            **data,
        )

    # ── checkpoints + history ───────────────────────────────────────────────────

    def emit_checkpoint(self, checkpoint: BalanceCheckpoint, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.BALANCE_CHECKPOINT_REBUILT,
            checkpoint.org_id,
            checkpoint.id,
            actor_id,
            account_id=checkpoint.account_id,
            period_key=checkpoint.period_key,
            outstanding_minor=checkpoint.outstanding_minor,
            digest=checkpoint.digest,
        )

    def activity(
        self,
        *,
        org_id: str,
        action: LedgerAction,
        actor_id: str = "",
        account_id: str = "",
        period_key: str = "",
        journal_entry_id: str = "",
        credit_id: str = "",
        amount: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> LedgerActivity:
        return LedgerActivity(
            id=self.ids.new(PREFIX_LEDGER_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self.clock.now(),
            account_id=account_id,
            period_key=period_key,
            journal_entry_id=journal_entry_id,
            credit_id=credit_id,
            amount_minor=amount.minor if amount else 0,
            currency=amount.currency if amount else None,
            reason=reason,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject_id: str, actor_id: str, **data
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class JournalEntryRepository(ABC):
    """**Append-only, exactly-once, and totally ordered per account.**

    There is no update method and no delete method — not by convention, but because none exists to
    call. `append` is the whole write surface, and it must be safe to call twice with the same entry
    from two processes: implementations return the *stored* entry on conflict rather than raising,
    which is what turns a retried posting into one financial fact rather than two."""

    @abstractmethod
    def append(self, entry: JournalEntry) -> JournalEntry:
        """Store, or return what is already stored for this cause. Never raises on a duplicate;
        never writes a second row for one cause."""

    @abstractmethod
    def get(self, org_id: str, entry_id: str) -> JournalEntry | None: ...
    @abstractmethod
    def exists(self, org_id: str, entry_id: str) -> bool: ...
    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 1000
    ) -> tuple[JournalEntry, ...]:
        """Every entry for an account, in sequence order. The balance folds this."""

    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, period_key: str
    ) -> tuple[JournalEntry, ...]: ...
    @abstractmethod
    def since_sequence(
        self, org_id: str, account_id: str, sequence: int
    ) -> tuple[JournalEntry, ...]:
        """Entries after a position — what a checkpoint is rolled forward from, so a balance read
        need not fold every entry ever recorded."""

    @abstractmethod
    def reversals_of(self, org_id: str, entry_id: str) -> tuple[JournalEntry, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 1000) -> tuple[JournalEntry, ...]: ...
    @abstractmethod
    def next_sequence(self, org_id: str, account_id: str) -> int:
        """The next position in this account's total order, from a **stored counter**.

        Not `max(sequence) + 1`: a derived maximum hands a number back after a rollback, and two
        entries that both occupied position 47 make the balance history ambiguous."""


class AccountingPeriodRepository(ABC):
    """Accounting periods. `replace` exists because a period's *state* advances; the close digest
    it carries never changes once written, which the lifecycle enforces."""

    @abstractmethod
    def add(self, period: AccountingPeriod) -> AccountingPeriod: ...
    @abstractmethod
    def get(self, org_id: str, period_id: str) -> AccountingPeriod | None: ...
    @abstractmethod
    def by_key(self, org_id: str, account_id: str, period_key: str) -> AccountingPeriod | None: ...
    @abstractmethod
    def open_for(self, org_id: str, account_id: str) -> AccountingPeriod | None:
        """The period currently accepting postings — where a late fact lands."""

    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[AccountingPeriod, ...]: ...
    @abstractmethod
    def replace(self, period: AccountingPeriod) -> AccountingPeriod:
        """State only. A closed period's totals are a historical fact."""


class BalanceCheckpointRepository(ABC):
    """Derived balance checkpoints. Replaceable by construction — they are recomputable from
    entries at any time, and a rebuilt checkpoint must *replace* rather than accumulate beside the
    figure it supersedes."""

    @abstractmethod
    def upsert(self, checkpoint: BalanceCheckpoint) -> BalanceCheckpoint: ...
    @abstractmethod
    def get(self, org_id: str, account_id: str, period_key: str) -> BalanceCheckpoint | None: ...
    @abstractmethod
    def latest(self, org_id: str, account_id: str) -> BalanceCheckpoint | None: ...


class CommercialCreditRepository(ABC):
    """Credit grants. Org-scoped by construction (§A11.2). No delete: a credit that reduced an
    issued invoice must remain readable."""

    @abstractmethod
    def add(self, credit: CommercialCredit) -> CommercialCredit: ...
    @abstractmethod
    def get(self, org_id: str, credit_id: str) -> CommercialCredit | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> CommercialCredit | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[CommercialCredit, ...]: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[CommercialCredit, ...]: ...
    @abstractmethod
    def replace(self, credit: CommercialCredit) -> CommercialCredit:
        """State only — consuming, expiring, revoking. The granted amount is never amended."""


class CreditApplicationRepository(ABC):
    """**Append-only** credit movements. Releasing a credit is a new application of the opposite
    sign, never the removal of one."""

    @abstractmethod
    def append(self, application: CreditApplication) -> CreditApplication: ...
    @abstractmethod
    def for_credit(self, org_id: str, credit_id: str) -> tuple[CreditApplication, ...]: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[CreditApplication, ...]: ...


class LedgerActivityRepository(ABC):
    """Append-only ledger history."""

    @abstractmethod
    def append(self, activity: LedgerActivity) -> LedgerActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[LedgerActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""

    @abstractmethod
    def for_entry(self, org_id: str, journal_entry_id: str) -> tuple[LedgerActivity, ...]: ...


__all__ = [
    "PREFIX_ACCOUNTING_PERIOD",
    "PREFIX_COMMERCIAL_CREDIT",
    "PREFIX_LEDGER_ACTIVITY",
    "POSTING_EVENTS",
    "AccountingPeriodRepository",
    "BalanceCheckpointRepository",
    "CommercialCreditRepository",
    "CreditApplicationRepository",
    "JournalEntryRepository",
    "LedgerActivityRepository",
    "LedgerFactory",
]
