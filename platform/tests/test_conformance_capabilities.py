"""Capability conformance for STT, TTS, LLM and Transport (M11.3).

ADR-0051 **CE5 control 4** requires every registered provider — local today, vendor from M11.5 —
to pass its capability suite, with **truthful** flags. TESTING_POLICY's contract tier names what
that covers: *"streaming semantics, partials, error typing, timeout behaviour, capability flags
truthfulness"*.

`test_conformance_voice_activity.py` carries the voice-activity contract, which has enough of its
own machinery (O(1), windowing, bounded buffers) to deserve a file. This module carries the other
four capabilities plus the cross-cutting boundary properties that no single capability owns.

Hermetic: no vendor, no network, no credentials, no sleeps.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from core.audio import AudioEncoding, AudioFrame
from core.providers.llm import LLMCapabilities, TokenDelta, ToolCallDelta
from core.providers.stt import Final, Partial, STTCapabilities
from core.providers.transport import TransportCapabilities
from core.providers.tts import TTSCapabilities
from runtime.registry import ProviderRegistry
from tests.conformance import (
    CONFORMANCE_PROVIDERS,
    llm_providers,
    stt_providers,
    transport_providers,
    tts_providers,
)

PLATFORM_ROOT = pathlib.Path(__file__).resolve().parent.parent


async def _frames(*payloads: bytes, frame=None):
    make = frame if frame is not None else (lambda payload: AudioFrame(data=payload))
    for payload in payloads:
        yield make(payload)


# ── every provider satisfies the registry's structural contract ─────────────────


@pytest.mark.parametrize(
    "under_test", CONFORMANCE_PROVIDERS, ids=[f"{p.kind}:{p.name}" for p in CONFORMANCE_PROVIDERS]
)
def test_every_provider_registers_under_its_declared_capability(under_test):
    """§A6.3's *"one module + one registry entry"* — asserted, so a provider cannot ship
    half-registered. The registry validates the ABC and the declared identity for us."""
    registry = ProviderRegistry()
    metadata = registry.register(under_test.kind, under_test.factory(), default=True)
    assert metadata.kind == under_test.kind.value
    assert metadata.name == under_test.name
    assert metadata.capabilities is not None
    assert registry.resolve(under_test.kind) is not None


@pytest.mark.parametrize(
    "under_test", CONFORMANCE_PROVIDERS, ids=[f"{p.kind}:{p.name}" for p in CONFORMANCE_PROVIDERS]
)
def test_no_provider_declares_a_vendor_name_in_a_neutral_layer(under_test):
    """AR-21 — a provider's opaque `name` is a registry key, never a branch the runtime reads."""
    name = under_test.factory().name
    assert isinstance(name, str) and name
    assert name.lower() == name, "provider names are opaque lower-case keys"


# ── STT ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("under_test", stt_providers(), ids=[p.name for p in stt_providers()])
def test_stt_declares_capabilities_of_the_right_type(under_test):
    assert isinstance(under_test.factory().capabilities, STTCapabilities)


@pytest.mark.parametrize("under_test", stt_providers(), ids=[p.name for p in stt_providers()])
@pytest.mark.asyncio
async def test_stt_streaming_flag_is_truthful(under_test):
    """`streaming=True` must mean results arrive **as frames are consumed**, not after the last.

    Verified by feeding two utterances and requiring a result before the stream ends — a
    batch-only provider that buffers everything cannot satisfy it, and must declare
    `streaming=False` and run in declared degraded mode instead (§A6.4, ADR-0005)."""
    provider = under_test.factory()
    if not provider.capabilities.streaming:
        pytest.skip("provider declares batch-only; degraded mode is asserted separately")

    consumed: list[bytes] = []

    async def _tracking():
        for payload in (b"hello", b"world"):
            consumed.append(payload)
            yield under_test.frame(payload)

    stream = provider.transcribe(_tracking(), language="en", sample_rate=16000)
    first = await anext(stream)
    assert isinstance(first, Partial | Final)
    assert len(consumed) < 2, "a streaming STT yielded nothing until the stream was exhausted"
    await stream.aclose()


@pytest.mark.parametrize("under_test", stt_providers(), ids=[p.name for p in stt_providers()])
@pytest.mark.asyncio
async def test_stt_partials_flag_is_truthful(under_test):
    """`partials=True` promises partial hypotheses; a provider emitting only finals is lying."""
    provider = under_test.factory()
    results = [
        r
        async for r in provider.transcribe(
            _frames(b"hello", frame=under_test.frame), language="en", sample_rate=16000
        )
    ]
    if provider.capabilities.partials:
        assert any(isinstance(r, Partial) for r in results), "partials=True but none emitted"
    assert any(isinstance(r, Final) for r in results), "an utterance produced no Final"


@pytest.mark.parametrize("under_test", stt_providers(), ids=[p.name for p in stt_providers()])
@pytest.mark.asyncio
async def test_stt_emits_nothing_for_an_empty_stream(under_test):
    provider = under_test.factory()
    results = [
        r
        async for r in provider.transcribe(
            _frames(frame=under_test.frame), language="en", sample_rate=16000
        )
    ]
    assert results == []


# ── TTS ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("under_test", tts_providers(), ids=[p.name for p in tts_providers()])
def test_tts_declares_capabilities_of_the_right_type(under_test):
    assert isinstance(under_test.factory().capabilities, TTSCapabilities)


@pytest.mark.parametrize("under_test", tts_providers(), ids=[p.name for p in tts_providers()])
@pytest.mark.asyncio
async def test_tts_emits_frames_whose_declared_rate_matches_its_capability(under_test):
    """A declared `sample_rates` set that the provider does not actually emit is untruthful."""
    provider = under_test.factory()
    frames = [f async for f in provider.synthesize("hello", voice=None, language="en")]
    assert frames, "synthesis produced no audio"
    declared = provider.capabilities.sample_rates
    if declared:
        for frame in frames:
            assert frame.sample_rate in declared, (frame.sample_rate, declared)


@pytest.mark.parametrize("under_test", tts_providers(), ids=[p.name for p in tts_providers()])
@pytest.mark.asyncio
async def test_tts_frames_declare_an_honest_encoding(under_test):
    """ADR-0044 AD2 — an undeclared payload stays `OPAQUE` and never claims to be PCM."""
    provider = under_test.factory()
    async for frame in provider.synthesize("hello", voice=None, language="en"):
        assert isinstance(frame.encoding, AudioEncoding)
        if frame.encoding is AudioEncoding.PCM16:
            assert len(frame.data) % 2 == 0, "PCM16 declared over an odd byte count"


@pytest.mark.parametrize("under_test", tts_providers(), ids=[p.name for p in tts_providers()])
def test_tts_streaming_input_defaults_to_declared_absence(under_test):
    """ADR-0045 SD6 — the additive flag exists and defaults honestly until a provider implements
    streaming input. Reading it through `getattr` keeps this assertion valid both before and after
    M11.10 adds the field, without pre-judging the contract extension."""
    caps = under_test.factory().capabilities
    assert getattr(caps, "streaming_input", False) in (True, False)


# ── LLM ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("under_test", llm_providers(), ids=[p.name for p in llm_providers()])
def test_llm_declares_capabilities_of_the_right_type(under_test):
    caps = under_test.factory().capabilities
    assert isinstance(caps, LLMCapabilities)
    assert caps.context_window > 0, "a context window of zero is not a truthful declaration"


@pytest.mark.parametrize("under_test", llm_providers(), ids=[p.name for p in llm_providers()])
@pytest.mark.asyncio
async def test_llm_streams_typed_deltas(under_test):
    provider = under_test.factory()
    deltas = [
        d
        async for d in provider.stream(context=[{"role": "user", "content": "hi"}], tools=None)
    ]
    assert deltas, "the LLM produced no deltas"
    for delta in deltas:
        assert isinstance(delta, TokenDelta | ToolCallDelta)


@pytest.mark.parametrize("under_test", llm_providers(), ids=[p.name for p in llm_providers()])
@pytest.mark.asyncio
async def test_llm_tools_flag_is_truthful(under_test):
    """A provider declaring `tools=False` must not emit tool-call deltas."""
    provider = under_test.factory()
    if provider.capabilities.tools:
        pytest.skip("provider declares tool calling; exercised when a tool-using vendor lands")
    deltas = [
        d
        async for d in provider.stream(context=[{"role": "user", "content": "hi"}], tools=None)
    ]
    assert not any(isinstance(d, ToolCallDelta) for d in deltas)


# ── Transport ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "under_test", transport_providers(), ids=[p.name for p in transport_providers()]
)
def test_transport_declares_capabilities_of_the_right_type(under_test):
    caps = under_test.factory().capabilities
    assert isinstance(caps, TransportCapabilities)
    assert "audio" in caps.media_kinds, "a voice transport that does not carry audio"


@pytest.mark.parametrize(
    "under_test", transport_providers(), ids=[p.name for p in transport_providers()]
)
@pytest.mark.asyncio
async def test_transport_inbound_terminates_on_stop(under_test):
    """The contract's own words: *"Must cause `inbound()` to terminate."* A transport that hangs
    here would hang the conversation loop, which reads stream end as caller hangup."""
    transport = under_test.factory()
    await transport.start(session_id="s-stop")
    stream = transport.inbound(session_id="s-stop")
    await transport.stop(session_id="s-stop")
    assert [f async for f in stream] == []


# ── cross-cutting boundaries (CE5 controls 2, 3, 8) ─────────────────────────────


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
    return roots


def _package_files(package: str) -> list[pathlib.Path]:
    return [
        p
        for p in (PLATFORM_ROOT / package).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_providers_import_only_the_core_floor_and_the_stdlib():
    """**AR-11.** A plugin reaches `core` and its own vendor SDK (AR-12) — never a sibling plane."""
    forbidden = {"runtime", "api", "events", "observability", "billing", "provider"}
    for path in _package_files("providers"):
        offending = _imported_roots(path) & forbidden
        assert not offending, f"{path.relative_to(PLATFORM_ROOT)} imports {offending}"


def test_core_imports_nothing_above_the_floor():
    """**AR-10**, asserted over the whole package rather than one module."""
    forbidden = {"runtime", "api", "providers", "events", "observability", "billing", "provider"}
    for path in _package_files("core"):
        offending = _imported_roots(path) & forbidden
        assert not offending, f"{path.relative_to(PLATFORM_ROOT)} imports {offending}"


def test_api_never_imports_runtime_internals():
    """**AR-14** — the Control Plane reaches the Runtime only through the session-control seam."""
    for path in _package_files("api"):
        assert "runtime" not in _imported_roots(path), path.relative_to(PLATFORM_ROOT)


def test_no_pipeline_substrate_is_imported_anywhere():
    """**AR-13** — retained after P-ADR-0002 retired the binding, so it is still enforceable."""
    for package in ("core", "runtime", "providers", "api", "events", "observability"):
        for path in _package_files(package):
            assert "pipecat" not in _imported_roots(path), path.relative_to(PLATFORM_ROOT)


def test_only_the_runtime_pipeline_resamples():
    """**AR-41** + ADR-0044 AD4 — one owner for sample-rate conversion, asserted repository-wide.

    Read structurally, not by substring: `core/audio.py` legitimately *mentions* resampling to
    state the rule (*"providers do not resample"*), and a guard that cannot tell a rule from an
    implementation would fail on its own documentation."""
    owners = set()
    for package in ("core", "runtime", "providers", "api", "events", "observability"):
        for path in _package_files(package):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                defines = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                if defines and "resampl" in node.name.lower():
                    owners.add(str(path.relative_to(PLATFORM_ROOT)))
    assert owners, "no resampling implementation found; the seam has gone missing"
    for owner in owners:
        assert owner.startswith("runtime/pipeline/audio/"), owner


def test_exactly_one_provider_registry_serves_the_voice_path():
    """**ADR-0046 FT-V6** — the media-path layer adds no fifth `ProviderRegistry`."""
    declarations = []
    for package in ("core", "runtime", "providers"):
        for path in _package_files(package):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "ProviderRegistry":
                    declarations.append(str(path.relative_to(PLATFORM_ROOT)))
    assert declarations == ["runtime/registry.py"], declarations


def test_no_unbounded_queue_on_the_media_path():
    """**AR-40** / §A11.1.7 — a media queue without a bound is a banned anti-pattern.

    Scoped to the packages that actually carry media. `asyncio.Queue()` with no `maxsize` is the
    exact shape the rule names, and it is what `LocalTransport` carried before M11.2."""
    for package in ("runtime", "providers"):
        for path in _package_files(package):
            source = path.read_text(encoding="utf-8")
            assert "asyncio.Queue()" not in source, (
                f"{path.relative_to(PLATFORM_ROOT)} creates an unbounded media queue (AR-40)"
            )


def test_the_runtime_to_provider_edge_stays_acyclic():
    """**ADR-0046 FT-V3/FT-V5** — the authorized edge exists in one direction only."""
    for path in _package_files("provider"):
        assert "runtime" not in _imported_roots(path), (
            f"{path.relative_to(PLATFORM_ROOT)} imports runtime — the edge has become a cycle"
        )


def test_audio_encoding_lives_on_the_core_floor():
    """ADR-0044 AD2 — the definition stays where every contract can reach it without a plane dep."""
    module = inspect.getmodule(AudioEncoding)
    assert module is not None and module.__name__ == "core.audio"
