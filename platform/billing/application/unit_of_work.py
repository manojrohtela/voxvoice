"""The Unit of Work (M9.3) — transaction boundary + application-event outbox.

The Application owns transaction boundaries; the **Foundation is unaware transactions
exist** (nothing in `billing.foundation` mentions commit, rollback, or a session). A use
case runs inside `with uow:`; application events it stages are published atomically on
commit and discarded on rollback. A clean exit commits; an exception rolls back and
re-raises — the Unit of Work never suppresses an exception, so the dispatcher's rejection
audit and the caller both still see it.

**Honest scope, named rather than hidden (M9.2/M9.3):** no persistence exists yet, so this
provides the boundary abstraction and the event outbox, not durable rollback of repository
writes. Real atomicity arrives with the DB-backed Unit of Work at **M9.4**, and every use
case is already written against this interface, so it gains that atomicity for free —
interface-first, exactly as the Foundation's repository interfaces were.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .errors import UnitOfWorkClosedError
from .events import ApplicationEvent, ApplicationEventEmitter


class UnitOfWork(ABC):
    """A single transactional scope. Use as a context manager."""

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

    @property
    @abstractmethod
    def staged(self) -> tuple[ApplicationEvent, ...]:
        """The not-yet-published outbox — observable for assertions, never for delivery."""


class StagingUnitOfWork(UnitOfWork):
    """The default Unit of Work: owns the boundary and the application-event outbox.

    Events are invisible until commit. Rollback discards them: an orchestration that failed
    must leave no trace claiming it succeeded."""

    def __init__(self, emitter: ApplicationEventEmitter) -> None:
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
        for event in self._outbox:
            self._emitter.emit(event)
        self._outbox.clear()
        self._closed = True
        self._committed = True

    def rollback(self) -> None:
        self._outbox.clear()
        self._closed = True

    @property
    def staged(self) -> tuple[ApplicationEvent, ...]:
        return tuple(self._outbox)

    @property
    def committed(self) -> bool:
        return self._committed


class UnitOfWorkFactory(ABC):
    @abstractmethod
    def new(self) -> UnitOfWork: ...


class StagingUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, emitter: ApplicationEventEmitter) -> None:
        self._emitter = emitter

    def new(self) -> UnitOfWork:
        return StagingUnitOfWork(self._emitter)
