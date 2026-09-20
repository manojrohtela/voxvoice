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
from .faults import greeting_state_for, require_valid_agent_spec
from .observability_plane import UNWIRED, ObservabilityPlane
from .registry import ProviderRegistry
from .worker import SessionWorker

log = logging.getLogger("voxvoice.runtime.service")


class RuntimeSessionService(SessionController):
    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        on_transcript: TranscriptSink | None = None,
        streaming_playback: bool = False,
        observability: ObservabilityPlane = UNWIRED,
    ) -> None:
        self._registry = registry
        self._on_transcript = on_transcript
        # M11.11: whether sessions speak through the clause-by-clause playback path. Set by the
        # composition root, never by a caller's session request — see `WorkerConfiguration`.
        self._streaming_playback = streaming_playback
        # M11.12: which Epic 10 facilities sessions are wired to. Supplied by the composition
        # root; `UNWIRED` (the default) is the pre-M11.12 behaviour exactly.
        self._observability = observability
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
            sample_rate=request.sample_rate,  # session property (M11.2, ADR-0044 AD3)
            # AR-25 (M11.4, ADR-0046 FT-D9): the Control Plane's choice finally reaches the
            # Runtime. Before this line `allow_interrupt` was hardcoded to its default and
            # barge-in was unreachable in production despite being fully implemented.
            allow_interrupt=request.allow_interruptions,
            # AR-25 again (M11.8): accepted configuration that actually acts.
            audio_endpointing=request.audio_endpointing,
            # AR-25: `first_message` stops being silently ignored. M11.4 does not *play* it —
            # D-11 gives the greeting to M11.10's Playback Scheduler — so it is declared
            # pending rather than dropped, and the validator enforces the two agree.
            greeting_state=greeting_state_for(request.first_message),
        )
        context = RuntimeContext(
            session=SessionContext(
                session_id=request.session_id,
                org_id=request.org_id,
                agent_id=request.agent_id,
                channel=request.channel,
                metadata=request.metadata,
            ),
            config=WorkerConfiguration(
                worker_id=f"wrk_{request.session_id}",
                streaming_playback=self._streaming_playback,
                observability=self._observability,
            ),
            registry=self._registry,
            agent=agent,
            on_transcript=self._on_transcript,
        )
        # AR-25 / GOVERNANCE §4: reject configuration that cannot be honoured rather than
        # accepting it and doing something else. `ConfigError` means "this session should never
        # start" — distinct from a degradation, which is a capability the runtime adapts to.
        require_valid_agent_spec(agent, self._registry)
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
