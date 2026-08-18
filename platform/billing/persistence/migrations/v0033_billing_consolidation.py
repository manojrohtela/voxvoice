"""Migration 0033 — Billing Period Scope & Consolidation (M9.23).

Declarative schema only. Additively creates the ten `cp_billing_*` consolidation tables: scope
definitions, scope snapshots, scope members, consolidation periods, consolidation runs, pinned
sources, exceptions, validations, results and the trail. `ALTER`s nothing, touches no Epic 5–8
table, and touches no existing Billing table — including `cp_billing_commercial_account`, whose
hierarchy every scope resolution reads and none of this writes.

ADR-0023 **PY5** holds at a fifth altitude. M9.19 made "a divergence is never auto-resolved"
structural for one finding, M9.20 for a group, M9.21 for the investigation of one, M9.22 for the
filing; these tables make it structural for the **scope**. A consolidation reaches `finalized` only
from `ready`, which is reachable only from a validation that passed — and an unattributable source,
an unreadable one, a period a domain cannot answer for, or a currency nobody approved a conversion
rule for each stop it. Nothing here lets a missing account be consolidated as zero.

**Nothing is seeded.** No scope definition, no period, no default membership. A fresh installation
consolidates nothing until an operator publishes a scope — the same posture 0025 takes on tax
rates, 0026 on payment routes, 0027 on collection strategies, 0028 on recognition policies, 0029 on
reconciliation, 0030 on correlation, 0031 on investigation and 0032 on reporting. A seeded scope
would be this milestone deciding which accounts belong in somebody's consolidated financial
picture, which is exactly the authority it does not have.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..consolidation_models import CONSOLIDATION_TABLES
from ..database import BillingDatabase

VERSION = "0033"
DESCRIPTION = (
    "cp_billing_* period scope & consolidation (scope definitions, scope snapshots, scope "
    "members, consolidation periods, consolidation runs, pinned sources, exceptions, "
    "validations, results, trail)"
)

TABLES = CONSOLIDATION_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0032 are.

    A schema rollback is never a licence to discard the record of which accounts a consolidated
    financial artifact covered, at which source revisions, and which accounts it could not include.
    Dropping these rows would leave the underlying figures in place with no trace of what was
    consolidated or on what basis — which reads as concealment rather than as a rollback."""
    return None
