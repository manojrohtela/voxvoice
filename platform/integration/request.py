"""Request entry (M6.7B-1) — request translation, trace/cancellation
creation, execution identifiers. **No orchestration** (design §3, this
bundle's Request Entry scope): these shapes translate a control-plane request
into the pieces Agent Runtime's own `submit_goal` accepts, and mint the one
root `trace_id` + the one `IntegrationCancellationToken`. They never drive the
goal FSM — `AgentExecutionEngine` owns that entirely (§4.2).

`PlatformRequest` is the one new request shape this layer owns (design §2). It
carries Epic 6 domain capabilities (`agent.AgentCapability`) already
translated by the API layer — never the control-plane `api.models.Agent`
persona row, a different concept sharing a name (§17 naming-collision risk).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import AgentCapability, AgentGoal
from core.ports.context import ContextRequest

from .cancellation import IntegrationCancellationToken
from .config import IntegrationConfiguration
from .context import IntegrationExecutionContext, default_trace_id


@dataclass(frozen=True, slots=True)
class PlatformRequest:
    """An org-scoped goal request (design §4.2). `capabilities` are already
    Epic 6 `AgentCapability` objects."""

    session_id: str
    agent_id: str
    goal_description: str
    conversation_id: str = ""
    capabilities: tuple[AgentCapability, ...] = ()
    org_id: str | None = None
    query: str = ""


@dataclass(frozen=True, slots=True)
class PlatformExecutionContext:
    """The per-request execution scope: the correlation context, the single
    cancellation token, and the already-translated capability-facing inputs
    (`AgentGoal`, capabilities, `ContextRequest`). Frozen — the only mutable
    thing it holds is the cancellation token, whose mutation is its own
    concern (design §3.1)."""

    request_id: str
    context: IntegrationExecutionContext
    cancellation: IntegrationCancellationToken
    goal: AgentGoal
    capabilities: tuple[AgentCapability, ...]
    context_request: ContextRequest

    @property
    def trace_id(self) -> str:
        return self.context.trace_id


@dataclass(frozen=True, slots=True)
class PlatformResponse:
    """The response shape returned to the API layer. `trace_id` is preserved
    across the whole execution (design §16.1)."""

    trace_id: str
    request_id: str
    goal_id: str
    execution_id: str
    final_state: str
    partial_success: bool
    error: str | None = None


class PlatformExecution:
    """Request translation + identifier minting. Holds no capability and drives
    no FSM (design Request Entry scope: "No orchestration")."""

    def __init__(self, config: IntegrationConfiguration | None = None) -> None:
        self._config = config or IntegrationConfiguration()

    def begin(self, request: PlatformRequest) -> PlatformExecutionContext:
        """Translate a `PlatformRequest` into a `PlatformExecutionContext`:
        mint the one root `trace_id`, the one `IntegrationCancellationToken`,
        and a deterministic `goal_id`; build the `AgentGoal`/`ContextRequest`
        Agent Runtime's own `submit_goal` accepts."""
        trace_id = self._mint_trace_id(request)
        goal_id = default_trace_id("goal", trace_id, request.agent_id, request.goal_description)
        conversation_id = request.conversation_id or default_trace_id("conv", trace_id)

        context = IntegrationExecutionContext(
            trace_id=trace_id, session_id=request.session_id, org_id=request.org_id,
            goal_id=goal_id, conversation_id=conversation_id,
        )
        goal = AgentGoal(
            goal_id=goal_id, agent_id=request.agent_id,
            conversation_id=conversation_id, description=request.goal_description,
        )
        context_request = ContextRequest(
            session_id=request.session_id, org_id=request.org_id,
            conversation_id=conversation_id, query=request.query or request.goal_description,
        )
        return PlatformExecutionContext(
            request_id=default_trace_id("req", trace_id),
            context=context,
            cancellation=IntegrationCancellationToken(trace_id),
            goal=goal,
            capabilities=tuple(request.capabilities),
            context_request=context_request,
        )

    def _mint_trace_id(self, request: PlatformRequest) -> str:
        factory = self._config.trace_id_factory
        if factory is not None:
            return factory()
        return default_trace_id(
            "trace", request.session_id, request.agent_id, request.goal_description,
        )
