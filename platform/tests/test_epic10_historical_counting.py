"""Historical counting in Epic 10 certification (M11.5.1) — ADR-0052 Amendment 1 (HC9).

M11.2.1 made the freeze and certification suites history-aware for **git-diff assertions** and
**digest recomputation**. It did not reach a third form of the same defect: controls whose
`examined` value is a **count of the live tree**. Since `examined` is a digest input, a later
epic legitimately adding a file, a member or a table moved Epic 10's published certification
digest **while every control still reported `satisfied`** — the digest was measuring the
repository's growth rather than the certified tree.

M11.5 hit exactly that and correctly stopped: adding the four AR-6 transport `EventType` members
it needs moved the digest from `7e57bc14764201b1…` to `1b65916c39c21a21…`.

These tests pin the repaired distinction, which is the whole point of the repair:

    CERTIFIED TREE  →  examined / digest input      (permanent; must not move)
    LIVE TREE       →  ongoing negative invariant   (must keep biting on new code)

Hermetic: git history and synthetic trees only. No network, no vendor, no sleeps.
"""

from __future__ import annotations

import inspect
import pathlib
import textwrap

import pytest

from tests.certification import epic10
from tests.certification.epic10 import (
    CERTIFICATION_COMMIT,
    Epic10ControlOutcome,
    certify_epic10,
)

#: The published Epic 10 certification digest. Pinned as a literal so this suite fails if the
#: repair moved it — the one outcome M11.5.1 was forbidden to produce.
PUBLISHED_CERTIFICATION_DIGEST = (
    "7e57bc14764201b1cd4c2e6ac21a1f9c94eb99c1c60d1e4e1c4f4d5c2e4b3f8a"
)

#: The counts M10.13 recorded. Historical facts now, not measurements of today's checkout.
CERTIFIED_EVENT_TYPES = 104
CERTIFIED_EVT_NAMES = 8
CERTIFIED_BILLING_MODULES = 240


def _synthetic_tree(root: pathlib.Path, events_source: str) -> pathlib.Path:
    """A minimal fake `platform/` whose `core/events.py` is whatever the test wants.

    Pointing `PLATFORM_ROOT` at this is how the **live** half of a control is driven without
    touching the real repository — the certified half keeps reading git, so the two sources stay
    genuinely independent."""
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "events.py").write_text(events_source, encoding="utf-8")
    return root


_EVENTS_TEMPLATE = """from enum import StrEnum


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    {extra}
"""


# ── A / D / F. the digest input comes from the certified tree ───────────────


def test_e10_5_examined_is_the_certified_event_type_count():
    result = next(r for r in certify_epic10().results if r.control_id == "E10-5")
    assert result.examined == CERTIFIED_EVENT_TYPES
    assert result.outcome is Epic10ControlOutcome.SATISFIED


def test_e10_3_examined_is_the_certified_name_count():
    result = next(r for r in certify_epic10().results if r.control_id == "E10-3")
    assert result.examined == CERTIFIED_EVT_NAMES
    assert result.outcome is Epic10ControlOutcome.SATISFIED


def test_e10_6_examined_is_the_certified_module_count():
    result = next(r for r in certify_epic10().results if r.control_id == "E10-6")
    assert result.examined == CERTIFIED_BILLING_MODULES
    assert result.outcome is Epic10ControlOutcome.SATISFIED


def test_the_certified_counts_are_readable_from_git_not_the_checkout():
    """The values above are facts about `db8c508`, provable without the working tree."""
    source = epic10._certified_source("platform/core/events.py")
    assert source, "the certified tree is unreadable — the evidence is missing"
    assert len(epic10._enum_members(source, "EventType")) == CERTIFIED_EVENT_TYPES

    billing = epic10._certified_python_sources("platform/billing")
    assert len(billing) == CERTIFIED_BILLING_MODULES


def test_the_certified_commit_is_pinned_and_resolvable():
    assert epic10._git("cat-file", "-t", CERTIFICATION_COMMIT).strip() == "commit"


# ── B. live-tree growth cannot move the historical count ────────────────────


def test_adding_event_types_to_the_live_tree_does_not_move_the_examined_count(
    tmp_path, monkeypatch
):
    """**The regression that closes M11.5's blocker.**

    Four extra members in the live tree — exactly the AR-6 transport events M11.5 needs — and the
    examined count does not budge, because it now describes the certified tree."""
    extra = "\n    ".join(
        f'{name} = "transport.{name.lower()}"'
        for name in ("TRANSPORT_CONNECTED", "TRANSPORT_MEDIA_READY", "TRANSPORT_DISCONNECTED")
    )
    root = _synthetic_tree(tmp_path, _EVENTS_TEMPLATE.format(extra=extra))
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", root)

    result = epic10._e10_5()
    assert result.examined == CERTIFIED_EVENT_TYPES
    assert result.outcome is Epic10ControlOutcome.SATISFIED


def test_a_shrinking_live_tree_also_does_not_move_the_examined_count(tmp_path, monkeypatch):
    """The count is historical in both directions — it is not a floor, it is a fact."""
    root = _synthetic_tree(tmp_path, _EVENTS_TEMPLATE.format(extra="pass"))
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", root)
    assert epic10._e10_5().examined == CERTIFIED_EVENT_TYPES


def test_the_full_digest_is_stable_under_a_legitimate_additive_event_type(
    tmp_path, monkeypatch
):
    """End-to-end: the published digest survives the exact change that used to break it."""
    before = certify_epic10().report_digest
    root = _synthetic_tree(
        tmp_path, _EVENTS_TEMPLATE.format(extra='TRANSPORT_CONNECTED = "transport.connected"')
    )
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", root)
    # E10-5's contribution is unchanged; that is the part this repair owns.
    assert epic10._e10_5().examined == CERTIFIED_EVENT_TYPES
    monkeypatch.undo()
    assert certify_epic10().report_digest == before


# ── C / E / G. the live invariants still bite ───────────────────────────────


def test_e10_5_still_detects_a_forbidden_metric_billing_member(tmp_path, monkeypatch):
    """**The negative guard.** A historical count must not turn the control into a no-op.

    `metric.billing.*` is reserved to Billing (ADR-0033 MS9) — an *ongoing* reservation, so it is
    still enforced against whatever the live tree contains."""
    root = _synthetic_tree(
        tmp_path, _EVENTS_TEMPLATE.format(extra='BILLING_X = "metric.billing.invoiced"')
    )
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", root)

    result = epic10._e10_5()
    assert result.outcome is Epic10ControlOutcome.VIOLATED
    assert any("metric.billing" in v for v in result.violations)


def test_e10_3_still_detects_a_table_without_the_evt_prefix(tmp_path, monkeypatch):
    """The prefix rule keeps biting on new code, which is the reason it is scanned live."""
    events = tmp_path / "events"
    events.mkdir(parents=True)
    (events / "models.py").write_text(
        textwrap.dedent(
            """
            class Bad:
                __tablename__ = "not_prefixed"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", tmp_path)

    result = epic10._e10_3()
    assert result.outcome is Epic10ControlOutcome.VIOLATED
    assert any("not_prefixed" in v for v in result.violations)
    # …and the examined count is still the certified one, not the synthetic tree's.
    assert result.examined == CERTIFIED_EVT_NAMES


def test_e10_6_still_detects_a_billing_module_subscribing_to_a_bus(tmp_path, monkeypatch):
    """`events → billing` stays forbidden, enforced against the live tree."""
    billing = tmp_path / "billing"
    billing.mkdir(parents=True)
    (billing / "leaky.py").write_text("bus.subscribe(handler)\n", encoding="utf-8")
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", tmp_path)

    result = epic10._e10_6()
    assert result.outcome is Epic10ControlOutcome.VIOLATED
    assert any("subscribe" in v for v in result.violations)
    assert result.examined == CERTIFIED_BILLING_MODULES


def test_a_vacuous_live_scan_is_inconclusive_not_a_pass(tmp_path, monkeypatch):
    """**Both halves need evidence** (ADR-0043 CD4).

    A historical count must not let an empty live tree slip through as a clean pass: if there is
    nothing under `events/` or `billing/` to check, the invariant was not verified — it simply had
    nothing to bite on. Epic 10's own negative guard for E10-3 caught this gap in the first draft
    of the M11.5.1 repair, and it is pinned here for E10-6 too."""
    monkeypatch.setattr(epic10, "PLATFORM_ROOT", tmp_path)  # empty tree
    assert epic10._e10_3().outcome is Epic10ControlOutcome.INCONCLUSIVE
    assert epic10._e10_6().outcome is Epic10ControlOutcome.INCONCLUSIVE


# ── H / I. the verdicts and digests are untouched ───────────────────────────


def test_the_certification_verdict_and_digest_are_unchanged():
    report = certify_epic10()
    assert report.verdict == "certified"
    assert report.report_digest.startswith("7e57bc14764201b1")


def test_the_freeze_verdict_and_digest_are_unchanged():
    from tests.freeze.epic10 import build_freeze_manifest, freeze_manifest_digest

    manifest = build_freeze_manifest(repository_wide_tests=5345, failed=0, skipped=0)
    assert manifest["freeze_verdict"] == "frozen"
    assert freeze_manifest_digest(manifest).startswith("d3d4858d5f18b7f6")


def test_no_control_was_deleted_by_the_repair():
    """A repair that removed a control would 'close' the defect by removing the evidence."""
    ids = {r.control_id for r in certify_epic10().results}
    assert ids == {f"E10-{n}" for n in range(1, 14)}


def test_every_control_still_runs():
    """ADR-0043 CD4 — `INCONCLUSIVE` does not pass, so a control that cannot read its evidence
    must not be able to hide behind the repair."""
    for result in certify_epic10().results:
        assert result.outcome is not Epic10ControlOutcome.INCONCLUSIVE, result.control_id


# ── the distinction, asserted structurally ──────────────────────────────────


def test_the_repaired_controls_read_both_trees():
    """Each repaired control must reference **both** a certified source and a live source.

    A control that read only history would stop guarding; one that read only the live tree is the
    defect being repaired. Asserting both keeps the two responsibilities visible in the source."""
    import inspect

    for control in (epic10._e10_3, epic10._e10_5, epic10._e10_6):
        source = inspect.getsource(control)
        assert "_certified" in source, f"{control.__name__} has no certified-tree input"
        assert "PLATFORM_ROOT" in source, f"{control.__name__} no longer inspects the live tree"


def _code_without_docstring(function) -> str:
    """A function's source with its docstring removed, read through the AST.

    `__doc__` is the *dedented* string and does not match the raw source, so a textual strip
    silently does nothing. That matters here because E10-5's docstring **explains** the removed
    `len(current)` shape — a guard that cannot tell prose from code fails on its own
    documentation, a trap this repository has sprung more than once."""
    import ast
    import textwrap

    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    node = tree.body[0]
    lines = source.splitlines()
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        del lines[first.lineno - 1 : first.end_lineno]
    return "\n".join(lines)


@pytest.mark.parametrize("control_id", ["E10-3", "E10-5", "E10-6"])
def test_examined_never_derives_from_a_live_count(control_id):
    """The specific shape that caused the defect — `len()` over a live-tree scan feeding
    `examined` — must not reappear in the repaired controls."""

    control = {"E10-3": epic10._e10_3, "E10-5": epic10._e10_5, "E10-6": epic10._e10_6}[control_id]
    body = _code_without_docstring(control)
    assert "len(current)" not in body
    assert "len(sources)" not in body
