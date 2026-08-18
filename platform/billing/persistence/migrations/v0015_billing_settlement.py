"""Migration 0015 — Billing settlement, tax, contracts, and the fact stream (M9.4).

Declarative schema only. Creates the tables that record *how money actually moved and what
was agreed*: payments, stored instruments, dunning, reconciliation findings, refunds,
disputes, tax profiles and determinations, contracts, licenses, and the append-only
`billing.*` event stream.

Additive and idempotent. Provider-side state is never stored as truth here — a payment row
records Billing's view plus the provider reports it has verified (ADR-0023 PY6).
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..models import (
    BillingContractRow,
    BillingDisputeRow,
    BillingDunningRow,
    BillingEventRow,
    BillingLicenseRow,
    BillingPaymentInstrumentRow,
    BillingPaymentRow,
    BillingReconciliationRow,
    BillingRefundRow,
    BillingTaxDeterminationRow,
    BillingTaxProfileRow,
)

VERSION = "0015"
DESCRIPTION = "cp_billing_* settlement, tax, contracts, licenses, and the billing event stream"

TABLES = (
    BillingPaymentRow,
    BillingPaymentInstrumentRow,
    BillingDunningRow,
    BillingReconciliationRow,
    BillingRefundRow,
    BillingDisputeRow,
    BillingTaxProfileRow,
    BillingTaxDeterminationRow,
    BillingContractRow,
    BillingLicenseRow,
    BillingEventRow,
)


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op: settlement and tax records are audit evidence and are kept."""
    return None
