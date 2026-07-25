"""Loop Engine Bundle 1 tests (M6.6B-1) — model validation, stopping policy,
deterministic hashing, serialization, and dependency boundaries. No pipeline
execution exists yet (M6.6B-2); these tests exercise shapes and pure
functions in isolation only.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.errors import ConfigError
from loop.core.cancellation import LoopCancellationToken
from loop.core.config import LoopConfiguration
from loop.core.domain import (
    LoopCritique,
    LoopDefinition,
    LoopExecutionContext,
    LoopIteration,
    LoopProposal,
    LoopRepair,
    LoopResult,
    LoopStatistics,
    LoopStoppingPolicy,
    LoopValidation,
)
from loop.core.factory import LoopFactory
from loop.core.ids import (
    content_hash,
    critique_id_for,
    iteration_hash_for,
    proposal_id_for,
    repair_id_for,
    validation_id_for,
)
from loop.core.serializer import LoopSerializer
from loop.core.stopping import (
    CONFIDENCE_THRESHOLD,
    CONVERGENCE,
    EXTERNAL_STOP,
    MAX_ITERATIONS,
    VALIDATION_SUCCESS,
    evaluate_stop,
)
from loop.core.validator import LoopValidator
from loop.diagnostics.diagnostics import build_diagnostics


def _proposal(pid: str = "p1", ref: str = "ref-a") -> LoopProposal:
    return LoopProposal(proposal_id=pid, content_ref=ref)


def _validation(
    *, passed: bool, confidence: float | None = None, pid: str = "p1",
) -> LoopValidation:
    return LoopValidation(
        validation_id="v1", proposal_id=pid, passed=passed, confidence=confidence,
    )


def _iteration(
    *, number: int = 1, ref: str = "ref-a", passed: bool = False, stopped: bool = False,
    stop_reason: str = "",
) -> LoopIteration:
    return LoopIteration(
        iteration_number=number, proposal=_proposal(ref=ref), critique=None, repair=None,
        validation=_validation(passed=passed), stopped=stopped, stop_reason=stop_reason,
    )


# ── Model validation ─────────────────────────────────────────────────────────
def test_loop_definition_defaults():
    d = LoopDefinition(loop_id="l1", task_description="improve the draft")
    assert d.stopping_policy == LoopStoppingPolicy()
    assert dict(d.metadata) == {}


def test_loop_definition_is_frozen():
    d = LoopDefinition(loop_id="l1", task_description="x")
    with pytest.raises(AttributeError):
        d.loop_id = "l2"  # type: ignore[misc]


def test_loop_iteration_allows_none_critique_and_repair():
    it = _iteration()
    assert it.critique is None
    assert it.repair is None


def test_loop_result_defaults_to_empty_iterations():
    r = LoopResult(
        loop_id="l1", final_proposal_id="p1", converged=True, iteration_count=1,
        stop_reason=VALIDATION_SUCCESS,
    )
    assert r.iterations == ()


def test_loop_execution_context_is_independent_of_agent_execution_context():
    ctx = LoopExecutionContext(loop_id="l1", session_id="s1", trace_id="t1")
    fields = {f for f in ctx.__dataclass_fields__}
    assert fields == {"loop_id", "session_id", "trace_id", "org_id", "iteration", "variables"}


def test_loop_statistics_defaults():
    stats = LoopStatistics()
    assert stats.loop_count == 0
    assert stats.convergence_rate == 0.0
    assert dict(stats.per_stop_reason) == {}


# ── Configuration ────────────────────────────────────────────────────────────
def test_loop_configuration_default():
    cfg = LoopConfiguration.default()
    assert cfg.name == "loop"
    assert cfg.default_stopping_policy == LoopStoppingPolicy()


def test_loop_configuration_rejects_empty_name():
    with pytest.raises(ConfigError):
        LoopConfiguration(name="")


def test_loop_configuration_rejects_zero_max_iterations():
    with pytest.raises(ConfigError):
        LoopConfiguration(default_stopping_policy=LoopStoppingPolicy(max_iterations=0))


def test_loop_configuration_rejects_out_of_range_confidence_threshold():
    with pytest.raises(ConfigError):
        LoopConfiguration(
            default_stopping_policy=LoopStoppingPolicy(confidence_threshold=1.5)
        )


def test_loop_configuration_fingerprint_is_deterministic():
    cfg1 = LoopConfiguration.default()
    cfg2 = LoopConfiguration.default()
    assert cfg1.fingerprint() == cfg2.fingerprint()


def test_loop_configuration_fingerprint_changes_with_policy():
    cfg1 = LoopConfiguration.default()
    cfg2 = LoopConfiguration(default_stopping_policy=LoopStoppingPolicy(max_iterations=10))
    assert cfg1.fingerprint() != cfg2.fingerprint()


# ── Factory ──────────────────────────────────────────────────────────────────
def test_factory_create_definition_uses_config_default_policy():
    cfg = LoopConfiguration.default()
    d = LoopFactory.create_definition(cfg, loop_id="l1", task_description="improve x")
    assert d.stopping_policy == cfg.default_stopping_policy


def test_factory_create_definition_honors_explicit_policy_override():
    cfg = LoopConfiguration.default()
    policy = LoopStoppingPolicy(max_iterations=2)
    d = LoopFactory.create_definition(
        cfg, loop_id="l1", task_description="x", stopping_policy=policy,
    )
    assert d.stopping_policy is policy


def test_factory_default_definition():
    d = LoopFactory.default_definition(loop_id="l1", task_description="x")
    assert d.loop_id == "l1"


def test_factory_create_context():
    ctx = LoopFactory.create_context(loop_id="l1", session_id="s1", trace_id="t1")
    assert ctx.iteration == 0
    assert dict(ctx.variables) == {}


# ── Content hashing ──────────────────────────────────────────────────────────
def test_content_hash_is_deterministic():
    assert content_hash("a", 1, 2.0) == content_hash("a", 1, 2.0)


def test_content_hash_is_order_sensitive():
    assert content_hash("a", "b") != content_hash("b", "a")


def test_id_helpers_are_deterministic():
    assert proposal_id_for("l1", 1, 100.0) == proposal_id_for("l1", 1, 100.0)
    assert critique_id_for("p1", 100.0) == critique_id_for("p1", 100.0)
    assert repair_id_for("c1", 100.0) == repair_id_for("c1", 100.0)
    assert validation_id_for("p1", 100.0) == validation_id_for("p1", 100.0)


def test_iteration_hash_is_deterministic_and_sensitive_to_content():
    it1 = _iteration(ref="ref-a")
    it2 = _iteration(ref="ref-a")
    it3 = _iteration(ref="ref-b")
    assert iteration_hash_for(it1) == iteration_hash_for(it2)
    assert iteration_hash_for(it1) != iteration_hash_for(it3)


def test_iteration_hash_handles_present_critique_and_repair():
    it = LoopIteration(
        iteration_number=1, proposal=_proposal(),
        critique=LoopCritique(critique_id="c1", proposal_id="p1", content_ref="ref-c"),
        repair=LoopRepair(repair_id="r1", critique_id="c1", content_ref="ref-r"),
        validation=_validation(passed=True), stopped=True, stop_reason=VALIDATION_SUCCESS,
    )
    assert iteration_hash_for(it)  # does not raise, produces a stable digest


# ── Stopping policy ──────────────────────────────────────────────────────────
def test_stop_on_max_iterations():
    policy = LoopStoppingPolicy(stop_on_validation_success=False, stop_on_convergence=False)
    stopped, reason = evaluate_stop(
        iteration_number=5, validation=_validation(passed=False),
        current_content_ref="ref-a", previous_content_ref="ref-z", policy=policy,
    )
    assert (stopped, reason) == (True, MAX_ITERATIONS)


def test_stop_on_validation_success():
    policy = LoopStoppingPolicy()
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=True),
        current_content_ref="ref-a", previous_content_ref=None, policy=policy,
    )
    assert (stopped, reason) == (True, VALIDATION_SUCCESS)


def test_validation_success_does_not_stop_when_policy_disables_it():
    policy = LoopStoppingPolicy(stop_on_validation_success=False, max_iterations=5)
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=True),
        current_content_ref="ref-a", previous_content_ref=None, policy=policy,
    )
    assert (stopped, reason) == (False, "")


def test_stop_on_confidence_threshold():
    policy = LoopStoppingPolicy(stop_on_validation_success=False, confidence_threshold=0.8)
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False, confidence=0.9),
        current_content_ref="ref-a", previous_content_ref=None, policy=policy,
    )
    assert (stopped, reason) == (True, CONFIDENCE_THRESHOLD)


def test_confidence_threshold_not_met_does_not_stop():
    policy = LoopStoppingPolicy(
        stop_on_validation_success=False, confidence_threshold=0.95, max_iterations=5,
    )
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False, confidence=0.5),
        current_content_ref="ref-a", previous_content_ref="ref-z", policy=policy,
    )
    assert (stopped, reason) == (False, "")


def test_stop_on_convergence():
    policy = LoopStoppingPolicy(stop_on_validation_success=False, max_iterations=5)
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False),
        current_content_ref="ref-a", previous_content_ref="ref-a", policy=policy,
    )
    assert (stopped, reason) == (True, CONVERGENCE)


def test_convergence_disabled_by_policy_does_not_stop():
    policy = LoopStoppingPolicy(
        stop_on_validation_success=False, stop_on_convergence=False, max_iterations=5,
    )
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False),
        current_content_ref="ref-a", previous_content_ref="ref-a", policy=policy,
    )
    assert (stopped, reason) == (False, "")


def test_convergence_requires_a_previous_content_ref():
    policy = LoopStoppingPolicy(stop_on_validation_success=False, max_iterations=5)
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False),
        current_content_ref="ref-a", previous_content_ref=None, policy=policy,
    )
    assert (stopped, reason) == (False, "")


def test_external_stop_wins_over_every_other_condition():
    token = LoopCancellationToken("t1")
    token.cancel(reason="user requested")
    policy = LoopStoppingPolicy()
    stopped, reason = evaluate_stop(
        iteration_number=5, validation=_validation(passed=True),
        current_content_ref="ref-a", previous_content_ref="ref-a", policy=policy,
        cancellation_token=token,
    )
    assert (stopped, reason) == (True, EXTERNAL_STOP)


def test_no_stop_condition_met_returns_not_stopped():
    policy = LoopStoppingPolicy(
        stop_on_validation_success=False, stop_on_convergence=False, max_iterations=5,
    )
    stopped, reason = evaluate_stop(
        iteration_number=1, validation=_validation(passed=False),
        current_content_ref="ref-a", previous_content_ref="ref-z", policy=policy,
    )
    assert (stopped, reason) == (False, "")


# ── Cancellation token ───────────────────────────────────────────────────────
def test_cancellation_token_starts_uncancelled():
    token = LoopCancellationToken("t1")
    assert not token.is_cancelled()
    assert token.reason == ""


def test_cancellation_token_cancel_is_idempotent():
    token = LoopCancellationToken("t1")
    token.cancel(reason="first")
    token.cancel(reason="second")
    assert token.is_cancelled()
    assert token.reason == "second"


# ── LoopValidator (interface only) ──────────────────────────────────────────
def test_loop_validator_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        LoopValidator()  # type: ignore[abstract]


def test_loop_validator_concrete_subclass_must_implement_validate():
    class AlwaysPass(LoopValidator):
        def validate(self, proposal: LoopProposal) -> LoopValidation:
            return LoopValidation(validation_id="v1", proposal_id=proposal.proposal_id, passed=True)

    v = AlwaysPass()
    result = v.validate(_proposal())
    assert result.passed is True


# ── Serializer round-trips ───────────────────────────────────────────────────
def test_serializer_round_trips_loop_definition():
    ser = LoopSerializer()
    d = LoopDefinition(
        loop_id="l1", task_description="improve x",
        stopping_policy=LoopStoppingPolicy(max_iterations=3), metadata={"k": "v"},
    )
    assert ser.definition_from_dict(ser.definition_to_dict(d)) == d


def test_serializer_round_trips_proposal_critique_repair_validation():
    ser = LoopSerializer()
    p = LoopProposal(proposal_id="p1", content_ref="ref-a", reasoning_decision_ref="rd1")
    c = LoopCritique(critique_id="c1", proposal_id="p1", content_ref="ref-c")
    r = LoopRepair(repair_id="r1", critique_id="c1", content_ref="ref-r")
    v = LoopValidation(
        validation_id="v1", proposal_id="p1", passed=True, confidence=0.9, reason="ok",
    )
    assert ser.proposal_from_dict(ser.proposal_to_dict(p)) == p
    assert ser.critique_from_dict(ser.critique_to_dict(c)) == c
    assert ser.repair_from_dict(ser.repair_to_dict(r)) == r
    assert ser.validation_from_dict(ser.validation_to_dict(v)) == v


def test_serializer_round_trips_iteration_with_none_critique_and_repair():
    ser = LoopSerializer()
    it = _iteration(stopped=True, stop_reason=VALIDATION_SUCCESS)
    assert ser.iteration_from_dict(ser.iteration_to_dict(it)) == it


def test_serializer_round_trips_loop_result():
    ser = LoopSerializer()
    it = _iteration(stopped=True, stop_reason=VALIDATION_SUCCESS)
    result = LoopResult(
        loop_id="l1", final_proposal_id="p1", converged=True, iteration_count=1,
        stop_reason=VALIDATION_SUCCESS, iterations=(it,), completed_at=123.0,
    )
    assert ser.result_from_dict(ser.result_to_dict(result)) == result


# ── Diagnostics ──────────────────────────────────────────────────────────────
def test_build_diagnostics_without_a_result():
    cfg = LoopConfiguration.default()
    diag = build_diagnostics(config=cfg)
    assert diag.runtime_version
    assert diag.loop_id is None
    assert diag.health_summary == "healthy"


def test_build_diagnostics_with_a_result():
    cfg = LoopConfiguration.default()
    result = LoopResult(
        loop_id="l1", final_proposal_id="p1", converged=True, iteration_count=2,
        stop_reason=VALIDATION_SUCCESS,
    )
    diag = build_diagnostics(config=cfg, result=result)
    assert diag.loop_id == "l1"
    assert diag.iteration_count == 2
    assert diag.converged is True


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


def test_loop_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "hooks", "workflow", "agent",
    }
    assert not (_imports("loop") & forbidden), _imports("loop")


def test_loop_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "typing", "hashlib",
    }
    tops = _imports("loop")
    assert tops <= allowed_extra | {"loop"}, tops


def test_no_pipeline_module_exists_in_bundle_1():
    """M6.6B-2 owns `loop/core/pipeline.py` (actual propose/critique/repair/
    validate/stop-check sequencing calling ReasoningPort + LoopValidator).
    Bundle 1 ships model shapes, policy checks, and interfaces only."""
    with pytest.raises(ModuleNotFoundError):
        import loop.core.pipeline  # noqa: F401


def test_no_replay_package_exists_in_bundle_1():
    """M6.6B-2 owns `loop/replay/` (LoopReplayRecord + replay engine)."""
    with pytest.raises(ModuleNotFoundError):
        import loop.replay  # noqa: F401
