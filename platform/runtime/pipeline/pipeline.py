"""RuntimePipeline — the streaming execution pipeline for one conversation (M2C.2).

    caller audio ─► Transport.inbound ─► STT.transcribe ─► (per Final utterance)
      ContextBuilder(minimal) ─► LLM.stream ─► TTS.synthesize ─► Transport.send ─► caller

Executes the conversation the Orchestrator owns. Every stage is reached ONLY
through its `core.providers` contract — no vendor names, no provider-specific
branching (Constitution §A6, AR-21). Providers are injected (resolved from the
Provider Registry by the Orchestrator); the pipeline never constructs one.

Scope (M2C.2): minimum streaming flow for a real multi-turn conversation. NO Turn
Intelligence / adaptive endpointing (a Final from the STT marks a completed user
utterance — M3 owns real turn detection). NO Memory / Prompt Runtime (context is
the system prompt + the current utterance — M5 owns memory). Transcripts are
delivered via an injected sink (the future Event Bus integration point, M4).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from core.providers.llm import TokenDelta
from core.providers.stt import Final, Partial

from ..contracts import TranscriptSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.audio import AudioFrame
    from core.providers.llm import LLMProvider
    from core.providers.stt import STTProvider
    from core.providers.transport import TransportProvider
    from core.providers.tts import TTSProvider

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
        (caller hangup) or the pipeline is stopped. Streams STT → LLM → TTS."""
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
                self._emit("user", result.text, is_final=False)
            elif isinstance(result, Final):
                if self._turns is not None:
                    self._turns.on_user_final()
                self._emit("user", result.text, is_final=True)
                await self._respond(result.text)
        log.info("pipeline %s conversation ended", self._session_id)

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
        self._emit("agent", agent_text, is_final=True)
        async for frame in self._tts.synthesize(
            agent_text, voice=self._tts_voice, language=self._language
        ):
            await self._transport.send(session_id=self._session_id, frame=frame)
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
