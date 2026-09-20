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

from core.events import EventMetadata, EventSource
from core.providers.transport import ObservableTransport, TransportEvent
from core.states import SessionState
from events import InProcessEventBus

from ..contracts import RuntimeContext
from ..conversation import ConversationStateMachine, TransitionObserver, TransitionRecord
from ..endpoint import (
    AudioEndpointDetector,
    EndpointCoordinator,
    EndpointEngine,
    EndpointPolicy,
)
from ..errors import OrchestrationError, OrchestrationStartupError
from ..event_bridge import RuntimeEventBridge
from ..faults import Degradation, DegradationKind, DegradationRecorder, ProviderGuard
from ..interrupt import InterruptCoordinator, InterruptEngine
from ..observability_plane import UNWIRED
from ..pipeline.pipeline import RuntimePipeline
from ..registry import CapabilityKind
from ..smart_turn import SmartTurnCoordinator, SmartTurnEngine
from ..speech import GreetingUnavailable, PlaybackEngine, PlaybackScheduler
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
        # Event Bus (M4.1): the single internal communication mechanism. Created on
        # begin; runtime facts are published via the bridge (engines stay decoupled
        # — they never know the bus exists). Exposed read-only. A guard so lifecycle
        # events publish exactly once.
        self._event_bus: InProcessEventBus | None = None
        self._bridge: RuntimeEventBridge | None = None
        # M11.12: the session's `metric.turn` producer and the metric buffer's subscription
        # handle. Both are per session and both are released in `_close_providers`.
        self._latency = None
        self._metrics_subscription = None
        self._session_started = False
        self._lifecycle_closed = False
        # M11.14: providers are released from both terminal paths, so the release must be a
        # once-only fact rather than a once-only *call site*.
        self._providers_closed = False
        # Turn Engine (M3.1): owns turn lifecycle for this conversation. The
        # orchestrator creates it and exposes it read-only; it never manipulates
        # turn state (the pipeline drives it via the coordinator).
        self._turn_engine: TurnEngine | None = None
        # Voice Activity Framework (M3.2): owns voice-activity state, created only
        # when a VAD provider is registered (graceful fallback otherwise). Exposed
        # read-only; driven by the pipeline via the coordinator, never here.
        self._voice_activity_engine: VoiceActivityEngine | None = None
        # AR-23/AR-24 (M11.4): one degradation record per session, and one guard per capability
        # sharing it — so a breaker opened by the transport and a degradation recorded by STT
        # land in the same place a reader can inspect afterwards.
        self._degradations = DegradationRecorder(
            session_id=self._context.session.session_id,
            sink=self._on_degradation,
        )
        self._guards: dict[str, ProviderGuard] = {
            capability: ProviderGuard(capability=capability, degradations=self._degradations)
            for capability in ("transport", "stt", "llm", "tts", "voice_activity")
        }
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

    # ── AR-6: transport reality, reported by the transport, never inferred ──

    def _on_transport_event(self, event: TransportEvent) -> None:
        """Publish one transport-reality fact onto the existing in-process seam.

        The runtime **relays** what the transport observed; it never synthesises a fact from an
        API call it made itself, which is the inversion AR-6 exists to prevent."""
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.transport_reality(event.kind.value, event.detail)
        except Exception as exc:  # noqa: BLE001 — observability never breaks a conversation
            log.warning("transport reality publish failed (ignored): %s", exc)

    # ── AR-24: degradations are declared, evented and recorded ──────────────

    def _on_degradation(self, degradation: Degradation) -> None:
        """Publish one declared degradation onto the existing in-process event seam.

        The **existing** seam, deliberately: ADR-0050 SW1 keeps the Epic 10 event spine unwired
        until M11.12, and AR-2 gives each event type one producer. Nothing here creates a second
        telemetry path."""
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.provider_degraded(degradation.as_payload())
        except Exception as exc:  # noqa: BLE001 — observability never breaks a conversation
            log.warning("degradation publish failed (ignored): %s", exc)

    @property
    def degradations(self) -> DegradationRecorder:
        """Read-only access to this session's degradation record (AR-24)."""
        return self._degradations

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
    def event_bus(self) -> InProcessEventBus | None:
        """Read-only access to the session's Event Bus (created on begin). The
        orchestrator publishes lifecycle events and wires engine observers to it via
        the bridge; it owns no consumer logic."""
        return self._event_bus

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
        # The Event Bus is the single internal communication mechanism (M4.1). Create
        # it first so startup facts (runtime started, providers connected) are events.
        session = self._context.session
        # M11.12: the session's bus is built **through** Epic 10's own extension points — a
        # durable sink and its failure hook — rather than beside them. `plane` is `UNWIRED` by
        # default, in which case this is byte-for-byte the M4.1 bus the runtime has always had.
        plane = getattr(self._context.config, "observability", UNWIRED)
        self._event_bus = InProcessEventBus(
            durable=plane.durable, on_durable_error=plane.on_durable_error
        )
        # The metric buffer subscribes **itself**, for `metric.turn` only (ADR-0039 MB1): the
        # runtime never decodes a metric, never builds a signal, and never reaches the
        # commercial plane.
        if plane.metrics is not None:
            self._metrics_subscription = plane.metrics.subscribe(self._event_bus)
        self._bridge = RuntimeEventBridge(
            self._event_bus,
            session_id=session.session_id,
            org_id=session.org_id,
            conversation_id=session.session_id,
        )
        # The `metric.turn` producer (ADR-0038 TP2, ADR-0033 MS8). One tap per session, holding
        # that session's turn state, publishing as `observability` — the sole authorized producer
        # of runtime `metric.*` (AR-2). Built here because it needs the session's bus.
        if plane.telemetry is not None:
            self._latency = plane.telemetry(
                self._event_bus.publisher(
                    EventMetadata(
                        session_id=session.session_id,
                        source=EventSource.OBSERVABILITY,
                        org_id=session.org_id,
                        conversation_id=session.session_id,
                    )
                )
            )
        self._bridge.runtime_started()
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
            self._bridge.session_started()
            self._session_started = True
        except Exception as exc:  # noqa: BLE001 — re-raised as a typed error
            # During startup we are in CREATED/CONNECTED, from which FAILED is legal.
            if self._sm.can_transition(SessionState.FAILED):
                self._sm.transition(
                    SessionState.FAILED, reason="startup_failed", source="orchestrator"
                )
            self._bridge.runtime_error(f"startup_failed:{type(exc).__name__}")
            self._close_lifecycle(reason="startup_failed")
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
            # Providers are released **before** the state machine terminalizes (M11.14). `fail()`
            # is synchronous — it has to be, because `SessionWorker._cleanup` calls it from a
            # `finally` — so it can close no async resource, and until this line a session that
            # failed mid-conversation left its transport connected, its pipeline unstopped, its
            # playback engine running and its metric subscription registered on a discarded bus.
            # A clean session failure is §A10's last rung; a *leaking* one is not on the ladder.
            await self._close_providers()
            self.fail(reason=f"pipeline_error:{type(exc).__name__}")
            return
        # The transport's inbound stream ended (caller hangup) — end the conversation.
        if not self._sm.is_terminal:
            await self.end(reason="completed", source="transport")

    async def end(self, *, reason: str = "completed", source: str = "orchestrator") -> None:
        """Coordinate graceful shutdown: → ENDING → ENDED. Idempotent once terminal."""
        if self._sm.is_terminal:
            # Still release providers (M11.14): a conversation that terminalized through `fail()`
            # reaches here already terminal, and returning without closing would make the stop
            # that follows a failure the one stop that cleans nothing up. `_close_providers` is
            # idempotent, so the ordinary "ended twice" path costs nothing.
            await self._close_providers()
            self._close_lifecycle(reason=reason)
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
            self._close_lifecycle(reason=reason)
            self._end_event.set()

    async def cancel(self, *, reason: str = "cancelled") -> None:
        """Coordinate cancellation — same terminal path as end(), reason-tagged."""
        await self.end(reason=reason, source="cancel")

    def fail(self, *, reason: str) -> None:
        """Route an unexpected failure through the approved state machine (no hidden
        transitions). Pre-conversation → FAILED; mid-conversation → ENDING → ENDED
        (the machine forbids IN_CONVERSATION → FAILED)."""
        if self._sm.is_terminal:
            self._close_lifecycle(reason=reason)
            self._end_event.set()
            return
        if self._bridge is not None:
            self._bridge.runtime_error(reason)
        if self._sm.can_transition(SessionState.FAILED):
            self._sm.transition(SessionState.FAILED, reason=reason, source="orchestrator")
        else:
            if self._sm.state in (SessionState.IN_CONVERSATION, SessionState.SUSPENDED):
                self._sm.transition(SessionState.ENDING, reason=reason, source="orchestrator")
            if self._sm.state is SessionState.ENDING:
                self._sm.transition(SessionState.ENDED, reason=reason, source="orchestrator")
        self._close_lifecycle(reason=reason)
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
        # The bridge publishes each engine's events to the bus (engines stay
        # decoupled — they only call their existing on_event observer).
        bridge = self._bridge
        transport = registry.resolve(CapabilityKind.TRANSPORT)
        stt = registry.resolve(CapabilityKind.STT, _sel(agent.stt_provider))
        llm = registry.resolve(CapabilityKind.LLM)
        tts = registry.resolve(CapabilityKind.TTS, _sel(agent.tts_provider))
        if bridge is not None:
            for cap, prov in (
                ("transport", transport), ("stt", stt), ("llm", llm), ("tts", tts),
            ):
                bridge.provider_connected(cap, getattr(prov, "name", "?"))
        # AR-6 (M11.5): a transport that knows real facts reports them. Structural, not inherited
        # — `LocalTransport` does not implement `ObservableTransport` and emits nothing, so the
        # local profile is byte-for-byte unchanged and no reality is ever fabricated for it.
        if bridge is not None and isinstance(transport, ObservableTransport):
            transport.observe(session_id, self._on_transport_event)
        self._turn_engine = TurnEngine(
            session_id, on_event=bridge.turn_observer if bridge else None
        )
        turns = TurnCoordinator(self._turn_engine)
        # Endpoint Intelligence Framework (M3.3): ALWAYS wired — it owns turn
        # completion (replacing the STT-final heuristic). The default policy confirms
        # a completed segment immediately, so the conversation behaves as before. It
        # notifies the same Turn Engine (via `turns`) when a turn should finish.
        # M11.8 (ADR-0047 ME1): with audio endpointing the turn is ended by detected
        # end-of-speech, so the policy stops letting a transcript segment confirm — otherwise the
        # audio boundary and the Final would each finish the same turn.
        audio_endpointing = bool(getattr(agent, "audio_endpointing", False))
        self._endpoint_engine = EndpointEngine(
            session_id,
            policy=EndpointPolicy(audio_driven=True) if audio_endpointing else None,
            on_event=bridge.endpoint_observer if bridge else None,
        )
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
            self._interrupt_engine = InterruptEngine(
                session_id, on_event=bridge.interrupt_observer if bridge else None
            )
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
            if bridge is not None:
                bridge.provider_connected(
                    "voice_activity", getattr(va_provider, "name", "?")
                )
            self._voice_activity_engine = VoiceActivityEngine(
                session_id, on_event=bridge.voice_activity_observer if bridge else None
            )
            voice_activity = VoiceActivityCoordinator(
                provider=va_provider,
                engine=self._voice_activity_engine,
                session_id=session_id,
                turns=turns,
                endpoint=endpoint,
                interrupt=interrupt,
            )
        # Audio-driven endpointing (M11.8, ADR-0047 ME1): wired only when the agent opts in AND a
        # VAD provider is registered. The pair is already guaranteed by validation, which refuses
        # the session otherwise (AR-25) — the second half of the condition is a belt-and-braces
        # guard, not a silent fallback. The detector is provider-neutral: it is handed booleans and
        # durations, and the VAD provider's declared `frame_ms` only supplies the duration to
        # credit a frame whose own format declares none (an OPAQUE local-profile frame).
        audio_endpoint = None
        vad_frame_ms = 0.0
        if audio_endpointing and voice_activity is not None:
            audio_endpoint = AudioEndpointDetector()
            vad_frame_ms = float(getattr(va_provider.capabilities, "frame_ms", 0) or 0)

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
                on_event=bridge.smart_turn_observer if bridge else None,
            )
            smart_turn = SmartTurnCoordinator(
                engine=self._smart_turn_engine, session_id=session_id
            )
        # Playback (M11.11, ADR-0048 PB1/PB5/PB7). Wired only when the composition root selected
        # the streaming-playback path. When absent the pipeline runs the M11.9 whole-utterance
        # response exactly as before — no existing deployment changes behaviour by this code
        # existing. There is no vendor branch here: the Scheduler is handed the resolved `tts`
        # capability, whatever it is (AR-21).
        playback = None
        if bool(getattr(self._context.config, "streaming_playback", False)):
            playback = self._build_playback(tts, agent)
        self._pipeline = RuntimePipeline(
            transport=transport,
            stt=stt,
            llm=llm,
            tts=tts,
            session_id=session_id,
            system_prompt=agent.system_prompt,
            sample_rate=agent.sample_rate,  # session property, never ambient (AR-41)
            llm_model=agent.llm_model,  # AR-25 (M11.4): accepted config now acts
            language=agent.language,
            tts_voice=agent.tts_voice,
            on_transcript=self._context.on_transcript,
            turns=turns,
            voice_activity=voice_activity,
            endpoint=endpoint,
            interrupt=interrupt,
            smart_turn=smart_turn,
            guards=self._guards,
            audio_endpoint=audio_endpoint,
            vad_frame_ms=vad_frame_ms,
            playback=playback,
            latency=self._latency,
        )
        await self._pipeline.start()
        await self._play_greeting(playback, agent)

    def _build_playback(self, tts, agent) -> PlaybackScheduler:
        """Assemble the Playback Engine + Scheduler for this session (M11.11).

        The Scheduler synthesizes through the **guarded** TTS call, so the breaker, per-item
        deadline and typed-error translation stay exactly where AR-23 put them rather than being
        re-implemented one layer up. Its degradation callback records the `streaming_input=False`
        adaptation through the same `DegradationRecorder` every other capability uses (AR-24).

        The Engine's route to the transport is attached by the pipeline at `start()`, because the
        guarded transport behind the single egress normalizer is a pipeline-owned detail."""
        guard = self._guards.get("tts")

        def synthesize(text: str, *, voice=None, language="en"):
            source = tts.synthesize(text, voice=voice, language=language)
            if guard is None:
                return source
            return guard.stream(tts.name, source, what="synthesize")

        def on_degraded(provider: str) -> None:
            if self._degradations is not None:
                self._degradations.record(
                    Degradation(
                        capability="tts",
                        kind=DegradationKind.NOT_STREAMING,
                        provider=provider,
                        detail="the provider declares no streaming text input",
                        fallback="synthesizing one clause per request",
                    )
                )

        return PlaybackScheduler(
            tts,
            PlaybackEngine(),
            voice=agent.tts_voice,
            language=agent.language,
            synthesize=synthesize,
            on_degraded=on_degraded,
        )

    async def _play_greeting(self, playback, agent) -> None:
        """Speak the agent's `first_message`, once, at session start (ADR-0048 PB8, D-11).

        Session-initiated by construction: this runs inside session startup, never at import or
        bootstrap. A greeting that cannot be synthesized is **not** fatal — the caller simply is
        not greeted and the conversation proceeds, which is why `GreetingUnavailable` is caught
        here rather than allowed to fail the session.

        `first_message` has been accepted and unspoken since M2C.2; this is where AR-25 is
        actually discharged for it — but only for sessions that run the playback path, which is
        why `GreetingState` keeps its existing vocabulary (R-62)."""
        if playback is None or not agent.first_message:
            return
        try:
            await playback.precache_greeting(agent.first_message)
            await playback.engine.start()
            await playback.play_greeting()
        except asyncio.CancelledError:
            raise
        except GreetingUnavailable as exc:
            log.warning(
                "session %s: greeting unavailable (%s)",
                self._context.session.session_id, exc,
            )

    async def _close_providers(self) -> None:
        """Release everything the conversation opened. **Idempotent** (M11.14).

        Called from both terminal paths now — the graceful `end()` and the failure routed through
        `run()` — and a session that fails and is then stopped reaches it twice. Tearing a
        transport down twice is not merely wasteful: `provider_disconnected` would be published
        again, and §16 requires no duplicate terminal event."""
        if self._providers_closed:
            return
        self._providers_closed = True
        if self._pipeline is not None:
            await self._pipeline.stop()
        # M11.12: release the per-session observability attachments. The metric buffer is
        # process-wide and long-lived, so leaving a dead session's handler registered on a bus
        # that is about to be discarded would be the leak this milestone must not introduce.
        subscription, self._metrics_subscription = self._metrics_subscription, None
        if subscription is not None and self._event_bus is not None:
            # `bus.subscribe` returns a `Subscription`; the bus owns removal, and removal is
            # idempotent — so a second close is harmless.
            self._event_bus.unsubscribe(subscription)
        # The tap holds this session's in-flight turn state; it is discarded with the session.
        self._latency = None
        registry = self._context.registry
        agent = self._context.agent
        if self._bridge is not None and registry is not None and agent is not None:
            for cap in ("tts", "llm", "stt", "transport"):
                self._bridge.provider_disconnected(cap, "?")

    def _close_lifecycle(self, *, reason: str | None = None) -> None:
        """Publish the terminal lifecycle events (once) and shut the bus down. Safe
        to call from every terminal path — idempotent via `_lifecycle_closed`."""
        if self._lifecycle_closed:
            return
        self._lifecycle_closed = True
        if self._bridge is not None:
            if self._session_started:
                self._bridge.session_ended(reason)
            self._bridge.runtime_stopped()
        if self._event_bus is not None:
            self._event_bus.shutdown()
