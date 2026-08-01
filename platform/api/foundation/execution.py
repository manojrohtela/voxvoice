"""Conversation execution seam + service (M8.14) — additive.

The **binding** between the M8.12 Conversation Runtime (which plans) and real provider
execution (which the `api.adapters` layer implements). The Foundation defines only:

  * `ExecutionEngine` — the ABC that turns a resolved plan + a user turn into an
    `ExecutionResult` (and a streaming variant). The real, provider-backed engine lives in
    `api.adapters` and is injected at the composition root; the Foundation never imports it.
  * `MockExecutionEngine` — a deterministic default (built on the M8.13 mock adapters) so the
    in-memory Foundation executes offline with no providers wired.
  * `ConversationExecutionService` — reuses the M8.12 session + the published version's provider
    config, drives the injected engine, appends the turn + persists messages (memory store), and
    emits the M8.14 audit facts. **No provider-specific logic and no planning logic here** — the
    runtime owns orchestration, the adapters own execution + translation (golden rules).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import replace

from .agent_domain import AgentConfig
from .errors import ConflictError, NotFoundError
from .events import FoundationEventType
from .execution_domain import (
    ConversationMessage,
    ExecutionResult,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from .playground_domain import ConversationTurn
from .rbac import Permission
from .repositories import (
    AgentVersionRepository,
    ConversationMessageRepository,
    ConversationSessionRepository,
)
from .runtime_domain import ConversationState, SessionStatus
from .runtime_mocks import MockAdapterSet
from .seams import PREFIX_EVENT
from .services import _Base


class ExecutionEngine(ABC):
    """Executes a resolved conversation turn through provider adapters. The plan is an input
    (the runtime already produced it); the engine performs no planning."""

    @abstractmethod
    def execute(
        self, state: ConversationState, user_input: str, *, options: dict
    ) -> ExecutionResult:
        """Run one turn to completion through the configured providers."""

    @abstractmethod
    def stream(
        self, state: ConversationState, user_input: str, *, options: dict
    ) -> Iterable[StreamEvent]:
        """Run one turn, yielding stream events (tokens/tools/voice) then a terminal event."""

    def describe_providers(self, options: dict) -> dict:
        """Provider diagnostics for the given options (selected provider/model + health)."""
        return {"llm": options.get("model", {}).get("provider", "")}


class MockExecutionEngine(ExecutionEngine):
    """Deterministic default engine over the M8.13 mock adapters — offline, no providers.
    Used when no real engine is injected (so `Foundation.default()` executes)."""

    def __init__(self, mocks: MockAdapterSet | None = None) -> None:
        self._mocks = mocks or MockAdapterSet.default()

    def execute(self, state, user_input, *, options) -> ExecutionResult:
        prompt = f"{state.resolved_prompt.text}\n\nUSER: {user_input}"
        llm = self._mocks.llm.complete(prompt, options=options.get("model", {}))
        tool_results = tuple(
            self._mocks.tools.invoke(s.tool_id, arguments={"input": user_input}, config=s.config)
            for s in state.tool_plan.steps if s.enabled
        )
        assistant = llm["text"]
        messages = (
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant},
        )
        return ExecutionResult(
            assistant_response=assistant, messages=messages, tool_results=tool_results,
            provider_metadata={"provider": "mock", "model": llm.get("model", "mock-model")},
            token_usage=TokenUsage(llm["echo"]["prompt_chars"] // 4, llm["token_estimate"]),
            latency_ms=llm["timing_ms"], errors=(), diagnostics=state.diagnostics,
            execution_metadata={"engine": "mock", "streamed": False},
        )

    def stream(self, state, user_input, *, options) -> Iterable[StreamEvent]:
        result = self.execute(state, user_input, options=options)
        seq = 0
        for word in result.assistant_response.split():
            yield StreamEvent(seq, StreamEventType.TOKEN, {"text": word + " "})
            seq += 1
        for tr in result.tool_results:
            yield StreamEvent(seq, StreamEventType.TOOL, tr)
            seq += 1
        yield StreamEvent(seq, StreamEventType.DONE, {"result": result.as_dict()})


class ConversationExecutionService(_Base):
    """Drives real (or mock) execution over the reused M8.12 session. Reuses the published
    version's provider config to select the provider; persists messages to the memory store;
    appends the turn to the session; emits M8.14 audit facts. No provider/planning logic here."""

    def __init__(
        self,
        *,
        sessions: ConversationSessionRepository,
        agent_versions: AgentVersionRepository,
        messages: ConversationMessageRepository,
        engine: ExecutionEngine,
        **base,
    ) -> None:
        super().__init__(**base)
        self._sessions = sessions
        self._versions = agent_versions
        self._messages = messages
        self._engine = engine

    def _load_active(self, org_id: str, session_id: str):
        s = self._sessions.get(org_id, session_id)
        if s is None:
            raise NotFoundError(f"conversation {session_id!r} not found")
        if s.status is not SessionStatus.ACTIVE:
            raise ConflictError(f"conversation {session_id!r} is {s.status.value}")
        return s

    def _provider_options(self, session) -> dict:
        """Reuse the published version's model/voice config to select the provider (the
        runtime already validated the version at session creation)."""
        version = self._versions.get(session.org_id, session.agent_id, session.agent_version)
        snapshot = version.snapshot.get("config", {}) if version else {}
        config = AgentConfig.from_dict(snapshot)
        return {"model": config.model.as_dict(), "voice": config.voice.as_dict()}

    def execute_turn(
        self, actor_user_id, org_id, workspace_id, project_id, session_id, *,
        user_input: str, stream: bool = False,
    ) -> ExecutionResult:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        session = self._load_active(org_id, session_id)
        options = self._provider_options(session)
        # M8.14/M8.15: thread execution context so the memory adapter + scoped tools (Knowledge)
        # can resolve their org/project/session.
        options.update({"org_id": org_id, "project_id": project_id, "session_id": session_id})
        provider = options["model"].get("provider", "")
        self._emit(
            FoundationEventType.EXECUTION_STARTED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
            streamed=stream,
        )
        self._emit(
            FoundationEventType.PROVIDER_SELECTED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
            provider=provider,
        )
        try:
            if stream:
                self._emit(
                    FoundationEventType.STREAMING_STARTED, org_id=org_id, actor=actor_user_id,
                    resource=session_id, workspace_id=workspace_id, project_id=project_id,
                )
                events = list(self._engine.stream(session.state, user_input, options=options))
                result = self._result_from_stream(events)
                self._emit(
                    FoundationEventType.STREAMING_FINISHED, org_id=org_id, actor=actor_user_id,
                    resource=session_id, workspace_id=workspace_id, project_id=project_id,
                    events=len(events),
                )
            else:
                result = self._engine.execute(session.state, user_input, options=options)
        except Exception as exc:
            self._emit(
                FoundationEventType.PROVIDER_FAILED, org_id=org_id, actor=actor_user_id,
                resource=session_id, workspace_id=workspace_id, project_id=project_id,
                provider=provider, error=str(exc),
            )
            self._emit(
                FoundationEventType.EXECUTION_FAILED_REAL, org_id=org_id, actor=actor_user_id,
                resource=session_id, workspace_id=workspace_id, project_id=project_id,
                error=str(exc),
            )
            raise
        self._persist(session, user_input, result, actor_user_id, workspace_id, project_id)
        return result

    def _result_from_stream(self, events: list[StreamEvent]) -> ExecutionResult:
        done = next((e for e in reversed(events) if e.type is StreamEventType.DONE), None)
        if done is not None:
            r = done.data.get("result", {})
            return ExecutionResult(
                assistant_response=r.get("assistant_response", ""),
                messages=tuple(r.get("messages", ())),
                tool_results=tuple(r.get("tool_results", ())),
                provider_metadata=r.get("provider_metadata", {}),
                token_usage=TokenUsage.from_dict(r.get("token_usage", {})),
                latency_ms=r.get("latency_ms", 0), errors=tuple(r.get("errors", ())),
                diagnostics=tuple(r.get("diagnostics", ())),
                execution_metadata={**r.get("execution_metadata", {}), "streamed": True},
                stream=tuple(e.as_dict() for e in events),
            )
        text = "".join(e.data.get("text", "") for e in events if e.type is StreamEventType.TOKEN)
        return ExecutionResult(
            assistant_response=text.strip(), stream=tuple(e.as_dict() for e in events),
            execution_metadata={"streamed": True},
        )

    def _persist(self, session, user_input, result, actor, workspace_id, project_id) -> None:
        org_id = session.org_id
        # 1) durable message store (memory adapter output): user + assistant (+ tool) messages.
        base_seq = self._messages.count(org_id, session.id)
        rows = [{"role": "user", "content": user_input}]
        rows.append({"role": "assistant", "content": result.assistant_response})
        for tr in result.tool_results:
            rows.append({"role": "tool", "content": str(tr.get("output", "")), "metadata": tr})
        for i, msg in enumerate(rows):
            self._messages.add(ConversationMessage(
                id=self._ids.new(PREFIX_EVENT), org_id=org_id, session_id=session.id,
                seq=base_seq + i, role=msg["role"], content=msg["content"],
                metadata=msg.get("metadata", {}), created_at=self._clock.now(),
            ))
        # 2) append a turn to the reused conversation state (M8.12/M8.13).
        index = len(session.state.history)
        turn = ConversationTurn(
            index=index, user_input=user_input,
            llm_output={"text": result.assistant_response, **result.provider_metadata,
                        "token_usage": result.token_usage.as_dict()},
            tool_invocations=result.tool_results, voice_output=None,
            token_estimate=result.token_usage.total,
            timing={"latency_ms": result.latency_ms}, diagnostics=result.diagnostics,
            created_at=self._clock.now(),
        )
        new_state = replace(session.state, history=(*session.state.history, turn.as_dict()))
        self._sessions.update(replace(session, state=new_state, updated_at=self._clock.now()))
        # 3) audit completion.
        for tr in result.tool_results:
            self._emit(
                FoundationEventType.TOOL_EXECUTED, org_id=org_id, actor=actor,
                resource=session.id, workspace_id=workspace_id, project_id=project_id,
                tool_id=tr.get("tool_id", ""),
            )
        self._emit(
            FoundationEventType.EXECUTION_COMPLETED, org_id=org_id, actor=actor,
            resource=session.id, workspace_id=workspace_id, project_id=project_id,
            tokens=result.token_usage.total, latency_ms=result.latency_ms,
            provider=result.provider_metadata.get("provider", ""),
        )

    # ── reads ─────────────────────────────────────────────────────────────────────
    def messages(self, org_id: str, session_id: str) -> tuple[ConversationMessage, ...]:
        return self._messages.list(org_id, session_id)

    def provider_diagnostics(self, org_id: str, session_id: str) -> dict:
        session = self._sessions.get(org_id, session_id)
        if session is None:
            raise NotFoundError(f"conversation {session_id!r} not found")
        return self._engine.describe_providers(self._provider_options(session))
