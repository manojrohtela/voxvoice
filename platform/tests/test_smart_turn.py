"""Smart Turn Intelligence Engine tests (M3.5).

Covers the deterministic arbitration of endpoint/interrupt/voice signals, adaptive
policies (silence, grace, dynamic interruption threshold, confidence weighting,
timing smoothing), decision-conflict resolution, the semantic extension interface
(a registered evaluator blends in with no engine change; a misbehaving one degrades
safely), invalid-policy rejection, and deterministic replay.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.providers.semantic_turn import (
    SemanticCapabilities,
    SemanticHint,
    SemanticTurnEvaluator,
)
from runtime.endpoint import EndpointDecision
from runtime.errors import InvalidSmartTurnPolicy, RuntimeConflict
from runtime.interrupt import InterruptDecision
from runtime.smart_turn import (
    SmartTurnCoordinator,
    SmartTurnDecision,
    SmartTurnEngine,
    SmartTurnInput,
    SmartTurnPolicy,
)

ST = SmartTurnDecision
EP = EndpointDecision
IN = InterruptDecision


def _dec(eng, **kw):
    return eng.decide(SmartTurnInput(**kw))


# ── default (identity) policy reproduces pre-M3.5 behaviour ──────────────────
def test_default_endpoint_confirmed_finishes():
    eng = SmartTurnEngine("s")
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.FINISH_TURN
    assert eng.decide(SmartTurnInput(endpoint=EP.TIMEOUT_ENDPOINT)) is ST.FINISH_TURN


def test_default_continue_and_potential_and_cancel():
    eng = SmartTurnEngine("s")
    assert eng.decide(SmartTurnInput(endpoint=EP.CONTINUE_LISTENING)) is ST.CONTINUE_LISTENING
    assert eng.decide(SmartTurnInput(endpoint=EP.POTENTIAL_ENDPOINT)) is ST.WAIT
    assert eng.decide(SmartTurnInput(endpoint=EP.CANCEL_ENDPOINT)) is ST.CANCEL_ENDPOINT


def test_default_interrupt_confirmed_and_cancelled():
    eng = SmartTurnEngine("s")
    assert eng.decide(SmartTurnInput(interrupt=IN.INTERRUPT_CONFIRMED)) is ST.CONFIRM_INTERRUPT
    assert eng.decide(SmartTurnInput(interrupt=IN.INTERRUPT_CANCELLED)) is ST.RESUME_ASSISTANT


# ── decision-conflict resolution: interrupt over endpoint ────────────────────
def test_interrupt_takes_precedence_over_endpoint():
    eng = SmartTurnEngine("s")
    decision = eng.decide(
        SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED, interrupt=IN.INTERRUPT_CONFIRMED)
    )
    assert decision is ST.CONFIRM_INTERRUPT  # barge-in wins the conflict


# ── adaptive silence → DELAY_RESPONSE + exposed hold ─────────────────────────
def test_adaptive_silence_delays_response():
    eng = SmartTurnEngine("s", policy=SmartTurnPolicy(adaptive_silence_frames=4))
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.DELAY_RESPONSE
    assert eng.pending_delay_frames == 4


def test_adaptive_silence_scale_rounds():
    eng = SmartTurnEngine(
        "s", policy=SmartTurnPolicy(adaptive_silence_frames=3, adaptive_silence_scale=2.0)
    )
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.DELAY_RESPONSE
    assert eng.pending_delay_frames == 6


# ── adaptive grace: an early endpoint waits before finishing ─────────────────
def test_adaptive_grace_waits_then_finishes():
    eng = SmartTurnEngine("s", policy=SmartTurnPolicy(adaptive_grace_frames=3))
    # endpoint reached "early" (few elapsed frames) → WAIT
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED, elapsed_frames=1)) is ST.WAIT
    # enough elapsed → finish
    assert _dec(eng, endpoint=EP.ENDPOINT_CONFIRMED, elapsed_frames=5) is ST.FINISH_TURN


# ── dynamic interruption threshold + confidence weighting ────────────────────
def test_dynamic_interruption_threshold_ignores_low_confidence():
    eng = SmartTurnEngine("s", policy=SmartTurnPolicy(dynamic_interruption_threshold=0.8))
    assert _dec(eng, interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.3) is ST.IGNORE_INTERRUPT
    assert _dec(eng, interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.9) is ST.CONFIRM_INTERRUPT


def test_confidence_weight_scales_toward_threshold():
    eng = SmartTurnEngine(
        "s",
        policy=SmartTurnPolicy(
            dynamic_interruption_threshold=1.0, speech_confidence_weight=2.0
        ),
    )
    # 0.6 * 2.0 = 1.2 >= 1.0 → confirm
    assert _dec(eng, interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.6) is ST.CONFIRM_INTERRUPT


# ── timing smoothing: a changed decision needs N consistent frames ───────────
def test_timing_smoothing_requires_repeats():
    eng = SmartTurnEngine("s", policy=SmartTurnPolicy(timing_smoothing_frames=3))
    # First establish a stable CONTINUE_LISTENING baseline.
    assert _dec(eng, endpoint=EP.CONTINUE_LISTENING) is ST.WAIT  # first-ever (from None)
    eng.decide(SmartTurnInput(endpoint=EP.CONTINUE_LISTENING))
    eng.decide(SmartTurnInput(endpoint=EP.CONTINUE_LISTENING))  # now committed CONTINUE_LISTENING
    assert eng.context.last_decision is ST.CONTINUE_LISTENING
    # A new FINISH raw decision must repeat 3x before it commits; WAIT bridges.
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.WAIT
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.WAIT
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.FINISH_TURN


# ── invalid policy is rejected at construction (no silent config) ────────────
def test_invalid_policy_raises():
    with pytest.raises(InvalidSmartTurnPolicy):
        SmartTurnPolicy(adaptive_silence_frames=-1)
    with pytest.raises(InvalidSmartTurnPolicy):
        SmartTurnPolicy(speech_confidence_weight=-2.0)


def test_smart_turn_error_roots_in_runtime_conflict():
    assert issubclass(InvalidSmartTurnPolicy, RuntimeConflict)


# ── semantic extension: a registered evaluator blends in, no engine change ───
class _EOTBoost(SemanticTurnEvaluator):
    """A stand-in semantic module: pushes interrupt intent up (never calls an LLM
    here, but this is exactly where a model/service would plug in)."""

    name = "eot-boost"
    capabilities = SemanticCapabilities(interrupt_intent=True)

    def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
        return SemanticHint(interrupt_intent=0.9)


def test_semantic_hint_influences_interrupt_decision():
    # Threshold 0.8, raw confidence 0.2 → would IGNORE; the semantic hint (0.9) tips
    # it over the line → CONFIRM. No engine code changed — only an injected evaluator.
    eng = SmartTurnEngine(
        "s", policy=SmartTurnPolicy(dynamic_interruption_threshold=0.8), semantic=_EOTBoost()
    )
    assert _dec(eng, interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.2) is ST.CONFIRM_INTERRUPT


def test_semantic_weight_zero_disables_hint():
    eng = SmartTurnEngine(
        "s",
        policy=SmartTurnPolicy(dynamic_interruption_threshold=0.8, semantic_weight=0.0),
        semantic=_EOTBoost(),
    )
    assert _dec(eng, interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.2) is ST.IGNORE_INTERRUPT


class _BrokenEvaluator(SemanticTurnEvaluator):
    name = "broken"
    capabilities = SemanticCapabilities()

    def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
        raise RuntimeError("semantic model crashed")


def test_broken_semantic_evaluator_degrades_to_identity():
    eng = SmartTurnEngine("s", semantic=_BrokenEvaluator())
    # Must not raise; falls back to the deterministic decision.
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.FINISH_TURN


def test_semantic_delay_scale_stretches_hold():
    class _Stretch(SemanticTurnEvaluator):
        name = "stretch"
        capabilities = SemanticCapabilities()

        def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
            return SemanticHint(delay_scale=2.0)

    eng = SmartTurnEngine(
        "s", policy=SmartTurnPolicy(adaptive_silence_frames=2), semantic=_Stretch()
    )
    assert eng.decide(SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED)) is ST.DELAY_RESPONSE
    assert eng.pending_delay_frames == 4  # 2 * 2.0


# ── determinism ──────────────────────────────────────────────────────────────
def test_deterministic_replay():
    def run():
        eng = SmartTurnEngine("s", policy=SmartTurnPolicy(dynamic_interruption_threshold=0.5))
        seq = [
            SmartTurnInput(endpoint=EP.CONTINUE_LISTENING),
            SmartTurnInput(endpoint=EP.POTENTIAL_ENDPOINT),
            SmartTurnInput(interrupt=IN.INTERRUPT_CONFIRMED, confidence=0.7),
            SmartTurnInput(endpoint=EP.ENDPOINT_CONFIRMED),
        ]
        return [eng.decide(i) for i in seq]

    assert run() == run()


# ── coordinator façade builds inputs + returns decisions ─────────────────────
def test_coordinator_arbitrates_and_exposes_delay():
    coord = SmartTurnCoordinator(
        engine=SmartTurnEngine("s", policy=SmartTurnPolicy(adaptive_silence_frames=5)),
        session_id="s",
    )
    assert coord.on_endpoint(EP.ENDPOINT_CONFIRMED) is ST.DELAY_RESPONSE
    assert coord.pending_delay_frames == 5
    assert coord.on_interrupt(IN.INTERRUPT_CONFIRMED) is ST.CONFIRM_INTERRUPT
