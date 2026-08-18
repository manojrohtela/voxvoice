"""Migration 0030 — Financial Integrity Correlation & Audit (M9.20).

Declarative schema only. Additively creates the six `cp_billing_*` incident tables: incidents,
finding links, the audit trail, merge decisions, correlation corrections, and repair references.
`ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing table — including every
M9.19 table, which M9.20 reads and never writes.

ADR-0023 **PY5** stays true at a second altitude. M9.19 made "a divergence is never auto-resolved"
structural for one finding; these tables make it structural for a *group* of them: an incident
reaches `resolved` only through `verifying`, and the trail that says so is append-only.

**Nothing is seeded.** No incident, no correlation rule, no severity override. A fresh installation
correlates nothing until reconciliation produces findings — the same posture 0025 takes on tax
rates, 0026 on payment routes, 0027 on collection strategies, 0028 on recognition policies and 0029
on reconciliation. A seeded incident would assert that Billing had noticed something on an
installation where nothing had happened.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..incident_models import INCIDENT_TABLES

VERSION = "0030"
DESCRIPTION = (
    "cp_billing_* financial integrity correlation (incidents, finding links, audit trail, "
    "merges, correlation corrections, repair references)"
)

TABLES = INCIDENT_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0029 are.

    A schema rollback is never a licence to discard the record of *what Billing noticed, when, who
    was told, and on what evidence it was declared resolved*. These rows are the audit trail an
    external examiner reads to reconstruct how a financial divergence was handled. Dropping them
    would leave the corrected figures in place with no trace of the correction — which reads as
    concealment rather than as a rollback."""
    return None
