"""Production Unit of Work (M9.4) — the durable, transactional implementation.

Implements the **existing** Application abstraction (`billing.application.UnitOfWork`) with
no interface change, so every M9.3 use case gains real atomicity without one line of it
changing — the payoff for having written the boundary interface-first.

This closes the limitation M9.3 documented honestly: there, rollback discarded the staged
events but there were no repository writes to undo. Here both move together.

    commit    → commit the DB transaction, **then** publish the staged events
    rollback  → roll the DB transaction back **and** discard the staged events

The ordering is deliberate and not symmetric: events are published only *after* durable
success, because an application event announcing a charge that no longer exists is worse
than a late one. `__exit__` never suppresses an exception, so the dispatcher's rejection
audit and the caller both still see it.
"""

from __future__ import annotations

from billing.application import (
    ApplicationEvent,
    ApplicationEventEmitter,
    UnitOfWork,
    UnitOfWorkClosedError,
    UnitOfWorkFactory,
)

from .database import BillingDatabase


class SqlBillingUnitOfWork(UnitOfWork):
    """One transactional scope over the Billing database session + an application-event
    outbox. Repositories participate by writing into the same session; they never end it."""

    def __init__(self, database: BillingDatabase, emitter: ApplicationEventEmitter) -> None:
        self._db = database
        self._emitter = emitter
        self._outbox: list[ApplicationEvent] = []
        self._closed = False
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        if self._closed:
            raise UnitOfWorkClosedError("unit of work has already been closed")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False  # never suppress — the exception must propagate

    def stage(self, event: ApplicationEvent) -> None:
        if self._closed:
            raise UnitOfWorkClosedError("cannot stage into a closed unit of work")
        self._outbox.append(event)

    def commit(self) -> None:
        if self._closed:
            return
        self._db.session.commit()  # durable first
        for event in self._outbox:  # announce second
            self._emitter.emit(event)
        self._outbox.clear()
        self._closed = True
        self._committed = True

    def rollback(self) -> None:
        if self._closed:
            return
        # Undo the repository writes and discard the staged events together: an
        # orchestration that failed must leave no trace claiming it succeeded.
        self._db.session.rollback()
        self._outbox.clear()
        self._closed = True

    @property
    def staged(self) -> tuple[ApplicationEvent, ...]:
        return tuple(self._outbox)

    @property
    def committed(self) -> bool:
        return self._committed


class BillingUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, database: BillingDatabase, emitter: ApplicationEventEmitter) -> None:
        self._db = database
        self._emitter = emitter

    def new(self) -> UnitOfWork:
        return SqlBillingUnitOfWork(self._db, self._emitter)
