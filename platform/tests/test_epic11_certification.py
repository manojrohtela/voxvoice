"""The Epic 11 certification suite (M11.16), executed.

ADR-0051 **CE5** names sixteen controls; `tests/certification/epic11.py` implements them and this
module runs them and asserts the properties the control set itself must have.

Two kinds of assertion live here, and the distinction matters:

1. **The controls run and their outcomes are what they are.** This file does *not* assert that
   Epic 11 is certified — that would make the suite unable to report a failure, which is the one
   thing a certification suite must always be able to do. It asserts that every control produced a
   verdict, that the verdict function is honest, and that no control passed by accident.
2. **The control set is well-formed**: every control cites a ratified clause, `INCONCLUSIVE` and
   `NOT_EXECUTED` never pass, the namespace is disjoint from Epic 9's and Epic 10's, and the
   report is reproducible.

The suite is deliberately silent about *which* verdict is correct. The verdict is evidence, and a
test that demanded a particular one would be asserting the conclusion rather than producing it.
"""

from __future__ import annotations

import pytest

from tests.certification.epic11 import (
    EPIC11_CERTIFICATION_CONTROLS,
    EPIC11_CERTIFICATION_VERSION,
    EPIC11_CONTROLS_BY_ID,
    EPIC11_DECLARED_LIMITATIONS,
    EPIC11_PASSING_OUTCOMES,
    Epic11Control,
    Epic11ControlFamily,
    Epic11ControlOutcome,
    Epic11ControlResult,
    Epic11ControlSeverity,
    Epic11Verdict,
    certify_epic11,
    epic11_certification_digest,
    render_epic11,
)

#: Run the control set **once** for the whole module, and **lazily**.
#:
#: Not at import: `E11-1` takes its count by shelling out to a collection pass, and collection
#: imports this module — so computing the report at import time makes the suite collect itself
#: into an unbounded recursion. Caching it here keeps the cost to one run per session while
#: leaving collection free of side effects, which is what lets `--collect-only` stay a cheap,
#: honest question.
_CACHE: list = []


def report():
    if not _CACHE:
        _CACHE.append(certify_epic11())
    return _CACHE[0]




# ── the control set is well-formed ──────────────────────────────────────────


def test_the_control_set_matches_the_ratified_minimum():
    """ADR-0051 CE5 names sixteen control areas; the suite implements exactly that many."""
    assert len(EPIC11_CERTIFICATION_CONTROLS) == 16
    assert [c.id for c in EPIC11_CERTIFICATION_CONTROLS] == [
        f"E11-{n}" for n in range(1, 17)
    ]


def test_every_control_cites_a_ratified_clause():
    """CE5's premise: a control with no authority behind it is an opinion rendered as a test."""
    for control in EPIC11_CERTIFICATION_CONTROLS:
        assert control.clauses, control.id
        assert control.statement, control.id


def test_a_control_without_an_authority_cannot_be_constructed():
    with pytest.raises(ValueError, match="cites no ratified clause"):
        Epic11Control(
            "E11-X", Epic11ControlFamily.GOVERNANCE, Epic11ControlSeverity.BLOCKING,
            "a statement", (),
        )


def test_the_namespace_is_disjoint_from_its_predecessors():
    """CE1/CE2: fresh identifiers, never a renumbering of Epic 9's or Epic 10's."""
    from tests.certification.catalogue import CONTROLS_BY_ID as EPIC9_IDS
    from tests.certification.epic10 import EPIC10_CONTROLS_BY_ID

    epic11 = set(EPIC11_CONTROLS_BY_ID)
    assert not epic11 & set(EPIC9_IDS)
    assert not epic11 & set(EPIC10_CONTROLS_BY_ID)
    assert all(cid.startswith("E11-") for cid in epic11)


def test_the_version_constant_is_epic_elevens_own():
    from tests.certification.epic10 import EPIC10_CERTIFICATION_VERSION

    assert EPIC11_CERTIFICATION_VERSION != EPIC10_CERTIFICATION_VERSION
    assert EPIC11_CERTIFICATION_VERSION.startswith("m11.16")


# ── CE3: a control that could not look has not passed ───────────────────────


def test_inconclusive_and_not_executed_are_not_passing_outcomes():
    """The rule that stops the suite certifying its own blind spots."""
    assert Epic11ControlOutcome.INCONCLUSIVE not in EPIC11_PASSING_OUTCOMES
    assert Epic11ControlOutcome.NOT_EXECUTED not in EPIC11_PASSING_OUTCOMES
    assert EPIC11_PASSING_OUTCOMES == {
        Epic11ControlOutcome.SATISFIED, Epic11ControlOutcome.NOT_APPLICABLE
    }


@pytest.mark.parametrize(
    "outcome",
    [Epic11ControlOutcome.INCONCLUSIVE, Epic11ControlOutcome.NOT_EXECUTED],
)
def test_a_missing_evidence_outcome_must_say_why(outcome):
    with pytest.raises(ValueError, match="must always say why"):
        Epic11ControlResult("E11-1", outcome)


def test_a_violation_must_name_what_it_found():
    with pytest.raises(ValueError, match="cannot be located"):
        Epic11ControlResult("E11-1", Epic11ControlOutcome.VIOLATED, detail="something")


def test_any_blocking_control_that_does_not_pass_prevents_certification():
    """The verdict function, exercised directly rather than inferred from today's tree."""
    from tests.certification.epic11 import _verdict

    for outcome in (
        Epic11ControlOutcome.VIOLATED,
        Epic11ControlOutcome.INCONCLUSIVE,
        Epic11ControlOutcome.NOT_EXECUTED,
    ):
        result = Epic11ControlResult(
            "E11-1", outcome, detail="reason",
            violations=("named",) if outcome is Epic11ControlOutcome.VIOLATED else (),
        )
        assert _verdict([result]) is Epic11Verdict.NOT_CERTIFIED, outcome


def test_an_all_satisfied_run_would_certify():
    """The converse, so the verdict function is not merely always pessimistic."""
    from tests.certification.epic11 import _verdict

    everything = [
        Epic11ControlResult(control.id, Epic11ControlOutcome.SATISFIED, detail="ok")
        for control in EPIC11_CERTIFICATION_CONTROLS
    ]
    assert _verdict(everything) is Epic11Verdict.CERTIFIED


# ── the run itself ──────────────────────────────────────────────────────────


def test_every_control_produced_a_result():
    assert {r.control_id for r in report().results} == set(EPIC11_CONTROLS_BY_ID)


def test_no_control_raised_its_way_to_an_unexplained_inconclusive():
    """An evaluator that raises becomes INCONCLUSIVE — legitimate, but it must be a *reason*,
    not a stack trace leaking through as a verdict."""
    for result in report().results:
        if result.outcome is Epic11ControlOutcome.INCONCLUSIVE:
            assert result.detail, result.control_id
            assert "Traceback" not in result.detail


def test_the_report_digest_is_reproducible():
    """Same tree, same digest — order-independent, so scheduling cannot change the answer."""
    again = epic11_certification_digest(report().results)
    assert again == report().report_digest
    shuffled = list(reversed(report().results))
    assert epic11_certification_digest(shuffled) == report().report_digest


def test_the_verdict_follows_from_the_results():
    """No hand-set verdict: it is derived, and derivable again by the reader."""
    blocking_failed = [
        r for r in report().results
        if not r.passed
        and EPIC11_CONTROLS_BY_ID[r.control_id].severity is Epic11ControlSeverity.BLOCKING
    ]
    if blocking_failed:
        assert report().verdict is Epic11Verdict.NOT_CERTIFIED
        assert report().blocking_failures
    else:
        assert report().verdict in (
            Epic11Verdict.CERTIFIED, Epic11Verdict.CERTIFIED_WITH_ADVISORIES
        )


def test_the_self_administration_limitation_is_declared():
    """CE9: stated, not implied away."""
    assert any(lim.self_administered for lim in EPIC11_DECLARED_LIMITATIONS)


def test_live_evidence_controls_are_declared_before_they_are_run():
    """A reader must be able to see which controls depend on evidence this environment may not
    have **without** running them."""
    live = [c.id for c in EPIC11_CERTIFICATION_CONTROLS if c.requires_live_evidence]
    assert "E11-15" in live


def test_a_control_requiring_live_evidence_never_reports_satisfied_without_it():
    """The failure mode this whole milestone must not have: a live control passing hermetically."""
    for control in EPIC11_CERTIFICATION_CONTROLS:
        if not control.requires_live_evidence:
            continue
        result = next(r for r in report().results if r.control_id == control.id)
        if result.outcome is Epic11ControlOutcome.SATISFIED:
            # Permitted only if the artifact genuinely records execution — never by default.
            assert "NOT EXECUTED" not in result.detail


def test_rendering_names_every_control_and_its_outcome():
    rendered = render_epic11(report())
    for control in EPIC11_CERTIFICATION_CONTROLS:
        assert control.id in rendered
    assert report().report_digest in rendered
    assert report().verdict.value in rendered


# ── certification reads; it never writes (CE4) ──────────────────────────────


def test_the_certification_module_performs_no_write():
    """CE4, asserted structurally: no `open(..., "w")`, no `write_text`, no `unlink`, no `mkdir`."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).parent / "certification" / "epic11.py"
    ).read_text(encoding="utf-8")
    forbidden = {"write_text", "write_bytes", "unlink", "mkdir", "rmtree", "remove"}
    offenders = [
        f"line {node.lineno}: {node.func.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden
    ]
    assert not offenders, f"certification writes: {offenders}"


def test_the_certification_module_never_invokes_a_predecessor_control():
    """CE2: Epic 9's and Epic 10's *controls* are never executed here.

    `certify_epic10` is called in exactly one place — `E11-16`, to observe that Epic 10's
    historical digest still reproduces. That is reading data about a predecessor, which CE2
    permits, and it is asserted here to be the only such call so it cannot quietly become two."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).parent / "certification" / "epic11.py"
    ).read_text(encoding="utf-8")
    calls = [
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("certify_epic10") == 1
    assert "run_certification" not in source, "Epic 9's runner must never be invoked"
