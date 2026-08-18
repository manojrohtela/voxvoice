"""Migration 0031 — Financial Investigation & Audit Evidence (M9.21).

Declarative schema only. Additively creates the seven `cp_billing_*` investigation tables:
investigations, the timeline, the evidence chain of custody, questions, observations, conclusions
and audit packages. `ALTER`s nothing, touches no Epic 5–8 table, and touches no existing Billing
table — including every M9.19 and M9.20 table, which M9.21 reads and never writes.

ADR-0023 **PY5** holds at a third altitude. M9.19 made "a divergence is never auto-resolved"
structural for one finding and M9.20 for a group of them; these tables make the *investigation* of
one non-falsifiable: closure is reachable only through a recorded conclusion, evidence is
append-only and content-addressed, and the audit package that says so is immutable. Nothing here
lets a divergence be declared handled by anybody simply saying it was.

**Nothing is seeded.** No investigation, no question template, no evidence source, no package. A
fresh installation has investigated nothing until an incident exists and somebody opens a case —
the same posture 0025 takes on tax rates, 0026 on payment routes, 0027 on collection strategies,
0028 on recognition policies, 0029 on reconciliation and 0030 on correlation. A seeded
investigation would assert that somebody had looked at something on an installation where nothing
had happened.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..investigation_models import INVESTIGATION_TABLES

VERSION = "0031"
DESCRIPTION = (
    "cp_billing_* financial investigation & audit evidence (investigations, timeline, evidence "
    "chain of custody, questions, observations, conclusions, audit packages)"
)

TABLES = INVESTIGATION_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0030 are, and more so.

    A schema rollback is never a licence to discard the record of *who examined a financial
    divergence, what evidence they held, what they concluded and on what basis*. These rows are the
    chain of custody an external examiner reads. Dropping them would leave the corrected figures in
    place with no trace of who checked them — which reads as concealment rather than as a
    rollback."""
    return None
