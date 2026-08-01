"""Transaction abstraction — the Unit of Work (M8.3).

The Application Layer owns transaction boundaries (the Foundation owns domain behaviour,
not transactions). A use case runs inside `with uow:`; application events it produces are
**staged** and published atomically on commit, or discarded on rollback. A clean exit
commits; an exception rolls back and re-raises.

Honest scope (named, not hidden): with the M8.2 **in-memory** Foundation repositories,
the Unit of Work provides the transaction *boundary abstraction* + an application-event
**outbox** (events become observable only on commit — the §A5 durable-outbox shape). It
does not roll back the Foundation's in-memory writes, because those repositories are not
transactional. Durable, repository-level atomic rollback lands when a DB-backed Unit of
Work + repositories replace the in-memory default (M8.1 plan). The interface is defined
now so every use case is written against it and gains real atomicity for free later —
interface-first, exactly as the Foundation's repository interfaces were.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .events import ApplicationEvent, ApplicationEventEmitter


class UnitOfWork(ABC):
    """A single transactional scope. Use as a context manager; commit on clean exit,
    rollback on exception."""

    @abstractmethod
    def __enter__(self) -> UnitOfWork: ...
    @abstractmethod
    def __exit__(self, exc_type, exc, tb) -> bool: ...
    @abstractmethod
    def stage(self, event: ApplicationEvent) -> None:
        """Buffer an application event to publish atomically on commit."""
    @abstractmethod
    def commit(self) -> None: ...
    @abstractmethod
    def rollback(self) -> None: ...


class InMemoryUnitOfWork(UnitOfWork):
    """The default Unit of Work over in-memory repositories: owns the boundary + an
    application-event outbox. Never suppresses an exception (returns False from
    `__exit__`), so the dispatcher's rejection audit and the caller both still see it."""

    def __init__(self, emitter: ApplicationEventEmitter) -> None:
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
        for event in self._outbox:
            self._emitter.emit(event)
        self._outbox.clear()
        self._closed = True

    def rollback(self) -> None:
        # Discard staged application events; in-memory Foundation writes are not
        # rolled back (documented limitation until the DB-backed UoW lands).
        self._outbox.clear()
        self._closed = True


class UnitOfWorkFactory(ABC):
    @abstractmethod
    def new(self) -> UnitOfWork: ...


class InMemoryUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, emitter: ApplicationEventEmitter) -> None:
        self._emitter = emitter

    def new(self) -> UnitOfWork:
        return InMemoryUnitOfWork(self._emitter)
