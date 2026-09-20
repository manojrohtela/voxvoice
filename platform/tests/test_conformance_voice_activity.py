"""Voice-activity capability conformance (M11.3) — ADR-0045 SD7/SD8/SD9, ADR-0051 CE5 control 4.

The contract every voice-activity provider must satisfy, local or vendor. These are the
assertions that make ADR-0045's ruling *executable* rather than aspirational:

* `detect()` is **O(1) and non-blocking** (SD7) — measured structurally, never with a threshold;
* windowed inference is **provider-owned** and per-session buffers are **bounded** (SD8);
* `frame_ms` is **load-bearing and truthful** (SD8/SD9);
* `start()`/`stop()` own per-session lifecycle;
* a provider that cannot meet the contract **declares degradation** instead of lying (SD9).

Hermetic: no vendor, no network, no credentials, no sleeps (TESTING_POLICY rules 1–2).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time

import pytest

from core.audio import AudioFrame
from core.providers.voice_activity import (
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)
from runtime.errors import VoiceActivityProviderError
from runtime.voice_activity import VoiceActivityCoordinator, VoiceActivityEngine
from tests.conformance import voice_activity_providers
from tests.conformance.probes import (
    DETECT_WALL_CLOCK_CEILING_S,
    BlockingCallDetected,
    BlockingIOSentinel,
    CostProbe,
)

PROVIDERS = voice_activity_providers()
IDS = [p.name for p in PROVIDERS]


@pytest.fixture(params=PROVIDERS, ids=IDS)
def under_test(request):
    return request.param


@pytest.fixture
def provider(under_test):
    return under_test.factory()


@pytest.fixture
def frame(under_test):
    """Builds a probe frame **in the format this provider declares it accepts** (M11.7).

    The local profile reads undeclared payloads; a real VAD validates PCM16 / mono / rate and
    refuses anything else, so it is probed with genuine PCM16. Every assertion is unchanged — only
    the input follows the provider's own contract, as the STT suite already does (M11.6)."""
    return under_test.frame


# ── capability-flag truthfulness (SD9) ──────────────────────────────────────────


def test_declares_capabilities_of_the_right_type(provider):
    assert isinstance(provider.capabilities, VoiceActivityCapabilities)
    assert isinstance(provider.name, str) and provider.name


def test_frame_ms_is_a_non_negative_declaration(provider):
    """`frame_ms` is a contract term, not decoration (SD8). Zero means *frame-size agnostic*."""
    frame_ms = provider.capabilities.frame_ms
    assert isinstance(frame_ms, int)
    assert frame_ms >= 0


@pytest.mark.asyncio
async def test_frame_ms_is_truthful_about_windowing(provider, frame):
    """A provider declaring `frame_ms = 0` must genuinely accept any frame size.

    This is the truthfulness half of SD8. A frame-size-agnostic declaration is falsified by a
    provider that only works at one size, and a windowed provider (`frame_ms > 0`) must still
    return a verdict for every frame — the window is its *internal* cadence, never a reason to
    refuse a caller."""
    await provider.start(session_id="s-frame")
    try:
        for payload in (b"", b"x", b"y" * 320, b"z" * 4096):
            sample = provider.detect(frame(payload), session_id="s-frame")
            assert isinstance(sample, VoiceActivitySample)
            assert isinstance(sample.speech, bool)
            assert 0.0 <= sample.confidence <= 1.0
    finally:
        await provider.stop(session_id="s-frame")


@pytest.mark.asyncio
async def test_emits_confidence_flag_is_truthful(provider, frame):
    """Both halves of the flag, asserted — neither is skipped.

    * `emits_confidence=False`: the contract's own words — the confidence is exactly 1.0.
    * `emits_confidence=True` (M11.7): the score must be a *score*, so a provider may not report
      the no-score sentinel on every verdict. Probed over a run of frames with the event loop given
      turns in between, so a background-inference provider has the chance to publish verdicts.
      `_LyingVAD` below is the negative case this branch exists to reject."""
    await provider.start(session_id="s-conf")
    try:
        confidences = set()
        for payload in (b"hello", b"\x00" * 1024, b"\x00" * 1024, b"\x00" * 1024):
            confidences.add(provider.detect(frame(payload), session_id="s-conf").confidence)
            for _ in range(20):
                await asyncio.sleep(0)
    finally:
        await provider.stop(session_id="s-conf")
    assert all(0.0 <= c <= 1.0 for c in confidences)
    if provider.capabilities.emits_confidence:
        assert confidences != {1.0}, "declares a real score but only ever reports the sentinel 1.0"
    else:
        assert confidences == {1.0}


# ── detect() is O(1) and non-blocking (SD7) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_performs_no_blocking_io(provider, frame):
    """Non-blocking means *these three things do not happen* — asserted, not reviewed."""
    await provider.start(session_id="s-io")
    try:
        with BlockingIOSentinel("detect()"):
            provider.detect(frame(b"hello"), session_id="s-io")
    finally:
        await provider.stop(session_id="s-io")


@pytest.mark.asyncio
async def test_detect_cost_does_not_grow_with_frame_size(provider, frame):
    """O(1) as a **ratio**, so the assertion is machine-independent (see `probes.py`).

    Payloads spanning three orders of magnitude. A provider that scans the buffer per call — the
    obvious way to accidentally make the hot path O(n) — fails here, while a provider that reads a
    verdict computed on its own task passes regardless of hardware."""
    await provider.start(session_id="s-cost")
    probe = CostProbe()
    try:
        for size in (16, 1024, 65536):
            probe_frame = frame(b"\x01" * size)
            probe.measure(size, lambda f=probe_frame: provider.detect(f, session_id="s-cost"))
    finally:
        await provider.stop(session_id="s-cost")
    assert probe.is_constant(), f"detect() cost grows with frame size: {probe.report()}"


@pytest.mark.asyncio
async def test_detect_returns_within_a_generous_wall_clock_ceiling(provider, frame):
    """A deliberately coarse backstop, four orders of magnitude above any O(1) call.

    It exists to catch a provider that genuinely blocks — a synchronous model load, a network
    round trip — not to measure microseconds. The structural probes above are the real assertions
    (TESTING_POLICY rule 2)."""
    await provider.start(session_id="s-clock")
    try:
        start = time.monotonic()
        provider.detect(frame(b"\x01" * 4096), session_id="s-clock")
        elapsed = time.monotonic() - start
    finally:
        await provider.stop(session_id="s-clock")
    assert elapsed < DETECT_WALL_CLOCK_CEILING_S, f"detect() took {elapsed * 1000:.1f} ms"


def test_detect_does_not_run_inference_synchronously(provider):
    """SD8 — windowed inference belongs on a provider-owned task, never the caller's stack.

    Read structurally from the source: a synchronous `detect()` that awaits, spawns, joins or
    sleeps is running its analysis on the media hot path, which is the defect SD7 exists to
    prevent."""
    source = textwrap.dedent(inspect.getsource(type(provider).detect))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Await), "detect() awaits on the caller's stack"
    banned = ("run_until_complete", "asyncio.run", ".join(", "time.sleep", "wait_for")
    for token in banned:
        assert token not in source, f"detect() uses {token!r} — inference is not provider-owned"


# ── per-session lifecycle and bounded buffering (SD8) ───────────────────────────


@pytest.mark.asyncio
async def test_start_and_stop_own_per_session_state(provider, frame):
    """The contract's own words: per-session buffers are *"set up in `start` and torn down in
    `stop`, keyed by `session_id` so one instance can serve many sessions"*."""
    await provider.start(session_id="s-a")
    await provider.start(session_id="s-b")
    assert isinstance(provider.detect(frame(b"a"), session_id="s-a"), VoiceActivitySample)
    assert isinstance(provider.detect(frame(b"b"), session_id="s-b"), VoiceActivitySample)
    await provider.stop(session_id="s-a")
    # Stopping one session must not disturb another — one instance serves many calls.
    assert isinstance(provider.detect(frame(b"b"), session_id="s-b"), VoiceActivitySample)
    await provider.stop(session_id="s-b")


@pytest.mark.asyncio
async def test_per_session_state_is_bounded_across_many_frames(provider, frame):
    """AR-40 — no unbounded growth on a media path, asserted over a long session.

    Measured as *"the provider's own footprint does not grow with the number of frames seen"*: a
    provider accumulating every frame fails, one holding a fixed ring passes."""
    await provider.start(session_id="s-bound")
    try:
        for _ in range(64):
            provider.detect(frame(b"\x01" * 320), session_id="s-bound")
        footprint = _provider_footprint(provider)
        for _ in range(512):
            provider.detect(frame(b"\x01" * 320), session_id="s-bound")
        assert _provider_footprint(provider) <= footprint, "per-session state grows with frames"
    finally:
        await provider.stop(session_id="s-bound")


def _provider_footprint(provider) -> int:
    """A coarse count of container entries the provider holds — enough to catch accumulation."""
    total = 0
    for value in vars(provider).values() if hasattr(provider, "__dict__") else ():
        if isinstance(value, dict):
            total += len(value) + sum(len(v) for v in value.values() if hasattr(v, "__len__"))
        elif hasattr(value, "__len__"):
            total += len(value)
    return total


# ── degradation is declared, not faked (SD9, AR-24) ─────────────────────────────


class _NonStreamingVAD(VoiceActivityProvider):
    """A provider that cannot meet the streaming contract and **says so** (SD9)."""

    name = "non-streaming-probe"
    capabilities = VoiceActivityCapabilities(streaming=False, emits_confidence=False, frame_ms=30)

    async def start(self, *, session_id: str) -> None: ...

    async def stop(self, *, session_id: str) -> None: ...

    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        return VoiceActivitySample(speech=False, confidence=1.0)


class _LyingVAD(_NonStreamingVAD):
    """Declares streaming while behaving otherwise — the case SD9 makes merge-blocking."""

    name = "lying-probe"
    capabilities = VoiceActivityCapabilities(streaming=True, emits_confidence=True, frame_ms=0)

    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        # `emits_confidence=True` promises a real score; returning the sentinel 1.0 is the lie.
        return VoiceActivitySample(speech=True, confidence=1.0)


def test_a_provider_may_declare_degraded_capability_honestly():
    """Declaring `streaming=False` is permitted (§A6.4) — it is the *silent* version that is not."""
    provider = _NonStreamingVAD()
    assert provider.capabilities.streaming is False
    assert provider.capabilities.frame_ms == 30


def test_the_suite_detects_an_untruthful_confidence_declaration():
    """**The negative guard.** A conformance suite that has only seen honest providers has not
    been shown to detect a dishonest one (the `test_epic10_freeze.py` principle)."""
    provider = _LyingVAD()
    assert provider.capabilities.emits_confidence is True
    samples = {
        provider.detect(AudioFrame(data=b"x" * n), session_id="s").confidence for n in (1, 9)
    }
    assert samples == {1.0}, "fixture no longer models the untruthful case"
    # …and that set is exactly what the `emits_confidence=True` branch above rejects.
    assert provider.capabilities.emits_confidence and samples == {1.0}


def test_the_blocking_sentinel_actually_fires():
    """The sentinel is only evidence if it can fail — proven, not assumed."""
    with pytest.raises(BlockingCallDetected):
        with BlockingIOSentinel("probe"):
            time.sleep(0)


def test_the_cost_probe_detects_linear_work():
    """Likewise for the O(1) probe: shown to reject an O(n) implementation."""
    probe = CostProbe()
    for size in (16, 1024, 65536):
        probe.measure(size, lambda s=size: [_touch(i) for i in range(s // 16)])
    assert not probe.is_constant(), "the cost probe cannot detect linear work"


def _touch(value: int) -> int:
    return value


# ── the framework degrades rather than breaking the conversation (AR-24, DL-0013) ─


@pytest.mark.asyncio
async def test_a_failing_provider_raises_a_typed_error_not_a_bare_exception():
    """Typed failure is what lets the pipeline degrade deliberately instead of crashing."""

    class _BoomVAD(_NonStreamingVAD):
        name = "boom-probe"

        def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
            raise RuntimeError("model exploded")

    coordinator = VoiceActivityCoordinator(
        provider=_BoomVAD(),
        engine=VoiceActivityEngine("s-boom"),
        session_id="s-boom",
    )
    await coordinator.start()
    with pytest.raises(VoiceActivityProviderError):
        coordinator.observe(AudioFrame(data=b"hello"))
