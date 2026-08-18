"""Migration 0032 — Billing Compliance & Regulatory Reporting (M9.22).

Declarative schema only. Additively creates the ten `cp_billing_*` reporting tables: definitions,
periods, input manifests, pinned sources, report runs, exceptions, validations, snapshots,
submission packages and the trail. `ALTER`s nothing, touches no Epic 5–8 table, and touches no
existing Billing table — including every table M9.22 reads and never writes.

ADR-0023 **PY5** holds at a fourth altitude. M9.19 made "a divergence is never auto-resolved"
structural for one finding, M9.20 for a group of them, M9.21 for the investigation of one; these
tables make it structural for the *filing*. A report reaches `finalized` only from `ready`, which is
reachable only from a validation that passed — and a blocking exception, an unreadable required
source or a mandatory field with no value each stop it. Nothing here lets missing evidence be filed
as zero.

**Nothing is seeded.** No report definition, no reporting period, no jurisdiction. A fresh
installation reports nothing until an operator publishes a definition — the same posture 0025 takes
on tax rates, 0026 on payment routes, 0027 on collection strategies, 0028 on recognition policies,
0029 on reconciliation, 0030 on correlation and 0031 on investigation. A seeded definition would be
this milestone deciding what a regulator requires, which is exactly the authority it does not have.

Idempotent: each table is created only if absent.
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..database import BillingDatabase
from ..reporting_models import REPORTING_TABLES

VERSION = "0032"
DESCRIPTION = (
    "cp_billing_* compliance & regulatory reporting (definitions, periods, input manifests, "
    "pinned sources, report runs, exceptions, validations, snapshots, submission packages, trail)"
)

TABLES = REPORTING_TABLES


def upgrade(database: BillingDatabase) -> None:
    existing = set(inspect(database.engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(database.engine, checkfirst=True)


def downgrade(database: BillingDatabase) -> None:
    """No-op, for the same reason migrations 0023–0031 are, and more so.

    A schema rollback is never a licence to discard filings somebody submitted to a regulator, the
    inputs they were derived from, or the record of who approved them. Dropping these rows would
    leave the underlying figures in place with no trace of what was reported or on what basis —
    which reads as concealment rather than as a rollback."""
    return None
