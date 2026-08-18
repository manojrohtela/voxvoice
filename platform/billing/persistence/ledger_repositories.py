"""Credit & Revenue Ledger repositories (M9.13) — additive to the M9.4 persistence layer.

Six SQL implementations of the M9.13 Foundation contracts plus the sequence allocator, reusing the
M9.4 machinery unchanged: the same `_Repo` session handle, the same codec, the same
projection-plus-payload mapping, the same "never commit, never roll back" rule, and the same
org-scoped-by-construction reads (§A11.2).

Three expose **no update or delete method at all**: journal entries, credit applications, and ledger
activity. Not by convention — there is no method to call.

## The concurrency-safe posting

`SqlJournalEntryRepository.append` is where exactly-once posting stops being a hope. The same three
defences M9.11 and M9.12 use, applied where a duplicate is financially unrecoverable:

1. **a derived primary key** — two processes posting the same cause compute the *same* id, so they
   collide rather than both succeeding;
2. **a read-before-write** — the common case (a re-run over an already-posted period) returns the
   stored entry without attempting an insert;
3. **a `SAVEPOINT` around the insert plus an `IntegrityError` catch** — the genuine race, where two
   workers both pass step 2. One wins; the loser's failure is caught, the savepoint is rolled back
   so the *outer* transaction survives, and the winner's row is read back and returned.

Step 3 is the one usually missing. Without the savepoint, a caught `IntegrityError` leaves the
session in a failed state and the whole surrounding transaction — including the activity entry and
the staged event — dies with it. In the ledger that would mean a posting that succeeded for one
worker and destroyed the audit trail for the other.

No repository here folds a balance, decides where an entry lands, computes a close digest, or picks
a credit. Those are Foundation decisions, and a repository that made one would put accounting logic
where nobody would look for it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    AccountingPeriod,
    AccountingPeriodRepository,
    AccountingPeriodState,
    BalanceCheckpoint,
    BalanceCheckpointRepository,
    CommercialCredit,
    CommercialCreditRepository,
    CreditApplication,
    CreditApplicationRepository,
    JournalEntry,
    JournalEntryRepository,
    LedgerActivity,
    LedgerActivityRepository,
)

from . import ledger_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _entry_projection(entry: JournalEntry) -> dict:
    references = entry.references
    return {
        "id": entry.id,
        "org_id": entry.org_id,
        "account_id": entry.account_id,
        "period_key": entry.period_key,
        "sequence": entry.sequence,
        "entry_type": entry.entry_type.value,
        "direction": entry.direction.value,
        "amount_minor": entry.amount.minor,
        "currency": entry.amount.currency.value,
        "signed_minor": entry.signed_minor,
        "source": entry.source.value,
        "source_id": entry.source_id,
        "source_digest": entry.source_digest,
        "subscription_id": references.subscription_id,
        "rating_run_id": references.rating_run_id,
        "charge_id": references.charge_id,
        "invoice_id": references.invoice_id,
        "payment_id": references.payment_id,
        "credit_id": references.credit_id,
        "recognition": entry.recognition.value,
        "reverses_entry_id": entry.reverses_entry_id,
        "adjusts_entry_id": entry.adjusts_entry_id,
        "disposition": entry.disposition.value,
        "description": entry.description,
        "posted_at": entry.posted_at,
        "posted_by": entry.posted_by,
        "digest": entry.digest,
    }


def _period_projection(period: AccountingPeriod) -> dict:
    return {
        "id": period.id,
        "org_id": period.org_id,
        "account_id": period.account_id,
        "period_key": period.period_key,
        "period_start": period.period.start,
        "period_end": period.period.end,
        "currency": period.currency.value,
        "state": period.state.value,
        "entry_count": period.entry_count,
        "debit_minor": period.debit_minor,
        "credit_minor": period.credit_minor,
        "closing_balance_minor": period.closing_balance_minor,
        "close_digest": period.close_digest,
        "opened_at": period.opened_at,
        "closing_at": period.closing_at,
        "closed_at": period.closed_at,
        "closed_by": period.closed_by,
        "updated_at": period.updated_at,
    }


def _checkpoint_projection(checkpoint: BalanceCheckpoint) -> dict:
    return {
        "id": checkpoint.id,
        "org_id": checkpoint.org_id,
        "account_id": checkpoint.account_id,
        "period_key": checkpoint.period_key,
        "currency": checkpoint.currency.value,
        "outstanding_minor": checkpoint.outstanding_minor,
        "debit_minor": checkpoint.debit_minor,
        "credit_minor": checkpoint.credit_minor,
        "entry_count": checkpoint.entry_count,
        "through_sequence": checkpoint.through_sequence,
        "digest": checkpoint.digest,
        "computed_at": checkpoint.computed_at,
    }


def _credit_projection(credit: CommercialCredit) -> dict:
    return {
        "id": credit.id,
        "org_id": credit.org_id,
        "account_id": credit.account_id,
        "code": credit.code,
        "kind": credit.kind.value,
        "amount_minor": credit.amount.minor,
        "currency": credit.amount.currency.value,
        "state": credit.state.value,
        "granted_at": credit.granted_at,
        "granted_by": credit.granted_by,
        "expires_at": credit.expires_at,
        "source_ref": credit.source_ref,
        "reason": credit.reason,
        "closed_at": credit.closed_at,
        "closed_by": credit.closed_by,
    }


def _application_projection(application: CreditApplication) -> dict:
    return {
        "id": application.id,
        "org_id": application.org_id,
        "account_id": application.account_id,
        "credit_id": application.credit_id,
        "amount_minor": application.amount.minor,
        "currency": application.amount.currency.value,
        "applied_at": application.applied_at,
        "applied_by": application.applied_by,
        "journal_entry_id": application.journal_entry_id,
        "invoice_id": application.invoice_id,
        "reverses_application_id": application.reverses_application_id,
        "reason": application.reason,
    }


def _activity_projection(activity: LedgerActivity) -> dict:
    return {
        "id": activity.id,
        "org_id": activity.org_id,
        "action": activity.action.value,
        "actor_id": activity.actor_id,
        "at": activity.at,
        "account_id": activity.account_id,
        "period_key": activity.period_key,
        "journal_entry_id": activity.journal_entry_id,
        "credit_id": activity.credit_id,
        "amount_minor": activity.amount_minor,
        "currency": activity.currency.value if activity.currency else "",
        "reason": activity.reason,
    }


class SqlJournalEntryRepository(_Repo, JournalEntryRepository):
    """**Append-only, exactly-once, totally ordered.**

    See the module docstring for why the savepoint in `append` is the part that matters."""

    def append(self, entry: JournalEntry) -> JournalEntry:
        existing = self._stored(entry.org_id, entry.id)
        if existing is not None:
            # The common case: a retry, or a re-run over an already-posted period. The first write
            # wins and the second call returns it unchanged, so no cause is ever recorded twice.
            return existing
        try:
            # A nested transaction (SAVEPOINT) so that losing the race does not poison the caller's
            # transaction. Without it, the loser's activity entry and staged event roll back too —
            # and in the ledger, losing the audit trail is worse than losing the write.
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingJournalEntryRow(payload=encode(entry), **_entry_projection(entry))
                )
                self._session.flush()
        except IntegrityError:
            # Another worker posted the same cause between our read and our write. That is not an
            # error: it is exactly-once posting working under concurrency.
            stored = self._stored(entry.org_id, entry.id)
            if stored is None:  # pragma: no cover — the constraint that fired guarantees a row
                raise
            return stored
        return entry

    def get(self, org_id: str, entry_id: str) -> JournalEntry | None:
        return self._stored(org_id, entry_id)

    def exists(self, org_id: str, entry_id: str) -> bool:
        return self._row(org_id, entry_id) is not None

    def for_account(
        self, org_id: str, account_id: str, limit: int = 1000
    ) -> tuple[JournalEntry, ...]:
        found = self._all(
            select(rows.BillingJournalEntryRow)
            .where(
                rows.BillingJournalEntryRow.org_id == org_id,
                rows.BillingJournalEntryRow.account_id == account_id,
            )
            .order_by(rows.BillingJournalEntryRow.sequence, rows.BillingJournalEntryRow.id)
            .limit(limit)
        )
        return tuple(from_row(JournalEntry, r) for r in found)

    def for_period(self, org_id: str, account_id: str, period_key: str) -> tuple[JournalEntry, ...]:
        found = self._all(
            select(rows.BillingJournalEntryRow)
            .where(
                rows.BillingJournalEntryRow.org_id == org_id,
                rows.BillingJournalEntryRow.account_id == account_id,
                rows.BillingJournalEntryRow.period_key == period_key,
            )
            .order_by(rows.BillingJournalEntryRow.sequence, rows.BillingJournalEntryRow.id)
        )
        return tuple(from_row(JournalEntry, r) for r in found)

    def since_sequence(
        self, org_id: str, account_id: str, sequence: int
    ) -> tuple[JournalEntry, ...]:
        found = self._all(
            select(rows.BillingJournalEntryRow)
            .where(
                rows.BillingJournalEntryRow.org_id == org_id,
                rows.BillingJournalEntryRow.account_id == account_id,
                rows.BillingJournalEntryRow.sequence > sequence,
            )
            .order_by(rows.BillingJournalEntryRow.sequence, rows.BillingJournalEntryRow.id)
        )
        return tuple(from_row(JournalEntry, r) for r in found)

    def reversals_of(self, org_id: str, entry_id: str) -> tuple[JournalEntry, ...]:
        found = self._all(
            select(rows.BillingJournalEntryRow).where(
                rows.BillingJournalEntryRow.org_id == org_id,
                rows.BillingJournalEntryRow.reverses_entry_id == entry_id,
            )
        )
        return tuple(from_row(JournalEntry, r) for r in found)

    def all(self, org_id: str, limit: int = 1000) -> tuple[JournalEntry, ...]:
        found = self._all(
            select(rows.BillingJournalEntryRow)
            .where(rows.BillingJournalEntryRow.org_id == org_id)
            .order_by(
                rows.BillingJournalEntryRow.posted_at.desc(),  # type: ignore[attr-defined]
                rows.BillingJournalEntryRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(JournalEntry, r) for r in found)

    def next_sequence(self, org_id: str, account_id: str) -> int:
        """Allocate from the durable counter. See `BillingLedgerSequenceRow` for why a stored
        counter rather than a derived maximum."""
        row = self._one(
            select(rows.BillingLedgerSequenceRow).where(
                rows.BillingLedgerSequenceRow.org_id == org_id,
                rows.BillingLedgerSequenceRow.account_id == account_id,
            )
        )
        if row is None:
            row = rows.BillingLedgerSequenceRow(
                org_id=org_id, account_id=account_id, last_sequence=0
            )
            self._add(row)
        row.last_sequence += 1
        self._session.flush()
        return row.last_sequence

    # ── internals ───────────────────────────────────────────────────────────────

    def _row(self, org_id: str, entry_id: str):
        return self._one(
            select(rows.BillingJournalEntryRow).where(
                rows.BillingJournalEntryRow.org_id == org_id,
                rows.BillingJournalEntryRow.id == entry_id,
            )
        )

    def _stored(self, org_id: str, entry_id: str) -> JournalEntry | None:
        row = self._row(org_id, entry_id)
        return from_row(JournalEntry, row) if row else None


class SqlAccountingPeriodRepository(_Repo, AccountingPeriodRepository):
    """Accounting periods. `replace` advances state; the close digest never changes once written,
    which the lifecycle — not this class — enforces."""

    def add(self, period: AccountingPeriod) -> AccountingPeriod:
        self._add(
            rows.BillingAccountingPeriodRow(payload=encode(period), **_period_projection(period))
        )
        return period

    def get(self, org_id: str, period_id: str) -> AccountingPeriod | None:
        row = self._one(
            select(rows.BillingAccountingPeriodRow).where(
                rows.BillingAccountingPeriodRow.org_id == org_id,
                rows.BillingAccountingPeriodRow.id == period_id,
            )
        )
        return from_row(AccountingPeriod, row) if row else None

    def by_key(self, org_id: str, account_id: str, period_key: str) -> AccountingPeriod | None:
        row = self._key_row(org_id, account_id, period_key)
        return from_row(AccountingPeriod, row) if row else None

    def open_for(self, org_id: str, account_id: str) -> AccountingPeriod | None:
        """The period currently accepting postings — where a late fact lands.

        Ordered by window start so "the next open period" is deterministic when more than one
        happens to be open, rather than depending on insertion order."""
        found = self._all(
            select(rows.BillingAccountingPeriodRow)
            .where(
                rows.BillingAccountingPeriodRow.org_id == org_id,
                rows.BillingAccountingPeriodRow.account_id == account_id,
                rows.BillingAccountingPeriodRow.state == AccountingPeriodState.OPEN.value,
            )
            .order_by(
                rows.BillingAccountingPeriodRow.period_start,
                rows.BillingAccountingPeriodRow.id,
            )
        )
        return from_row(AccountingPeriod, found[0]) if found else None

    def for_account(self, org_id: str, account_id: str) -> tuple[AccountingPeriod, ...]:
        found = self._all(
            select(rows.BillingAccountingPeriodRow)
            .where(
                rows.BillingAccountingPeriodRow.org_id == org_id,
                rows.BillingAccountingPeriodRow.account_id == account_id,
            )
            .order_by(
                rows.BillingAccountingPeriodRow.period_start,
                rows.BillingAccountingPeriodRow.id,
            )
        )
        return tuple(from_row(AccountingPeriod, r) for r in found)

    def replace(self, period: AccountingPeriod) -> AccountingPeriod:
        existing = self._key_row(period.org_id, period.account_id, period.period_key)
        if existing is None:
            return self.add(period)
        update_row(existing, period, _period_projection(period))
        self._session.flush()
        return period

    def _key_row(self, org_id: str, account_id: str, period_key: str):
        return self._one(
            select(rows.BillingAccountingPeriodRow).where(
                rows.BillingAccountingPeriodRow.org_id == org_id,
                rows.BillingAccountingPeriodRow.account_id == account_id,
                rows.BillingAccountingPeriodRow.period_key == period_key,
            )
        )


class SqlBalanceCheckpointRepository(_Repo, BalanceCheckpointRepository):
    """Derived checkpoints. The only replaceable rows in this milestone, and only because a
    checkpoint is a cache with a proof rather than a financial fact."""

    def upsert(self, checkpoint: BalanceCheckpoint) -> BalanceCheckpoint:
        existing = self._one(
            select(rows.BillingBalanceCheckpointRow).where(
                rows.BillingBalanceCheckpointRow.org_id == checkpoint.org_id,
                rows.BillingBalanceCheckpointRow.id == checkpoint.id,
            )
        )
        if existing is None:
            self._add(
                rows.BillingBalanceCheckpointRow(
                    payload=encode(checkpoint), **_checkpoint_projection(checkpoint)
                )
            )
            return checkpoint
        update_row(existing, checkpoint, _checkpoint_projection(checkpoint))
        self._session.flush()
        return checkpoint

    def get(self, org_id: str, account_id: str, period_key: str) -> BalanceCheckpoint | None:
        row = self._one(
            select(rows.BillingBalanceCheckpointRow).where(
                rows.BillingBalanceCheckpointRow.org_id == org_id,
                rows.BillingBalanceCheckpointRow.account_id == account_id,
                rows.BillingBalanceCheckpointRow.period_key == period_key,
            )
        )
        return from_row(BalanceCheckpoint, row) if row else None

    def latest(self, org_id: str, account_id: str) -> BalanceCheckpoint | None:
        found = self._all(
            select(rows.BillingBalanceCheckpointRow)
            .where(
                rows.BillingBalanceCheckpointRow.org_id == org_id,
                rows.BillingBalanceCheckpointRow.account_id == account_id,
            )
            .order_by(
                rows.BillingBalanceCheckpointRow.through_sequence.desc(),  # type: ignore[attr-defined]
                rows.BillingBalanceCheckpointRow.id,
            )
        )
        return from_row(BalanceCheckpoint, found[0]) if found else None


class SqlCommercialCreditRepository(_Repo, CommercialCreditRepository):
    """Credit grants. No delete: a credit that reduced an issued invoice must remain readable."""

    def add(self, credit: CommercialCredit) -> CommercialCredit:
        self._add(
            rows.BillingCommercialCreditRow(payload=encode(credit), **_credit_projection(credit))
        )
        return credit

    def get(self, org_id: str, credit_id: str) -> CommercialCredit | None:
        row = self._row(org_id, credit_id)
        return from_row(CommercialCredit, row) if row else None

    def by_code(self, org_id: str, code: str) -> CommercialCredit | None:
        row = self._one(
            select(rows.BillingCommercialCreditRow).where(
                rows.BillingCommercialCreditRow.org_id == org_id,
                rows.BillingCommercialCreditRow.code == code,
            )
        )
        return from_row(CommercialCredit, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[CommercialCredit, ...]:
        found = self._all(
            select(rows.BillingCommercialCreditRow)
            .where(
                rows.BillingCommercialCreditRow.org_id == org_id,
                rows.BillingCommercialCreditRow.account_id == account_id,
            )
            .order_by(
                rows.BillingCommercialCreditRow.granted_at,
                rows.BillingCommercialCreditRow.id,
            )
        )
        return tuple(from_row(CommercialCredit, r) for r in found)

    def all(self, org_id: str) -> tuple[CommercialCredit, ...]:
        found = self._all(
            select(rows.BillingCommercialCreditRow)
            .where(rows.BillingCommercialCreditRow.org_id == org_id)
            .order_by(rows.BillingCommercialCreditRow.code)
        )
        return tuple(from_row(CommercialCredit, r) for r in found)

    def replace(self, credit: CommercialCredit) -> CommercialCredit:
        existing = self._row(credit.org_id, credit.id)
        if existing is None:
            return self.add(credit)
        update_row(existing, credit, _credit_projection(credit))
        self._session.flush()
        return credit

    def _row(self, org_id: str, credit_id: str):
        return self._one(
            select(rows.BillingCommercialCreditRow).where(
                rows.BillingCommercialCreditRow.org_id == org_id,
                rows.BillingCommercialCreditRow.id == credit_id,
            )
        )


class SqlCreditApplicationRepository(_Repo, CreditApplicationRepository):
    """**Append-only** credit movements, idempotent on the derived id."""

    def append(self, application: CreditApplication) -> CreditApplication:
        existing = self._row(application.org_id, application.id)
        if existing is not None:
            return from_row(CreditApplication, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCreditApplicationRow(
                        payload=encode(application), **_application_projection(application)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(application.org_id, application.id)
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(CreditApplication, winner)
        return application

    def for_credit(self, org_id: str, credit_id: str) -> tuple[CreditApplication, ...]:
        found = self._all(
            select(rows.BillingCreditApplicationRow)
            .where(
                rows.BillingCreditApplicationRow.org_id == org_id,
                rows.BillingCreditApplicationRow.credit_id == credit_id,
            )
            .order_by(
                rows.BillingCreditApplicationRow.applied_at,
                rows.BillingCreditApplicationRow.id,
            )
        )
        return tuple(from_row(CreditApplication, r) for r in found)

    def for_account(self, org_id: str, account_id: str) -> tuple[CreditApplication, ...]:
        found = self._all(
            select(rows.BillingCreditApplicationRow)
            .where(
                rows.BillingCreditApplicationRow.org_id == org_id,
                rows.BillingCreditApplicationRow.account_id == account_id,
            )
            .order_by(
                rows.BillingCreditApplicationRow.applied_at,
                rows.BillingCreditApplicationRow.id,
            )
        )
        return tuple(from_row(CreditApplication, r) for r in found)

    def _row(self, org_id: str, application_id: str):
        return self._one(
            select(rows.BillingCreditApplicationRow).where(
                rows.BillingCreditApplicationRow.org_id == org_id,
                rows.BillingCreditApplicationRow.id == application_id,
            )
        )


class SqlLedgerActivityRepository(_Repo, LedgerActivityRepository):
    """Append-only ledger history."""

    def append(self, activity: LedgerActivity) -> LedgerActivity:
        self._add(
            rows.BillingLedgerActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[LedgerActivity, ...]:
        statement = select(rows.BillingLedgerActivityRow).where(
            rows.BillingLedgerActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingLedgerActivityRow.account_id == account_id)
        found = self._all(
            statement.order_by(
                rows.BillingLedgerActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingLedgerActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(LedgerActivity, r) for r in found)

    def for_entry(self, org_id: str, journal_entry_id: str) -> tuple[LedgerActivity, ...]:
        found = self._all(
            select(rows.BillingLedgerActivityRow)
            .where(
                rows.BillingLedgerActivityRow.org_id == org_id,
                rows.BillingLedgerActivityRow.journal_entry_id == journal_entry_id,
            )
            .order_by(rows.BillingLedgerActivityRow.at, rows.BillingLedgerActivityRow.id)
        )
        return tuple(from_row(LedgerActivity, r) for r in found)
