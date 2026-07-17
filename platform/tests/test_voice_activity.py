"""Voice Activity Framework tests (M3.2).

Covers the activity state model + hysteresis (VoiceActivityEngine), the pipeline
façade + provider execution + Turn Engine notification (VoiceActivityCoordinator),
the deterministic local provider, and registry registration/validation:
valid/invalid/duplicate transitions, false starts, resumed speech, provider
failure handling, and determinism.
"""

from __future__ import annotations

import pytest

from core.audio import AudioFrame
from core.providers.voice_activity import (
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)
from providers.voice_activity.local import LocalVoiceActivity
from runtime.errors import (
    CapabilityNotRegistered,
    InvalidVoiceActivityTransition,
    ProviderCapabilityMismatch,
    VoiceActivityProviderError,
)
from runtime.registry import CapabilityKind, ProviderRegistry
from runtime.turn import TurnCoordinator, TurnEngine, TurnPhase
from runtime.voice_activity import (
    VA_TRANSITIONS,
    VoiceActivityConfig,
    VoiceActivityCoordinator,
    VoiceActivityEngine,
)
from runtime.voice_activity import (
    VoiceActivityState as S,
)

SPEECH = VoiceActivitySample(speech=True)
SILENCE = VoiceActivitySample(speech=False)


def _engine(**kw) -> VoiceActivityEngine:
    return VoiceActivityEngine("s", **kw)


# ── VoiceActivityEngine: single-frame thresholds (defaults) ──────────────────
def test_initial_state_is_silence():
    assert _engine().state is S.SILENCE


def test_single_speech_frame_cascades_to_speaking():
    eng = _engine()  # start/end thresholds default to 1
    assert eng.observe(SPEECH) is S.SPEAKING
    assert eng.state is S.SPEAKING


def test_single_silence_frame_confirms_end():
    eng = _engine()
    eng.observe(SPEECH)  # → SPEAKING
    assert eng.observe(SILENCE) is S.SILENCE_CONFIRMED
    # Momentary — normalizes back to SILENCE on the next observe.
    assert eng.observe(SILENCE) is None
    assert eng.state is S.SILENCE


def test_full_cycle_visits_every_state():
    seen: list[S] = []
    eng = _engine(on_event=lambda e: seen.append(e.state))
    eng.observe(SPEECH)  # SILENCE → SPEECH_STARTING → SPEAKING
    eng.observe(SILENCE)  # SPEAKING → SPEECH_ENDING → SILENCE_CONFIRMED
    eng.observe(SILENCE)  # SILENCE_CONFIRMED → SILENCE
    assert seen == [
        S.SILENCE,  # initial record
        S.SPEECH_STARTING,
        S.SPEAKING,
        S.SPEECH_ENDING,
        S.SILENCE_CONFIRMED,
        S.SILENCE,
    ]


def test_sustained_speech_and_silence_report_no_change():
    eng = _engine()
    eng.observe(SPEECH)  # → SPEAKING
    assert eng.observe(SPEECH) is None  # sustained speech
    eng.observe(SILENCE)  # → SILENCE_CONFIRMED
    eng.observe(SILENCE)  # → SILENCE
    assert eng.observe(SILENCE) is None  # sustained silence


# ── VoiceActivityEngine: hysteresis (thresholds > 1) ─────────────────────────
def test_speech_start_hysteresis():
    eng = _engine(config=VoiceActivityConfig(speech_start_frames=2))
    assert eng.observe(SPEECH) is S.SPEECH_STARTING  # candidate, not yet confirmed
    assert eng.observe(SPEECH) is S.SPEAKING  # confirmed on the 2nd frame
    assert eng.state is S.SPEAKING


def test_false_start_returns_to_silence():
    eng = _engine(config=VoiceActivityConfig(speech_start_frames=3))
    eng.observe(SPEECH)  # SPEECH_STARTING
    assert eng.observe(SILENCE) is S.SILENCE  # silence before confirm → false start
    assert eng.state is S.SILENCE


def test_silence_end_hysteresis():
    eng = _engine(config=VoiceActivityConfig(silence_end_frames=2))
    eng.observe(SPEECH)  # → SPEAKING
    assert eng.observe(SILENCE) is S.SPEECH_ENDING  # not yet confirmed
    assert eng.observe(SILENCE) is S.SILENCE_CONFIRMED  # confirmed on 2nd silence


def test_speech_resumes_before_end_confirmed():
    eng = _engine(config=VoiceActivityConfig(silence_end_frames=3))
    eng.observe(SPEECH)  # SPEAKING
    eng.observe(SILENCE)  # SPEECH_ENDING
    assert eng.observe(SPEECH) is S.SPEAKING  # resumed → back to SPEAKING
    assert eng.state is S.SPEAKING


def test_thresholds_are_clamped_to_one():
    eng = _engine(config=VoiceActivityConfig(speech_start_frames=0, silence_end_frames=-5))
    assert eng.observe(SPEECH) is S.SPEAKING  # clamped to 1 → single-frame confirm


def test_two_utterances_cycle_cleanly():
    eng = _engine()
    for _ in range(2):
        assert eng.observe(SPEECH) is S.SPEAKING
        assert eng.observe(SILENCE) is S.SILENCE_CONFIRMED
        assert eng.observe(SILENCE) is None  # normalize back to SILENCE
    assert eng.state is S.SILENCE


def test_timing_records_state_entries():
    ticks = iter([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    eng = _engine(clock=lambda: next(ticks))
    eng.observe(SPEECH)  # STARTING @11, SPEAKING @12
    eng.observe(SILENCE)  # ENDING @13, CONFIRMED @14
    t = eng.context.timing
    assert t.speech_started_at == 11.0
    assert t.speaking_at == 12.0
    assert t.speech_ending_at == 13.0
    assert t.silence_confirmed_at == 14.0


def test_observer_errors_do_not_break_engine():
    def boom(_e):
        raise RuntimeError("observer boom")

    eng = _engine(on_event=boom)
    assert eng.observe(SPEECH) is S.SPEAKING  # must not raise


def test_transition_table_has_no_terminal_states():
    # The activity model is cyclic — every state has at least one outgoing edge.
    for state, targets in VA_TRANSITIONS.items():
        assert targets, f"{state} unexpectedly terminal"


def test_illegal_transition_raises():
    eng = _engine()
    # Directly forcing an illegal edge (SILENCE → SPEAKING skips SPEECH_STARTING).
    with pytest.raises(InvalidVoiceActivityTransition):
        eng._transition(S.SPEAKING, 1.0)


# ── LocalVoiceActivity provider ──────────────────────────────────────────────
async def test_local_provider_classifies_frames():
    p = LocalVoiceActivity()
    await p.start(session_id="s")
    assert p.detect(AudioFrame(data=b"hello"), session_id="s").speech is True
    assert p.detect(AudioFrame(data=b"   "), session_id="s").speech is False
    assert p.detect(AudioFrame(data=b""), session_id="s").speech is False
    await p.stop(session_id="s")


# ── Registry registration & validation ───────────────────────────────────────
def test_registry_registers_and_resolves_vad():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.VOICE_ACTIVITY, LocalVoiceActivity(), default=True)
    assert reg.has(CapabilityKind.VOICE_ACTIVITY)
    assert isinstance(reg.resolve(CapabilityKind.VOICE_ACTIVITY), LocalVoiceActivity)


def test_registry_absent_vad_reports_false():
    reg = ProviderRegistry()
    assert reg.has(CapabilityKind.VOICE_ACTIVITY) is False
    with pytest.raises(CapabilityNotRegistered):
        reg.resolve(CapabilityKind.VOICE_ACTIVITY)


def test_registry_rejects_wrong_capability():
    reg = ProviderRegistry()
    # A VAD provider registered under the STT capability fails the isinstance check.
    with pytest.raises(ProviderCapabilityMismatch):
        reg.register(CapabilityKind.STT, LocalVoiceActivity())


# ── VoiceActivityCoordinator ─────────────────────────────────────────────────
async def test_coordinator_notifies_turn_engine_on_speech():
    turn_engine = TurnEngine("s")
    coord = VoiceActivityCoordinator(
        provider=LocalVoiceActivity(),
        engine=VoiceActivityEngine("s"),
        session_id="s",
        turns=TurnCoordinator(turn_engine),
    )
    await coord.start()
    coord.observe(AudioFrame(data=b"hello"))  # speech → SPEAKING → notify turn
    assert turn_engine.current is not None
    assert turn_engine.current.phase is TurnPhase.USER_SPEAKING
    await coord.stop()


async def test_coordinator_silence_does_not_start_turn():
    turn_engine = TurnEngine("s")
    coord = VoiceActivityCoordinator(
        provider=LocalVoiceActivity(),
        engine=VoiceActivityEngine("s"),
        session_id="s",
        turns=TurnCoordinator(turn_engine),
    )
    await coord.start()
    coord.observe(AudioFrame(data=b"   "))  # silence → no turn
    assert turn_engine.current is None
    await coord.stop()


async def test_coordinator_start_failure_is_typed():
    class BadProvider(VoiceActivityProvider):
        name = "bad"
        capabilities = VoiceActivityCapabilities()

        async def start(self, *, session_id: str) -> None:
            raise RuntimeError("cannot start")

        async def stop(self, *, session_id: str) -> None:
            pass

        def detect(self, frame, *, session_id: str):
            return SPEECH

    coord = VoiceActivityCoordinator(
        provider=BadProvider(), engine=VoiceActivityEngine("s"), session_id="s"
    )
    with pytest.raises(VoiceActivityProviderError):
        await coord.start()


async def test_coordinator_detect_failure_is_typed():
    class BadDetect(VoiceActivityProvider):
        name = "bad"
        capabilities = VoiceActivityCapabilities()

        async def start(self, *, session_id: str) -> None:
            pass

        async def stop(self, *, session_id: str) -> None:
            pass

        def detect(self, frame, *, session_id: str):
            raise RuntimeError("detect boom")

    coord = VoiceActivityCoordinator(
        provider=BadDetect(), engine=VoiceActivityEngine("s"), session_id="s"
    )
    await coord.start()
    with pytest.raises(VoiceActivityProviderError):
        coord.observe(AudioFrame(data=b"x"))


async def test_coordinator_stop_failure_is_swallowed():
    class BadStop(VoiceActivityProvider):
        name = "bad"
        capabilities = VoiceActivityCapabilities()

        async def start(self, *, session_id: str) -> None:
            pass

        async def stop(self, *, session_id: str) -> None:
            raise RuntimeError("stop boom")

        def detect(self, frame, *, session_id: str):
            return SPEECH

    coord = VoiceActivityCoordinator(
        provider=BadStop(), engine=VoiceActivityEngine("s"), session_id="s"
    )
    await coord.stop()  # must not raise (best-effort teardown)


def test_deterministic_state_history():
    def run() -> list[S]:
        seen: list[S] = []
        eng = _engine(on_event=lambda e: seen.append(e.state))
        eng.observe(SPEECH)
        eng.observe(SILENCE)
        eng.observe(SILENCE)
        return seen

    assert run() == run()
