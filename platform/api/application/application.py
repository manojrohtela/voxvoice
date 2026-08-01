"""ApplicationFactory + the `Application` facade (M8.3).

The composition root of the Application Layer — the one place the buses, use cases, Unit
of Work factory, authorization orchestrator, and application event emitter are wired over
a `Foundation`. `.default()` builds a fully in-memory, deterministic Application on top of
`Foundation.default(...)` with shared seams, so a client dispatches commands/queries
through `app.commands` / `app.queries` with no other setup.

A production wiring swaps the seams (real clock/ids), the Unit of Work factory (a
DB-backed one), and the Foundation's repositories — without touching a use case, a
handler, or the Foundation domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.foundation import (
    Clock,
    DefaultIdGenerator,
    Foundation,
    FoundationFactory,
    IdGenerator,
    SystemClock,
)

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import ApplicationEventEmitter, RecordingApplicationEventEmitter
from .handlers import register_command_handlers, register_query_handlers
from .unit_of_work import InMemoryUnitOfWorkFactory, UnitOfWorkFactory
from .use_cases import CommandUseCases, QueryUseCases


@dataclass(frozen=True, slots=True)
class Application:
    """The assembled Application Layer: the two buses (the CQRS surface a future API
    dispatches through), the use cases, the authorization orchestrator, the Unit of Work
    factory, the application event emitter (audit trail), and the underlying Foundation."""

    commands: CommandBus
    queries: QueryBus
    command_use_cases: CommandUseCases
    query_use_cases: QueryUseCases
    authorization: AuthorizationOrchestrator
    uow_factory: UnitOfWorkFactory
    emitter: ApplicationEventEmitter
    foundation: Foundation


class ApplicationFactory:
    @staticmethod
    def create(
        *,
        foundation: Foundation,
        ids: IdGenerator,
        clock: Clock,
        emitter: ApplicationEventEmitter,
        uow_factory: UnitOfWorkFactory,
    ) -> Application:
        authz = AuthorizationOrchestrator(foundation.authorizer)
        command_uc = CommandUseCases(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock,
        )
        query_uc = QueryUseCases(foundation=foundation)
        command_bus = CommandBus(emitter=emitter, ids=ids, clock=clock)
        query_bus = QueryBus()
        register_command_handlers(command_bus, command_uc)
        register_query_handlers(query_bus, query_uc)
        # M8.6 — additive Organization Lifecycle + Invitation use cases/handlers.
        from .org_lifecycle import build_and_register as _register_org_lifecycle

        _register_org_lifecycle(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.7 — additive Workspace Lifecycle use cases/handlers.
        from .workspace_lifecycle import build_and_register as _register_workspace_lifecycle

        _register_workspace_lifecycle(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.8 — additive Project Lifecycle use cases/handlers.
        from .project_lifecycle import build_and_register as _register_project_lifecycle

        _register_project_lifecycle(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.9 — additive Audit & Activity queries (recent/user activity).
        from .activity import build_and_register as _register_activity

        _register_activity(foundation=foundation, query_bus=query_bus)
        # M8.10 — additive Agent Builder use cases/handlers.
        from .agent_builder import build_and_register as _register_agent_builder

        _register_agent_builder(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.11 — additive Tool Framework use cases/handlers.
        from .tool_framework import build_and_register as _register_tool_framework

        _register_tool_framework(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.12 — additive Conversation Runtime use cases/handlers.
        from .runtime import build_and_register as _register_runtime

        _register_runtime(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.13 — additive Conversation Playground use cases/handlers.
        from .playground import build_and_register as _register_playground

        _register_playground(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.14 — additive provider execution use cases/handlers.
        from .execution import build_and_register as _register_execution

        _register_execution(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.15 — additive Knowledge Tool use cases/handlers.
        from .knowledge import build_and_register as _register_knowledge

        _register_knowledge(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.16 — additive Deployment Actuator use cases/handlers (ADR-0022).
        from .deployment import build_and_register as _register_deployment

        _register_deployment(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        # M8.17 — additive Observability use cases/handlers.
        from .observability import build_and_register as _register_observability

        _register_observability(
            foundation=foundation, authz=authz, uow_factory=uow_factory,
            ids=ids, clock=clock, command_bus=command_bus, query_bus=query_bus,
        )
        return Application(
            commands=command_bus,
            queries=query_bus,
            command_use_cases=command_uc,
            query_use_cases=query_uc,
            authorization=authz,
            uow_factory=uow_factory,
            emitter=emitter,
            foundation=foundation,
        )

    @staticmethod
    def default(
        *,
        foundation: Foundation | None = None,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
    ) -> Application:
        """A fully in-memory, deterministic Application. Pass a `SequentialIdGenerator`
        + `FixedClock` (shared with the Foundation) for byte-reproducible tests."""
        ids = ids or DefaultIdGenerator()
        clock = clock or SystemClock()
        foundation = foundation or FoundationFactory.default(ids=ids, clock=clock)
        emitter = RecordingApplicationEventEmitter()
        return ApplicationFactory.create(
            foundation=foundation,
            ids=ids,
            clock=clock,
            emitter=emitter,
            uow_factory=InMemoryUnitOfWorkFactory(emitter),
        )
