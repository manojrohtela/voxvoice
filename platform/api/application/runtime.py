"""Conversation Runtime application layer (M8.12) — additive.

Commands, queries, use cases and handler registration for the Conversation Runtime vertical
slice, in a separate module so the M8.3 modules stay untouched (mirrors `agent_builder.py`).
Use cases coordinate the additive Foundation `conversation_runtime` service — no domain logic
is duplicated; the deterministic planning pipeline lives entirely in the Foundation. Session
mutations run inside a Unit of Work (so the durable audit rows commit atomically with the
session write); inspection queries are read-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from api.foundation import Foundation, SessionSummaryView, SessionView

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateSession:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    runtime_variables: dict | None = None
    correlation_id: str = ""
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class _SessionOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    session_id: str


class ResumeSession(_SessionOp): ...
class CloseSession(_SessionOp): ...
class ExpireSession(_SessionOp): ...


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetSession:
    org_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ListSessions:
    org_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class GetRuntimeState:
    org_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class GetExecutionPlan:
    org_id: str
    session_id: str


# ── use cases (write side) ─────────────────────────────────────────────────────────


class ConversationRuntimeUseCases(_UseCaseBase):
    """Write side of the Conversation Runtime — one Unit of Work per session mutation."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def create_session(self, cmd: CreateSession) -> SessionView:
        with self._uow.new() as uow:
            session = self._f.conversation_runtime.create_session(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.agent_id,
                runtime_variables=cmd.runtime_variables, correlation_id=cmd.correlation_id,
                metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.SESSION_CREATED, "create_session",
                org_id=cmd.org_id, actor=cmd.actor_user_id, session_id=session.id,
            )
        return SessionView.of(session)

    def resume_session(self, cmd: ResumeSession) -> SessionView:
        return self._lifecycle(
            cmd, self._f.conversation_runtime.resume_session,
            ApplicationEventType.SESSION_RESUMED, "resume_session",
        )

    def close_session(self, cmd: CloseSession) -> SessionView:
        return self._lifecycle(
            cmd, self._f.conversation_runtime.close_session,
            ApplicationEventType.SESSION_CLOSED, "close_session",
        )

    def expire_session(self, cmd: ExpireSession) -> SessionView:
        return self._lifecycle(
            cmd, self._f.conversation_runtime.expire_session,
            ApplicationEventType.SESSION_EXPIRED, "expire_session",
        )

    def _lifecycle(self, cmd: _SessionOp, fn, event, use_case) -> SessionView:
        with self._uow.new() as uow:
            session = fn(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.session_id
            )
            self._stage(uow, event, use_case, org_id=cmd.org_id, actor=cmd.actor_user_id,
                        session_id=session.id)
        return SessionView.of(session)


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConversationRuntimeQueries:
    """Read side — session inspection, resolved runtime state, and the execution plan."""

    foundation: Foundation

    def get(self, query: GetSession) -> dict:
        return asdict(SessionView.of(
            self.foundation.conversation_runtime.get_session(query.org_id, query.session_id)
        ))

    def list(self, query: ListSessions) -> list[dict]:
        return [
            asdict(SessionSummaryView.of(s))
            for s in self.foundation.conversation_runtime.list_sessions(
                query.org_id, query.agent_id
            )
        ]

    def state(self, query: GetRuntimeState) -> dict:
        s = self.foundation.conversation_runtime.get_session(query.org_id, query.session_id)
        return {
            "session_id": s.id, "agent_id": s.agent_id, "agent_version": s.agent_version,
            "status": s.status.value, **s.state.as_dict(redact_secrets=True),
        }

    def plan(self, query: GetExecutionPlan) -> dict:
        s = self.foundation.conversation_runtime.get_session(query.org_id, query.session_id)
        state = s.state
        return {
            "session_id": s.id,
            "agent_id": s.agent_id,
            "agent_version": s.agent_version,
            "resolved_prompt": state.resolved_prompt.as_dict(),
            "resolved_variables": [
                v.as_dict(redact_secrets=True) for v in state.resolved_variables
            ],
            "tool_plan": state.tool_plan.as_dict(),
            "execution_metadata": state.execution_metadata,
            "timing": state.timing,
            "diagnostics": list(state.diagnostics),
        }


# ── registration ───────────────────────────────────────────────────────────────────


def build_and_register(
    *,
    foundation: Foundation,
    authz: AuthorizationOrchestrator,
    uow_factory: UnitOfWorkFactory,
    ids,
    clock,
    command_bus: CommandBus,
    query_bus: QueryBus,
) -> None:
    """Wire the M8.12 use cases + handlers into the existing buses (additive)."""
    uc = ConversationRuntimeUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = ConversationRuntimeQueries(foundation=foundation)
    command_bus.register(CreateSession, uc.create_session)
    command_bus.register(ResumeSession, uc.resume_session)
    command_bus.register(CloseSession, uc.close_session)
    command_bus.register(ExpireSession, uc.expire_session)
    query_bus.register(GetSession, q.get)
    query_bus.register(ListSessions, q.list)
    query_bus.register(GetRuntimeState, q.state)
    query_bus.register(GetExecutionPlan, q.plan)
