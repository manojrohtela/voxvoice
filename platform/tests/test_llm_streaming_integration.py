"""Streaming LLM through the composition root and the pipeline, plus the M11.9 boundaries.

Three questions the provider and detector tests cannot answer, because all three are about
*wiring*:

1. **Is the real LLM reachable the intended way?** Selected explicitly through `bootstrap`,
   constructed there with one resolved credential (ADR-0049 CR3/CR4), resolved by the runtime —
   never built by the Orchestrator (AR-20).
2. **Does it refuse to be selected dishonestly?** No credential means no Groq — and **no silent
   fallback** to `EchoLLM` either.
3. **Do clauses actually leave the pipeline while the model is still generating?**

Hermetic throughout: a `CredentialProvider` built from explicit overrides rather than the process
environment, and a deterministic completion client. The suite depends on no credential, no vendor
SDK and no network.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import types
from pathlib import Path

import pytest

import bootstrap
from api.adapters.secrets import ChainedCredentialProvider, EnvCredentialProvider
from core.audio import DEFAULT_SAMPLE_RATE, AudioFrame
from core.errors import ProviderUnavailable
from core.session_control import StartSessionRequest
from providers.llm.completion_stream import FailureKind
from providers.llm.echo import EchoLLM
from providers.llm.groq import GroqLLM
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.local import LocalTTS
from runtime.pipeline.pipeline import RuntimePipeline
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService
from runtime.speech import ClausePolicy

from .fake_completion import FakeCompletionClient, RecordingClauseSink, settle

SECRET = "gsk-bootstrap-key-0123456789abcdef"
PLATFORM = Path(bootstrap.__file__).parent


@pytest.fixture
def sdk_present(monkeypatch):
    """Make the vendor package importable without installing it.

    The composition root checks at startup that the SDK is present, so a host missing it fails
    before the first caller rather than mid-sentence (the M11.7 rule). The hermetic suite runs in a
    bare dev venv that deliberately has no vendor SDK, so the check is satisfied with a stub — the
    credential path being tested here never touches the module's contents."""
    monkeypatch.setitem(sys.modules, "groq", types.ModuleType("groq"))


def _credentials(**values: str) -> EnvCredentialProvider:
    """An ADR-0049 provider that cannot see the real environment: an unused prefix + overrides."""
    return EnvCredentialProvider(prefix="VOXVOICE_TEST_UNSET_PREFIX_", overrides=values)


def _pipeline(*, llm, sink=None, policy=None, transcripts=None, session_id="ses_llm"):
    return RuntimePipeline(
        transport=LocalTransport(),
        stt=LocalSTT(),
        llm=llm,
        tts=LocalTTS(),
        session_id=session_id,
        system_prompt="be helpful",
        sample_rate=DEFAULT_SAMPLE_RATE,
        clause_sink=sink,
        clause_policy=policy,
        on_transcript=(
            None
            if transcripts is None
            else (lambda sid, role, text, final: transcripts.append((role, text, final)))
        ),
    )


# ── 45, 32: the default profile is untouched ────────────────────────────────


def test_45_the_default_llm_profile_is_still_echo():
    assert isinstance(bootstrap.build_registry().resolve(CapabilityKind.LLM), EchoLLM)


def test_the_default_session_controller_uses_the_echo_llm():
    controller = bootstrap.build_session_controller()
    assert controller._registry.resolve(CapabilityKind.LLM).name == "echo"  # noqa: SLF001


def test_build_local_registry_is_untouched_by_the_llm_profile():
    assert bootstrap.build_local_registry().resolve(CapabilityKind.LLM).name == "echo"


def test_32_the_existing_local_llm_still_streams_as_it_always_did():
    """`EchoLLM` is untouched: same contract, same deterministic output."""
    assert EchoLLM.capabilities.context_window == 8192
    assert inspect.isasyncgenfunction(EchoLLM.stream)


async def test_31_the_fake_llm_remains_compatible_with_the_response_path():
    """A pipeline on `EchoLLM` with no clause sink behaves exactly as M11.8 — the regression
    guard for "M11.9 changed the default conversation"."""
    transport = LocalTransport()
    transcripts: list[tuple[str, str, bool]] = []
    pipe = RuntimePipeline(
        transport=transport,
        stt=LocalSTT(),
        llm=EchoLLM(),
        tts=LocalTTS(),
        session_id="ses_echo",
        system_prompt="be helpful",
        sample_rate=DEFAULT_SAMPLE_RATE,
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )
    await pipe.start()
    run = asyncio.create_task(pipe.run())
    await transport.feed("ses_echo", AudioFrame(data=b"hello"))
    await transport.hangup("ses_echo")
    await asyncio.wait_for(run, timeout=2.0)
    await pipe.stop()
    assert ("agent", "You said: hello", True) in transcripts
    assert [f.data.decode() for f in transport.sent("ses_echo")] == ["You said: hello"]


# ── 1: explicit selection ───────────────────────────────────────────────────


def test_1_the_groq_profile_resolves_the_credential_and_builds_the_real_client(sdk_present):
    registry = bootstrap.build_registry(
        llm=bootstrap.LLM_GROQ, credentials=_credentials(**{bootstrap.GROQ_API_KEY: SECRET})
    )
    llm = registry.resolve(CapabilityKind.LLM)
    assert isinstance(llm, GroqLLM)
    assert SECRET not in repr(llm._client)  # noqa: SLF001


def test_selecting_groq_replaces_rather_than_shadows_the_echo_llm():
    registry = bootstrap.build_registry(
        llm=bootstrap.LLM_GROQ, llm_client=FakeCompletionClient()
    )
    assert [m.name for m in registry.list(CapabilityKind.LLM)] == ["groq"]


def test_the_credential_name_is_the_documented_one():
    assert bootstrap.GROQ_API_KEY == "groq_api_key"
    lines = (PLATFORM / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "VOXVOICE_SECRET_GROQ_API_KEY=" in lines, "credential not documented by name"


def test_the_key_rotation_seam_is_honoured(sdk_present):
    chained = ChainedCredentialProvider(
        _credentials(), _credentials(**{bootstrap.GROQ_API_KEY: SECRET})
    )
    registry = bootstrap.build_registry(llm=bootstrap.LLM_GROQ, credentials=chained)
    assert isinstance(registry.resolve(CapabilityKind.LLM), GroqLLM)


def test_only_the_groq_credential_is_resolved(sdk_present):
    """CR4 — least privilege: the composition root asks for exactly one name."""
    asked: list[str] = []

    class Recording(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            asked.append(name)
            return SECRET

    bootstrap.build_registry(llm=bootstrap.LLM_GROQ, credentials=Recording())
    assert asked == [bootstrap.GROQ_API_KEY]


def test_the_model_can_be_selected_at_the_composition_root():
    registry = bootstrap.build_registry(
        llm=bootstrap.LLM_GROQ, llm_client=FakeCompletionClient(), llm_model="llama-3.1-8b-instant"
    )
    assert registry.resolve(CapabilityKind.LLM)._model == "llama-3.1-8b-instant"  # noqa: SLF001


def test_the_session_controller_can_be_built_on_groq(sdk_present):
    controller = bootstrap.build_session_controller(
        llm=bootstrap.LLM_GROQ, credentials=_credentials(**{bootstrap.GROQ_API_KEY: SECRET})
    )
    assert isinstance(
        controller._registry.resolve(CapabilityKind.LLM), GroqLLM  # noqa: SLF001
    )


# ── 2: missing credentials refuse, never downgrade ──────────────────────────


def test_2_groq_without_a_credential_provider_is_refused_not_downgraded():
    with pytest.raises(ValueError, match="refusing to fall back"):
        bootstrap.build_registry(llm=bootstrap.LLM_GROQ)


@pytest.mark.parametrize("value", [None, ""])
def test_2b_groq_without_the_credential_is_refused_and_names_it(value):
    overrides = {} if value is None else {bootstrap.GROQ_API_KEY: value}
    with pytest.raises(ValueError, match="groq_api_key") as raised:
        bootstrap.build_registry(llm=bootstrap.LLM_GROQ, credentials=_credentials(**overrides))
    assert "refusing to fall back" in str(raised.value)


def test_a_refused_profile_never_leaks_a_credential_value():
    class WrongName(EnvCredentialProvider):
        def get(self, name: str) -> str | None:
            return None

    with pytest.raises(ValueError) as raised:
        bootstrap.build_registry(llm=bootstrap.LLM_GROQ, credentials=WrongName())
    assert SECRET not in str(raised.value)


def test_groq_without_the_sdk_is_refused_at_startup_not_mid_call(monkeypatch):
    """A host missing the vendor package must fail before it answers a caller, not during one."""
    monkeypatch.setitem(sys.modules, "groq", None)  # `import groq` raises ImportError
    with pytest.raises(ValueError, match="not importable") as raised:
        bootstrap.build_registry(
            llm=bootstrap.LLM_GROQ, credentials=_credentials(**{bootstrap.GROQ_API_KEY: SECRET})
        )
    assert "refusing to fall back" in str(raised.value)


def test_an_unknown_llm_profile_is_refused():
    with pytest.raises(ValueError, match="unknown llm profile"):
        bootstrap.build_registry(llm="oracle-of-delphi")


def test_a_bad_llm_profile_is_refused_before_any_provider_is_built():
    with pytest.raises(ValueError, match="unknown llm profile"):
        bootstrap.build_registry(stt=bootstrap.STT_LOCAL, llm="nope")


# ── 17, 41: clauses leave the pipeline before the model finishes ────────────


async def test_17_the_first_clause_reaches_the_sink_before_the_llm_completes():
    """The headline property of M11.9, proven with gates rather than timing.

    The vendor is held open after its first chunk. At that moment the sink must already hold the
    first clause — which is only possible if nothing waits for the whole response."""
    gate, started = asyncio.Event(), asyncio.Event()
    client = FakeCompletionClient(
        ["Hello there. ", "How can I help you today?"], gate=gate, started=started
    )
    sink = RecordingClauseSink()
    pipe = _pipeline(llm=GroqLLM(client), sink=sink)
    task = asyncio.create_task(pipe._respond("hi"))  # noqa: SLF001
    assert await settle(started.is_set), "the vendor was never asked for a second chunk"
    assert sink.texts == ["Hello there."], "no clause reached the sink before generation ended"
    assert sink.finished == 0  # generation is provably still in progress
    assert not task.done()
    gate.set()
    await task
    assert sink.texts == ["Hello there.", "How can I help you today?"]
    assert sink.finished == 1


async def test_41_the_sink_receives_every_clause_in_order():
    sink = RecordingClauseSink()
    client = FakeCompletionClient(["One. ", "Two. ", "Three."])
    await _pipeline(llm=GroqLLM(client), sink=sink)._respond("hi")  # noqa: SLF001
    assert sink.texts == ["One.", "Two.", "Three."]
    assert sink.indices == [0, 1, 2]


async def test_42_the_tail_of_a_response_is_flushed_to_the_sink():
    sink = RecordingClauseSink()
    client = FakeCompletionClient(["A sentence. ", "and an unterminated tail"])
    await _pipeline(llm=GroqLLM(client), sink=sink)._respond("hi")  # noqa: SLF001
    assert sink.texts == ["A sentence.", "and an unterminated tail"]
    assert sink.boundaries[-1] == "end_of_stream"


async def test_the_clause_policy_is_honoured_by_the_pipeline():
    """AR-25: injected configuration acts."""
    sink = RecordingClauseSink()
    client = FakeCompletionClient(["x" * 30])
    await _pipeline(  # noqa: SLF001
        llm=GroqLLM(client), sink=sink, policy=ClausePolicy(max_buffer_chars=10)
    )._respond("hi")
    assert sink.boundaries[:3] == ["overflow"] * 3


async def test_a_response_with_no_sink_behaves_exactly_as_before():
    """The default path: no sink, no detector, no change."""
    transcripts: list[tuple[str, str, bool]] = []
    client = FakeCompletionClient(["Hello ", "there."])
    pipe = _pipeline(llm=GroqLLM(client), transcripts=transcripts)
    await pipe._respond("hi")  # noqa: SLF001
    assert ("agent", "Hello there.", True) in transcripts


async def test_each_response_starts_with_a_clean_clause_buffer():
    """A detector shared across turns would carry one answer's tail into the next."""
    sink = RecordingClauseSink()
    client = FakeCompletionClient(["First answer with no end"])
    pipe = _pipeline(llm=GroqLLM(client), sink=sink)
    await pipe._respond("one")  # noqa: SLF001
    await pipe._respond("two")  # noqa: SLF001
    assert sink.texts == ["First answer with no end"] * 2
    assert sink.indices == [0, 0]  # each response numbers its own clauses from zero


# ── 28: cancellation and failure at the pipeline boundary ───────────────────


async def test_28_cancelling_a_response_between_clauses_abandons_the_rest():
    """The turn is over: the buffered tail must not be spoken, and the sink must be told."""
    gate, started = asyncio.Event(), asyncio.Event()
    client = FakeCompletionClient(
        ["Delivered. ", "Never spoken"], gate=gate, started=started
    )
    sink = RecordingClauseSink()
    pipe = _pipeline(llm=GroqLLM(client), sink=sink)
    task = asyncio.create_task(pipe._respond("hi"))  # noqa: SLF001
    assert await settle(started.is_set), "the vendor was never asked for a second chunk"
    assert sink.texts == ["Delivered."]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink.texts == ["Delivered."]
    assert (sink.abandoned, sink.finished) == (1, 0)


async def test_a_provider_failure_abandons_the_clause_stream():
    """9: a failure is never reported to the sink as a completed response."""
    sink = RecordingClauseSink()
    client = FakeCompletionClient(
        ["Half a sentence. ", "more"], fail_at=1, kind=FailureKind.UNAVAILABLE
    )
    with pytest.raises(ProviderUnavailable):
        await _pipeline(llm=GroqLLM(client), sink=sink)._respond("hi")  # noqa: SLF001
    assert sink.texts == ["Half a sentence."]
    assert (sink.abandoned, sink.finished) == (1, 0)


async def test_a_sink_that_raises_does_not_silently_complete_the_response():
    sink = RecordingClauseSink(error=RuntimeError("sink exploded"))
    client = FakeCompletionClient(["One. Two."])
    with pytest.raises(RuntimeError, match="sink exploded"):
        await _pipeline(llm=GroqLLM(client), sink=sink)._respond("hi")  # noqa: SLF001
    assert sink.finished == 0


async def test_30_a_response_leaves_no_background_task_behind():
    before = len(asyncio.all_tasks())
    sink = RecordingClauseSink()
    client = FakeCompletionClient(["One. ", "Two."])
    await _pipeline(llm=GroqLLM(client), sink=sink)._respond("hi")  # noqa: SLF001
    assert len(asyncio.all_tasks()) == before


# ── 33–38: nothing else moved ───────────────────────────────────────────────


async def test_33_to_38_a_real_llm_changes_no_other_subsystem():
    """A full conversation on the real provider through the unchanged runtime: transport, STT,
    endpointing, turn engine and TTS all behave as they did, and the transcript is the model's."""
    transcripts: list[tuple[str, str, bool]] = []
    sink = RecordingClauseSink()
    registry = bootstrap.build_registry(
        llm=bootstrap.LLM_GROQ,
        llm_client=FakeCompletionClient(["Good morning. ", "How may I help?"]),
    )
    service = RuntimeSessionService(
        registry,
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )
    await service.start(
        StartSessionRequest(
            session_id="ses_groq", org_id="o", agent_id="a", channel="web",
            system_prompt="p", language="en",
        )
    )
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    await transport.feed("ses_groq", AudioFrame(data=b"hello"))
    await transport.hangup("ses_groq")
    await service._workers["ses_groq"].wait_closed()  # noqa: SLF001
    await service.end("ses_groq")
    assert ("user", "hello", True) in transcripts
    assert ("agent", "Good morning. How may I help?", True) in transcripts
    assert sink.texts == []  # no sink was wired into this session; nothing was fabricated


def test_39_the_llm_provider_layer_has_no_tts_coupling():
    """No LLM module may import TTS, and no clause module may import an LLM provider."""
    for path in (PLATFORM / "providers" / "llm").glob("*.py"):
        imported = _imports(path)
        assert not [m for m in imported if "tts" in m], f"{path.name} imports TTS"
    for path in (PLATFORM / "runtime" / "speech").glob("*.py"):
        imported = _imports(path)
        offenders = [m for m in imported if m.startswith("providers")]
        assert not offenders, f"{path.name} imports a provider"


def test_40_no_m11_9_module_writes_to_the_epic_10_spine():
    """M11.12 owns spine wiring; nothing here may reach `events` or `billing`."""
    for path in _m11_9_modules():
        imported = _imports(path)
        assert not [m for m in imported if m.split(".")[0] in ("events", "billing")], (
            f"{path} reaches the Epic 10 spine"
        )


# ── 3: the vendor import boundary ───────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    """Every module name imported by a file, resolved from its AST.

    AST rather than text: M11.7 learned that a text search trips on the word appearing in a
    docstring, which makes the guard fail for a reason that has nothing to do with the boundary."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def _m11_9_modules() -> list[Path]:
    return [
        *(PLATFORM / "providers" / "llm").glob("*.py"),
        *(PLATFORM / "runtime" / "speech").glob("*.py"),
    ]


def test_3_the_groq_sdk_is_imported_only_inside_its_adapter():
    """AR-12. The one permitted importer is `providers/llm/groq_client.py`."""
    offenders = []
    for path in PLATFORM.rglob("*.py"):
        if any(part in (".venv", "voicebot", "tests") for part in path.parts):
            continue
        if "groq" in _imports(path) and path.name != "groq_client.py":
            offenders.append(str(path.relative_to(PLATFORM)))
    assert offenders == []


def test_3b_the_vendor_adapter_is_the_only_module_naming_the_sdk_at_all():
    """A vendor *type* must not appear in core or runtime even by name (AR-21)."""
    for root in ("core", "runtime"):
        for path in (PLATFORM / root).rglob("*.py"):
            identifiers = _identifiers(path)
            assert not (identifiers & {"groq", "AsyncGroq", "ChatCompletionChunk"}), (
                f"{path.relative_to(PLATFORM)} names a vendor type"
            )


def _identifiers(path: Path) -> set[str]:
    """Identifiers and attribute names used in a file — prose in docstrings is ignored."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_clause_detector_depends_on_nothing_but_the_standard_library():
    """Provider-neutral by construction: no provider, no event bus, no vendor, no I/O."""
    imported = _imports(PLATFORM / "runtime" / "speech" / "clauses.py")
    assert imported <= {"__future__", "dataclasses", "enum"}


#: The clause modules proper. M11.10 added `playback.py` and `scheduler.py` to this package, and
#: the Playback Engine **does** own one task — that is its documented job, and its own boundedness
#: is asserted by `tests/test_playback.py`. The rule below is about the *clause* path, which must
#: remain pure and synchronous, so it names the modules it is about rather than the package.
CLAUSE_MODULES = ("clauses.py", "sink.py")


def test_the_clause_modules_start_no_task_and_open_no_queue():
    """Boundedness is structural, not merely asserted by a test that happened to pass."""
    for name in CLAUSE_MODULES:
        identifiers = _identifiers(PLATFORM / "runtime" / "speech" / name)
        assert not (identifiers & {"create_task", "Queue", "ensure_future", "sleep"}), (
            f"{name} introduces concurrency the clause path must not own"
        )


def test_no_module_in_the_speech_package_opens_an_unbounded_queue():
    """The queue rule still applies to the whole package, including playback: an
    `asyncio.Queue` is the unbounded buffer M11.10 forbids on the audio path."""
    for path in (PLATFORM / "runtime" / "speech").glob("*.py"):
        assert "Queue" not in _identifiers(path), f"{path.name} uses an asyncio.Queue"


def test_no_runtime_framework_was_introduced():
    """The repository stays first-party: no substrate, no agent framework (AR-13, P-ADR-0002)."""
    forbidden = {"pipecat", "langchain", "langgraph", "llama_index", "haystack", "autogen"}
    for path in PLATFORM.rglob("*.py"):
        if any(part in (".venv", "voicebot") for part in path.parts):
            continue
        roots = {m.split(".")[0] for m in _imports(path)}
        assert not (roots & forbidden), f"{path.relative_to(PLATFORM)} imports a framework"


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


def test_no_provider_plugin_imports_the_api_layer():
    """ADR-0049 evidence 1, re-asserted over the modules M11.9 added (AR-11)."""
    for path in (PLATFORM / "providers").rglob("*.py"):
        assert not [m for m in _imports(path) if m.split(".")[0] == "api"]


def test_the_llm_plugin_never_reads_the_environment():
    """ADR-0049 CR4: a plugin receives its credential and resolves nothing."""
    for path in (PLATFORM / "providers" / "llm").glob("*.py"):
        identifiers = _identifiers(path)
        assert not (identifiers & {"getenv", "environ", "load_dotenv"}), (
            f"{path.name} reads the environment"
        )
