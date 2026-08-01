"""Production Unit of Work (M8.4) — the durable, transactional implementation.

Implements the **existing** Application abstraction (`api.application.UnitOfWork`) with no
interface change. It backs the boundary with a real database transaction over the shared
`Database.session` (commit-as-you-go): on a clean exit it commits the DB transaction **and
then** publishes the staged application events; on any raised exception it rolls the DB
transaction back **and** discards the staged events. Repository writes made inside the
block are therefore atomic — they become durable together on commit and vanish together on
rollback. This closes the honest limitation M8.3 documented for the in-memory Unit of Work
(events rolled back but data did not); here both do.
"""

from __future__ import annotations

from api.application import ApplicationEvent, ApplicationEventEmitter, UnitOfWork, UnitOfWorkFactory

from .database import Database


class SqlUnitOfWork(UnitOfWork):
    """One transactional scope over the database session + an application-event outbox.
    Never suppresses an exception (returns False from `__exit__`)."""

    def __init__(self, database: Database, emitter: ApplicationEventEmitter) -> None:
        self._db = database
        self._emitter = emitter
        self._outbox: list[ApplicationEvent] = []
        self._closed = False

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False  # never suppress — the exception must propagate

    def stage(self, event: ApplicationEvent) -> None:
        if self._closed:
            raise RuntimeError("unit of work is already closed")
        self._outbox.append(event)

    def commit(self) -> None:
        if self._closed:
            return
        # Commit the DB transaction first; publish events only after durable success.
        self._db.session.commit()
        for event in self._outbox:
            self._emitter.emit(event)
        self._outbox.clear()
        self._closed = True

    def rollback(self) -> None:
        if self._closed:
            return
        # Undo the repository writes and discard the staged events together.
        self._db.session.rollback()
        self._outbox.clear()
        self._closed = True


class SqlUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, database: Database, emitter: ApplicationEventEmitter) -> None:
        self._db = database
        self._emitter = emitter

    def new(self) -> UnitOfWork:
        return SqlUnitOfWork(self._db, self._emitter)
