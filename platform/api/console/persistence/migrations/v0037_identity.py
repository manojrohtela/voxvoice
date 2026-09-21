"""Migration 0037 — console identity (M12.2).

Additively creates `dp_user`, `dp_membership` and `dp_console_session`. It alters nothing and
touches no M11, Epic 8, Billing or Epic 10 table. Idempotent: each table is created only if
absent. On a database built by `create_all` it is a no-op (the 0034 precedent).
"""

from __future__ import annotations

from sqlalchemy import inspect

from ..models import ConsoleSessionRow, MembershipRow, UserRow

VERSION = "0037"
DESCRIPTION = "dp_user, dp_membership, dp_console_session (console identity)"

TABLES = (UserRow, MembershipRow, ConsoleSessionRow)


def upgrade(engine) -> None:
    existing = set(inspect(engine).get_table_names())
    for table in TABLES:
        if table.__tablename__ not in existing:
            table.__table__.create(engine, checkfirst=True)


def downgrade(engine) -> None:
    """No-op. Dropping identity tables would lock every user out; a rollback of the code
    tolerates these tables because nothing outside the console reads them."""
    return None
