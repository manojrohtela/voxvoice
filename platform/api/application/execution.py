"""Provider execution application layer (M8.14) — additive.

Commands + queries for real (provider-backed) conversation execution, in a separate module so
the M8.3 modules stay untouched (mirrors `playground.py`). Use cases coordinate the additive
Foundation `conversation_execution` service — no provider or planning logic here. Execution runs
inside a Unit of Work (so the durable message rows + audit rows commit atomically). Prompt /
variable / tool-plan inspection continues to reuse the M8.12 runtime queries (unchanged).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from api.foundation import ExecutionResultView, Foundation, MessageView

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecuteTurn:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    session_id: str
    user_input: str = ""
    stream: bool = False


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetConversationMessages:
    org_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class GetProviderDiagnostics:
    org_id: str
    session_id: str


# ── use cases ──────────────────────────────────────────────────────────────────────


class ExecutionUseCases(_UseCaseBase):
    """Write side — one Unit of Work per executed turn (messages + audit commit atomically)."""

    def execute_turn(self, cmd: ExecuteTurn) -> ExecutionResultView:
        with self._uow.new() as uow:
            result = self._f.conversation_execution.execute_turn(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.session_id, user_input=cmd.user_input, stream=cmd.stream,
            )
            uow.stage(ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT),
                type=(
                    ApplicationEventType.EXECUTION_STREAMED if cmd.stream
                    else ApplicationEventType.EXECUTION_COMPLETED
                ),
                ts=self._clock.now(), use_case="execute_turn", outcome="completed",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
                data={"session_id": cmd.session_id, "tokens": result.token_usage.total},
            ))
        return ExecutionResultView.of(result)


@dataclass(frozen=True, slots=True)
class ExecutionQueries:
    """Read side — durable messages + provider diagnostics."""

    foundation: Foundation

    def messages(self, query: GetConversationMessages) -> list[dict]:
        return [
            asdict(MessageView.of(m))
            for m in self.foundation.conversation_execution.messages(
                query.org_id, query.session_id
            )
        ]

    def diagnostics(self, query: GetProviderDiagnostics) -> dict:
        return self.foundation.conversation_execution.provider_diagnostics(
            query.org_id, query.session_id
        )


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
    """Wire the M8.14 use cases + handlers into the existing buses (additive)."""
    uc = ExecutionUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = ExecutionQueries(foundation=foundation)
    command_bus.register(ExecuteTurn, uc.execute_turn)
    query_bus.register(GetConversationMessages, q.messages)
    query_bus.register(GetProviderDiagnostics, q.diagnostics)
