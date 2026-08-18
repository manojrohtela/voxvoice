"""Billing Account repositories (M9.8) — additive to the M9.4 persistence layer.

Three SQL implementations of the M9.8 Foundation contracts, reusing the M9.4 machinery
unchanged: the same `_Repo` session handle, the same codec, the same projection-plus-payload
mapping, the same "never commit, never roll back" rule, and the same org-scoped-by-construction
reads (§A11.2).

The activity store is append-only and exposes no update or delete method at all.

No repository here resolves a hierarchy, detects a cycle, or decides a payer. `children_of` is a
single indexed row selection; walking the tree, finding a cycle, and choosing the ultimate payer
are Foundation decisions, and a repository that made them would be putting commercial logic
where nobody would look for it.
"""

from __future__ import annotations

from sqlmodel import select

from billing.foundation import (
    AccountActivity,
    AccountActivityRepository,
    AccountNumberAllocator,
    CommercialAccount,
    CommercialAccountRepository,
    format_account_number,
)

from . import account_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _account_projection(a: CommercialAccount) -> dict:
    address = a.addresses.billing_address
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_number": a.account_number,
        "display_name": a.display_name,
        "currency": a.currency.value,
        "kind": a.kind.value,
        "state": a.state.value,
        "commercial_status": a.commercial_status.value,
        "parent_account_id": a.parent_account_id,
        "payer_account_id": a.payer_account_id,
        "legal_name": a.legal_entity.legal_name,
        "registration_number": a.legal_entity.registration_number,
        "tax_identifier": a.legal_entity.tax_identifier,
        "country": (address.country if address else a.legal_entity.incorporation_country),
        "risk_rating": a.credit.risk_rating.value,
        "credit_hold": a.credit.credit_hold,
        "tax_profile_id": a.tax_profile_id,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
        "closed_at": a.closed_at,
    }


def _activity_projection(a: AccountActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "from_state": a.from_state,
        "to_state": a.to_state,
    }


class SqlCommercialAccountRepository(_Repo, CommercialAccountRepository):
    """Upsert on the primary key — `add` means *store this account*, which is what makes
    `replace` and `add` the same operation for a whole aggregate."""

    def add(self, account: CommercialAccount) -> CommercialAccount:
        existing = self._one(
            select(rows.BillingCommercialAccountRow).where(
                rows.BillingCommercialAccountRow.org_id == account.org_id,
                rows.BillingCommercialAccountRow.id == account.id,
            )
        )
        if existing is not None:
            update_row(existing, account, _account_projection(account))
            self._session.flush()
            return account
        self._add(
            rows.BillingCommercialAccountRow(
                payload=encode(account), **_account_projection(account)
            )
        )
        return account

    def get(self, org_id: str, account_id: str) -> CommercialAccount | None:
        row = self._one(
            select(rows.BillingCommercialAccountRow).where(
                rows.BillingCommercialAccountRow.org_id == org_id,
                rows.BillingCommercialAccountRow.id == account_id,
            )
        )
        return from_row(CommercialAccount, row) if row else None

    def by_number(self, org_id: str, account_number: str) -> CommercialAccount | None:
        row = self._one(
            select(rows.BillingCommercialAccountRow).where(
                rows.BillingCommercialAccountRow.org_id == org_id,
                rows.BillingCommercialAccountRow.account_number == account_number,
            )
        )
        return from_row(CommercialAccount, row) if row else None

    def all(self, org_id: str) -> tuple[CommercialAccount, ...]:
        found = self._all(
            select(rows.BillingCommercialAccountRow)
            .where(rows.BillingCommercialAccountRow.org_id == org_id)
            .order_by(rows.BillingCommercialAccountRow.account_number)
        )
        return tuple(from_row(CommercialAccount, r) for r in found)

    def children_of(self, org_id: str, parent_account_id: str) -> tuple[CommercialAccount, ...]:
        """One indexed row selection. Walking the tree is the Foundation's."""
        found = self._all(
            select(rows.BillingCommercialAccountRow)
            .where(
                rows.BillingCommercialAccountRow.org_id == org_id,
                rows.BillingCommercialAccountRow.parent_account_id == parent_account_id,
            )
            .order_by(rows.BillingCommercialAccountRow.account_number)
        )
        return tuple(from_row(CommercialAccount, r) for r in found)

    def replace(self, account: CommercialAccount) -> CommercialAccount:
        return self.add(account)


class SqlAccountActivityRepository(_Repo, AccountActivityRepository):
    """Append-only account history."""

    def append(self, activity: AccountActivity) -> AccountActivity:
        self._add(
            rows.BillingAccountActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[AccountActivity, ...]:
        statement = select(rows.BillingAccountActivityRow).where(
            rows.BillingAccountActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingAccountActivityRow.account_id == account_id)
        found = self._all(
            statement.order_by(
                rows.BillingAccountActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingAccountActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(AccountActivity, r) for r in found)


class SqlAccountNumberAllocator(_Repo, AccountNumberAllocator):
    """A per-organization monotonic sequence.

    A stored counter rather than `max(account_number) + 1`: deleting a draft account must not
    make its number available again, and a derived maximum would hand it straight back."""

    def next_number(self, org_id: str) -> str:
        row = self._one(
            select(rows.BillingAccountNumberRow).where(
                rows.BillingAccountNumberRow.org_id == org_id
            )
        )
        if row is None:
            row = rows.BillingAccountNumberRow(org_id=org_id, last_sequence=0)
            self._add(row)
        row.last_sequence += 1
        self._session.flush()
        return format_account_number(row.last_sequence)


ACCOUNT_SQL_REPOSITORIES = (
    (CommercialAccountRepository, SqlCommercialAccountRepository),
    (AccountActivityRepository, SqlAccountActivityRepository),
    (AccountNumberAllocator, SqlAccountNumberAllocator),
)
