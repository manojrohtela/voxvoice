"""Failure injection for the **production** runtime (M11.14).

M11.11's `test_e2e_conversation` proved a conversation works. This module is the same stack with
one difference: every vendor seam can be told to fail, and to fail *at a chosen moment*.

```
transport ─► VAD ─► endpointing ─► STT ─► Turn Engine ─► LLM
           ─► clauses ─► TTS ─► playback ─► transport
      ▲        ▲                    ▲                ▲          ▲
      └────────┴────── every one of these can be made to fail ───┘
```

## What is faked and what is not

Exactly what M11.11 faked: the vendor's socket, its recognizer, its model and its voice. The
runtime under test is `RuntimeSessionService` → `SessionWorker` → `ConversationOrchestrator` →
`RuntimePipeline`, with the **real** `DeepgramSTT`, `GroqLLM`, `CartesiaTTS`, `SileroVoiceActivity`
and WebRTC transport classes over those fakes. `ProviderGuard`, the `DegradationRecorder`, the Turn
Engine, the clause detector, the Playback Scheduler and the Playback Engine are all the production
objects — a resilience test that bypassed one of them would be evidence about the harness.

## Why failures are injected at the vendor seam and not at the guard

Because §A10 is a claim about the *whole* path: detect → classify → record → recover → scope →
clean up → observe. Raising a `ProviderError` directly at the guard would skip the first two steps
and assert the rest against an input the runtime never actually has to interpret.

## Nothing here sleeps

Every ordering question is settled by yielding to the event loop with a bounded spin. A resilience
test that waits a duration proves only that the machine was slow enough, and a resilience test that
waits on an `Event` a defect prevents from ever being set is a **hang**, not a failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import bootstrap
from core.audio import AudioFrame
from core.session_control import StartSessionRequest
from runtime.observability_plane import ObservabilityPlane
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService

from .fake_completion import FakeCompletionClient
from .fake_conversation import (
    ConversationEndpoint,
    TurnWatcher,
    pcm_tone,
    scripted_recognizer,
    vad_drained,
)
from .fake_speech import FakeSpeechClient, pcm, settle, spin
from .fake_speech_model import EnergyModel, FailingModel
from .fake_transcription import FakeTranscriptionClient

SAMPLE_RATE = 16000
FRAME_BYTES = 640  # 20 ms of PCM16 at 16 kHz
SESSION = "ses_resilience"

#: Frames per utterance, matching M11.11's: 12 loud frames clear the 120 ms speech threshold and
#: 22 silent ones clear the 240 ms trailing-silence hold, with room for the VAD's window lag.
SPEECH_FRAMES = 12
SILENCE_FRAMES = 22


def speech(seq: int, *, loud: bool = True) -> AudioFrame:
    """One 20 ms inbound frame. Loud peaks at 30000/32767 = 0.92, comfortably above the 0.5
    speech threshold; silence is exactly zero."""
    return pcm_tone(30000 if loud else 0, nbytes=FRAME_BYTES, seq=seq, rate=SAMPLE_RATE)


# ── transports that fail in specific, distinguishable ways ───────────────────


class FailingSendEndpoint(ConversationEndpoint):
    """A transport that accepts `ok_frames` and then refuses every send.

    Two knobs rather than one, because *"the transport was never usable"* and *"the transport died
    after the caller had already heard part of the answer"* are different failures with different
    obligations: the second must not replay what was already spoken."""

    def __init__(self, *, ok_frames: int = 0, error: Exception | None = None) -> None:
        super().__init__()
        self._ok = ok_frames
        self._error = error or ConnectionResetError("transport send failed")
        self.refused = 0

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        if len(self.written) >= self._ok:
            self.refused += 1
            raise self._error
        await super().send(session_id, frame)


class BreakingInboundEndpoint(ConversationEndpoint):
    """A transport whose **inbound** stream breaks rather than ending.

    The distinction the whole media path rests on (ADR-0045 SD4, AR-6): a stream that *ends* is a
    caller hangup, a stream that *raises* is a vendor disconnect. They must never be conflated."""

    def __init__(self, *, after: int = 0, error: Exception | None = None) -> None:
        super().__init__()
        self._after = after
        self._error = error or ConnectionResetError("inbound stream broke")
        self.delivered = 0

    async def inbound(self, session_id: str) -> AsyncIterator[AudioFrame]:
        self.started.set()
        while True:
            frame = await self._inbound.get()
            if frame is None:
                return
            if self.delivered >= self._after:
                raise self._error
            self.delivered += 1
            yield frame


class RefusingConnectEndpoint(ConversationEndpoint):
    """A transport that cannot be reached at all — failure *before* connection."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self._error = error or ConnectionRefusedError("transport unreachable")

    async def connect(self, session_id: str) -> None:
        raise self._error


class HangingSendEndpoint(ConversationEndpoint):
    """A transport that accepts a send and never returns — the outbound-stall fixture.

    Used to prove that a producer blocked on back-pressure is *released* by cancellation rather
    than deadlocking, which is the failure mode a bounded buffer creates if it is done wrong."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        self.entered.set()
        await self.release.wait()
        await super().send(session_id, frame)


# ── a voice-activity provider that fails on demand ───────────────────────────


def failing_vad(fail_after: int = 0):
    """A zero-argument factory yielding M11.7's `FailingModel`: classify `fail_after` windows,
    then raise from every subsequent inference.

    The *model* fails, not the provider: `SileroVoiceActivity` — the production class, with its
    worker thread, its buffering and its declared capabilities — stays in the path, which is where
    a real Silero failure would actually occur. Replacing the provider instead would prove only
    that a substituted object behaves as substituted."""
    return lambda: FailingModel(fail_after)


# ── observability sinks that misbehave ───────────────────────────────────────


class RaisingDurableSink:
    """An Epic 10 durable sink whose every append fails (ADR-0035 DW4's failure hook)."""

    def __init__(self) -> None:
        self.attempts = 0
        self.errors: list[object] = []

    def append(self, event: object) -> bool:
        self.attempts += 1
        raise RuntimeError("durable sink unavailable")

    def on_error(self, *args: object) -> None:
        self.errors.append(args)


class RaisingMetricSink:
    """A metric buffer whose subscribed handler raises on every event."""

    def __init__(self) -> None:
        self.events = 0

    def subscribe(self, bus: object):  # noqa: ANN001
        def handler(event: object) -> None:
            self.events += 1
            raise RuntimeError("metering sink unavailable")

        return bus.subscribe(handler, types=("metric.turn",))


class RaisingTelemetry:
    """A `metric.turn` producer that raises from every tap (ADR-0038 TP9's contract)."""

    def __init__(self) -> None:
        self.records = 0
        self.completions = 0

    def __call__(self, publisher: object) -> RaisingTelemetry:
        return self

    def record(self, session_id, turn_index, boundary, *, backdate_ms: float = 0.0) -> None:  # noqa: ANN001
        self.records += 1
        raise RuntimeError("telemetry unavailable")

    def complete(self, session_id, turn_index, status) -> None:  # noqa: ANN001
        self.completions += 1
        raise RuntimeError("telemetry unavailable")


class RecordingTelemetry:
    """A `metric.turn` producer that records rather than publishes — the fabrication detector.

    Keeping the stamps per turn is what makes *"no fake `tts_first_audio`"* checkable: the question
    is not whether a metric was emitted but whether a boundary the audio never reached was."""

    def __init__(self) -> None:
        self.by_turn: dict[int, list[str]] = {}
        self.completed: list[tuple[int, str]] = []

    def __call__(self, publisher: object) -> RecordingTelemetry:
        return self

    def record(self, session_id, turn_index, boundary, *, backdate_ms: float = 0.0) -> None:  # noqa: ANN001
        self.by_turn.setdefault(turn_index, []).append(boundary)

    def complete(self, session_id, turn_index, status) -> None:  # noqa: ANN001
        self.completed.append((turn_index, status))

    @property
    def stamps(self) -> list[str]:
        return [b for boundaries in self.by_turn.values() for b in boundaries]


# ── the stack ────────────────────────────────────────────────────────────────


@dataclass
class Stack:
    """One assembled session, with the handles a resilience assertion needs."""

    service: RuntimeSessionService
    request: StartSessionRequest
    endpoint: ConversationEndpoint
    registry: object
    llm_client: FakeCompletionClient
    tts_client: FakeSpeechClient
    stt_client: FakeTranscriptionClient
    telemetry: object | None = None
    durable: object | None = None
    metrics: object | None = None
    _seq: int = field(default=0, repr=False)
    _watcher: object | None = field(default=None, repr=False)

    # ── handles into the running session ─────────────────────────────────────

    @property
    def worker(self):
        return self.service._workers.get(self.request.session_id)  # noqa: SLF001

    @property
    def orchestrator(self):
        worker = self.worker
        return worker.orchestrator if worker is not None else None

    @property
    def pipeline(self):
        orchestrator = self.orchestrator
        return orchestrator._pipeline if orchestrator is not None else None  # noqa: SLF001

    @property
    def playback(self):
        pipeline = self.pipeline
        return pipeline._playback if pipeline is not None else None  # noqa: SLF001

    @property
    def degradations(self):
        orchestrator = self.orchestrator
        return orchestrator.degradations if orchestrator is not None else None

    @property
    def degradation_kinds(self) -> list[str]:
        recorder = self.degradations
        return [] if recorder is None else [d.kind.value for d in recorder.history]

    def degraded(self, capability: str, kind: str) -> bool:
        recorder = self.degradations
        if recorder is None:
            return False
        return any(
            d.capability == capability and d.kind.value == kind for d in recorder.history
        )

    # ── driving a conversation ───────────────────────────────────────────────

    async def start(self) -> None:
        await self.service.start(self.request)
        orchestrator = self.orchestrator
        if orchestrator is not None and orchestrator.event_bus is not None:
            self._watcher = TurnWatcher(orchestrator.event_bus)

    async def speak(self, *, wait: bool = True) -> None:
        """Push one complete utterance: sustained speech, then trailing silence.

        `wait` drives the turn to a terminal phase where one is expected. A failure test often
        expects *no* response, and waiting for one would then be waiting for something that must
        never arrive — so the caller says which it is.

        **M11.15 (R-70).** The waiting `wait=True` does is the runtime's own `turn.completed` /
        `turn.cancelled` event plus a VAD-drained check, not a spin budget. Both terminal phases
        count: a resilience test very often expects the turn to end by *failing*, and a signal
        that only fired on success would hang on exactly the cases this file exists to cover."""
        if wait and self._watcher is not None:
            self._watcher.arm()
        for _ in range(SPEECH_FRAMES):
            await self._deliver(speech(self._seq))
            self._seq += 1
        for _ in range(SILENCE_FRAMES):
            await self._deliver(speech(self._seq, loud=False))
            self._seq += 1
        if not wait:
            # Asserting an *absence*. There is no positive signal to await, so the audio is
            # driven through the VAD — a runtime condition — and then the loop is allowed to
            # settle before the test asserts nothing happened.
            await self._drain_vad()
            await spin(200)
            return
        if self._watcher is None:
            await spin(400)
            return
        await self._watcher.wait()
        await self._drain_vad()

    async def _deliver(self, frame) -> None:
        """Push one frame and let voice activity catch up before the next (M11.15, R-70/R-67).

        The same pacing the E2E harness uses, and for the same reason: a frame pushed before the
        previous one has been analysed lands against a stale verdict, and under any competition
        for the event loop that lag is unbounded. Waiting on the provider's own `buffered_ms`
        makes it exactly one frame, whatever else is running."""
        await self.endpoint.push(frame)
        await self._drain_vad()

    async def _drain_vad(self) -> None:
        """Let the voice-activity worker catch up with the audio already pushed."""
        provider = self.registry.resolve(CapabilityKind.VOICE_ACTIVITY)
        try:
            await vad_drained(provider, self.request.session_id)
        except TimeoutError:
            # A test that deliberately breaks the VAD leaves audio permanently unanalysed; that
            # is the scenario under test, not a harness failure.
            return

    async def hangup(self) -> None:
        await self.endpoint.hangup()
        worker = self.worker
        if worker is not None:
            await worker.wait_closed()

    async def end(self) -> None:
        await self.service.end(self.request.session_id)

    async def finish(self) -> None:
        """Hang up and stop, tolerating a session that already failed itself."""
        await self.hangup()
        await self.end()
        await spin(30)


def build(
    *,
    words: list[str] | None = None,
    reply: list[str] | None = None,
    endpoint: ConversationEndpoint | None = None,
    tts_chunks: list[bytes] | None = None,
    llm_open_error=None,
    llm_fail_at: int | None = None,
    llm_kind=None,
    llm_gate: asyncio.Event | None = None,
    llm_started: asyncio.Event | None = None,
    tts_open_error=None,
    tts_fail_at: int | None = None,
    tts_kind=None,
    tts_gate: asyncio.Event | None = None,
    tts_started: asyncio.Event | None = None,
    speech_model_factory=None,
    allow_interruptions: bool = False,
    audio_endpointing: bool = True,
    first_message: str | None = None,
    transcripts: list | None = None,
    telemetry=None,
    durable=None,
    metrics=None,
    on_durable_error=None,
    session_id: str = SESSION,
) -> Stack:
    """Assemble the real stack with the requested failure injected at the vendor seam."""
    from providers.llm.completion_stream import FailureKind as LLMFailure
    from providers.tts.speech_stream import FailureKind as TTSFailure

    endpoint = endpoint if endpoint is not None else ConversationEndpoint()
    stt_client = FakeTranscriptionClient(
        responder=scripted_recognizer(words if words is not None else ["hello there"])
    )
    llm_client = FakeCompletionClient(
        reply if reply is not None else ["A reply. "],
        open_error=llm_open_error,
        fail_at=llm_fail_at,
        kind=llm_kind if llm_kind is not None else LLMFailure.UNAVAILABLE,
        gate=llm_gate,
        started=llm_started,
    )
    tts_client = FakeSpeechClient(
        tts_chunks if tts_chunks is not None else [pcm(FRAME_BYTES), pcm(FRAME_BYTES, fill=2)],
        open_error=tts_open_error,
        fail_at=tts_fail_at,
        kind=tts_kind if tts_kind is not None else TTSFailure.UNAVAILABLE,
        gate=tts_gate,
        started=tts_started,
    )
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=endpoint,
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=stt_client,
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=speech_model_factory or EnergyModel,
        llm=bootstrap.LLM_GROQ,
        llm_client=llm_client,
        tts=bootstrap.TTS_CARTESIA,
        tts_client=tts_client,
        tts_voice="voice-1",
    )
    sink = None
    if transcripts is not None:

        def sink(session_id_, role, text, final):  # noqa: ANN001
            transcripts.append((role, text, final))

    plane = ObservabilityPlane(
        durable=durable,
        on_durable_error=on_durable_error,
        metrics=metrics,
        telemetry=telemetry,
    )
    service = RuntimeSessionService(
        registry,
        on_transcript=sink,
        streaming_playback=True,
        observability=plane,
    )
    request = StartSessionRequest(
        session_id=session_id,
        org_id="o",
        agent_id="a",
        channel="web",
        system_prompt="be helpful",
        language="en",
        sample_rate=SAMPLE_RATE,
        allow_interruptions=allow_interruptions,
        audio_endpointing=audio_endpointing,
        first_message=first_message,
    )
    return Stack(
        service=service,
        request=request,
        endpoint=endpoint,
        registry=registry,
        llm_client=llm_client,
        tts_client=tts_client,
        stt_client=stt_client,
        telemetry=telemetry,
        durable=durable,
        metrics=metrics,
    )


def vendor_streams(stack: Stack) -> list:
    """Every TTS stream the vendor actually opened — the provider-stream-leak evidence."""
    tts = stack.registry.resolve(CapabilityKind.TTS)
    return list(tts._client.streams)  # noqa: SLF001


async def census() -> int:
    """Live asyncio tasks, after letting the loop settle. The resource-leak baseline."""
    await spin(60)
    return len(asyncio.all_tasks())


__all__ = [
    "FRAME_BYTES",
    "SAMPLE_RATE",
    "SESSION",
    "SILENCE_FRAMES",
    "SPEECH_FRAMES",
    "BreakingInboundEndpoint",
    "FailingSendEndpoint",
    "HangingSendEndpoint",
    "RaisingDurableSink",
    "RaisingMetricSink",
    "RaisingTelemetry",
    "RecordingTelemetry",
    "RefusingConnectEndpoint",
    "Stack",
    "build",
    "census",
    "failing_vad",
    "settle",
    "speech",
    "spin",
    "vendor_streams",
]
