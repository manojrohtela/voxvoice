"""Playback through the composition root and the pipeline, plus the M11.10 boundaries.

The questions the unit tests cannot answer, because they are about *wiring*: is real TTS reachable
the intended way, does it refuse to be selected dishonestly, does clause text actually become
ordered audio at the transport, do the latency boundaries mark real events, and does barge-in
actually silence the assistant.

Hermetic throughout: a `CredentialProvider` built from explicit overrides rather than the process
environment, and deterministic providers. The suite depends on no credential, no vendor SDK and no
network.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

import bootstrap
from api.adapters.secrets import ChainedCredentialProvider, EnvCredentialProvider
from core.audio import DEFAULT_SAMPLE_RATE, AudioFrame
from providers.llm.echo import EchoLLM
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.cartesia import CartesiaTTS
from providers.tts.local import LocalTTS
from runtime.pipeline.pipeline import RuntimePipeline
from runtime.registry import CapabilityKind
from runtime.speech import PlaybackEngine, PlaybackScheduler

from .fake_speech import FakeSpeechClient, ScriptedTTS, settle, spin

SECRET = "csk-bootstrap-key-0123456789abcdef"
PLATFORM = Path(bootstrap.__file__).parent


@pytest.fixture
def sdk_present(monkeypatch):
    """Make the vendor package importable without installing it (the M11.7 precedent).

    The composition root checks at startup that the SDK is present so a host missing it fails
    before a caller is waiting; the hermetic suite runs in a bare dev venv with no vendor SDK, so
    the check is satisfied with a stub. The credential path under test never touches its
    contents."""
    monkeypatch.setitem(sys.modules, "cartesia", types.ModuleType("cartesia"))


def _credentials(**values: str) -> EnvCredentialProvider:
    return EnvCredentialProvider(prefix="VOXVOICE_TEST_UNSET_PREFIX_", overrides=values)


class CollectingTransport(LocalTransport):
    """The local transport, with the frames it was sent exposed in order."""

    @property
    def fills(self) -> list[int]:
        return [f.data[0] if f.data else -1 for f in self.sent("ses_pb")]


def _pipeline(*, tts=None, playback=None, transcripts=None, **kwargs):
    transport = kwargs.pop("transport", None) or CollectingTransport()
    pipe = RuntimePipeline(
        transport=transport,
        stt=LocalSTT(),
        llm=kwargs.pop("llm", None) or EchoLLM(),
        tts=tts or LocalTTS(),
        session_id="ses_pb",
        system_prompt="be helpful",
        sample_rate=kwargs.pop("sample_rate", DEFAULT_SAMPLE_RATE),
        playback=playback,
        on_transcript=(
            None
            if transcripts is None
            else (lambda sid, role, text, final: transcripts.append((role, text, final)))
        ),
        **kwargs,
    )
    return pipe, transport


def _playback(tts, **kwargs) -> PlaybackScheduler:
    engine = PlaybackEngine(capacity_ms=kwargs.pop("capacity_ms", 10_000))
    return PlaybackScheduler(tts, engine, **kwargs)


# ── 69: the default profile is untouched ────────────────────────────────────


def test_69_the_default_tts_profile_is_still_local():
    assert isinstance(bootstrap.build_registry().resolve(CapabilityKind.TTS), LocalTTS)


def test_the_default_session_controller_uses_local_tts():
    controller = bootstrap.build_session_controller()
    assert controller._registry.resolve(CapabilityKind.TTS).name == "local"  # noqa: SLF001


def test_build_local_registry_is_untouched_by_the_tts_profile():
    assert bootstrap.build_local_registry().resolve(CapabilityKind.TTS).name == "local"


def test_local_tts_keeps_its_exact_capabilities():
    """The additive `streaming_input` flag defaults False, so `LocalTTS` means what it did."""
    assert LocalTTS.capabilities.streaming is True
    assert LocalTTS.capabilities.streaming_input is False


async def test_a_pipeline_without_playback_behaves_exactly_as_m11_9():
    """The default response path: no scheduler, no engine, unchanged conversation."""
    transcripts: list[tuple[str, str, bool]] = []
    pipe, transport = _pipeline(transcripts=transcripts)
    await pipe.start()
    run = asyncio.create_task(pipe.run())
    await transport.feed("ses_pb", AudioFrame(data=b"hello"))
    await transport.hangup("ses_pb")
    await asyncio.wait_for(run, timeout=2.0)
    await pipe.stop()
    assert ("agent", "You said: hello", True) in transcripts
    assert [f.data.decode() for f in transport.sent("ses_pb")] == ["You said: hello"]


# ── 1, 2: explicit selection and refusal ────────────────────────────────────


def test_1_the_cartesia_profile_resolves_the_credential_and_builds_the_real_client(sdk_present):
    registry = bootstrap.build_registry(
        tts=bootstrap.TTS_CARTESIA,
        tts_voice="voice-1",
        credentials=_credentials(**{bootstrap.CARTESIA_API_KEY: SECRET}),
    )
    tts = registry.resolve(CapabilityKind.TTS)
    assert isinstance(tts, CartesiaTTS)
    assert SECRET not in repr(tts._client)  # noqa: SLF001


def test_selecting_cartesia_replaces_rather_than_shadows_local_tts():
    registry = bootstrap.build_registry(
        tts=bootstrap.TTS_CARTESIA, tts_client=FakeSpeechClient(), tts_voice="v"
    )
    assert [m.name for m in registry.list(CapabilityKind.TTS)] == ["cartesia"]


def test_the_credential_name_is_the_documented_one():
    assert bootstrap.CARTESIA_API_KEY == "cartesia_api_key"
    lines = (PLATFORM / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "VOXVOICE_SECRET_CARTESIA_API_KEY=" in lines, "credential not documented by name"


def test_the_key_rotation_seam_is_honoured(sdk_present):
    chained = ChainedCredentialProvider(
        _credentials(), _credentials(**{bootstrap.CARTESIA_API_KEY: SECRET})
    )
    registry = bootstrap.build_registry(
        tts=bootstrap.TTS_CARTESIA, tts_voice="v", credentials=chained
    )
    assert isinstance(registry.resolve(CapabilityKind.TTS), CartesiaTTS)


def test_only_the_cartesia_credential_is_resolved(sdk_present):
    """CR4 — least privilege: the composition root asks for exactly one name."""
    asked: list[str] = []

    class Recording(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            asked.append(name)
            return SECRET

    bootstrap.build_registry(tts=bootstrap.TTS_CARTESIA, tts_voice="v", credentials=Recording())
    assert asked == [bootstrap.CARTESIA_API_KEY]


def test_2_cartesia_without_a_credential_provider_is_refused_not_downgraded():
    with pytest.raises(ValueError, match="refusing to fall back"):
        bootstrap.build_registry(tts=bootstrap.TTS_CARTESIA, tts_voice="v")


@pytest.mark.parametrize("value", [None, ""])
def test_2b_cartesia_without_the_credential_is_refused_and_names_it(value):
    overrides = {} if value is None else {bootstrap.CARTESIA_API_KEY: value}
    with pytest.raises(ValueError, match="cartesia_api_key") as raised:
        bootstrap.build_registry(
            tts=bootstrap.TTS_CARTESIA, tts_voice="v", credentials=_credentials(**overrides)
        )
    assert "refusing to fall back" in str(raised.value)


def test_cartesia_without_a_voice_is_refused_at_startup():
    """A synthesis with no voice fails mid-call; refusing here turns it into a config error."""
    with pytest.raises(ValueError, match="requires a voice"):
        bootstrap.build_registry(
            tts=bootstrap.TTS_CARTESIA,
            credentials=_credentials(**{bootstrap.CARTESIA_API_KEY: SECRET}),
        )


def test_cartesia_without_the_sdk_is_refused_at_startup(monkeypatch):
    monkeypatch.setitem(sys.modules, "cartesia", None)
    with pytest.raises(ValueError, match="not importable"):
        bootstrap.build_registry(
            tts=bootstrap.TTS_CARTESIA,
            tts_voice="v",
            credentials=_credentials(**{bootstrap.CARTESIA_API_KEY: SECRET}),
        )


def test_a_refused_profile_never_leaks_a_credential_value():
    class WrongName(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            return None

    with pytest.raises(ValueError) as raised:
        bootstrap.build_registry(tts=bootstrap.TTS_CARTESIA, tts_voice="v", credentials=WrongName())
    assert SECRET not in str(raised.value)


def test_an_unknown_tts_profile_is_refused():
    with pytest.raises(ValueError, match="unknown tts profile"):
        bootstrap.build_registry(tts="gramophone")


def test_the_session_controller_can_be_built_on_cartesia(sdk_present):
    controller = bootstrap.build_session_controller(
        tts=bootstrap.TTS_CARTESIA,
        tts_voice="v",
        credentials=_credentials(**{bootstrap.CARTESIA_API_KEY: SECRET}),
    )
    assert isinstance(
        controller._registry.resolve(CapabilityKind.TTS), CartesiaTTS  # noqa: SLF001
    )


# ── 58–63: the complete clause-to-audio pipeline ────────────────────────────


async def test_58_to_63_clause_text_becomes_ordered_audio_at_the_transport():
    """LLM → ClauseDetector → ClauseSink → Scheduler → Engine → Transport, in one assertion.

    `EchoLLM` answers "You said: hello" and the clause detector flushes it as one clause;
    `ScriptedTTS` turns it into two identifiable frames, which must arrive in order."""
    tts = ScriptedTTS(frames_per_call=2)
    playback = _playback(tts)
    pipe, transport = _pipeline(tts=tts, playback=playback)
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert tts.spoken == ["You said: hello"]
    assert transport.fills == [10, 11]


async def test_multiple_clauses_reach_the_transport_in_clause_order():
    class TwoClauseLLM(EchoLLM):
        name = "two-clause"

        async def stream(self, *, context, tools=None, model=None):
            from core.providers.llm import TokenDelta

            for part in ["First one. ", "Second one. "]:
                yield TokenDelta(text=part)

    tts = ScriptedTTS(frames_per_call=2)
    playback = _playback(tts)
    pipe, transport = _pipeline(tts=tts, playback=playback, llm=TwoClauseLLM())
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert tts.spoken == ["First one.", "Second one."]
    assert transport.fills == [10, 11, 20, 21]


async def test_the_agent_transcript_is_still_emitted_with_playback_wired():
    transcripts: list[tuple[str, str, bool]] = []
    tts = ScriptedTTS(frames_per_call=1)
    pipe, _ = _pipeline(tts=tts, playback=_playback(tts), transcripts=transcripts)
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert ("agent", "You said: hello", True) in transcripts


async def test_playback_normalizes_at_the_single_egress_point():
    """AR-41: the Engine never resamples; the pipeline's egress normalizer does, once."""
    tts = ScriptedTTS(frames_per_call=1, sample_rate=8000)
    pipe, transport = _pipeline(
        tts=tts, playback=_playback(tts), sample_rate=16000
    )
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert transport.sent("ses_pb")[0].sample_rate == 16000


# ── 53–57: the latency boundaries ───────────────────────────────────────────


class Recorder:
    """A `LatencyObserver` that records which boundaries were stamped, in order."""

    def __init__(self) -> None:
        self.boundaries: list[str] = []
        self.completed: list[str] = []

    def record(self, session_id, turn_index, boundary, *, backdate_ms: float = 0.0) -> None:
        self.boundaries.append(boundary)

    def complete(self, session_id, turn_index, status) -> None:
        self.completed.append(status)


async def test_53_and_54_tts_first_audio_precedes_first_audio_out():
    """Both boundaries mark real events, in the only order physically possible."""
    latency = Recorder()
    tts = ScriptedTTS(frames_per_call=2)
    pipe, _ = _pipeline(tts=tts, playback=_playback(tts), latency=latency)
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert "tts_first_audio" in latency.boundaries
    assert "first_audio_out" in latency.boundaries
    assert latency.boundaries.index("tts_first_audio") < latency.boundaries.index(
        "first_audio_out"
    )


async def test_57_no_boundary_is_stamped_when_no_audio_exists():
    """A provider that produces nothing must not stamp `tts_first_audio` — synthetic timing is
    exactly what the metric must never carry."""
    from .fake_speech import SilentTTS

    latency = Recorder()
    tts = SilentTTS()
    pipe, transport = _pipeline(tts=tts, playback=_playback(tts), latency=latency)
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert "tts_first_audio" not in latency.boundaries
    assert "first_audio_out" not in latency.boundaries
    assert transport.sent("ses_pb") == []


async def test_55_first_audio_out_is_stamped_after_the_transport_accepts_the_frame():
    """Back-pressure must move the metric, not be papered over: while the transport accepts
    nothing, `first_audio_out` has not happened."""
    gate = asyncio.Event()

    class SlowTransport(CollectingTransport):
        async def send(self, *, session_id: str, frame: AudioFrame) -> None:
            await gate.wait()
            await super().send(session_id=session_id, frame=frame)

    latency = Recorder()
    tts = ScriptedTTS(frames_per_call=2)
    pipe, transport = _pipeline(
        tts=tts, playback=_playback(tts), latency=latency, transport=SlowTransport()
    )
    await pipe.start()
    task = asyncio.create_task(pipe._respond("hello"))  # noqa: SLF001
    assert await settle(lambda: "tts_first_audio" in latency.boundaries)
    await spin()
    assert "first_audio_out" not in latency.boundaries, "stamped before the transport accepted it"
    gate.set()
    await task
    await pipe.stop()
    assert "first_audio_out" in latency.boundaries


async def test_56_the_metric_turn_boundary_names_are_unchanged():
    """M11.8's chain is preserved: M11.10 renames nothing and adds no metric event."""
    from observability import STAGE_BOUNDARIES, STAGE_INTERVALS

    assert STAGE_BOUNDARIES == (
        "speech_end",
        "endpoint_decided",
        "stt_final",
        "llm_first_token",
        "tts_first_audio",
        "first_audio_out",
    )
    assert [name for name, _, _ in STAGE_INTERVALS] == [
        "endpoint",
        "stt",
        "llm",
        "tts",
        "egress",
    ]


# ── 39–43: barge-in ─────────────────────────────────────────────────────────


async def test_43_a_barge_in_discards_queued_assistant_audio():
    """The Scheduler's abandon path, exercised through the pipeline's pre-empt."""
    tts = ScriptedTTS(frames_per_call=4)
    playback = _playback(tts, capacity_ms=10_000)
    gate = asyncio.Event()

    class HeldTransport(CollectingTransport):
        async def send(self, *, session_id: str, frame: AudioFrame) -> None:
            await gate.wait()
            await super().send(session_id=session_id, frame=frame)

    pipe, transport = _pipeline(tts=tts, playback=playback, transport=HeldTransport())
    await pipe.start()
    task = asyncio.create_task(pipe._respond("hello"))  # noqa: SLF001
    assert await settle(lambda: playback.engine.queued_frames > 0 or task.done())
    await playback.abandon()
    gate.set()
    await spin()
    await task
    await pipe.stop()
    assert transport.sent("ses_pb") == [], "queued audio played after the barge-in"


async def test_39_ordinary_speech_does_not_cancel_playback_without_interruptions():
    """`allow_interruptions=False` → no interrupt coordinator is wired, so nothing pre-empts."""
    tts = ScriptedTTS(frames_per_call=2)
    playback = _playback(tts)
    pipe, transport = _pipeline(tts=tts, playback=playback)
    assert pipe._interrupt is None  # noqa: SLF001
    await pipe.start()
    await pipe._respond("hello")  # noqa: SLF001
    await pipe.stop()
    assert len(transport.sent("ses_pb")) == 2


async def test_a_new_response_after_a_barge_in_speaks_again():
    """The regression that would otherwise mute the assistant for the rest of the call."""
    tts = ScriptedTTS(frames_per_call=1)
    playback = _playback(tts)
    pipe, transport = _pipeline(tts=tts, playback=playback)
    await pipe.start()
    await pipe._respond("one")  # noqa: SLF001
    await playback.abandon()
    await pipe._respond("two")  # noqa: SLF001
    await pipe.stop()
    assert tts.spoken == ["You said: one", "You said: two"]


async def test_a_cancelled_response_stops_synthesis_and_discards_audio():
    gate, started = asyncio.Event(), asyncio.Event()
    tts = ScriptedTTS(frames_per_call=6, gate=gate, started=started)
    playback = _playback(tts)
    pipe, transport = _pipeline(tts=tts, playback=playback)
    await pipe.start()
    task = asyncio.create_task(pipe._respond("hello"))  # noqa: SLF001
    assert await settle(started.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await pipe.stop()
    assert playback.engine.state.value in {"cancelled", "failed"}


# ── 70 + architecture ───────────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def _identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


PLAYBACK_MODULES = ["playback.py", "scheduler.py"]


def test_the_cartesia_sdk_is_imported_only_inside_its_adapter():
    offenders = []
    for path in PLATFORM.rglob("*.py"):
        if any(part in (".venv", "voicebot", "tests") for part in path.parts):
            continue
        if "cartesia" in _imports(path) and path.name != "cartesia_client.py":
            offenders.append(str(path.relative_to(PLATFORM)))
    assert offenders == []


def test_no_vendor_tts_type_appears_in_core_or_runtime():
    for root in ("core", "runtime"):
        for path in (PLATFORM / root).rglob("*.py"):
            assert not (_identifiers(path) & {"cartesia", "AsyncCartesia", "TTSSSEEvent"}), (
                f"{path.relative_to(PLATFORM)} names a vendor type"
            )


def test_the_playback_engine_imports_nothing_but_core_and_the_stdlib():
    """Provider-neutral by construction: it cannot reach an LLM, a VAD, endpointing or a vendor."""
    imported = _imports(PLATFORM / "runtime" / "speech" / "playback.py")
    assert imported <= {"__future__", "asyncio", "collections", "collections.abc", "enum",
                        "core.audio"}


def test_the_playback_modules_import_no_provider_implementation():
    for name in PLAYBACK_MODULES:
        imported = _imports(PLATFORM / "runtime" / "speech" / name)
        offenders = [m for m in imported if m.startswith("providers")]
        assert not offenders, f"{name} imports a provider implementation"


def test_the_playback_modules_reach_no_llm_vad_or_endpointing():
    for name in PLAYBACK_MODULES:
        imported = _imports(PLATFORM / "runtime" / "speech" / name)
        for forbidden in ("llm", "voice_activity", "endpoint", "turn", "smart_turn", "interrupt"):
            assert not [m for m in imported if forbidden in m], f"{name} reaches {forbidden}"


def test_the_tts_provider_never_imports_playback():
    for path in (PLATFORM / "providers" / "tts").glob("*.py"):
        imported = _imports(path)
        assert not [m for m in imported if "speech" in m and "runtime" in m]
        assert not [m for m in imported if m.startswith("runtime")]


def test_70_no_playback_module_writes_to_the_epic_10_spine():
    for name in PLAYBACK_MODULES:
        imported = _imports(PLATFORM / "runtime" / "speech" / name)
        assert not [m for m in imported if m.split(".")[0] in ("events", "billing")]
    for path in (PLATFORM / "providers" / "tts").glob("*.py"):
        imported = _imports(path)
        assert not [m for m in imported if m.split(".")[0] in ("events", "billing")]


def test_no_unbounded_queue_is_used_on_the_audio_path():
    """`asyncio.Queue()` with no maxsize is the unbounded buffer this milestone forbids."""
    for name in PLAYBACK_MODULES:
        identifiers = _identifiers(PLATFORM / "runtime" / "speech" / name)
        assert "Queue" not in identifiers, f"{name} uses an asyncio.Queue"


def test_no_runtime_framework_was_introduced():
    forbidden = {"pipecat", "langchain", "langgraph", "llama_index"}
    for path in PLATFORM.rglob("*.py"):
        if any(part in (".venv", "voicebot") for part in path.parts):
            continue
        assert not ({m.split(".")[0] for m in _imports(path)} & forbidden)


def test_there_is_still_exactly_one_orchestrator():
    orchestrators = [
        path
        for path in (PLATFORM / "runtime").rglob("*.py")
        if any(
            isinstance(node, ast.ClassDef) and node.name.endswith("Orchestrator")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert [p.name for p in orchestrators] == ["orchestrator.py"]


def test_the_tts_plugin_never_reads_the_environment():
    for path in (PLATFORM / "providers" / "tts").glob("*.py"):
        assert not (_identifiers(path) & {"getenv", "environ", "load_dotenv"})
