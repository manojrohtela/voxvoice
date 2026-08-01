"""Conversation Playground application layer (M8.13) — additive.

Commands, queries, use cases and handler registration for the playground vertical slice, in a
separate module so the M8.3 modules stay untouched (mirrors `runtime.py`). Use cases coordinate
the additive Foundation `playground` service — no domain logic is duplicated; the deterministic
mock-turn execution lives entirely in the Foundation. Playground mutations run inside a Unit of
Work (so the durable audit rows commit atomically with the session write). Prompt/variable/tool
inspection **reuses the M8.12 runtime queries** (`GetRuntimeState`/`GetExecutionPlan`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from api.foundation import ConversationView, Foundation, SessionView, TurnView

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StartConversation:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    runtime_variables: dict | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class ContinueConversation:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    session_id: str
    user_input: str = ""


@dataclass(frozen=True, slots=True)
class _ConversationOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    session_id: str


class ResetConversation(_ConversationOp): ...
class CloseConversation(_ConversationOp): ...


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetConversation:
    org_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ListConversationTurns:
    org_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class GetAdapterOutputs:
    org_id: str
    session_id: str


# ── use cases (write side) ─────────────────────────────────────────────────────────


class PlaygroundUseCases(_UseCaseBase):
    """Write side of the playground — one Unit of Work per conversation mutation."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def start_conversation(self, cmd: StartConversation) -> SessionView:
        with self._uow.new() as uow:
            session = self._f.playground.start_conversation(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.agent_id,
                runtime_variables=cmd.runtime_variables, metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.CONVERSATION_STARTED, "start_conversation",
                org_id=cmd.org_id, actor=cmd.actor_user_id, session_id=session.id,
            )
        return SessionView.of(session)

    def continue_conversation(self, cmd: ContinueConversation) -> TurnView:
        with self._uow.new() as uow:
            turn = self._f.playground.continue_conversation(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.session_id, user_input=cmd.user_input,
            )
            self._stage(
                uow, ApplicationEventType.TURN_COMPLETED, "continue_conversation",
                org_id=cmd.org_id, actor=cmd.actor_user_id, session_id=cmd.session_id,
                turn=turn.index,
            )
        return TurnView.of(turn)

    def reset_conversation(self, cmd: ResetConversation) -> SessionView:
        with self._uow.new() as uow:
            session = self._f.playground.reset_conversation(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.session_id
            )
            self._stage(
                uow, ApplicationEventType.CONVERSATION_RESET, "reset_conversation",
                org_id=cmd.org_id, actor=cmd.actor_user_id, session_id=cmd.session_id,
            )
        return SessionView.of(session)

    def close_conversation(self, cmd: CloseConversation) -> SessionView:
        with self._uow.new() as uow:
            session = self._f.playground.close_conversation(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.session_id
            )
            self._stage(
                uow, ApplicationEventType.CONVERSATION_CLOSED, "close_conversation",
                org_id=cmd.org_id, actor=cmd.actor_user_id, session_id=cmd.session_id,
            )
        return SessionView.of(session)


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlaygroundQueries:
    """Read side — conversation (session + turns + messages), turns, and adapter outputs."""

    foundation: Foundation

    def conversation(self, query: GetConversation) -> dict:
        return asdict(ConversationView.of(
            self.foundation.playground.get_conversation(query.org_id, query.session_id)
        ))

    def turns(self, query: ListConversationTurns) -> list[dict]:
        return [
            asdict(TurnView.of(t))
            for t in self.foundation.playground.turns(query.org_id, query.session_id)
        ]

    def adapter_outputs(self, query: GetAdapterOutputs) -> dict:
        return self.foundation.playground.adapter_outputs(query.org_id, query.session_id)


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
    """Wire the M8.13 use cases + handlers into the existing buses (additive)."""
    uc = PlaygroundUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = PlaygroundQueries(foundation=foundation)
    command_bus.register(StartConversation, uc.start_conversation)
    command_bus.register(ContinueConversation, uc.continue_conversation)
    command_bus.register(ResetConversation, uc.reset_conversation)
    command_bus.register(CloseConversation, uc.close_conversation)
    query_bus.register(GetConversation, q.conversation)
    query_bus.register(ListConversationTurns, q.turns)
    query_bus.register(GetAdapterOutputs, q.adapter_outputs)
