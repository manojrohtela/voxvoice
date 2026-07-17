"""RuntimeSessionService — the Runtime Plane's implementation of the Control
Plane ↔ Runtime seam (M2C.2).

Implements `core.session_control.SessionController`: creates one `SessionWorker`
per session (each hosting a `ConversationOrchestrator` driving a `RuntimePipeline`
whose providers are resolved from the injected `ProviderRegistry`), tracks live
sessions, and coordinates their end. It is injected into the Control Plane at the
composition root — `api` never imports `runtime`.

Ownership: this service owns the *set* of live sessions (creation/teardown). Each
SessionWorker owns its worker lifecycle; each Orchestrator owns its conversation.
The service never touches conversation state directly.
"""

from __future__ import annotations

import logging

from core.session_control import (
    SessionController,
    StartSessionRequest,
    StartSessionResult,
    TranscriptSink,
)

from .contracts import (
    AgentSpec,
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from .registry import ProviderRegistry
from .worker import SessionWorker

log = logging.getLogger("voxvoice.runtime.service")


class RuntimeSessionService(SessionController):
    def __init__(
        self, registry: ProviderRegistry, *, on_transcript: TranscriptSink | None = None
    ) -> None:
        self._registry = registry
        self._on_transcript = on_transcript
        self._workers: dict[str, SessionWorker] = {}

    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        agent = AgentSpec(
            system_prompt=request.system_prompt,
            first_message=request.first_message,
            language=request.language,
            stt_provider=request.stt_provider,
            tts_provider=request.tts_provider,
            tts_voice=request.tts_voice,
            llm_model=request.llm_model,
        )
        context = RuntimeContext(
            session=SessionContext(
                session_id=request.session_id,
                org_id=request.org_id,
                agent_id=request.agent_id,
                channel=request.channel,
                metadata=request.metadata,
            ),
            config=WorkerConfiguration(worker_id=f"wrk_{request.session_id}"),
            registry=self._registry,
            agent=agent,
            on_transcript=self._on_transcript,
        )
        worker = SessionWorker(context)
        await worker.start()  # begins the conversation; the pipeline runs in the background
        self._workers[request.session_id] = worker
        log.info("runtime session %s started", request.session_id)
        return StartSessionResult(
            session_id=request.session_id,
            engine="voxvoice",
            channel=request.channel,
            web={"transport": "local"},
        )

    async def end(self, session_id: str) -> None:
        worker = self._workers.pop(session_id, None)
        if worker is not None:
            await worker.stop()
            log.info("runtime session %s ended", session_id)

    def conversation_state(self, session_id: str) -> str | None:
        worker = self._workers.get(session_id)
        if worker is None or worker.conversation_state is None:
            return None
        return worker.conversation_state.value
