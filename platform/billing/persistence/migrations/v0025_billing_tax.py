"""Migration 0025 — Tax Determination Engine (M9.15).

Declarative schema only. Additively creates the seven `cp_billing_*` tax tables: jurisdictions,
classifications, nexus, exemptions, declared rates, assessments, and the audit trail. `ALTER`s
nothing, touches no Epic 5–8 table, and touches no existing Billing table — including M9.2's
`cp_billing_tax_profile` and `cp_billing_tax_determination`, which stay exactly as they are.

**Nothing is seeded.** No jurisdiction, no classification, and above all no rate. A fresh
installation determines no tax at all until an operator declares one, which is the shape ADR-0023
TX1 requires: determination is a rented edge, evidence is an owned fact. A migration that inserted a
GST or VAT percentage would make this plane the owner of a number it has no authority over, and that
number would still be here when the jurisdiction changes it.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..tax_models import TAX_TABLES

VERSION = "0025"
DESCRIPTION = (
    "cp_billing_* tax determination (jurisdictions, classifications, nexus, exemptions, "
    "declared rate evidence, assessments, audit trail)"
)

TABLES = TAX_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023 and 0024 are.

    A schema rollback is never a licence to discard the evidence of what tax was charged, to whom,
    under which authority, and on what date — nor the rate declarations that authorised it. ADR-0023
    TX2: a determination is recorded immutably, and that applies to the schema that holds it."""
    return None
