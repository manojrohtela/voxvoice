"""Conversation Playground domain service (M8.13) — additive.

A developer testing environment that drives the M8.12 Conversation Runtime through **mock
adapters only** (deterministic, network-free). It reuses the published version, the resolved
execution plan, the Tool Framework, the durable Audit Platform, and the existing
`ConversationSession` — it adds **no new persistence** (turns are appended to the session's
`ConversationState.history`).

**This is not production.** No real provider, no real LLM API, no real tool execution, no audio
synthesis. Every turn is a pure function of the resolved plan + the user input (via the
deterministic mocks), so replaying the same conversation yields byte-identical turns. The
playground never mutates the agent's published configuration or its resolved plan — it only
appends conversation history to the session.

**Reuse-first:** no new RBAC scope — playground actions authorize via the existing workspace-
scoped `PROJECT_MANAGE` (start/continue/reset/close are all developer mutations within a project).
"""

from __future__ import annotations

from dataclasses import replace

from .errors import ConflictError, NotFoundError
from .events import FoundationEventType
from .playground_domain import ConversationTurn
from .rbac import Permission
from .repositories import ConversationSessionRepository
from .runtime import ConversationRuntimeService
from .runtime_domain import ConversationSession, SessionStatus
from .runtime_mocks import MockAdapterSet
from .services import _Base


class PlaygroundService(_Base):
    """Runs playground conversations against the mock runtime. Start reuses the runtime's
    session creation (which validates the published version + plans deterministically); turns
    run the mock adapters over that resolved plan and append to the session history."""

    def __init__(
        self,
        *,
        runtime: ConversationRuntimeService,
        sessions: ConversationSessionRepository,
        mocks: MockAdapterSet | None = None,
        **base,
    ) -> None:
        super().__init__(**base)
        self._runtime = runtime
        self._sessions = sessions
        self._mocks = mocks or MockAdapterSet.default()

    # ── conversation lifecycle ────────────────────────────────────────────────────
    def start_conversation(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, *,
        runtime_variables=None, metadata=None,
    ) -> ConversationSession:
        # Reuse the runtime: validates the PUBLISHED version, plans, persists, emits
        # session.created + execution.planned. A draft never executes (runtime enforces).
        session = self._runtime.create_session(
            actor_user_id, org_id, workspace_id, project_id, agent_id,
            runtime_variables=runtime_variables, metadata=metadata,
        )
        self._emit(
            FoundationEventType.CONVERSATION_STARTED, org_id=org_id, actor=actor_user_id,
            resource=session.id, workspace_id=workspace_id, project_id=project_id,
            agent_id=agent_id,
        )
        return session

    def _load_active(self, org_id: str, session_id: str) -> ConversationSession:
        s = self._sessions.get(org_id, session_id)
        if s is None:
            raise NotFoundError(f"conversation {session_id!r} not found")
        if s.status is not SessionStatus.ACTIVE:
            raise ConflictError(f"conversation {session_id!r} is {s.status.value}")
        return s

    def continue_conversation(
        self, actor_user_id, org_id, workspace_id, project_id, session_id, *, user_input: str
    ) -> ConversationTurn:
        """Run one debugging turn through the mock adapters and append it to the history."""
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        session = self._load_active(org_id, session_id)
        state = session.state
        index = len(state.history)
        self._emit(
            FoundationEventType.TURN_PLANNED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
            turn=index,
        )
        turn = self._run_turn(state, index, user_input)
        new_state = replace(state, history=(*state.history, turn.as_dict()))
        updated = self._sessions.update(
            replace(session, state=new_state, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.TURN_COMPLETED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
            turn=index, tokens=turn.token_estimate,
        )
        # `updated` keeps the appended turn durable; return the turn for the response.
        assert updated.state.history[-1]["index"] == index
        return turn

    def _run_turn(self, state, index: int, user_input: str) -> ConversationTurn:
        """Deterministic: mock LLM over the resolved prompt + user input, mock tools over each
        enabled plan step, mock voice metadata when the plan requires voice. No wall clock."""
        model_meta = state.execution_metadata
        llm_prompt = f"{state.resolved_prompt.text}\n\nUSER: {user_input}"
        llm_output = self._mocks.llm.complete(
            llm_prompt, options={"model": model_meta.get("model", "mock-model")}
        )
        tool_invocations = tuple(
            self._mocks.tools.invoke(
                step.tool_id, arguments={"input": user_input}, config=step.config
            )
            for step in state.tool_plan.steps if step.enabled
        )
        voice_output = None
        if state.tool_plan.required_adapters.get("voice"):
            voice_output = self._mocks.voice.synthesize(
                llm_output["text"], options={"voice_name": model_meta.get("voice_name", "")}
            )
        self._mocks.memory.append(f"turn-{index}", {"input": user_input})
        return ConversationTurn(
            index=index, user_input=user_input, llm_output=llm_output,
            tool_invocations=tool_invocations, voice_output=voice_output,
            token_estimate=llm_output["token_estimate"],
            timing={"llm_ms": llm_output["timing_ms"], "tools": len(tool_invocations)},
            diagnostics=state.diagnostics, created_at=self._clock.now(),
        )

    def reset_conversation(
        self, actor_user_id, org_id, workspace_id, project_id, session_id
    ) -> ConversationSession:
        """Clear the conversation history (turns) while keeping the resolved plan intact."""
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        session = self._load_active(org_id, session_id)
        new_state = replace(session.state, history=())
        updated = self._sessions.update(
            replace(session, state=new_state, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.CONVERSATION_RESET, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
        )
        return updated

    def close_conversation(
        self, actor_user_id, org_id, workspace_id, project_id, session_id
    ) -> ConversationSession:
        # Reuse the runtime close (emits session.closed), then record the playground fact.
        session = self._runtime.close_session(
            actor_user_id, org_id, workspace_id, project_id, session_id
        )
        self._emit(
            FoundationEventType.CONVERSATION_CLOSED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
        )
        return session

    # ── reads ─────────────────────────────────────────────────────────────────────
    def get_conversation(self, org_id: str, session_id: str) -> ConversationSession:
        s = self._sessions.get(org_id, session_id)
        if s is None:
            raise NotFoundError(f"conversation {session_id!r} not found")
        return s

    def turns(self, org_id: str, session_id: str) -> tuple[ConversationTurn, ...]:
        s = self.get_conversation(org_id, session_id)
        return tuple(ConversationTurn.from_dict(t) for t in s.state.history)

    def adapter_outputs(self, org_id: str, session_id: str) -> dict:
        """The mock adapter descriptor + the latest turn's adapter outputs (or empty)."""
        turns = self.turns(org_id, session_id)
        last = turns[-1] if turns else None
        return {
            "adapters": self._mocks.describe(),
            "latest_turn": last.as_dict() if last else None,
        }
