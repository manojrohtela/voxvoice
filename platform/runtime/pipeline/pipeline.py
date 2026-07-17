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

from ..contracts import TranscriptSink
from ..endpoint import FINAL_ENDPOINT_DECISIONS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.audio import AudioFrame
    from core.providers.llm import LLMProvider
    from core.providers.stt import STTProvider
    from core.providers.transport import TransportProvider
    from core.providers.tts import TTSProvider

    from ..endpoint import EndpointCoordinator
    from ..interrupt import InterruptCoordinator
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
        language: str = "en",
        sample_rate: int = 16000,
        tts_voice: str | None = None,
        on_transcript: TranscriptSink | None = None,
        turns: TurnCoordinator | None = None,
        voice_activity: VoiceActivityCoordinator | None = None,
        endpoint: EndpointCoordinator | None = None,
        interrupt: InterruptCoordinator | None = None,
    ) -> None:
        self._transport = transport
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._language = language
        self._sample_rate = sample_rate
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
        # The in-flight assistant response task (interruptible path only).
        self._response_task: asyncio.Task[None] | None = None
        # The most recent Final transcript awaiting an endpoint decision (so the
        # stream-end flush can close out a turn without dropping the utterance).
        self._pending_text: str | None = None

    # ── lifecycle (provider startup/shutdown; coordinated by the Orchestrator) ─
    async def start(self) -> None:
        t0 = time.monotonic()
        await self._transport.start(session_id=self._session_id)
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
                self._voice_activity = None
        log.info(
            "pipeline %s started (transport=%s stt=%s llm=%s tts=%s) in %.1fms",
            self._session_id, self._transport.name, self._stt.name, self._llm.name,
            self._tts.name, (time.monotonic() - t0) * 1000,
        )

    async def stop(self) -> None:
        t0 = time.monotonic()
        if self._response_task is not None and not self._response_task.done():
            self._response_task.cancel()  # cancel in-flight assistant playback
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass
        if self._interrupt is not None:
            self._interrupt.cancel()  # clear any in-flight interrupt state
        if self._endpoint is not None:
            self._endpoint.cancel()  # terminalize any pending endpoint on shutdown
        if self._turns is not None:
            self._turns.cancel()  # terminalize any active turn on shutdown
        if self._voice_activity is not None:
            await self._voice_activity.stop()  # best-effort provider teardown
        await self._transport.stop(session_id=self._session_id)
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
        inbound = self._transport.inbound(session_id=self._session_id)
        # Tap the inbound stream for voice activity only when a coordinator is
        # present — STT receives byte-identical frames either way (M3.2).
        if self._voice_activity is not None:
            inbound = self._tap_voice_activity(inbound)
        async for result in self._stt.transcribe(
            inbound,
            language=self._language,
            sample_rate=self._sample_rate,
        ):
            if isinstance(result, Partial):
                if self._turns is not None:
                    self._turns.on_user_speech()
                if self._endpoint is not None:
                    self._endpoint.on_user_speech()  # speech in progress; keeps turn open
                self._emit("user", result.text, is_final=False)
            elif isinstance(result, Final):
                self._emit("user", result.text, is_final=True)
                await self._on_segment(result.text)
        # Stream ended (caller hangup): close out a turn still awaiting confirmation
        # so the last utterance is never silently dropped.
        if self._endpoint is not None and self._pending_text is not None:
            decision = self._endpoint.flush()
            if decision in FINAL_ENDPOINT_DECISIONS:
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
        if decision in FINAL_ENDPOINT_DECISIONS:
            await self._complete_turn()

    async def _complete_turn(self) -> None:
        """Respond to the caller's now-complete turn and ready the endpoint engine
        for the next one. The Turn Engine was already told the user finished by the
        EndpointCoordinator on the final decision."""
        text = self._pending_text
        self._pending_text = None
        if text is None:
            return
        await self._respond(text)
        if self._endpoint is not None:
            self._endpoint.reset()  # back to CONTINUE_LISTENING for the next turn

    # ── the interruptible turn loop (M3.4; barge-in enabled) ─────────────────
    async def _run_interruptible(self) -> None:
        """Like `run()`, but assistant playback runs as a concurrent task so caller
        barge-in can pre-empt it. The inbound tap feeds the Voice Activity Framework
        (which notifies the Interrupt coordinator); a confirmed interrupt sets the
        coordinator's `stop_requested` signal, which this loop acts on."""
        log.info("pipeline %s conversation started (interruptible)", self._session_id)
        inbound = self._transport.inbound(session_id=self._session_id)
        if self._voice_activity is not None:
            inbound = self._tap_voice_activity(inbound)
        async for result in self._stt.transcribe(
            inbound, language=self._language, sample_rate=self._sample_rate
        ):
            # A barge-in confirmed while the assistant is speaking → pre-empt now.
            if self._interrupt.stop_requested.is_set() and self._response_active():
                await self._preempt_response()
            if isinstance(result, Partial):
                if self._turns is not None:
                    self._turns.on_user_speech()
                if self._endpoint is not None:
                    self._endpoint.on_user_speech()
                self._emit("user", result.text, is_final=False)
            elif isinstance(result, Final):
                self._emit("user", result.text, is_final=True)
                # Guard: never overlap two responses. If one is still playing and no
                # interrupt was confirmed, let it finish before starting the next.
                if self._response_active():
                    await self._await_response()
                self._start_segment(result.text)
        await self._await_response()  # drain a still-playing response before ending
        if self._endpoint is not None and self._pending_text is not None:
            decision = self._endpoint.flush()
            if decision in FINAL_ENDPOINT_DECISIONS:
                self._start_response()
                await self._await_response()
        log.info("pipeline %s conversation ended", self._session_id)

    def _response_active(self) -> bool:
        return self._response_task is not None and not self._response_task.done()

    def _start_segment(self, user_text: str) -> None:
        """Endpoint decides; on a final decision launch the response as a task."""
        self._pending_text = user_text
        decision = self._endpoint.on_segment() if self._endpoint is not None else None
        if decision in FINAL_ENDPOINT_DECISIONS:
            self._start_response()

    def _start_response(self) -> None:
        text = self._pending_text
        self._pending_text = None
        if text is None:
            return
        self._response_task = asyncio.create_task(self._respond_task(text))

    async def _respond_task(self, user_text: str) -> None:
        await self._respond(user_text)
        if self._endpoint is not None:
            self._endpoint.reset()  # ready for the next turn (mirrors _complete_turn)

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
        self._interrupt.start_user_turn()  # → USER_TURN_STARTING (cancels the turn)
        self._interrupt.complete()  # → INTERRUPT_COMPLETED
        if self._endpoint is not None:
            self._endpoint.reset()  # drop any stale pending decision
        self._pending_text = None

    async def _respond(self, user_text: str) -> None:
        if self._turns is not None:
            self._turns.on_assistant_thinking()
        # Minimal context assembly (Memory/Prompt Runtime are M5).
        context = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_text},
        ]
        parts: list[str] = []
        async for delta in self._llm.stream(context=context):
            if isinstance(delta, TokenDelta):
                parts.append(delta.text)
        agent_text = "".join(parts).strip()
        if not agent_text:
            if self._turns is not None:
                self._turns.on_turn_completed()  # empty response still completes the turn
            return
        if self._turns is not None:
            self._turns.on_assistant_speaking()
        if self._interrupt is not None:
            self._interrupt.on_assistant_started()  # begin barge-in monitoring
        self._emit("agent", agent_text, is_final=True)
        async for frame in self._tts.synthesize(
            agent_text, voice=self._tts_voice, language=self._language
        ):
            if self._interrupt is not None and self._interrupt.stop_requested.is_set():
                # A barge-in was confirmed → stop playback gracefully. The run loop
                # drives the interrupt action phase + the new user turn.
                return
            await self._transport.send(session_id=self._session_id, frame=frame)
        if self._interrupt is not None:
            self._interrupt.on_assistant_finished()  # playback completed on its own
        if self._turns is not None:
            self._turns.on_turn_completed()

    async def _tap_voice_activity(
        self, frames: AsyncIterator[AudioFrame]
    ) -> AsyncIterator[AudioFrame]:
        """Pass frames through to STT unchanged while feeding a copy to the Voice
        Activity Framework. VAD is auxiliary: an observe/provider failure is logged
        and skipped so it can never break the conversation (DL-0013)."""
        async for frame in frames:
            if self._voice_activity is not None:
                try:
                    self._voice_activity.observe(frame)
                except Exception as exc:  # noqa: BLE001 — VAD must not break the pipeline
                    log.warning(
                        "pipeline %s: voice-activity observe failed (ignored): %s",
                        self._session_id, exc,
                    )
            yield frame

    def _emit(self, role: str, text: str, *, is_final: bool) -> None:
        if self._on_transcript is None:
            return
        try:
            self._on_transcript(self._session_id, role, text, is_final)
        except Exception as exc:  # noqa: BLE001 — a sink must never break the pipeline
            log.warning("transcript sink raised (ignored): %s", exc)
