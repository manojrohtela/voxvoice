"""Command & Query buses (M9.3) — the CQRS dispatch surface.

Handlers are registered by message type at wiring time; a client dispatches a message and
the bus routes it to its single handler.

The `CommandBus` additionally owns **audit orchestration** as a cross-cutting concern:
every dispatched command emits exactly one `use_case.completed` (on success) or
`use_case.rejected` (on any raised error), so auditing is uniform and cannot be forgotten
per handler. The rejection audit is emitted **directly**, never through the rolled-back
Unit of Work — a rejection is itself a real fact that must survive the failed transaction.

Query dispatch is pure routing: reads are neither audited nor transactional.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from billing.foundation import Clock, IdGenerator

from .errors import DuplicateHandlerError, NoHandlerError
from .events import (
    PREFIX_APP_EVENT,
    ApplicationEvent,
    ApplicationEventEmitter,
    ApplicationEventType,
)

CommandHandler = Callable[[Any], Any]
QueryHandler = Callable[[Any], Any]


class CommandBus:
    """Routes a command to its registered handler and audits the outcome."""

    def __init__(self, *, emitter: ApplicationEventEmitter, ids: IdGenerator, clock: Clock) -> None:
        self._handlers: dict[type, CommandHandler] = {}
        self._emitter = emitter
        self._ids = ids
        self._clock = clock

    def register(self, command_type: type, handler: CommandHandler) -> None:
        if command_type in self._handlers:
            raise DuplicateHandlerError(f"handler already registered for {command_type.__name__}")
        self._handlers[command_type] = handler

    @property
    def registered(self) -> tuple[type, ...]:
        return tuple(self._handlers)

    def dispatch(self, command: Any) -> Any:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise NoHandlerError(f"no handler for command {type(command).__name__}")
        name = type(command).__name__
        actor = getattr(command, "actor_id", "") or ""
        org_id = getattr(command, "org_id", "") or ""
        try:
            result = handler(command)
        except Exception as exc:  # audit the rejection, then re-raise unchanged
            self._audit(
                ApplicationEventType.USE_CASE_REJECTED,
                name,
                org_id,
                actor,
                outcome="rejected",
                error=type(exc).__name__,
            )
            raise
        self._audit(
            ApplicationEventType.USE_CASE_COMPLETED, name, org_id, actor, outcome="completed"
        )
        return result

    def _audit(
        self,
        type_: ApplicationEventType,
        use_case: str,
        org_id: str,
        actor: str,
        *,
        outcome: str,
        **data: object,
    ) -> None:
        self._emitter.emit(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT),
                type=type_,
                ts=self._clock.now(),
                use_case=use_case,
                outcome=outcome,
                org_id=org_id,
                actor=actor,
                data=dict(data),
            )
        )


class QueryBus:
    """Routes a query to its registered handler. Read-only: no audit, no transaction."""

    def __init__(self) -> None:
        self._handlers: dict[type, QueryHandler] = {}

    def register(self, query_type: type, handler: QueryHandler) -> None:
        if query_type in self._handlers:
            raise DuplicateHandlerError(f"handler already registered for {query_type.__name__}")
        self._handlers[query_type] = handler

    @property
    def registered(self) -> tuple[type, ...]:
        return tuple(self._handlers)

    def dispatch(self, query: Any) -> Any:
        handler = self._handlers.get(type(query))
        if handler is None:
            raise NoHandlerError(f"no handler for query {type(query).__name__}")
        return handler(query)
