"""Event-spine database engine + session lifetime (M10.2).

Owns the SQLAlchemy engine and the one `Session` the durable log reads and writes, in the
shape `api/persistence/database.py` (M8.4) and `billing/persistence/database.py` (M9.4)
already established.

`create_all()` creates **only** `evt_*` tables. The events plane brings its **own**
`Database` rather than importing the Control Plane's or Billing's, for the ADR-0034 DS5
reasons: importing `api` would invert the dependency graph the whole seam exists to
protect, and a plane whose storage lives in another plane's package cannot later be
extracted as a *deployment* decision rather than a re-architecture. Pointing this at the
same URL as the Control Plane is a deployment choice; nothing here assumes it.

For in-memory SQLite a `StaticPool` pins one connection so schema and data survive for the
object's lifetime. Any SQLAlchemy URL (Postgres/Supabase) works unchanged (ADR-0034 DS10).

**Restart-durability tests must use a file-backed URL** (`sqlite:///<tmp_path>/x.db`), never
in-memory. An in-memory database is pinned to this object's lifetime by construction, so a
"restart" against it cannot fail — the test would pass while proving nothing (ADR-0034 DS10,
alternative C6). `SqlDurableEventLog`'s restart test uses `tmp_path` for exactly this reason.
"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .models import EVENT_TABLES


class EventDatabase:
    def __init__(self, url: str = "sqlite://", *, echo: bool = False) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_kwargs: dict = {"echo": echo, "connect_args": connect_args}
        # In-memory SQLite: pin one shared connection so the DB survives across access.
        if url in ("sqlite://", "sqlite:///:memory:"):
            engine_kwargs["poolclass"] = StaticPool
        self.url = url
        self.engine = create_engine(url, **engine_kwargs)
        self._session: Session | None = None

    def create_all(self) -> None:
        """Create the `evt_*` tables (idempotent). Touches no other plane's table."""
        SQLModel.metadata.create_all(self.engine, tables=[t.__table__ for t in EVENT_TABLES])

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = Session(self.engine)
        return self._session

    def close(self) -> None:
        """Close the session and dispose the engine, releasing the file handle.

        Disposing matters here in a way it does not for a long-lived control plane: the
        restart tests close a database and reopen the same SQLite file, and a pooled
        connection left open is exactly how a "restart" test quietly keeps talking to the
        old process's state."""
        if self._session is not None:
            self._session.close()
            self._session = None
        self.engine.dispose()
