"""Loop Engine Bundle 2 tests (M6.6B-2) — pipeline execution, stop
conditions, replay validation, diagnostics/statistics/observability, and
dependency boundaries.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from collections.abc import Iterator

from core.ports.reasoning import Decision, DecisionKind, ReasoningRequest
from loop.core.cancellation import LoopCancellationToken
from loop.core.config import LoopConfiguration
from loop.core.domain import LoopDefinition, LoopProposal, LoopStoppingPolicy, LoopValidation
from loop.core.pipeline import run_loop
from loop.core.stopping import (
    CONFIDENCE_THRESHOLD,
    EXTERNAL_STOP,
    MAX_ITERATIONS,
    VALIDATION_SUCCESS,
)
from loop.core.validator import LoopValidator
from loop.diagnostics.diagnostics import build_diagnostics
from loop.diagnostics.observability import trace_for
from loop.diagnostics.statistics import compute_statistics
from loop.replay.replay import loop_replay_record_for
from loop.replay.replay_engine import LoopReplayEngine


# ── Fakes ─────────────────────────────────────────────────────────────────
class _FakeReasoningPort:
    """Yields a distinct, incrementing content string per call, so
    convergence never accidentally fires unless a test wants it to."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ReasoningRequest] = []

    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        self.requests.append(request)
        self.calls += 1
        yield Decision(kind=DecisionKind.TOKEN, payload={"text": "thinking"})
        yield Decision(kind=DecisionKind.FINAL, payload={"content": f"draft-{self.calls}"})


class _ConstantReasoningPort:
    """Always yields the same content — used to exercise convergence."""

    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        yield Decision(kind=DecisionKind.FINAL, payload={"content": "same-draft"})


class _NoFinalReasoningPort:
    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        yield Decision(kind=DecisionKind.TOKEN, payload={"text": "thinking"})


class _SequenceValidator(LoopValidator):
    """Returns `passed` values from a fixed sequence, one per call; the last
    value repeats once the sequence is exhausted."""

    def __init__(self, passes: list[bool], *, confidence: float | None = None) -> None:
        self._passes = passes
        self._confidence = confidence
        self.calls = 0

    def validate(self, proposal: LoopProposal) -> LoopValidation:
        i = min(self.calls, len(self._passes) - 1)
        passed = self._passes[i]
        self.calls += 1
        return LoopValidation(
            validation_id=f"v{self.calls}", proposal_id=proposal.proposal_id,
            passed=passed, confidence=self._confidence,
        )


def _definition(**overrides: object) -> LoopDefinition:
    policy = LoopStoppingPolicy(**overrides) if overrides else LoopStoppingPolicy()
    return LoopDefinition(
        loop_id="l1", task_description="improve the draft", stopping_policy=policy,
    )


def _clock() -> Iterator[float]:
    t = 0.0
    while True:
        t += 1.0
        yield t


def _ticking_clock():
    gen = _clock()
    return lambda: next(gen)


# ── Pipeline execution ───────────────────────────────────────────────────────
def test_pipeline_stops_immediately_when_initial_proposal_validates():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    assert result.iteration_count == 1
    assert result.stop_reason == VALIDATION_SUCCESS
    assert result.converged is True
    assert result.iterations[0].critique is None
    assert result.iterations[0].repair is None
    assert reasoning.calls == 1  # only the initial proposal call -- no critique/repair


def test_pipeline_runs_critique_and_repair_when_initial_validation_fails_then_passes():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False, True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    assert result.iteration_count == 1
    assert result.stop_reason == VALIDATION_SUCCESS
    it = result.iterations[0]
    assert it.critique is not None
    assert it.repair is not None
    assert reasoning.calls == 3  # propose + critique + repair


def test_pipeline_reaches_max_iterations_when_validator_never_passes():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False])
    result = run_loop(
        _definition(max_iterations=2, stop_on_convergence=False),
        reasoning_port=reasoning, validator=validator, session_id="s1", clock=_ticking_clock(),
    )
    assert result.stop_reason == MAX_ITERATIONS
    assert result.converged is False
    assert result.iteration_count == 2
    # Budget is exhausted at the *start* of iteration 2, before another
    # critique/repair pass is spent.
    assert result.iterations[-1].critique is None
    assert result.iterations[-1].repair is None


def test_pipeline_stops_on_confidence_threshold():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False], confidence=0.95)
    result = run_loop(
        _definition(stop_on_validation_success=False, confidence_threshold=0.9),
        reasoning_port=reasoning, validator=validator, session_id="s1", clock=_ticking_clock(),
    )
    assert result.stop_reason == CONFIDENCE_THRESHOLD
    assert result.converged is True


def test_pipeline_stops_on_convergence_when_repair_reproduces_identical_content():
    reasoning = _ConstantReasoningPort()
    validator = _SequenceValidator([False])
    result = run_loop(
        _definition(stop_on_validation_success=False, max_iterations=5),
        reasoning_port=reasoning, validator=validator, session_id="s1", clock=_ticking_clock(),
    )
    # The repaired proposal's content is identical to the proposal that
    # started the iteration (constant reasoning output), so convergence
    # fires on iteration 1, immediately after the one repair pass.
    assert result.stop_reason == "convergence"
    assert result.converged is True
    assert result.iteration_count == 1
    assert result.iterations[0].repair is not None


def test_pipeline_stops_on_external_cancellation():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False])
    token = LoopCancellationToken("t1")
    token.cancel(reason="user requested")
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(), cancellation_token=token,
    )
    assert result.stop_reason == EXTERNAL_STOP
    assert result.converged is False
    assert result.iteration_count == 1
    assert reasoning.calls == 1


def test_pipeline_tolerates_no_final_decision():
    reasoning = _NoFinalReasoningPort()
    validator = _SequenceValidator([True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    assert result.iteration_count == 1
    assert result.iterations[0].proposal.content_ref  # a stable hash of "" content


def test_pipeline_never_manufactures_a_critique_for_an_already_passing_proposal():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    assert all(it.critique is None for it in result.iterations if it.stop_reason)


def test_pipeline_passes_org_id_and_model_through_to_reasoning_requests():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([True])
    run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        org_id="org-1", model="gpt-test", clock=_ticking_clock(),
    )
    assert reasoning.requests[0].org_id == "org-1"
    assert reasoning.requests[0].model == "gpt-test"


# ── Replay ───────────────────────────────────────────────────────────────────
def test_replay_record_validates_against_its_own_result():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False, True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    record = loop_replay_record_for(result, now=100.0)
    engine = LoopReplayEngine()
    comparison = engine.validate(record)
    assert comparison.matches is True
    assert comparison.mismatches == ()


def test_replay_record_is_deterministic_for_identical_results():
    reasoning1 = _FakeReasoningPort()
    reasoning2 = _FakeReasoningPort()
    validator1 = _SequenceValidator([True])
    validator2 = _SequenceValidator([True])
    result1 = run_loop(
        _definition(), reasoning_port=reasoning1, validator=validator1, session_id="s1",
        clock=lambda: 1.0,
    )
    result2 = run_loop(
        _definition(), reasoning_port=reasoning2, validator=validator2, session_id="s1",
        clock=lambda: 1.0,
    )
    record1 = loop_replay_record_for(result1, now=1.0)
    record2 = loop_replay_record_for(result2, now=1.0)
    assert record1.replay_hash == record2.replay_hash
    assert LoopReplayEngine().compare(record1, record2).matches is True


def test_replay_engine_detects_tampering():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    record = loop_replay_record_for(result, now=1.0)
    tampered = dataclasses.replace(record, iteration_hashes=("tampered-hash",))
    comparison = LoopReplayEngine().validate(tampered)
    assert comparison.matches is False
    assert comparison.mismatches == ("replay_hash",)


def test_replay_engine_compare_detects_divergence():
    reasoning_a = _FakeReasoningPort()
    reasoning_b = _FakeReasoningPort()
    validator_a = _SequenceValidator([True])
    validator_b = _SequenceValidator([False, True])
    result_a = run_loop(
        _definition(), reasoning_port=reasoning_a, validator=validator_a, session_id="s1",
        clock=_ticking_clock(),
    )
    result_b = run_loop(
        _definition(), reasoning_port=reasoning_b, validator=validator_b, session_id="s1",
        clock=_ticking_clock(),
    )
    record_a = loop_replay_record_for(result_a, now=1.0)
    record_b = loop_replay_record_for(result_b, now=1.0)
    comparison = LoopReplayEngine().compare(record_a, record_b)
    assert comparison.matches is False


# ── Diagnostics / statistics / observability ─────────────────────────────────
def test_build_diagnostics_wired_over_a_real_pipeline_result():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    cfg = LoopConfiguration.default()
    diag = build_diagnostics(config=cfg, result=result)
    assert diag.loop_id == "l1"
    assert diag.iteration_count == 1
    assert diag.converged is True


def test_compute_statistics_aggregates_multiple_results():
    reasoning1 = _FakeReasoningPort()
    reasoning2 = _FakeReasoningPort()
    result1 = run_loop(
        _definition(), reasoning_port=reasoning1, validator=_SequenceValidator([True]),
        session_id="s1", clock=_ticking_clock(),
    )
    result2 = run_loop(
        _definition(max_iterations=1), reasoning_port=reasoning2,
        validator=_SequenceValidator([False]), session_id="s1", clock=_ticking_clock(),
    )
    stats = compute_statistics([result1, result2])
    assert stats.loop_count == 2
    assert stats.per_stop_reason[VALIDATION_SUCCESS] == 1
    assert stats.per_stop_reason[MAX_ITERATIONS] == 1
    assert stats.convergence_rate == 0.5
    assert stats.avg_iteration_count == (result1.iteration_count + result2.iteration_count) / 2


def test_compute_statistics_handles_empty_sequence():
    stats = compute_statistics([])
    assert stats.loop_count == 0
    assert stats.avg_iteration_count == 0.0
    assert stats.convergence_rate == 0.0


def test_trace_for_counts_critiques_and_repairs():
    reasoning = _FakeReasoningPort()
    validator = _SequenceValidator([False, True])
    result = run_loop(
        _definition(), reasoning_port=reasoning, validator=validator, session_id="s1",
        clock=_ticking_clock(),
    )
    trace = trace_for(result, now=5.0)
    assert trace.loop_id == "l1"
    assert trace.critique_count == 1
    assert trace.repair_count == 1
    assert trace.iteration_count == result.iteration_count
    assert trace.stop_reason == result.stop_reason


# ── Dependency rules (loop stays acyclic; no ownership leakage) ─────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_loop_bundle_2_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "hooks", "workflow", "agent",
        "reasoning",
    }
    assert not (_imports("loop") & forbidden), _imports("loop")


def test_loop_only_reaches_reasoning_via_core_ports():
    """Loop Engine's sole external dependency is `core.ports.reasoning.
    ReasoningPort` -- never the `reasoning` implementation package itself."""
    for p in pathlib.Path("loop").rglob("*.py"):
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == "reasoning":
                raise AssertionError(f"{p} imports the reasoning package directly")
