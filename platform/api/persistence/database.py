"""Database engine + session lifetime (M8.4).

Owns the SQLAlchemy engine and the one `Session` that the repositories read/write and
that the `SqlUnitOfWork` commits/rolls back (commit-as-you-go: the Unit of Work owns the
transaction boundary, reads run outside any explicit transaction). Same shape as the
existing `api/db.py`, isolated to the Customer-Platform persistence.

For SQLite in-memory (tests) a `StaticPool` keeps every access on the one connection so
the schema + data persist for the `Database`'s lifetime. Any SQLAlchemy URL (Postgres/
Supabase) works unchanged. A single shared session is correct for the single-worker
Control Plane (§A9 Stage 1–2); per-request `scoped_session` is the documented Stage-3+
upgrade for multi-worker deployments.
"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .models import CP_TABLES  # noqa: F401 — imported so tables register on metadata


class Database:
    def __init__(self, url: str = "sqlite://", *, echo: bool = False) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_kwargs: dict = {"echo": echo, "connect_args": connect_args}
        # In-memory SQLite: pin one shared connection so the DB survives across access.
        if url in ("sqlite://", "sqlite:///:memory:"):
            engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **engine_kwargs)
        self._session: Session | None = None

    def create_all(self) -> None:
        """Create the `cp_*` tables (idempotent). The migration bootstrap."""
        SQLModel.metadata.create_all(
            self.engine, tables=[t.__table__ for t in CP_TABLES]
        )

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = Session(self.engine)
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
