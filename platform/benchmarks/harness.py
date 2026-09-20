"""Drives real conversation turns and collects the runtime's own `metric.turn` (M11.13).

The harness starts a **production** session — `RuntimeSessionService` → `SessionWorker` →
`ConversationOrchestrator` → `RuntimePipeline` — with the M11.12 observability plane attached, and
reads the `metric.turn` events the runtime publishes. It measures nothing itself.

That is the whole design constraint: if the harness timed the turn with its own stopwatch, the
baseline would describe the harness. Reading `metric.turn` means the numbers are the ones the
runtime will report in production, and a defect in the taps shows up as a missing boundary rather
than as a plausible number.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import bootstrap
from core.events import Event, EventType
from core.session_control import StartSessionRequest
from providers.voice_activity.silero import SileroVoiceActivity
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService

from .providers import (
    BenchmarkEndpoint,
    BenchmarkLLM,
    BenchmarkSTT,
    BenchmarkTTS,
    DelayProfile,
    EnergySpeechModel,
    loud_frame,
    silent_frame,
)

#: Frames per utterance. These clear M11.8's real thresholds — 120 ms of speech to open an
#: utterance, 240 ms of trailing silence to close it — with margin for the VAD's 32 ms analysis
#: windows lagging the frames feeding them. **Not tuned for the benchmark**: the same counts the
#: M11.11 end-to-end tests use, so the baseline measures the same path those prove correct.
SPEECH_FRAMES = 12
SILENCE_FRAMES = 22

#: The real duration one inbound frame represents. Frames are delivered at this cadence, because
#: a caller produces audio in real time and the VAD's verdict lags the frames feeding it.
#:
#: Delivering a whole utterance in one event-loop turn — which the first version of this harness
#: did — makes every trailing-silence frame inherit the stale "speech" verdict from the last
#: completed analysis window, so the endpoint never closes and the turn dies at the deadline. That
#: is an artefact of instantaneous delivery, not a runtime defect, and pacing removes it. It also
#: makes the endpoint measurement mean something: a 240 ms hold is 240 ms of *audio*.
FRAME_PERIOD_S = 0.020


class MetricCollector:
    """A durable-log stand-in that keeps only `metric.turn` payloads.

    It satisfies Epic 10's append port, so it attaches through the **real** M11.12 plane rather
    than through a benchmark-only hook — which means a run also exercises the durable path it is
    measuring. It stores payloads, never audio and never transcripts."""

    def __init__(self) -> None:
        self.metrics: list[dict] = []
        self.event_count = 0

    def append(self, event: Event) -> bool:
        self.event_count += 1
        if event.type is EventType.METRIC_TURN:
            self.metrics.append(event.data)
        return True


@dataclass(slots=True)
class TurnSamples:
    """Per-stage samples gathered across runs, keyed by the runtime's own metric names."""

    stages: dict[str, list[float]] = field(default_factory=dict)
    voice_to_voice: list[float] = field(default_factory=list)
    #: Boundaries that were expected but absent — a missing tap, never silently tolerated.
    missing: dict[str, int] = field(default_factory=dict)
    turns: int = 0
    failures: int = 0

    def absorb(self, payload: dict) -> None:
        self.turns += 1
        for stage, value in (payload.get("ms") or {}).items():
            self.stages.setdefault(stage, []).append(float(value))
        v2v = payload.get("voice_to_voice_ms")
        if v2v is None:
            self.missing["voice_to_voice_ms"] = self.missing.get("voice_to_voice_ms", 0) + 1
        else:
            self.voice_to_voice.append(float(v2v))
        at = payload.get("at") or {}
        for boundary in (
            "speech_end", "endpoint_decided", "stt_final",
            "llm_first_token", "tts_first_audio", "first_audio_out",
        ):
            if boundary not in at:
                self.missing[boundary] = self.missing.get(boundary, 0) + 1


def build_service(delays: DelayProfile, *, telemetry: bool, collector: MetricCollector | None):
    """Assemble one production session service over the benchmark stand-ins.

    The **runtime** is entirely real — registry, guards, orchestrator, turn engine, endpointing,
    clause detector, playback scheduler and engine. Only what sits outside the platform (the
    recognizer, the model, the voice, the media peer) is replaced, and the real Silero VAD
    provider runs over a deterministic energy model."""
    endpoint = BenchmarkEndpoint(delays)
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=endpoint,
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=EnergySpeechModel,
    )
    registry.unregister(CapabilityKind.STT, registry.resolve(CapabilityKind.STT).name)
    registry.register(CapabilityKind.STT, BenchmarkSTT("benchmark utterance", delays), default=True)
    registry.unregister(CapabilityKind.LLM, registry.resolve(CapabilityKind.LLM).name)
    registry.register(
        CapabilityKind.LLM, BenchmarkLLM(["A first clause. ", "A second clause."], delays),
        default=True,
    )
    registry.unregister(CapabilityKind.TTS, registry.resolve(CapabilityKind.TTS).name)
    registry.register(CapabilityKind.TTS, BenchmarkTTS(2, delays), default=True)
    assert isinstance(registry.resolve(CapabilityKind.VOICE_ACTIVITY), SileroVoiceActivity)

    plane = bootstrap.build_observability_plane(durable=collector, telemetry=telemetry)
    service = RuntimeSessionService(registry, streaming_playback=True, observability=plane)
    return service, endpoint


#: How long the harness will wait for a turn to progress before declaring the run failed. Ample
#: for any delay this harness injects; a real ceiling so a stalled runtime fails the run instead
#: of contributing a silently truncated sample.
TURN_DEADLINE_S = 10.0


async def _settle(predicate, *, deadline_s: float = TURN_DEADLINE_S) -> bool:
    """Wait until `predicate` holds, or the deadline passes.

    It sleeps a **real** millisecond per iteration rather than yielding with `sleep(0)`. That
    distinction cost a debugging cycle and is worth stating: `sleep(0)` yields to other tasks but
    cannot advance a timer, so against a provider that waits 40 ms it spins out instantly and the
    harness hangs up mid-turn — producing `status=cancelled` turns and missing boundaries that look
    exactly like a runtime defect. Profile B injects real delays, so the harness must spend real
    time."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.001)
    return predicate()


async def run_session(
    delays: DelayProfile,
    *,
    turns: int,
    telemetry: bool = True,
    collector: MetricCollector | None = None,
) -> MetricCollector:
    """Run one session of `turns` utterances and return what the runtime recorded."""
    collector = collector or MetricCollector()
    service, endpoint = build_service(delays, telemetry=telemetry, collector=collector)
    request = StartSessionRequest(
        session_id="ses_bench", org_id="bench-org", agent_id="bench-agent", channel="web",
        system_prompt="benchmark", language="en", sample_rate=16000, audio_endpointing=True,
    )
    await service.start(request)
    worker = service._workers["ses_bench"]  # noqa: SLF001
    pipeline = worker.orchestrator._pipeline  # noqa: SLF001
    seq = 0
    for _ in range(turns):
        completed = len(collector.metrics)
        spoken = len(endpoint.written)
        for _ in range(SPEECH_FRAMES):
            await endpoint.push(loud_frame(seq))
            seq += 1
            await asyncio.sleep(FRAME_PERIOD_S)
        for _ in range(SILENCE_FRAMES):
            await endpoint.push(silent_frame(seq))
            seq += 1
            await asyncio.sleep(FRAME_PERIOD_S)
        # Wait for **outbound audio**, which both response paths produce and which exists whether
        # or not telemetry is attached. Two earlier predicates were wrong in instructive ways:
        # `_response_task` is only set when the transcript is already pending at end-of-speech (so
        # every Profile B turn was truncated), and the turn's `metric.turn` does not exist at all
        # in the telemetry-detached arm (so that arm measured this harness's deadline, not the
        # runtime). Audio reaching the transport is the one signal common to every configuration.
        await _settle(lambda before=spoken: len(endpoint.written) > before)
        if collector.metrics or telemetry:
            # With telemetry attached, also let the turn's metric land before the next utterance,
            # so a turn's boundaries cannot be attributed to its successor.
            await _settle(
                lambda before=completed: len(collector.metrics) > before, deadline_s=2.0
            )
        if pipeline._response_task is not None:  # noqa: SLF001
            await _settle(lambda: not pipeline._response_active())  # noqa: SLF001
            pipeline._response_task = None  # noqa: SLF001
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_bench")
    return collector


async def measure(
    delays: DelayProfile,
    *,
    sessions: int,
    turns_per_session: int,
    warmup_sessions: int,
    telemetry: bool = True,
) -> tuple[TurnSamples, TurnSamples]:
    """Run warmup then measured sessions; return `(cold, warm)` samples separately.

    `cold` is the **first** session of the run — process caches unfilled, the VAD model not yet
    loaded, every import cold. It is reported separately because mixing one cold session into a
    warm distribution moves the tail and explains nothing."""
    cold = TurnSamples()
    warm = TurnSamples()
    for index in range(warmup_sessions + sessions):
        collector = MetricCollector()
        try:
            await run_session(
                delays, turns=turns_per_session, telemetry=telemetry, collector=collector
            )
        except Exception:  # noqa: BLE001 — a failed run is counted, never silently dropped
            target = cold if index == 0 else warm
            target.failures += 1
            continue
        target = cold if index == 0 else (warm if index >= warmup_sessions else None)
        if target is None:
            continue  # a warmup session after the first: exercised, not measured
        for payload in collector.metrics:
            target.absorb(payload)
    return cold, warm


async def session_wall_clock(
    delays: DelayProfile, *, turns: int, telemetry: bool, durable: bool
) -> float:
    """Wall-clock seconds for one whole session, used only for the telemetry-overhead comparison.

    A wall-clock measure is unavoidable here: with telemetry off there is no `metric.turn` to
    read, so the two arms cannot be compared on the runtime's own metric. It is therefore a
    *harness* measurement and is reported as one — it includes the harness's own paced frame
    delivery, which dominates it, so only the **difference** between arms carries any meaning."""
    collector = MetricCollector() if durable else None
    started = asyncio.get_running_loop().time()
    await run_session(delays, turns=turns, telemetry=telemetry, collector=collector)
    return (asyncio.get_running_loop().time() - started) * 1000.0


__all__ = [
    "FRAME_PERIOD_S",
    "SILENCE_FRAMES",
    "SPEECH_FRAMES",
    "MetricCollector",
    "TurnSamples",
    "build_service",
    "measure",
    "run_session",
    "session_wall_clock",
]
