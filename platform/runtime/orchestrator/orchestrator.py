"""ConversationOrchestrator — the sole coordinator of conversation execution
(M2B.2; pipeline wiring M2C.2).

Component specification (Constitution §A11.3):
  responsibilities : own the ConversationStateMachine and be its SOLE writer;
                     coordinate the conversation lifecycle phases (begin → run →
                     end) and failure routing; resolve provider capabilities from
                     the Provider Registry and coordinate their lifecycle (open on
                     begin, close on end); drive the RuntimePipeline. It performs
                     no speech processing, AI inference, provider implementation,
                     turn logic, or business logic.
  inputs           : a RuntimeContext (session, provider registry, agent spec,
                     transcript sink, config); an optional transition observer.
  outputs          : conversation state + immutable history (read-only);
                     transition notifications (via the state machine observer).
  dependencies     : core.states, runtime.conversation (the state machine),
                     runtime.contracts, runtime.errors, provider *interfaces* via
                     DI. Never imports `api`; never a provider implementation.
  lifecycle        : conversation CREATED → CONNECTED → IN_CONVERSATION (begin) →
                     … → ENDING → ENDED (end); FAILED on startup error. Every
                     move goes through the approved state machine — no hidden
                     transitions, no silent recovery.
  failure modes    : startup failure → conversation FAILED + OrchestrationStartupError;
                     mid-conversation failure → routed ENDING → ENDED (the state
                     machine forbids IN_CONVERSATION → FAILED, §A4.4).
  recovery         : SUSPENDED → IN_CONVERSATION is the recovery edge (driven by
                     the transport reconnect path, wired at M2C+).

Ownership boundary: the SessionWorker owns the worker; this Orchestrator owns the
conversation. The worker delegates conversation control here and never touches
conversation state. Providers own capabilities. Turn execution (M3) is separate.

Scope (M2C.2): the running phase drives the RuntimePipeline (Transport → STT →
LLM → TTS → Transport) whose providers are resolved from the registry. When there
is no registry/agent (lifecycle-only paths) the conversation runs idle, awaiting
an end signal. Turn Intelligence (M3), the Event Bus (M4), and Memory (M5) remain
out of scope.
"""

from __future__ import annotations

import asyncio
import logging

from core.states import SessionState

from ..contracts import RuntimeContext
from ..conversation import ConversationStateMachine, TransitionObserver, TransitionRecord
from ..endpoint import EndpointCoordinator, EndpointEngine
from ..errors import OrchestrationError, OrchestrationStartupError
from ..interrupt import InterruptCoordinator, InterruptEngine
from ..pipeline.pipeline import RuntimePipeline
from ..registry import CapabilityKind
from ..smart_turn import SmartTurnCoordinator, SmartTurnEngine
from ..turn import TurnCoordinator, TurnEngine
from ..voice_activity import VoiceActivityCoordinator, VoiceActivityEngine

log = logging.getLogger("voxvoice.runtime.orchestrator")


class ConversationOrchestrator:
    """Sole owner/writer of one conversation's execution."""

    def __init__(
        self,
        context: RuntimeContext,
        *,
        on_transition: TransitionObserver | None = None,
    ) -> None:
        self._context = context
        # The state machine is PRIVATE — the orchestrator is its only writer.
        self._sm = ConversationStateMachine(on_transition=on_transition)
        self._end_event = asyncio.Event()
        self._pipeline: RuntimePipeline | None = None
        # Turn Engine (M3.1): owns turn lifecycle for this conversation. The
        # orchestrator creates it and exposes it read-only; it never manipulates
        # turn state (the pipeline drives it via the coordinator).
        self._turn_engine: TurnEngine | None = None
        # Voice Activity Framework (M3.2): owns voice-activity state, created only
        # when a VAD provider is registered (graceful fallback otherwise). Exposed
        # read-only; driven by the pipeline via the coordinator, never here.
        self._voice_activity_engine: VoiceActivityEngine | None = None
        # Endpoint Intelligence Framework (M3.3): owns endpoint-decision state,
        # ALWAYS created (it replaces the STT-final completion heuristic). Exposed
        # read-only; driven by the pipeline/VAF via the coordinator, never here.
        self._endpoint_engine: EndpointEngine | None = None
        # Interrupt Intelligence Framework (M3.4): owns interrupt-decision state,
        # created only when the agent opts in AND a VAD provider is registered
        # (barge-in needs a speech signal). Exposed read-only; driven by the
        # pipeline/VAF via the coordinator, never here.
        self._interrupt_engine: InterruptEngine | None = None
        # Smart Turn Intelligence Engine (M3.5): owns final conversational-timing
        # decisions, created only when the agent opts in. Blends an optional
        # registered semantic evaluator. Exposed read-only; the pipeline drives it.
        self._smart_turn_engine: SmartTurnEngine | None = None

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def state(self) -> SessionState:
        return self._sm.state

    @property
    def is_terminal(self) -> bool:
        return self._sm.is_terminal

    @property
    def history(self) -> tuple[TransitionRecord, ...]:
        return self._sm.history

    @property
    def turn_engine(self) -> TurnEngine | None:
        """Read-only access to the turn subsystem (created on begin). The
        orchestrator never manipulates turn state — that is the engine's, driven
        by the pipeline via the coordinator."""
        return self._turn_engine

    @property
    def voice_activity_engine(self) -> VoiceActivityEngine | None:
        """Read-only access to the voice-activity subsystem (created on begin only
        when a VAD provider is registered; None under graceful fallback). The
        orchestrator never manipulates activity state — the pipeline drives it via
        the coordinator."""
        return self._voice_activity_engine

    @property
    def endpoint_engine(self) -> EndpointEngine | None:
        """Read-only access to the endpoint subsystem (created on begin, always
        wired when a pipeline is built). The orchestrator never manipulates
        endpoint state — the pipeline/VAF drive it via the coordinator."""
        return self._endpoint_engine

    @property
    def interrupt_engine(self) -> InterruptEngine | None:
        """Read-only access to the interrupt subsystem (created on begin only when
        the agent opts in and a VAD provider is registered; None under graceful
        fallback). The orchestrator never manipulates interrupt state — the
        pipeline/VAF drive it via the coordinator."""
        return self._interrupt_engine

    @property
    def smart_turn_engine(self) -> SmartTurnEngine | None:
        """Read-only access to the Smart Turn subsystem (created on begin only when
        the agent opts in; None under graceful fallback). The orchestrator never
        manipulates Smart Turn state — the pipeline drives it via the coordinator."""
        return self._smart_turn_engine

    # ── coordination phases ─────────────────────────────────────────────────
    async def begin(self) -> None:
        """Coordinate conversation startup: CREATED → CONNECTED → IN_CONVERSATION.
        On failure: conversation → FAILED and raise OrchestrationStartupError."""
        if self._sm.state is not SessionState.CREATED:
            raise OrchestrationError(
                f"begin() requires CREATED, conversation is {self._sm.state.value}"
            )
        try:
            self._sm.transition(
                SessionState.CONNECTED, reason="transport_ready", source="orchestrator"
            )
            await self._open_providers()
            self._sm.transition(
                SessionState.IN_CONVERSATION,
                reason="conversation_started",
                source="orchestrator",
            )
        except Exception as exc:  # noqa: BLE001 — re-raised as a typed error
            # During startup we are in CREATED/CONNECTED, from which FAILED is legal.
            if self._sm.can_transition(SessionState.FAILED):
                self._sm.transition(
                    SessionState.FAILED, reason="startup_failed", source="orchestrator"
                )
            self._end_event.set()
            log.error("conversation startup failed: %s", exc)
            raise OrchestrationStartupError(f"conversation startup failed: {exc}") from exc

    async def run(self) -> None:
        """Coordinate the running phase. Drives the RuntimePipeline when one was
        built (a registry+agent were provided); otherwise awaits the end signal
        (idle conversation). Tolerates the conversation having already ended/ending
        (e.g. the session was stopped before this task started) — it returns
        promptly. Only raises if run() is called before begin() (a genuine misuse)."""
        if self._sm.is_terminal or self._sm.state is SessionState.ENDING:
            return
        if self._sm.state is not SessionState.IN_CONVERSATION:
            raise OrchestrationError(
                f"run() requires IN_CONVERSATION, conversation is {self._sm.state.value}"
            )
        if self._pipeline is None:
            await self._end_event.wait()  # no pipeline configured — idle conversation
            return
        try:
            await self._pipeline.run()
        except Exception as exc:  # noqa: BLE001 — routed through the state machine
            self.fail(reason=f"pipeline_error:{type(exc).__name__}")
            return
        # The transport's inbound stream ended (caller hangup) — end the conversation.
        if not self._sm.is_terminal:
            await self.end(reason="completed", source="transport")

    async def end(self, *, reason: str = "completed", source: str = "orchestrator") -> None:
        """Coordinate graceful shutdown: → ENDING → ENDED. Idempotent once terminal."""
        if self._sm.is_terminal:
            self._end_event.set()
            return
        try:
            if self._sm.state in (SessionState.IN_CONVERSATION, SessionState.SUSPENDED):
                self._sm.transition(SessionState.ENDING, reason=reason, source=source)
            if self._sm.state is SessionState.ENDING:
                await self._close_providers()
                self._sm.transition(SessionState.ENDED, reason=reason, source=source)
            else:
                # CREATED/CONNECTED: the conversation never really started; the
                # state machine has no graceful path to ENDED from here.
                self._sm.transition(
                    SessionState.FAILED,
                    reason=f"ended_before_start:{reason}",
                    source=source,
                )
        finally:
            self._end_event.set()

    async def cancel(self, *, reason: str = "cancelled") -> None:
        """Coordinate cancellation — same terminal path as end(), reason-tagged."""
        await self.end(reason=reason, source="cancel")

    def fail(self, *, reason: str) -> None:
        """Route an unexpected failure through the approved state machine (no hidden
        transitions). Pre-conversation → FAILED; mid-conversation → ENDING → ENDED
        (the machine forbids IN_CONVERSATION → FAILED)."""
        if self._sm.is_terminal:
            self._end_event.set()
            return
        if self._sm.can_transition(SessionState.FAILED):
            self._sm.transition(SessionState.FAILED, reason=reason, source="orchestrator")
        else:
            if self._sm.state in (SessionState.IN_CONVERSATION, SessionState.SUSPENDED):
                self._sm.transition(SessionState.ENDING, reason=reason, source="orchestrator")
            if self._sm.state is SessionState.ENDING:
                self._sm.transition(SessionState.ENDED, reason=reason, source="orchestrator")
        self._end_event.set()
        log.error("conversation failed: %s", reason)

    # ── provider coordination (resolved via the registry; M2C.2) ────────────
    async def _open_providers(self) -> None:
        """Resolve the capability providers for this agent from the Provider
        Registry, build the RuntimePipeline, and start the transport. Providers
        are requested by *capability* (optionally by opaque name) — never by
        vendor identity. Without a registry/agent (lifecycle-only paths) this is
        a no-op and the conversation runs idle."""
        registry = self._context.registry
        agent = self._context.agent
        if registry is None or agent is None:
            return

        def _sel(name: str | None) -> str | None:
            return None if (not name or name == "default") else name

        session_id = self._context.session.session_id
        transport = registry.resolve(CapabilityKind.TRANSPORT)
        stt = registry.resolve(CapabilityKind.STT, _sel(agent.stt_provider))
        llm = registry.resolve(CapabilityKind.LLM)
        tts = registry.resolve(CapabilityKind.TTS, _sel(agent.tts_provider))
        self._turn_engine = TurnEngine(session_id)
        turns = TurnCoordinator(self._turn_engine)
        # Endpoint Intelligence Framework (M3.3): ALWAYS wired — it owns turn
        # completion (replacing the STT-final heuristic). The default policy confirms
        # a completed segment immediately, so the conversation behaves as before. It
        # notifies the same Turn Engine (via `turns`) when a turn should finish.
        self._endpoint_engine = EndpointEngine(session_id)
        endpoint = EndpointCoordinator(
            engine=self._endpoint_engine, session_id=session_id, turns=turns
        )
        # Interrupt Intelligence Framework (M3.4): wired only when the agent opts in
        # (`allow_interrupt`) AND a VAD provider is registered — barge-in needs a
        # speech signal. Otherwise the pipeline runs the M3.3 sequential path
        # (uninterruptible playback; graceful fallback). Shares the one Turn Engine;
        # the VAF feeds it speech/silence during assistant playback.
        interrupt = None
        want_interrupt = bool(getattr(agent, "allow_interrupt", False))
        if want_interrupt and registry.has(CapabilityKind.VOICE_ACTIVITY):
            self._interrupt_engine = InterruptEngine(session_id)
            interrupt = InterruptCoordinator(
                engine=self._interrupt_engine, session_id=session_id, turns=turns
            )
        # Voice Activity Framework (M3.2): wired only when the capability is
        # registered — otherwise the pipeline runs exactly as M3.1 (graceful
        # fallback). The VAF shares the one TurnCoordinator, feeding speech-onset
        # signals to the same Turn Engine, and enriches endpoint + interrupt
        # decisions with speech/silence observations.
        voice_activity = None
        if registry.has(CapabilityKind.VOICE_ACTIVITY):
            va_provider = registry.resolve(CapabilityKind.VOICE_ACTIVITY)
            self._voice_activity_engine = VoiceActivityEngine(session_id)
            voice_activity = VoiceActivityCoordinator(
                provider=va_provider,
                engine=self._voice_activity_engine,
                session_id=session_id,
                turns=turns,
                endpoint=endpoint,
                interrupt=interrupt,
            )
        # Smart Turn Intelligence (M3.5): wired only when the agent opts in
        # (`smart_turn`). It arbitrates the endpoint/interrupt decisions via its
        # adaptive policy; an optional registered `SEMANTIC_TURN` evaluator is
        # resolved and blended (a future semantic model registers → no runtime
        # change). Under the default identity policy behaviour is unchanged.
        smart_turn = None
        if bool(getattr(agent, "smart_turn", False)):
            semantic = (
                registry.resolve(CapabilityKind.SEMANTIC_TURN)
                if registry.has(CapabilityKind.SEMANTIC_TURN)
                else None
            )
            self._smart_turn_engine = SmartTurnEngine(
                session_id,
                policy=getattr(agent, "smart_turn_policy", None),
                semantic=semantic,
            )
            smart_turn = SmartTurnCoordinator(
                engine=self._smart_turn_engine, session_id=session_id
            )
        self._pipeline = RuntimePipeline(
            transport=transport,
            stt=stt,
            llm=llm,
            tts=tts,
            session_id=session_id,
            system_prompt=agent.system_prompt,
            language=agent.language,
            tts_voice=agent.tts_voice,
            on_transcript=self._context.on_transcript,
            turns=turns,
            voice_activity=voice_activity,
            endpoint=endpoint,
            interrupt=interrupt,
            smart_turn=smart_turn,
        )
        await self._pipeline.start()

    async def _close_providers(self) -> None:
        if self._pipeline is not None:
            await self._pipeline.stop()
