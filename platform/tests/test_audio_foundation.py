"""Audio foundation tests (M11.2) — ADR-0044.

Proves the four things M11.2 claims:

  A. `AudioFrame` is extended **additively** — old construction still works and every
     new field carries a behaviour-preserving default.
  B. `AudioEncoding` lives on the core floor with `OPAQUE` as the default, and `core`
     acquires no dependency on `runtime`/`providers` (AR-10).
  C. The session's sample rate reaches the pipeline explicitly, with **no** ambient
     assumption left in the runtime path (AR-41, ADR-0044 AD3).
  D. There are exactly **two** conversion points, ingress precedes the voice-activity
     tap, and VAD and STT observe the identical frame (AR-41, ADR-0044 AD4/AD5).

Hermetic: no vendor SDK, no network, no credentials, no sleeps (TESTING_POLICY §2).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import struct
from array import array

import pytest

from bootstrap import build_local_registry
from core.audio import DEFAULT_SAMPLE_RATE, AudioEncoding, AudioFrame
from core.session_control import StartSessionRequest
from providers.transport.local import LocalTransport
from runtime.contracts import AgentSpec
from runtime.pipeline.audio import AudioNormalizer
from runtime.pipeline.pipeline import RuntimePipeline
from runtime.service import RuntimeSessionService

_PIPELINE_SRC = pathlib.Path(inspect.getfile(RuntimePipeline))


def _method_body(source: str, name: str) -> str:
    """The source of exactly one method, bounded at both ends.

    Read from the AST rather than by splitting on a `def` line: an unbounded split runs on into
    the *next* method, which is how a guard meant for `_respond` silently started reading
    `_normalize_ingress` as well."""
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{name} not found in the pipeline source")


# ── A. AudioFrame backward compatibility + additive defaults ────────────────────


def test_audioframe_old_construction_still_works():
    """The pre-M11.2 construction form is unchanged — the additive-extension claim."""
    frame = AudioFrame(data=b"hello", sample_rate=16000)
    assert frame.data == b"hello"
    assert frame.sample_rate == 16000


def test_audioframe_data_only_construction_uses_default_rate():
    assert AudioFrame(data=b"x").sample_rate == DEFAULT_SAMPLE_RATE


def test_audioframe_new_fields_default_to_behaviour_preserving_values():
    frame = AudioFrame(data=b"x")
    assert frame.encoding is AudioEncoding.OPAQUE
    assert frame.channels == 1
    assert frame.captured_at is None
    assert frame.seq is None


def test_audioframe_remains_frozen_and_slotted():
    frame = AudioFrame(data=b"x")
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is a dataclass detail
        frame.data = b"y"  # type: ignore[misc]
    assert not hasattr(frame, "__dict__")  # slots=True retained


def test_audioframe_positional_field_order_is_preserved():
    """`data` then `sample_rate` keep their positions, so positional callers are safe."""
    frame = AudioFrame(b"x", 8000)
    assert (frame.data, frame.sample_rate) == (b"x", 8000)


# ── B. AudioEncoding contract + core floor purity ───────────────────────────────


def test_opaque_is_the_default_encoding_not_pcm16():
    """A PCM16 default would make every local text-loopback frame falsely declare PCM."""
    assert AudioFrame(data=b"x").encoding is AudioEncoding.OPAQUE
    assert AudioEncoding.OPAQUE != AudioEncoding.PCM16


def test_audio_encoding_declares_only_actionable_members():
    """No vendor names, and only encodings the runtime can act on (ADR-0044 AD2)."""
    assert {e.value for e in AudioEncoding} == {"opaque", "pcm16"}


def test_core_audio_imports_nothing_above_the_floor():
    """AR-10: `core` imports nothing above it — asserted structurally, not by convention."""
    source = pathlib.Path(inspect.getfile(AudioFrame)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = ("runtime", "providers", "api", "events", "provider")
    assert not [m for m in imported if m.split(".")[0] in forbidden], imported


# ── C. Session sample rate reaches the pipeline explicitly ──────────────────────


def test_start_session_request_carries_the_session_rate():
    assert StartSessionRequest(
        session_id="s", org_id="o", agent_id="a", channel="web", system_prompt="p"
    ).sample_rate == DEFAULT_SAMPLE_RATE
    assert StartSessionRequest(
        session_id="s", org_id="o", agent_id="a", channel="web", system_prompt="p",
        sample_rate=24000,
    ).sample_rate == 24000


def test_agent_spec_carries_the_session_rate():
    assert AgentSpec(system_prompt="p").sample_rate == DEFAULT_SAMPLE_RATE
    assert AgentSpec(system_prompt="p", sample_rate=8000).sample_rate == 8000


@pytest.mark.asyncio
async def test_session_rate_flows_from_request_through_service_to_pipeline():
    """The end-to-end seam: StartSessionRequest → AgentSpec → RuntimePipeline."""
    service = RuntimeSessionService(build_local_registry())
    await service.start(
        StartSessionRequest(
            session_id="ses_rate", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=24000,
        )
    )
    worker = service._workers["ses_rate"]  # noqa: SLF001 — asserting internal wiring
    pipeline = worker.orchestrator._pipeline  # noqa: SLF001
    assert pipeline._sample_rate == 24000  # noqa: SLF001
    assert pipeline._audio.target_rate == 24000  # noqa: SLF001
    await service.end("ses_rate")


def test_pipeline_requires_an_explicit_sample_rate():
    """AR-41: no ambient assumption. The parameter has no default to fall back on."""
    signature = inspect.signature(RuntimePipeline.__init__)
    parameter = signature.parameters["sample_rate"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_no_hardcoded_rate_literal_remains_in_the_runtime_path():
    """The M11.2 target: no numeric rate literal survives anywhere in the pipeline's code.

    Asserted over the parsed AST rather than the raw text, so prose that *describes* the
    removed assumption cannot make the guard pass or fail spuriously."""
    tree = ast.parse(_PIPELINE_SRC.read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
    ]
    assert 16000 not in literals


# ── D. Resampler behaviour ──────────────────────────────────────────────────────


def test_equal_rate_frame_is_returned_unconverted_and_identical():
    """No unnecessary conversion — the common case must allocate nothing."""
    normalizer = AudioNormalizer(16000)
    frame = AudioFrame(data=b"abc", sample_rate=16000)
    assert normalizer.normalize(frame) is frame


def test_opaque_payload_is_never_reinterpreted_or_restamped():
    """An undeclared payload cannot be converted; re-stamping its rate would be a lie."""
    normalizer = AudioNormalizer(8000)
    frame = AudioFrame(data=b"local-text", sample_rate=16000)
    out = normalizer.normalize(frame)
    assert out is frame
    assert out.sample_rate == 16000


def test_pcm16_downsample_halves_the_frame_and_restamps_the_rate():
    samples = array("h", [0, 100, 200, 300, 400, 500, 600, 700])
    frame = AudioFrame(
        data=samples.tobytes(), sample_rate=16000, encoding=AudioEncoding.PCM16
    )
    out = AudioNormalizer(8000).normalize(frame)
    assert out.sample_rate == 8000
    assert out.encoding is AudioEncoding.PCM16
    assert len(out.data) == 2 * 4  # 8 source frames → 4 at half the rate


def test_pcm16_upsample_preserves_metadata():
    frame = AudioFrame(
        data=array("h", [0, 1000]).tobytes(), sample_rate=8000,
        encoding=AudioEncoding.PCM16, channels=1, captured_at=12.5, seq=7,
    )
    out = AudioNormalizer(16000).normalize(frame)
    assert out.sample_rate == 16000
    assert (out.captured_at, out.seq, out.channels) == (12.5, 7, 1)


def test_pcm16_conversion_endpoints_are_preserved():
    """Linear interpolation must keep the first and last sample exact."""
    frame = AudioFrame(
        data=array("h", [0, 400, 800, 1200]).tobytes(), sample_rate=16000,
        encoding=AudioEncoding.PCM16,
    )
    out = AudioNormalizer(32000).normalize(frame)
    values = array("h")
    values.frombytes(out.data)
    assert values[0] == 0
    assert values[-1] == 1200


def test_malformed_pcm16_is_passed_through_not_raised():
    """Audio handling is auxiliary — a bad payload must never break the conversation."""
    frame = AudioFrame(data=b"\x01", sample_rate=16000, encoding=AudioEncoding.PCM16)
    out = AudioNormalizer(8000).normalize(frame)
    assert out is frame


def test_normalizer_rejects_a_nonsense_target_rate():
    with pytest.raises(ValueError, match="positive"):
        AudioNormalizer(0)


def test_stereo_pcm16_channels_are_resampled_independently():
    """Per-channel interpolation must never mix channels together.

    Updated at M11.16.9: the normalizer now also normalizes *channel count*, and its default
    target is the session's canonical mono — so exercising the multi-channel resampling path
    requires asking for a stereo target explicitly. The assertion below is unchanged in intent;
    only the target is now stated rather than implied."""
    # L ramps up, R stays constant — a channel mix-up would disturb R.
    interleaved = array("h", [0, 500, 100, 500, 200, 500, 300, 500])
    frame = AudioFrame(
        data=interleaved.tobytes(), sample_rate=16000,
        encoding=AudioEncoding.PCM16, channels=2,
    )
    out = AudioNormalizer(8000, target_channels=2).normalize(frame)
    values = array("h")
    values.frombytes(out.data)
    assert out.channels == 2
    assert len(values) % 2 == 0
    assert all(values[i] == 500 for i in range(1, len(values), 2))


# ── D/E. Exactly two call sites, ingress before the tap, VAD == STT frame ───────


def test_exactly_two_conversion_call_sites_in_the_pipeline():
    """AR-41 asserted mechanically: `self._audio.normalize(...)` appears exactly twice —
    once at ingress, once at egress."""
    source = _PIPELINE_SRC.read_text(encoding="utf-8")
    assert source.count("self._audio.normalize(") == 2


def test_egress_conversion_happens_at_the_transport_send_call():
    """AR-41 — the last thing done to a frame before it leaves is normalization.

    Asserted over the playback block rather than by matching one line of source. The original
    M11.2 form matched the literal `frame=self._audio.normalize(frame)`, which stopped describing
    the property the moment M11.4 routed `transport.send` through the AR-23 guard — the
    normalization still happens immediately before the send, it is simply bound to a name first.
    Checking *order within the block* keeps the assertion about the property instead of about the
    formatting, and it is strictly harder to satisfy accidentally.

    Re-targeted at M11.10 for the same reason it was re-targeted at M11.4: the egress moved. It now
    lives in `_send_outbound`, which **both** response paths (the legacy whole-utterance one and
    the playback one) send through — so the property this test describes is now true of one
    function rather than of one branch, which is strictly stronger."""
    source = _PIPELINE_SRC.read_text(encoding="utf-8")
    block = _method_body(source, "_send_outbound")
    normalize_at = block.index("self._audio.normalize(")
    send_at = block.index("self._transport.send(")
    assert normalize_at < send_at, "playback frames reach the transport before normalization"
    # …and nothing else converts on the way out: one egress conversion, not two (ADR-0044 AD4).
    assert block.count("self._audio.normalize(") == 1


def test_ingress_is_composed_before_the_voice_activity_tap():
    """ADR-0044 AD5 — normalization wraps the transport stream, and the tap wraps that."""
    source = _PIPELINE_SRC.read_text(encoding="utf-8")
    inbound = source.split("def _inbound(self)")[1].split("async def _normalize_ingress")[0]
    normalize_at = inbound.index("_normalize_ingress(")
    tap_at = inbound.index("_tap_voice_activity(")
    assert normalize_at < tap_at


@pytest.mark.asyncio
async def test_vad_and_stt_observe_the_identical_normalized_frame():
    """The M3.2 invariant preserved over normalized audio: what VAD saw is what STT gets."""
    observed: list[AudioFrame] = []
    delivered: list[AudioFrame] = []

    class RecordingVAD:
        def __init__(self) -> None:
            self.engine = None

        async def start(self) -> None: ...
        async def stop(self) -> None: ...

        def observe(self, frame):
            observed.append(frame)
            return None

    class Named:
        """The pipeline logs provider names at startup; these stages are unused here."""

        def __init__(self, name: str) -> None:
            self.name = name

    class RecordingSTT:
        name = "recording-stt"
        capabilities = None

        async def transcribe(self, frames, *, language, sample_rate):
            async for frame in frames:
                delivered.append(frame)
            return
            yield  # pragma: no cover — empty async generator

    transport = LocalTransport()
    pipe = RuntimePipeline(
        transport=transport,
        stt=RecordingSTT(),
        llm=Named("stub-llm"),
        tts=Named("stub-tts"),
        session_id="ses_vad",
        system_prompt="p",
        sample_rate=16000,
        voice_activity=RecordingVAD(),
    )
    await pipe.start()
    await transport.feed("ses_vad", AudioFrame(data=b"hello", sample_rate=16000))
    await transport.hangup("ses_vad")
    await pipe.run()

    assert len(observed) == 1 and len(delivered) == 1
    assert observed[0] is delivered[0]  # the identical object, not merely equal
    assert delivered[0].sample_rate == 16000


@pytest.mark.asyncio
async def test_ingress_normalizes_pcm_before_stt_sees_it():
    """A rate mismatch is resolved once, at ingress, and STT receives the session rate."""
    delivered: list[AudioFrame] = []

    class Named:
        """The pipeline logs provider names at startup; these stages are unused here."""

        def __init__(self, name: str) -> None:
            self.name = name

    class RecordingSTT:
        name = "recording-stt"
        capabilities = None

        async def transcribe(self, frames, *, language, sample_rate):
            async for frame in frames:
                delivered.append(frame)
            return
            yield  # pragma: no cover

    transport = LocalTransport()
    pipe = RuntimePipeline(
        transport=transport, stt=RecordingSTT(), llm=Named("stub-llm"),
        tts=Named("stub-tts"), session_id="ses_rs", system_prompt="p", sample_rate=8000,
    )
    await pipe.start()
    await transport.feed(
        "ses_rs",
        AudioFrame(
            data=struct.pack("<4h", 0, 100, 200, 300),
            sample_rate=16000,
            encoding=AudioEncoding.PCM16,
        ),
    )
    await transport.hangup("ses_rs")
    await pipe.run()

    assert len(delivered) == 1
    assert delivered[0].sample_rate == 8000
    assert delivered[0].encoding is AudioEncoding.PCM16


# ── F. Local profile compatibility ──────────────────────────────────────────────


def test_local_tts_frames_are_opaque_not_falsely_pcm():
    """The local text-loopback payload must never claim to be PCM (ADR-0044 AD2)."""
    frame = AudioFrame(data=b"hi", sample_rate=DEFAULT_SAMPLE_RATE)
    assert frame.encoding is AudioEncoding.OPAQUE


@pytest.mark.asyncio
async def test_local_profile_conversation_is_unchanged_end_to_end():
    """The regression safety net: the text-loopback conversation still completes."""
    transcripts: list[tuple[str, str, bool]] = []
    service = RuntimeSessionService(
        build_local_registry(),
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )
    await service.start(
        StartSessionRequest(
            session_id="ses_local", org_id="o", agent_id="a", channel="web",
            system_prompt="be helpful",
        )
    )
    registry = service._registry  # noqa: SLF001
    worker = service._workers["ses_local"]  # noqa: SLF001
    transport = registry.resolve("transport")
    await transport.feed("ses_local", AudioFrame(data=b"hello"))
    await transport.hangup("ses_local")
    await worker.wait_closed()  # the hangup ends the conversation naturally
    await service.end("ses_local")

    assert ("user", "hello", True) in transcripts
    assert any(role == "agent" for role, _, _ in transcripts)
    for frame in transport.sent("ses_local"):
        assert frame.encoding is AudioEncoding.OPAQUE


# ── G. Architecture boundaries ──────────────────────────────────────────────────


def test_providers_do_not_resample():
    """AR-41 + `core/audio.py`'s rule: conversion lives in the runtime, never in a plugin."""
    root = pathlib.Path(inspect.getfile(LocalTransport)).parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            defines = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            if defines and "resampl" in node.name.lower():
                offenders.append(path.name)
    assert offenders == []


def test_resampler_has_no_third_party_dependency():
    """P-ADR-0002: commodity work is rented narrowly, and this seam rents nothing."""
    source = pathlib.Path(inspect.getfile(AudioNormalizer)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            modules.extend(a.name.split(".")[0] for a in node.names)
    assert set(modules) <= {"__future__", "array", "core"}, modules


# ══════════════════════════════════════════════════════════════════════════════
# M11.16.9 — channel normalization (the first real browser call's failure)
# ══════════════════════════════════════════════════════════════════════════════


def _interleaved(pairs: list[tuple[int, int]]) -> bytes:
    import struct

    flat: list[int] = []
    for left, right in pairs:
        flat.extend((left, right))
    return struct.pack(f"<{len(flat)}h", *flat)


def test_a_stereo_browser_frame_is_downmixed_to_the_session_s_mono_format():
    """The defect the first real browser conversation exposed.

    `aiortc`'s Opus decoder is hard-coded to `layout = "stereo"`, so **every** frame arriving from
    a browser is 48 kHz stereo — while the session's canonical format is mono 16 kHz, and both
    Silero and Deepgram correctly refuse non-mono audio. Before this, the normalizer converted the
    rate and left `channels` at 2, so the first real turn died with `CapabilityUnsupported`.

    Nothing hermetic caught it: every fake produced mono, and the aiortc↔aiortc tests asserted
    only `channels >= 1`."""
    from core.audio import AudioEncoding, AudioFrame
    from runtime.pipeline.audio import AudioNormalizer

    frame = AudioFrame(
        data=_interleaved([(1000, 2000)] * 960),  # 20 ms at 48 kHz, stereo
        sample_rate=48000,
        encoding=AudioEncoding.PCM16,
        channels=2,
    )
    out = AudioNormalizer(16000).normalize(frame)

    assert out.sample_rate == 16000
    assert out.channels == 1, "a stereo frame must reach the providers as mono"
    assert len(out.data) == 320 * 2, "20 ms of mono 16 kHz PCM16 is 320 samples"


def test_downmix_averages_rather_than_dropping_a_channel():
    """A browser that puts the microphone on one side of a stereo pair must not be muted."""
    import struct

    from core.audio import AudioEncoding, AudioFrame
    from runtime.pipeline.audio import AudioNormalizer

    # All signal on the right channel, silence on the left.
    frame = AudioFrame(
        data=_interleaved([(0, 4000)] * 160),
        sample_rate=16000,
        encoding=AudioEncoding.PCM16,
        channels=2,
    )
    out = AudioNormalizer(16000).normalize(frame)
    assert out.channels == 1 and out.sample_rate == 16000
    assert struct.unpack("<h", out.data[:2])[0] == 2000, "signal was dropped, not mixed"


def test_a_mono_frame_at_the_target_rate_is_still_passed_through_untouched():
    """The hot path must keep allocating nothing for a correctly configured session."""
    from core.audio import AudioEncoding, AudioFrame
    from runtime.pipeline.audio import AudioNormalizer

    frame = AudioFrame(
        data=b"\x00\x01" * 320, sample_rate=16000, encoding=AudioEncoding.PCM16, channels=1
    )
    assert AudioNormalizer(16000).normalize(frame) is frame


def test_an_opaque_stereo_frame_is_passed_through_rather_than_reinterpreted():
    """An undeclared payload may be moved but never reinterpreted (ADR-0044 AD2)."""
    from core.audio import AudioEncoding, AudioFrame
    from runtime.pipeline.audio import AudioNormalizer

    frame = AudioFrame(
        data=b"whatever", sample_rate=48000, encoding=AudioEncoding.OPAQUE, channels=2
    )
    assert AudioNormalizer(16000).normalize(frame) is frame
