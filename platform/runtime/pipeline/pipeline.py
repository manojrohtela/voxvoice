"""RuntimePipeline — the streaming execution pipeline for one conversation (M2C.2).

    caller audio ─► Transport.inbound ─► STT.transcribe ─► (per Final utterance)
      ContextBuilder(minimal) ─► LLM.stream ─► TTS.synthesize ─► Transport.send ─► caller

Executes the conversation the Orchestrator owns. Every stage is reached ONLY
through its `core.providers` contract — no vendor names, no provider-specific
branching (Constitution §A6, AR-21). Providers are injected (resolved from the
Provider Registry by the Orchestrator); the pipeline never constructs one.

Scope (M2C.2 + M3.3): minimum streaming flow for a real multi-turn conversation.
Turn *completion* is decided by the Endpoint Intelligence Framework (M3.3): the
pipeline no longer responds on every STT Final — it feeds transcript segments to
the `EndpointCoordinator` and responds only when an endpoint decision is *final*.
With the default policy a completed segment confirms immediately, so the local
profile behaves exactly as before. NO Memory / Prompt Runtime (context is the
system prompt + the current utterance — M5 owns memory). Transcripts are delivered
via an injected sink (the future Event Bus integration point, M4).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from core.providers.llm import TokenDelta
from core.providers.stt import Final, Partial

from ..contracts import LatencyObserver, TranscriptSink
from ..endpoint import FINAL_ENDPOINT_DECISIONS, AudioEndpointKind, frame_duration_ms
from ..faults import DegradationKind, ProviderGuard
from ..interrupt import InterruptDecision
from ..smart_turn import FINISH_DECISIONS, PREEMPT_DECISIONS
from ..speech import ClauseDetector, ClauseRouter
from ..voice_activity import VoiceActivityState
from .audio import AudioNormalizer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.audio import AudioFrame
    from core.providers.llm import LLMProvider
    from core.providers.stt import STTProvider
    from core.providers.transport import TransportProvider
    from core.providers.tts import TTSProvider

    from ..endpoint import AudioEndpointDetector, EndpointCoordinator, EndpointDecision
    from ..interrupt import InterruptCoordinator
    from ..smart_turn import SmartTurnCoordinator
    from ..speech import ClausePolicy, ClauseSink, PlaybackScheduler
    from ..turn import TurnCoordinator
    from ..voice_activity import VoiceActivityCoordinator

log = logging.getLogger("voxvoice.runtime.pipeline")


class RuntimePipeline:
    def __init__(
        self,
        *,
        transport: TransportProvider,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        session_id: str,
        system_prompt: str,
        sample_rate: int,
        language: str = "en",
        llm_model: str | None = None,
        tts_voice: str | None = None,
        on_transcript: TranscriptSink | None = None,
        turns: TurnCoordinator | None = None,
        voice_activity: VoiceActivityCoordinator | None = None,
        endpoint: EndpointCoordinator | None = None,
        interrupt: InterruptCoordinator | None = None,
        smart_turn: SmartTurnCoordinator | None = None,
        latency: LatencyObserver | None = None,
        guards: dict[str, ProviderGuard] | None = None,
        audio_endpoint: AudioEndpointDetector | None = None,
        vad_frame_ms: float = 0.0,
        clause_sink: ClauseSink | None = None,
        clause_policy: ClausePolicy | None = None,
        playback: PlaybackScheduler | None = None,
    ) -> None:
        self._transport = transport
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._language = language
        # The session's declared rate (ADR-0044 AD3) — required, never defaulted here.
        # AR-41 forbids an ambient assumption, and until M11.2 this parameter carried a
        # hardcoded 16000 that the Orchestrator never overrode, which *was* that assumption.
        self._sample_rate = sample_rate
        # The one ingress + one egress conversion mechanism (ADR-0044 AD4, AR-41). Owned
        # here so providers never resample and `core` stays behaviour-free.
        self._audio = AudioNormalizer(sample_rate)
        # AR-23 (M11.4, ADR-0046 FT-D1/FT-D4). Every capability call below goes through a guard —
        # timeout, typed error, circuit breaker, ordered failover. Absent guards mean an
        # unguarded pipeline, which is the pre-M11.4 defect; `_guard()` therefore *always*
        # returns one rather than branching on presence, so a caller cannot accidentally opt out.
        self._guards = dict(guards or {})
        # AR-25 (M11.4): the session's selected model, carried to the provider-neutral LLM
        # contract's additive `model` argument. Accepted-and-ignored since M2C.2 until now.
        self._llm_model = llm_model
        self._tts_voice = tts_voice
        self._on_transcript = on_transcript
        # Turn Engine integration (M3.1): notify-only. When absent, the pipeline
        # behaves exactly as before — turn tracking never affects speech/audio.
        self._turns = turns
        # Voice Activity Framework integration (M3.2): analyzes a copy of each
        # inbound frame; notify-only. When absent, the inbound stream is handed to
        # STT untapped — the pipeline behaves exactly as M3.1.
        self._voice_activity = voice_activity
        # Endpoint Intelligence Framework integration (M3.3): decides when a caller's
        # turn is complete. The pipeline feeds it transcript segments and responds
        # only on a *final* endpoint decision. When absent (a lifecycle-only path)
        # the pipeline falls back to the legacy respond-on-Final behaviour.
        self._endpoint = endpoint
        # Interrupt Intelligence Framework integration (M3.4): when present, assistant
        # playback runs concurrently with inbound reading so caller barge-in can
        # pre-empt it. When absent, the pipeline runs the M3.3 sequential path
        # (assistant playback is uninterruptible) — graceful fallback, unchanged.
        self._interrupt = interrupt
        # Smart Turn Intelligence Framework integration (M3.5): when present, the
        # pipeline routes endpoint/interrupt decisions through the Smart Turn engine
        # (arbitration + adaptive policy + optional semantic hint). When absent, the
        # pipeline consumes the raw endpoint/interrupt decisions exactly as M3.4.
        self._smart_turn = smart_turn
        # Latency observation (M10.9, ADR-0038 TP2): notify-only, and the same optional shape
        # every framework above uses. When absent the pipeline behaves *exactly* as before — no
        # branch below does anything but call `_tap`, which returns immediately.
        self._latency = latency
        # The in-flight assistant response task (interruptible path only).
        self._response_task: asyncio.Task[None] | None = None
        # The most recent Final transcript awaiting an endpoint decision (so the
        # stream-end flush can close out a turn without dropping the utterance).
        self._pending_text: str | None = None
        # Audio-driven endpointing (M11.8, ADR-0047 ME1). When present, the caller's turn is ended
        # by **audio** — a detected end-of-speech — instead of by an STT Final. Absent (the
        # default) the pipeline behaves exactly as M3.3/M11.7 did.
        self._audio_endpoint = audio_endpoint
        # The duration to credit a frame whose format does not declare one (an OPAQUE local-profile
        # frame). Supplied by the composition root from the VAD provider's declared `frame_ms`.
        self._vad_frame_ms = vad_frame_ms
        # True once audio closed the user turn and the response has not yet been produced.
        self._audio_turn_closed = False
        # The turn a terminal tap belongs to. A turn is *archived* by the transition that ends it,
        # so by the time `_tap_terminal` runs the engine has no current turn and would report 0 —
        # which silently orphaned every `metric.turn` of a session that wired a Turn Engine
        # (M11.8, DL-0186). Remembering the last active index keeps the terminal attributed to the
        # turn whose boundaries were recorded.
        self._last_turn_index = 0
        # Clause-boundary LLM→TTS pipelining (M11.9, ADR-0048 PB1). When a sink is injected, the
        # assistant's streamed text is segmented into clauses and each completed clause is handed
        # over **as it forms**, rather than after generation. When absent (the default) the
        # detector is not even constructed and the response path behaves exactly as M11.8's.
        #
        # A fresh detector and router are built **per response**: clause state belongs to one
        # assistant turn, and a detector shared across turns would carry a previous answer's tail
        # into the next one. The sink itself is long-lived, because M11.10's consumer will be.
        # Playback (M11.10, ADR-0048 PB5/PB7). When a Scheduler is wired the response path becomes
        # clause-driven: text becomes audio while the model is still writing, and the whole-input
        # wait in front of synthesis is gone. When absent (the default) the legacy whole-utterance
        # path runs exactly as M11.9 left it, so no existing profile changes behaviour.
        self._playback = playback
        # The Scheduler *is* a `ClauseSink`, so wiring playback wires the clause seam with it.
        # An explicit sink still wins, which keeps M11.9's collector usable for observation.
        self._clause_sink = clause_sink if clause_sink is not None else playback
        self._clause_policy = clause_policy
        # Whether `first_audio_out` is still owed for the current response.
        self._first_audio_out_pending = True

    # ── AR-23: the fault-tolerance seam every provider call passes through ────

    def _guard(self, capability: str) -> ProviderGuard:
        """The guard for one capability, created on demand so **no call site can be unguarded**.

        AR-23 says direct raw provider calls are defects, so this deliberately does not return
        `None`: a pipeline constructed without guards gets default ones rather than silently
        reverting to the pre-M11.4 behaviour. A composition root supplies configured guards
        (shared breakers, tuned deadlines); everything else still gets the floor."""
        guard = self._guards.get(capability)
        if guard is None:
            guard = ProviderGuard(capability=capability)
            self._guards[capability] = guard
        return guard

    # ── lifecycle (provider startup/shutdown; coordinated by the Orchestrator) ─
    def _attach_playback(self) -> None:
        """Bind the Playback Scheduler to this session's output route (M11.10).

        Done here rather than at construction because the route — the guarded transport behind the
        single egress normalizer — is a property of *this* session, while the Scheduler and Engine
        are ordinary objects a composition root or a test can build anywhere."""
        if self._playback is None:
            return
        self._playback.attach(
            send=self._send_outbound, on_first_audio=self._on_first_synthesized_audio
        )

    async def start(self) -> None:
        t0 = time.monotonic()
        self._attach_playback()
        await self._guard("transport").call(
            self._transport.name,
            lambda: self._transport.start(session_id=self._session_id),
            what="start",
        )
        # Voice activity is auxiliary (DL-0013): a startup failure degrades to the
        # untapped pipeline (logged, never fatal) rather than breaking the call.
        if self._voice_activity is not None:
            try:
                await self._voice_activity.start()
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                log.warning(
                    "pipeline %s: voice-activity start failed; continuing without VAD: %s",
                    self._session_id, exc,
                )
                # DL-0013 says degrade rather than break; **AR-24 says say so**. Before M11.4
                # this was a log line and nothing else, which is exactly the silent degradation
                # AR-24 forbids — the call continued and no record showed VAD was absent.
                self._guard("voice_activity").auxiliary_failed(
                    getattr(self._voice_activity, "provider_name", "voice_activity"),
                    f"start failed: {exc}",
                    fallback="conversation continues without voice activity",
                )
                self._voice_activity = None
        log.info(
            "pipeline %s started (transport=%s stt=%s llm=%s tts=%s) in %.1fms",
            self._session_id, self._transport.name, self._stt.name, self._llm.name,
            self._tts.name, (time.monotonic() - t0) * 1000,
        )

    async def stop(self) -> None:
        if self._playback is not None:
            await self._playback.engine.stop()
        t0 = time.monotonic()
        if self._response_task is not None and not self._response_task.done():
            self._response_task.cancel()  # cancel in-flight assistant playback
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass
        if self._smart_turn is not None:
            self._smart_turn.cancel()  # clear adaptive/smoothing state
        if self._interrupt is not None:
            self._interrupt.cancel()  # clear any in-flight interrupt state
        if self._endpoint is not None:
            self._endpoint.cancel()  # terminalize any pending endpoint on shutdown
        if self._audio_endpoint is not None:
            # Discard the in-progress utterance without emitting a boundary: a `speech_end` the
            # audio never produced would be a fabricated measurement (ADR-0038 TP5).
            self._audio_endpoint.reset()
            self._audio_turn_closed = False
        if self._turns is not None:
            self._turns.cancel()  # terminalize any active turn on shutdown
        self._tap_terminal("cancelled")  # emits only if boundaries were recorded (TP6)
        if self._voice_activity is not None:
            await self._voice_activity.stop()  # best-effort provider teardown
        await self._guard("transport").call(
            self._transport.name,
            lambda: self._transport.stop(session_id=self._session_id),
            what="stop",
        )
        log.info(
            "pipeline %s stopped in %.1fms", self._session_id, (time.monotonic() - t0) * 1000
        )

    # ── the streaming turn loop ───────────────────────────────────────────────
    async def run(self) -> None:
        """Drive the conversation until the transport's inbound stream ends
        (caller hangup) or the pipeline is stopped. Streams STT → LLM → TTS.

        When Interrupt Intelligence is wired the interruptible path runs assistant
        playback concurrently with inbound reading (barge-in). Otherwise the M3.3
        sequential path runs unchanged (graceful fallback)."""
        if self._interrupt is not None:
            await self._run_interruptible()
            return
        log.info("pipeline %s conversation started", self._session_id)
        inbound = self._inbound()
        async for result in self._guard("stt").stream(
            self._stt.name,
            self._stt.transcribe(
                inbound, language=self._language, sample_rate=self._sample_rate
            ),
            what="transcribe",
        ):
            if isinstance(result, Partial):
                if self._turns is not None:
                    self._turns.on_user_speech()
                if self._endpoint is not None:
                    self._endpoint.on_user_speech()  # speech in progress; keeps turn open
                self._emit("user", result.text, is_final=False)
            elif isinstance(result, Final):
                self._tap("stt_final")
                self._emit("user", result.text, is_final=True)
                await self._on_segment(result.text)
        # Stream ended (caller hangup): close out a turn still awaiting confirmation
        # so the last utterance is never silently dropped.
        self._flush_audio_endpoint()
        if self._audio_endpoint_owes_response() and self._pending_text is not None:
            self._audio_turn_closed = False
            await self._complete_turn()
        elif self._endpoint is not None and self._pending_text is not None:
            decision = self._endpoint.flush()
            if self._endpoint_finishes(decision):
                await self._complete_turn()
        log.info("pipeline %s conversation ended", self._session_id)

    async def _on_segment(self, user_text: str) -> None:
        """A completed transcript segment. The Endpoint Intelligence Framework
        decides whether it finishes the turn (M3.3). Without a coordinator the
        pipeline falls back to the legacy respond-on-Final behaviour."""
        if self._endpoint is None:
            if self._turns is not None:
                self._turns.on_user_final()
            await self._respond(user_text)
            return
        self._pending_text = user_text
        decision = self._endpoint.on_segment()  # notifies the Turn Engine if final
        if self._audio_endpoint_owes_response():
            # Audio already decided this turn was over (ME1); the transcript is what was missing.
            self._audio_turn_closed = False
            await self._complete_turn()
            return
        if self._endpoint_finishes(decision):
            self._tap("endpoint_decided")
            await self._complete_turn()

    async def _complete_turn(self) -> None:
        """Respond to the caller's now-complete turn and ready the endpoint engine
        for the next one. The Turn Engine was already told the user finished by the
        EndpointCoordinator on the final decision."""
        text = self._pending_text
        self._pending_text = None
        if text is None:
            return
        await self._respond_once(text)

    # ── the interruptible turn loop (M3.4; barge-in enabled) ─────────────────
    async def _run_interruptible(self) -> None:
        """Like `run()`, but assistant playback runs as a concurrent task so caller
        barge-in can pre-empt it. The inbound tap feeds the Voice Activity Framework
        (which notifies the Interrupt coordinator); a confirmed interrupt sets the
        coordinator's `stop_requested` signal, which this loop acts on."""
        log.info("pipeline %s conversation started (interruptible)", self._session_id)
        inbound = self._inbound()
        async for result in self._guard("stt").stream(
            self._stt.name,
            self._stt.transcribe(
                inbound, language=self._language, sample_rate=self._sample_rate
            ),
            what="transcribe",
        ):
            # A barge-in confirmed while the assistant is speaking → pre-empt now,
            # unless Smart Turn arbitrates it away (IGNORE_INTERRUPT → keep speaking).
            if self._interrupt.stop_requested.is_set() and self._response_active():
                if self._interrupt_preempts():
                    await self._preempt_response()
                else:
                    self._ignore_interrupt()
            if isinstance(result, Partial):
                if self._turns is not None:
                    self._turns.on_user_speech()
                if self._endpoint is not None:
                    self._endpoint.on_user_speech()
                self._emit("user", result.text, is_final=False)
            elif isinstance(result, Final):
                self._tap("stt_final")
                self._emit("user", result.text, is_final=True)
                # Guard: never overlap two responses. If one is still playing and no
                # interrupt was confirmed, let it finish before starting the next.
                if self._response_active():
                    await self._await_response()
                self._start_segment(result.text)
        await self._await_response()  # drain a still-playing response before ending
        self._flush_audio_endpoint()
        if self._audio_endpoint_owes_response() and self._pending_text is not None:
            self._audio_turn_closed = False
            self._start_response()
            await self._await_response()
        elif self._endpoint is not None and self._pending_text is not None:
            decision = self._endpoint.flush()
            if decision in FINAL_ENDPOINT_DECISIONS:
                self._start_response()
                await self._await_response()
        log.info("pipeline %s conversation ended", self._session_id)

    def _response_active(self) -> bool:
        return self._response_task is not None and not self._response_task.done()

    # ── Smart Turn arbitration (M3.5; consumes the sub-frameworks' decisions) ─
    def _endpoint_finishes(self, decision: EndpointDecision | None) -> bool:
        """Whether an endpoint decision should finish the user turn (respond). Smart
        Turn (when wired) arbitrates via its adaptive policy; otherwise the raw
        endpoint FINAL decisions apply (exact M3.4 behaviour). The adaptive hold
        attached to a DELAY_RESPONSE is exposed on the engine (`pending_delay_frames`)
        for a real transport scheduler — the in-process profile records it, the
        decision still resolves to respond."""
        if decision is None:
            return False
        if self._smart_turn is None:
            return decision in FINAL_ENDPOINT_DECISIONS
        st = self._smart_turn.on_endpoint(
            decision, assistant_speaking=self._response_active()
        )
        return st in FINISH_DECISIONS

    def _interrupt_preempts(self) -> bool:
        """Whether a confirmed interrupt should pre-empt playback. Smart Turn (when
        wired) may IGNORE it per the dynamic threshold; otherwise a confirmed
        interrupt always pre-empts (exact M3.4 behaviour)."""
        if self._smart_turn is None:
            return True
        st = self._smart_turn.on_interrupt(
            InterruptDecision.INTERRUPT_CONFIRMED, assistant_speaking=True
        )
        return st in PREEMPT_DECISIONS

    def _ignore_interrupt(self) -> None:
        """Smart Turn ignored a confirmed barge-in: clear the interrupt and re-arm
        monitoring so the assistant keeps speaking (RESUME_ASSISTANT)."""
        if self._interrupt is not None:
            self._interrupt.reset()  # clear confirmed + stop_requested
            self._interrupt.on_assistant_started()  # re-arm while still speaking

    def _start_segment(self, user_text: str) -> None:
        """Endpoint decides; on a final decision launch the response as a task."""
        self._pending_text = user_text
        decision = self._endpoint.on_segment() if self._endpoint is not None else None
        if self._audio_endpoint_owes_response():
            self._audio_turn_closed = False
            self._start_response()
            return
        if self._endpoint_finishes(decision):
            self._tap("endpoint_decided")
            self._start_response()

    def _start_response(self) -> None:
        text = self._pending_text
        self._pending_text = None
        if text is None:
            return
        self._response_task = asyncio.create_task(self._respond_task(text))

    async def _respond_task(self, user_text: str) -> None:
        await self._respond_once(user_text)

    async def _respond_once(self, user_text: str) -> None:
        """Produce one response and leave the session ready for the next turn either way.

        **Why this wrapper exists (M11.14).** Both response paths used to call `_respond` and then
        reset the endpoint on the next line. A provider failure skipped that line — and, worse,
        left the Turn Engine parked in `ASSISTANT_THINKING` for a turn that would never complete.
        The *next* utterance then asked the engine to enter `ASSISTANT_THINKING` again, the engine
        correctly refused an illegal transition, and the session went permanently deaf: every
        later turn failed at its first transition, with no transcript, no audio and no second
        degradation to explain it. One vendor hiccup cost the whole call.

        A response failure is **turn-scoped** (§A10, §A4.4): the capability's guard has already
        typed the error and recorded the degradation, so the obligation here is only to retire the
        turn deterministically and let the caller speak again. Nothing is retried, nothing is
        fabricated, and the session is not ended — ending it would be the process-wide recovery for
        a turn-local failure that the failure-scope model forbids."""
        try:
            await self._respond(user_text)
        except asyncio.CancelledError:
            # Rule 2 everywhere: the caller withdrawing is not the vendor misbehaving. The
            # interrupt path owns its own cleanup; this must not run a second, competing one.
            raise
        except Exception as exc:  # noqa: BLE001 — typed and recorded by the capability's guard
            log.warning(
                "pipeline %s: response failed (%s); retiring the turn",
                self._session_id, type(exc).__name__,
            )
            self._retire_failed_turn()
            return
        if self._endpoint is not None:
            self._endpoint.reset()  # back to CONTINUE_LISTENING for the next turn

    def _retire_failed_turn(self) -> None:
        """Retire a turn whose response could not be produced (M11.14).

        The same deterministic retirement `_abandon_closed_turn` already performs for a turn that
        produced no transcript — `TURN_CANCELLED` is the existing phase for *"this turn will not
        be completed"*, and the terminal tap releases its metric state (ADR-0038 TP6/TP8) without
        inventing a boundary the audio never reached. `cancelled` is the honest status: the turn
        did not complete, and `metric.turn` has no `failed` in its vocabulary — M11.14 does not
        change that vocabulary."""
        self._pending_text = None
        self._audio_turn_closed = False
        # The next response owes its own `first_audio_out`; leaving this False would suppress the
        # boundary on the turn that finally succeeds.
        self._first_audio_out_pending = True
        if self._turns is not None:
            self._turns.cancel()  # idempotent — a no-op if the turn already terminalized
        self._tap_terminal("cancelled")  # emits only if boundaries were recorded (TP6)
        if self._interrupt is not None:
            self._interrupt.cancel()  # drop any half-armed barge-in state
        if self._endpoint is not None:
            self._endpoint.reset()  # back to CONTINUE_LISTENING for the next turn

    async def _await_response(self) -> None:
        task = self._response_task
        self._response_task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:  # pragma: no cover — defensive
                pass

    async def _preempt_response(self) -> None:
        """Pre-empt assistant playback for a confirmed interrupt: stop the playback
        task, cancel the interrupted turn, and hand off to a new user turn. The new
        turn itself begins via the normal speech path (Turn Engine stays the owner)."""
        self._interrupt.begin_stop()  # INTERRUPT_CONFIRMED → ASSISTANT_STOPPING
        task = self._response_task
        self._response_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._playback is not None:
            # Discard queued assistant audio *before* the new user turn opens. Anything still
            # buffered answers a question the caller has already interrupted, and letting it play
            # out is exactly the stale-audio defect barge-in exists to prevent (ADR-0048 PB6).
            await self._playback.abandon()
        self._interrupt.start_user_turn()  # → USER_TURN_STARTING (cancels the turn)
        self._interrupt.complete()  # → INTERRUPT_COMPLETED
        self._tap_terminal("interrupted")
        if self._endpoint is not None:
            self._endpoint.reset()  # drop any stale pending decision
        # The interrupted turn is over; the caller's *new* utterance is already in progress, so the
        # detector keeps its state and only the "a transcript is owed" flag is cleared.
        self._audio_turn_closed = False
        self._pending_text = None

    async def _generate(self, context: list[dict[str, str]]) -> str:
        """Stream the assistant's reply, routing completed clauses onward as they form.

        Two things happen to the same stream, and they are not the same thing:

        1. **Clause routing (M11.9, ADR-0048 PB1).** Each `TokenDelta` is fed to the clause
           detector and every completed clause reaches the sink immediately — the first one long
           before generation finishes. This is the streaming topology §A8 requires.
        2. **Text assembly.** The joined text is still returned, because the *transcript* is a
           whole-utterance artefact (`agent.transcript` carries the final text) and because the
           existing whole-utterance `synthesize()` call still consumes it.

        Point 2 is why **ADR-0048 PB2 is not yet discharged**: the wait-for-whole-input step in
        front of TTS still exists, since no TTS provider can accept clauses until M11.10 builds
        the streaming-input path (PB3) and the Playback Engine (PB5). M11.9 builds the producing
        half and proves it delivers incrementally; M11.10 removes the wait by connecting the
        consuming half. Claiming the removal here would be claiming a latency improvement that the
        audio path does not yet have."""
        router = None
        if self._clause_sink is not None:
            router = ClauseRouter(ClauseDetector(self._clause_policy), self._clause_sink)
        parts: list[str] = []
        first_token = True
        try:
            async for delta in self._guard("llm").stream(
                self._llm.name,
                self._llm.stream(context=context, model=self._llm_model),
                what="stream",
            ):
                if isinstance(delta, TokenDelta):
                    if first_token:
                        # The *first* actual token, observed inside the stream — the boundary no
                        # bus-derived design can see (ADR-0038 TP3 #4).
                        self._tap("llm_first_token")
                        first_token = False
                    parts.append(delta.text)
                    if router is not None:
                        await router.feed(delta.text)
        except BaseException:
            # Every non-normal exit — a provider failure, and the cancellation of an interrupted
            # turn — abandons the response. The buffered tail is dropped rather than flushed: the
            # conversation has moved on, and speaking the fragment the model had reached would
            # answer a question that is no longer being asked.
            if router is not None:
                await _abandon(router)
            raise
        if router is not None:
            # The natural end of generation: the tail is flushed as a final clause, so no text the
            # model produced is ever silently lost.
            await router.finish()
        return "".join(parts).strip()

    async def _respond_through_playback(self, context: list[dict[str, str]]) -> None:
        """The M11.10 response path: clauses become audio **while the model is still writing**.

        This is ADR-0048 PB1/PB2 completed on the consuming side. The legacy path below waits for
        the whole response, then synthesizes it, then sends it; here the clause detector hands each
        completed clause to the Playback Scheduler, which synthesizes it and feeds the Playback
        Engine, which sends frames to the transport under bounded back-pressure. Nothing waits for
        the whole anything (§A8).

        The turn does not transition to *assistant speaking* when generation starts — it
        transitions when the **first audio frame actually exists**, through the Scheduler's
        first-audio callback. Announcing it earlier would claim speech for a response that may
        still turn out to be empty or to fail before a single frame is produced."""
        playback = self._playback
        assert playback is not None  # guarded by the caller
        playback.begin_response()
        # Each turn stamps its own `first_audio_out`; the boundary belongs to a turn, not to a
        # session (ADR-0038 TP6).
        self._first_audio_out_pending = True
        # Recorded once per response: a provider that cannot accept text as it is produced is
        # synthesized clause by clause, and AR-24 forbids adapting invisibly (ADR-0045 SD6).
        playback.note_input_mode()
        await playback.engine.start()
        try:
            agent_text = await self._generate(context)
        except BaseException:
            await playback.engine.cancel()
            raise
        if agent_text:
            self._emit("agent", agent_text, is_final=True)
        if self._interrupt is not None and self._interrupt.stop_requested.is_set():
            # A barge-in landed while the response was being spoken. The queued tail is already
            # discarded by the scheduler's `abandon`; the run loop drives the interrupt phase.
            self._tap_terminal("interrupted")
            return
        if self._turns is not None:
            if agent_text:
                self._turns.on_turn_completed()
            else:
                # No text at all: the turn still completes, and no speech was ever claimed.
                self._turns.on_turn_completed()
        if self._interrupt is not None and agent_text:
            self._interrupt.on_assistant_finished()
        self._tap_terminal("completed")

    def _on_first_synthesized_audio(self) -> None:
        """The first audio of a response exists (ADR-0038 TP3 #5, `tts_first_audio`).

        Called by the Playback Scheduler at the moment the first frame comes back from TTS —
        **not** when synthesis was requested, and not when the text was ready. This is also where
        the assistant genuinely starts speaking, so the turn transition and the barge-in monitor
        start here rather than on an assumption made earlier."""
        self._tap("tts_first_audio")
        self._tap("playback_start")  # lifecycle marker, not a latency stage (TP3)
        if self._turns is not None:
            self._turns.on_assistant_speaking()
        if self._interrupt is not None:
            self._interrupt.on_assistant_started()  # begin barge-in monitoring

    async def _send_outbound(self, frame: AudioFrame) -> None:
        """MEDIA EGRESS: the one and only outbound conversion point (AR-41, ADR-0044 AD4).

        **Both** response paths send through here — the legacy whole-utterance path and the
        M11.10 playback path — so there is literally one place in the pipeline where a frame is
        converted on its way out, rather than one per path. M11.10 briefly had two, and the M11.2
        guard caught it; unifying is what makes AR-41 true of the code rather than of one branch.

        `first_audio_out` is stamped **after** the transport has accepted the frame, so a
        back-pressured send moves the metric rather than being papered over (ADR-0038 TP3 #6)."""
        outbound = self._audio.normalize(frame)
        await self._guard("transport").call(
            self._transport.name,
            lambda f=outbound: self._transport.send(session_id=self._session_id, frame=f),
            what="send",
        )
        if self._first_audio_out_pending:
            self._first_audio_out_pending = False
            # The caller is now hearing audio — the terminus of voice-to-voice (TP3 #6).
            self._tap("first_audio_out")

    async def _respond(self, user_text: str) -> None:
        if self._turns is not None:
            self._turns.on_assistant_thinking()
        # Minimal context assembly (Memory/Prompt Runtime are M5).
        context = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_text},
        ]
        if self._playback is not None:
            await self._respond_through_playback(context)
            return
        agent_text = await self._generate(context)
        if not agent_text:
            if self._turns is not None:
                self._turns.on_turn_completed()  # empty response still completes the turn
            self._tap_terminal("completed")
            return
        if self._turns is not None:
            self._turns.on_assistant_speaking()
        self._tap("playback_start")  # lifecycle marker, not a latency stage (TP3)
        if self._interrupt is not None:
            self._interrupt.on_assistant_started()  # begin barge-in monitoring
        self._emit("agent", agent_text, is_final=True)
        first_audio = True
        # Each turn stamps its own `first_audio_out` (ADR-0038 TP6).
        self._first_audio_out_pending = True
        async for frame in self._guard("tts").stream(
            self._tts.name,
            self._tts.synthesize(agent_text, voice=self._tts_voice, language=self._language),
            what="synthesize",
        ):
            if first_audio:
                self._tap("tts_first_audio")  # TP3 #5
                first_audio = False
            if self._interrupt is not None and self._interrupt.stop_requested.is_set():
                # A barge-in was confirmed → stop playback gracefully. The run loop
                # drives the interrupt action phase + the new user turn.
                self._tap_terminal("interrupted")
                return
            await self._send_outbound(frame)
        if self._interrupt is not None:
            self._interrupt.on_assistant_finished()  # playback completed on its own
        if self._turns is not None:
            self._turns.on_turn_completed()
        self._tap_terminal("completed")

    # ── media ingress (M11.2, ADR-0044 AD4/AD5) ──────────────────────────────

    def _inbound(self) -> AsyncIterator[AudioFrame]:
        """The single media ingress: transport → normalize → (voice-activity tap) → STT.

        Both run loops compose their inbound stream here, so the pipeline has exactly
        **one** ingress conversion point and one egress point — AR-41's requirement, and
        a property a test can assert rather than a convention a reader must trust.

        Normalization happens **before** the tap (AD5), so the Voice Activity Framework
        and STT observe the *identical* normalized frame. That preserves the M3.2
        invariant — the tap yields onward exactly what it observed — now over audio at
        the session's declared rate."""
        frames = self._normalize_ingress(
            self._guard("transport").stream(
                self._transport.name,
                self._transport.inbound(session_id=self._session_id),
                what="inbound",
            )
        )
        if self._voice_activity is not None:
            frames = self._tap_voice_activity(frames)
        return frames

    async def _normalize_ingress(
        self, frames: AsyncIterator[AudioFrame]
    ) -> AsyncIterator[AudioFrame]:
        """MEDIA INGRESS: the one and only inbound conversion point (AR-41). A frame
        already at the session rate is passed through untouched — the common case, which
        allocates nothing."""
        async for frame in frames:
            yield self._audio.normalize(frame)

    async def _tap_voice_activity(
        self, frames: AsyncIterator[AudioFrame]
    ) -> AsyncIterator[AudioFrame]:
        """Pass frames through to STT unchanged while feeding a copy to the Voice
        Activity Framework. VAD is auxiliary: an observe/provider failure is logged
        and skipped so it can never break the conversation (DL-0013)."""
        async for frame in frames:
            if self._voice_activity is not None:
                try:
                    # `observe` already returns the *changed* state; M10.9 reads the return value
                    # the M3.2 tap discarded. This is true end-of-utterance (ADR-0038 TP3 #1),
                    # not an STT artefact.
                    changed = self._voice_activity.observe(frame)
                    if (
                        changed is VoiceActivityState.SILENCE_CONFIRMED
                        and self._audio_endpoint is None
                    ):
                        # Legacy (M10.9) boundary: the VAF's own one-frame hysteresis. When audio
                        # endpointing is active the boundary comes from the detector instead —
                        # later, and from the *policy* that defines true end-of-speech (ME1) — so
                        # tapping here too would stamp `speech_end` at the wrong instant.
                        self._tap("speech_end")
                    if self._audio_endpoint is not None:
                        self._observe_audio_endpoint(frame)
                    # A bounded provider buffer that overflowed discarded audio (M11.7). The drop
                    # is correct behaviour, and AR-24 requires it to be recorded, not inferred.
                    # Read only where the coordinator offers it, so a minimal coordinator double
                    # is not misreported as a failing VAD.
                    drain = getattr(self._voice_activity, "drain_dropped_ms", None)
                    dropped_ms = drain() if drain is not None else 0.0
                    if dropped_ms:
                        self._guard("voice_activity").declared_limit(
                            getattr(self._voice_activity, "provider_name", "voice_activity"),
                            DegradationKind.AUDIO_DROPPED,
                            f"analysis buffer overflowed; {dropped_ms:.0f} ms of audio discarded",
                        )
                    # The frame was analysed without raising: any open voice-activity degradation
                    # episode ends here (M11.15, R-48). Placed after the work rather than before
                    # it, so a frame that fails mid-analysis does not first announce recovery.
                    self._guard("voice_activity").auxiliary_recovered("voice_activity")
                except Exception as exc:  # noqa: BLE001 — VAD must not break the pipeline
                    log.warning(
                        "pipeline %s: voice-activity observe failed (ignored): %s",
                        self._session_id, exc,
                    )
                    # Recorded, not merely swallowed (AR-24). The conversation continues — the
                    # M11.3 conformance contract for `detect()` is untouched, and a failing VAD
                    # never becomes a failing call (DL-0013).
                    self._guard("voice_activity").auxiliary_failed(
                        "voice_activity",
                        f"observe failed: {exc}",
                        fallback="frame passed through unanalyzed",
                    )
                    if self._audio_endpoint is not None:
                        # **A failed provider is not silence** (ADR-0047 ME8): no counter advances
                        # and no boundary is produced, so an outage can neither end a turn nor
                        # invent one. The frame still passes through to STT, unchanged.
                        self._audio_endpoint.note_unavailable()
            yield frame

    # ── audio-driven endpointing (M11.8, ADR-0047 ME1) ───────────────────────

    def _observe_audio_endpoint(self, frame: AudioFrame) -> None:
        """Account for one classified frame and act on a boundary it produced.

        Synchronous and cheap by construction — this runs inside the **inbound audio path**
        (for a duplex STT provider, inside its upload task), so it may never await, never run a
        provider, and never start a response. It advances turn state, records the two boundaries
        and lets the existing run loop respond when a transcript is available."""
        sample = self._voice_activity.last_sample if self._voice_activity is not None else None
        if sample is None:
            return
        signal = self._audio_endpoint.observe(
            speech=sample.speech,
            duration_ms=frame_duration_ms(frame, fallback_ms=self._vad_frame_ms),
        )
        if signal.kind is AudioEndpointKind.SPEECH_START:
            self._begin_audio_turn(signal.backdate_ms)
        elif signal.kind is AudioEndpointKind.SPEECH_END:
            self._close_audio_turn(signal.backdate_ms)

    def _begin_audio_turn(self, backdate_ms: float) -> None:
        """An utterance began. Opens the turn *before* stamping, so the boundary is attributed to
        the turn it belongs to rather than the previous one."""
        if self._audio_turn_closed:
            # The previous utterance was closed by audio but never produced a transcript, so no
            # response was ever owed for it. Retire that turn deterministically instead of
            # advancing a finished turn twice (which the Turn Engine would rightly refuse).
            self._abandon_closed_turn()
        if self._turns is not None:
            self._turns.on_user_speech()
        if self._endpoint is not None:
            self._endpoint.on_user_speech()
        self._tap("speech_start", backdate_ms=backdate_ms)

    def _close_audio_turn(self, backdate_ms: float) -> None:
        """True end-of-speech. Closes the *user* half of the turn.

        Endpointing still invokes no LLM, no TTS and no playback **inline**: when the transcript
        for this utterance has already arrived, the response is *scheduled* as a task through the
        same `_start_response` the endpoint pathway uses. Scheduling is what keeps the inbound
        audio path non-blocking — this runs inside the VAD tap, and awaiting a response here would
        stall ingress for the whole turn.

        **Why this is not merely an optimization (M11.11, closes R-50).** M11.8 left the response
        to be triggered by a transcript, on the assumption that a Final arrives *after* end of
        speech. Real recognizers finalize as soon as the caller stops — before the trailing-silence
        hold elapses — so the transcript is already pending when audio closes the turn, and nothing
        was left to trigger it. The caller was then answered only at the *next* utterance or at
        hangup, which is not a conversation. This is the minimum defect that makes the M11.11 path
        executable, and it changes no M11.8 semantics: the same turn, the same single response, the
        same boundaries, only dispatched at the moment both halves are actually ready."""
        self._tap("speech_end", backdate_ms=backdate_ms)
        if self._endpoint is not None:
            self._endpoint.on_speech_end()  # confirms → the coordinator notifies the Turn Engine
        elif self._turns is not None:
            self._turns.on_user_final()
        self._tap("endpoint_decided")
        self._audio_turn_closed = True
        if self._pending_text is not None and not self._response_active():
            # Both halves are ready: audio says the turn is over and the transcript is in hand.
            # `_start_response` consumes `_pending_text`, so a later segment cannot answer twice.
            self._audio_turn_closed = False
            self._start_response()

    def _abandon_closed_turn(self) -> None:
        """Retire a turn that audio closed but that produced no transcript to answer.

        `TURN_CANCELLED` is the existing phase for *"this turn will not be completed"*, and the
        terminal tap releases the metric state for it (ADR-0038 TP6/TP8). Nothing is fabricated:
        the turn simply ends without a response, which is what actually happened."""
        self._audio_turn_closed = False
        self._pending_text = None
        if self._turns is not None:
            self._turns.cancel()
        self._tap_terminal("cancelled")
        if self._endpoint is not None:
            self._endpoint.reset()

    def _flush_audio_endpoint(self) -> None:
        """The audio stream ended mid-utterance: close it once so the last turn is not dropped.

        The accrued trailing silence is still backdated, so a hangup cannot inflate the measured
        end-of-speech into a latency the caller never experienced."""
        if self._audio_endpoint is None:
            return
        signal = self._audio_endpoint.flush()
        if signal.kind is AudioEndpointKind.SPEECH_END:
            self._close_audio_turn(signal.backdate_ms)

    def _audio_endpoint_owes_response(self) -> bool:
        """Whether audio already ended the turn, so a transcript may be answered immediately."""
        return self._audio_endpoint is not None and self._audio_turn_closed

    def _emit(self, role: str, text: str, *, is_final: bool) -> None:
        if self._on_transcript is None:
            return
        try:
            self._on_transcript(self._session_id, role, text, is_final)
        except Exception as exc:  # noqa: BLE001 — a sink must never break the pipeline
            log.warning("transcript sink raised (ignored): %s", exc)

    # ── latency observation (M10.9, ADR-0038) ────────────────────────────────

    def _tap(self, boundary: str, *, backdate_ms: float = 0.0) -> None:
        """Record one stage boundary. Cheap by construction: the observer reads a clock and
        writes a dict — **nothing is published here** (ADR-0038 TP10), because
        `InProcessEventBus.publish()` is synchronous run-to-completion and would run every
        subscriber inside the voice-to-voice budget.

        `backdate_ms` (M11.8) is for boundaries the pipeline recognises *after* they happened: an
        audio endpoint is only knowable once its evidence accrued, so the detector reports how much
        audio ago the real boundary was. The observer still owns the clock — it subtracts."""
        if self._latency is None:
            return
        try:
            self._latency.record(
                self._session_id, self._turn_index(), boundary, backdate_ms=backdate_ms
            )
        except Exception as exc:  # noqa: BLE001 — a tap must never break the pipeline (TP9)
            log.warning(
                "pipeline %s: latency tap %r failed (ignored): %s",
                self._session_id, boundary, exc,
            )

    def _tap_terminal(self, status: str) -> None:
        """The turn reached a terminal outcome; the observer emits at most one metric and
        releases its state (ADR-0038 TP6/TP8)."""
        if self._latency is None:
            return
        try:
            self._latency.complete(self._session_id, self._turn_index(), status)
        except Exception as exc:  # noqa: BLE001 — TP9
            log.warning(
                "pipeline %s: latency completion %r failed (ignored): %s",
                self._session_id, status, exc,
            )

    def _turn_index(self) -> int:
        """The turn these boundaries belong to. `0` when no Turn Engine is wired — a
        lifecycle-only pipeline has no turn identity to attribute to, and inventing one would
        make two different turns share a key.

        When a turn has just reached a terminal phase the engine holds no current turn, so the
        **last active** index stands in: a completion belongs to the turn that completed, not to
        turn 0 (M11.8). Two different turns still never share a key, because the remembered index
        only advances."""
        if self._turns is None:
            return 0
        context = self._turns.engine.current_context
        if context is not None:
            self._last_turn_index = context.turn_index
        return self._last_turn_index


async def _abandon(router: ClauseRouter) -> None:
    """Tell the clause router the response was abandoned, without letting cleanup mask the cause.

    Called while an exception is already propagating — most often the `CancelledError` of an
    interrupted turn. A sink that raises here, or a second cancellation delivered during cleanup,
    must not replace the failure the caller is already handling: the original is what explains why
    the turn ended, and it is re-raised by the caller immediately after this returns."""
    try:
        await router.abandon()
    except BaseException as exc:  # noqa: BLE001 — see above; this is a cleanup path
        log.warning("clause routing failed while abandoning a response: %s", exc)
