"""M9.18 — Revenue Recognition & Deferred Revenue Engine.

Recognition is the first Billing domain whose output a **statutory filing** reports. Every earlier
milestone could be checked by asking "is the number right?"; this one is checked by asking:

> *Was this amount earned when we said it was, on evidence that has not changed since, under rules
> that were in force at the time — and can we prove it after the price book, the tax rates, the
> subscription and the policy have all been rewritten?*

So the suite is organised around the four ways that fails:

* **the money does not survive the split** — a paisa appears or disappears between the basis and
  the periods (§3);
* **history is silently restated** — a policy edit in April changes what March recognized (§4);
* **the same amount is recognized twice** — two workers, a restart, a replayed command (§6, §8);
* **cash is mistaken for revenue** — a paid invoice is assumed earned, or an unpaid one deferred
  (§7).

Every claim is demonstrated **structurally** — by a derived digest, a database constraint, an
absent FSM edge, an allocator that is the only division of money in the milestone, or two real
connections racing — rather than by a happy path assumed to imply the rest. §11 is the numbered
adversarial hunt.

The invoice pipeline is the real one: a closed accounting period, a rated run, a posted ledger, an
issued document with a pinned snapshot digest. Recognition reads that snapshot and nothing else.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tests.test_billing_invoice_engine as inv
from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ALL_REVENUE_COMMANDS,
    ALL_REVENUE_QUERIES,
    ActivateRevenueSchedule,
    AllowAllAuthorizer,
    CancelRevenueSchedule,
    DeactivateRecognitionPolicy,
    DeferredRevenueSummary,
    ExplainRecognition,
    GetRecognitionEntry,
    GetRecognitionPolicyVersions,
    GetRecognitionPosition,
    GetRevenueSchedule,
    GetSchedulesForDocument,
    ListRecognitionEntries,
    ListRecognitionPolicies,
    OpenAccountingPeriod,
    PostRecognitionToLedger,
    PublishRecognitionPolicy,
    RecognizeRevenue,
    ReverseRecognition,
    ScheduleRevenue,
    SkipRecognitionEntry,
    SupersedeRevenueSchedule,
    UnpostedRecognitions,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    EARNED_SCHEDULE_STATES,
    RECOGNIZED_ENTRY_STATES,
    REVENUE_ENTRY_LIFECYCLE,
    REVENUE_SCHEDULE_LIFECYCLE,
    TERMINAL_SCHEDULE_STATES,
    BillingConflict,
    BillingNotFound,
    BillingPeriod,
    BillingValidationError,
    Currency,
    EntryState,
    EntryTrigger,
    FixedClock,
    InvalidBillingTransition,
    Money,
    PeriodGranularity,
    PostingSource,
    RecognitionBasis,
    RecognitionMethod,
    RecognitionScheduler,
    RecognitionState,
    RecognitionValidator,
    RemainderRule,
    RevenueFactory,
    ScheduleLifecycleService,
    ScheduleStale,
    ScheduleState,
    ScheduleTrigger,
    SequentialIdGenerator,
    civil_from_days,
    days_from_civil,
    derive_basis_digest,
    derive_entry_id,
    derive_schedule_id,
    period_boundaries,
)
from billing.persistence.composition import build_persistent_billing_application

ORG, ACTOR = inv.ORG, inv.ACTOR
KW = inv.KW
INR = Currency.INR
SECRET = "test-secret-value-for-billing-revenue"

#: 2024 in UTC — a **leap year**, deliberately, so month lengths and 29 February are exercised by
#: the ordinary path rather than by a special case.
Y24_START, Y24_END = 1_704_067_200.0, 1_735_689_600.0
MONTH = 30 * 86_400.0


def _wired():
    """The real invoice pipeline: closed period, rated run, posted ledger, issued document."""
    generator = inv.wired.__wrapped__()
    persistent, account = next(generator)
    return persistent, account, generator


@pytest.fixture
def wired():
    persistent, account, generator = _wired()
    app = persistent.application
    inv._closed(app, account)
    app.commands.dispatch(
        PublishRecognitionPolicy(
            **KW,
            code="std",
            version=1,
            method=RecognitionMethod.STRAIGHT_LINE,
            granularity=PeriodGranularity.MONTH,
        )
    )
    yield persistent, account
    for _ in generator:  # pragma: no cover — drains the invoice fixture's teardown
        pass


def _issued(app, account):
    return inv._issue(app, account)


def _schedule(app, document_id: str, **overrides):
    body = dict(
        document_id=document_id,
        service_start=Y24_START,
        service_end=Y24_END,
        policy_code="std",
    )
    body.update(overrides)
    return app.commands.dispatch(ScheduleRevenue(**KW, **body))


def _active(app, account, **overrides):
    """An issued document with an activated schedule, ready to recognize."""
    document = _issued(app, account)
    schedule = _schedule(app, document.id, **overrides)
    app.commands.dispatch(ActivateRevenueSchedule(**KW, schedule_id=schedule.id))
    entries = app.queries.dispatch(ListRecognitionEntries(**KW, schedule_id=schedule.id))
    return document, schedule, entries


def _open_year(app, account):
    """An accounting period that will accept a 2024 posting."""
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW,
            account_id=account.id,
            period_start=Y24_START,
            period_end=Y24_END + 1,
            currency=INR,
        )
    )


def _basis(**overrides) -> RecognitionBasis:
    body = dict(
        org_id=ORG,
        account_id="bac_1",
        document_id="inv_1",
        revision=1,
        snapshot_digest="d" * 64,
        amount=Money(100_000, INR),
        service_period=BillingPeriod(start=Y24_START, end=Y24_END),
        method=RecognitionMethod.STRAIGHT_LINE,
        granularity=PeriodGranularity.MONTH,
        remainder=RemainderRule.LARGEST_REMAINDER,
        policy_code="std",
        policy_version=1,
        policy_id="rvp_1",
    )
    body.update(overrides)
    return RecognitionBasis(**body)


def _code_only(source: str) -> str:
    """Source with docstrings and comments stripped, so prose about a ban is not the thing
    being banned."""
    tree = ast.parse(source)
    spans: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body or not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            spans.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = (line for n, line in enumerate(source.splitlines(), 1) if n not in spans)
    return "\n".join(re.sub(r"#.*$", "", line) for line in kept)


def _html_code_only(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


REVENUE_FILES = (
    Path("billing/foundation/commercial_revenue.py"),
    Path("billing/foundation/revenue_rules.py"),
    Path("billing/foundation/revenue_factories.py"),
    Path("billing/application/revenue_engine.py"),
    Path("billing/persistence/revenue_models.py"),
    Path("billing/persistence/revenue_repositories.py"),
    Path("billing/customer/revenue_schemas.py"),
    Path("billing/customer/routers/revenue_engine.py"),
)


# ── 1. the audit: M9.2's RecognitionState is reused, not duplicated ────────────


def test_m9_2_recognition_state_is_reused_not_duplicated():
    """ADR-0023 RS6 says recognition is a **projection over the ledger**. That posture already had
    a vocabulary at M9.2, and this milestone declares no second one."""
    from billing.foundation import RecognitionState as Reused

    assert {m.value for m in Reused} == {"deferred", "recognized", "not_applicable"}
    source = Path("billing/foundation/commercial_revenue.py").read_text()
    assert "from .ledger import RecognitionState" in source
    assert "class RecognitionState" not in source


def test_the_two_meanings_of_deferred_are_never_conflated():
    """M9.13's `PostingOutcome.DEFERRED` means *the period was closed*, not *deferred revenue*.

    They are different facts with the same English word, and a domain that conflated them would
    report a late posting as unearned revenue."""
    from billing.foundation import PostingOutcome

    assert PostingOutcome.DEFERRED.value == "deferred"
    assert RecognitionState.DEFERRED.value == "deferred"
    # Recognition never stores a `PostingOutcome`; it stores the *disposition* M9.13 reported.
    from billing.foundation import RecognitionEntry

    assert "ledger_disposition" in RecognitionEntry.__dataclass_fields__
    assert "posting_outcome" not in RecognitionEntry.__dataclass_fields__


def test_the_posting_source_amendment_is_explicit():
    """`REVENUE_RECOGNITION` was added deliberately rather than reusing `MANUAL`, which would make
    every recognized period read as operator-entered in the one report where that is the question.

    Same precedent and same reasoning as M9.16 adding `PAYMENT`/`REFUND` (DL-0126)."""
    assert PostingSource.REVENUE_RECOGNITION.value == "revenue_recognition"
    source = Path("billing/foundation/commercial_ledger.py").read_text()
    assert "M9.18" in source and "DL-0129" in source


def test_no_second_accounting_period_model_exists():
    """M9.13 owns close semantics. This milestone reuses `LateEntryDisposition` and declares
    no second one."""
    for path in REVENUE_FILES:
        code = _code_only(path.read_text())
        banned = ("AccountingPeriod", "LateEntryDisposition", "close_digest", "PeriodState")
        for forbidden in banned:
            assert forbidden not in code, (path.name, forbidden)


# ── 2. identity: the basis digest is the reproducibility contract ──────────────


def test_the_basis_digest_covers_every_input_that_can_change_the_answer():
    """Change any one of them and the schedule identity changes. That is what stops a mutation."""
    base = dict(
        org_id=ORG, document_id="inv_1", revision=1, snapshot_digest="d" * 64,
        account_id="bac_1", amount_minor=100_000, currency="INR",
        service_start=Y24_START, service_end=Y24_END, method="straight_line",
        policy_code="std", policy_version=1, remainder="largest_remainder",
        granularity="month",
    )
    original = derive_basis_digest(**base)
    assert original == derive_basis_digest(**base)
    for field, changed in (
        ("revision", 2),
        ("snapshot_digest", "e" * 64),
        ("amount_minor", 100_001),
        ("service_start", Y24_START + 1),
        ("service_end", Y24_END + 1),
        ("method", "prorated"),
        ("policy_version", 2),
        ("remainder", "last_period"),
        ("granularity", "day"),
        ("currency", "USD"),
    ):
        assert derive_basis_digest(**{**base, field: changed}) != original, field


def test_the_basis_digest_contains_no_volatile_value():
    """A timestamp in here would make every re-derivation a new schedule and destroy the
    idempotency the digest exists to provide."""
    source = _code_only(_inspect.getsource(derive_basis_digest))
    for forbidden in ("time.time", "datetime", "now", "uuid", "random"):
        assert forbidden not in source, forbidden


def test_an_entry_id_is_derived_so_two_workers_cannot_both_be_period_three():
    assert derive_entry_id("rvs_1", 3) == derive_entry_id("rvs_1", 3)
    assert derive_entry_id("rvs_1", 3) != derive_entry_id("rvs_1", 4)
    assert derive_entry_id("rvs_1", 3) != derive_entry_id("rvs_2", 3)


def test_a_schedule_id_is_its_basis():
    basis = _basis()
    assert basis.schedule_id == derive_schedule_id(ORG, basis.digest)


# ── 3. the money: conservation is structural ───────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "method", "granularity"),
    [
        (100_000, RecognitionMethod.STRAIGHT_LINE, PeriodGranularity.MONTH),
        (100_001, RecognitionMethod.STRAIGHT_LINE, PeriodGranularity.MONTH),
        (7, RecognitionMethod.STRAIGHT_LINE, PeriodGranularity.MONTH),
        (1, RecognitionMethod.STRAIGHT_LINE, PeriodGranularity.MONTH),
        (100_000, RecognitionMethod.PRORATED, PeriodGranularity.MONTH),
        (99_999, RecognitionMethod.PRORATED, PeriodGranularity.MONTH),
        (13, RecognitionMethod.PRORATED, PeriodGranularity.MONTH),
        (100_000, RecognitionMethod.PRORATED, PeriodGranularity.DAY),
        (100_000, RecognitionMethod.POINT_IN_TIME, PeriodGranularity.NONE),
    ],
)
def test_conservation_holds_for_every_schedule_shape(amount, method, granularity):
    """**Not one minor unit appears or disappears.** Asserted by iteration, not by example."""
    basis = _basis(
        amount=Money(amount, INR), method=method, granularity=granularity
    )
    slices = RecognitionScheduler().build(basis)
    assert sum(s.amount.minor for s in slices) == amount, [s.amount.minor for s in slices]


@pytest.mark.parametrize(
    "remainder",
    [RemainderRule.LARGEST_REMAINDER, RemainderRule.FIRST_PERIOD, RemainderRule.LAST_PERIOD],
)
def test_every_remainder_rule_conserves(remainder):
    basis = _basis(amount=Money(100_001, INR), remainder=remainder)
    slices = RecognitionScheduler().build(basis)
    assert sum(s.amount.minor for s in slices) == 100_001


def test_the_remainder_goes_exactly_where_the_rule_says():
    """'Somewhere sensible' is how one paisa disappears between two reports."""
    first = RecognitionScheduler().build(
        _basis(amount=Money(1_000_005, INR), remainder=RemainderRule.FIRST_PERIOD)
    )
    last = RecognitionScheduler().build(
        _basis(amount=Money(1_000_005, INR), remainder=RemainderRule.LAST_PERIOD)
    )
    assert first[0].amount.minor > first[1].amount.minor
    assert last[-1].amount.minor > last[0].amount.minor


def test_allocation_is_stable_across_repeated_derivation():
    """Stable across process restart, worker ordering and database ordering — because it is a pure
    function of the weights and sorts by a total order, not by iteration order."""
    basis = _basis(amount=Money(99_991, INR), method=RecognitionMethod.PRORATED)
    runs = [tuple(s.amount.minor for s in RecognitionScheduler().build(basis)) for _ in range(5)]
    assert len(set(runs)) == 1


def test_no_floating_point_arithmetic_touches_money():
    """Integer minor units everywhere (ADR-0023 RS5). Asserted structurally over the AST."""
    for path in REVENUE_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                # True division would produce a float. Every split in this milestone uses
                # `Money.allocate`, and every internal division is floor division.
                raise AssertionError(f"{path.name}:{node.lineno} uses true division")


def test_a_zero_value_source_schedules_nothing():
    """A zero ledger posting is noise in a financial report; an empty schedule says it better."""
    assert RecognitionScheduler().build(_basis(amount=Money(0, INR))) == ()


def test_the_conservation_check_runs_at_construction():
    """A future allocator change that lost a paisa fails here, not in a quarterly report."""
    basis = _basis()
    slices = RecognitionScheduler().build(basis)
    broken = tuple(replace(s, amount=Money(s.amount.minor - 1, INR)) for s in slices)
    assert RecognitionValidator().validate_conservation(basis, slices).is_valid
    assert not RecognitionValidator().validate_conservation(basis, broken).is_valid


# ── 4. time: partial periods, month ends, leap years, UTC ─────────────────────


def test_a_leap_february_is_twenty_nine_days_without_a_days_in_month_table():
    """Calendar arithmetic is `datetime`'s. A second table would be a second answer."""
    periods = period_boundaries(
        BillingPeriod(start=Y24_START, end=Y24_END), PeriodGranularity.MONTH
    )
    assert len(periods) == 12
    assert periods[1].duration == 29 * 86_400.0  # February 2024
    assert periods[0].duration == 31 * 86_400.0  # January
    assert periods[3].duration == 30 * 86_400.0  # April


def test_a_non_leap_february_is_twenty_eight_days():
    y25_start, y25_end = 1_735_689_600.0, 1_767_225_600.0
    periods = period_boundaries(
        BillingPeriod(start=y25_start, end=y25_end), PeriodGranularity.MONTH
    )
    assert periods[1].duration == 28 * 86_400.0


def test_a_mid_month_start_produces_a_genuinely_partial_first_period():
    """Clipped to the window, so proration weights it by the time actually served."""
    mid_jan = Y24_START + 14 * 86_400.0
    periods = period_boundaries(
        BillingPeriod(start=mid_jan, end=Y24_START + 90 * 86_400.0), PeriodGranularity.MONTH
    )
    assert periods[0].start == mid_jan
    assert periods[0].duration == 17 * 86_400.0  # 15–31 January inclusive of the 15th


def test_periods_are_half_open_so_no_instant_is_counted_twice():
    """A boundary instant belongs to exactly one period. That is what stops double counting."""
    periods = period_boundaries(
        BillingPeriod(start=Y24_START, end=Y24_END), PeriodGranularity.MONTH
    )
    for earlier, later in zip(periods[:-1], periods[1:], strict=True):
        assert earlier.end == later.start
        assert earlier.contains(earlier.end) is False
        assert later.contains(later.start) is True
    assert sum(p.duration for p in periods) == Y24_END - Y24_START


def test_a_year_end_boundary_does_not_produce_a_thirteenth_period():
    periods = period_boundaries(
        BillingPeriod(start=Y24_START, end=Y24_END), PeriodGranularity.MONTH
    )
    assert len(periods) == 12


def test_proration_across_a_leap_year_gives_february_less_than_january():
    """The whole reason to weight by seconds rather than by period count."""
    slices = RecognitionScheduler().build(
        _basis(amount=Money(1_000_000, INR), method=RecognitionMethod.PRORATED)
    )
    assert slices[1].amount.minor < slices[0].amount.minor
    assert sum(s.amount.minor for s in slices) == 1_000_000


def test_the_civil_date_arithmetic_matches_the_standard_library_exactly():
    """**The calendar is checked, not trusted.**

    `datetime` is not importable in the Foundation — the architecture test bans it outright so the
    domain cannot read an ambient clock, and that ban was **not weakened** for this milestone.
    Instead the calendar is pure integer arithmetic (`civil_from_days`), and it is proven here
    against the standard library across ninety years of days, in both directions.

    A recognition schedule is only as trustworthy as its month boundaries, and "we wrote the
    algorithm carefully" is not evidence."""
    from datetime import UTC, datetime

    for day_number in range(-11_000, 22_000):  # ~1939 through ~2030
        year, month, day = civil_from_days(day_number)
        reference = datetime.fromtimestamp(day_number * 86_400, tz=UTC)
        assert (year, month, day) == (reference.year, reference.month, reference.day), day_number
        assert days_from_civil(year, month, day) == day_number, day_number


def test_the_foundation_still_imports_no_datetime():
    """The ban this milestone declined to weaken, asserted at the module that most wanted to."""
    tree = ast.parse(Path("billing/foundation/commercial_revenue.py").read_text())
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.append(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.extend(a.name.split(".")[0] for a in node.names)
        for root in roots:
            assert root not in {"datetime", "calendar", "time"}, root


def test_the_calendar_is_utc_and_says_so():
    source = Path("billing/foundation/commercial_revenue.py").read_text()
    assert "**UTC, always**" in source
    assert "SECONDS_PER_DAY = 86_400" in source


# ── 5. policy versioning: history cannot be restated ──────────────────────────


def test_a_policy_change_cannot_rewrite_a_historical_schedule():
    """**The mandated regression.** Build under v1, publish v2, re-derive — v1 is unchanged.

    The March schedule and the April one are *different rows*, because the policy version is inside
    the digest. There is no path by which the first becomes the second."""
    v1 = _basis(policy_version=1)
    v2 = _basis(policy_version=2, method=RecognitionMethod.POINT_IN_TIME)
    first = RecognitionScheduler().build(v1)
    assert len(first) == 12
    assert v1.schedule_id != v2.schedule_id
    assert tuple(s.amount.minor for s in RecognitionScheduler().build(v1)) == tuple(
        s.amount.minor for s in first
    )


def test_a_schedule_reads_the_policy_version_it_was_bound_to_not_the_latest():
    """`bound_policy` is the whole of reproducibility in one method, and `latest` appears exactly
    once in the Application — on the path that binds a **new** basis."""
    source = _code_only(Path("billing/application/revenue_engine.py").read_text())
    assert source.count("policies.latest(") == 1
    assert "def bound_policy" not in source or "policy_id" in source


def test_a_policy_version_is_immutable_by_contract():
    from billing.foundation import RecognitionPolicyRepository

    assert not hasattr(RecognitionPolicyRepository, "update")
    assert not hasattr(RecognitionPolicyRepository, "delete")


def test_an_unpinned_basis_is_refused():
    """A basis with no snapshot digest or no policy version cannot be reproduced, so it is not
    allowed to exist."""
    with pytest.raises(BillingValidationError):
        RevenueFactory().schedule(_basis(snapshot_digest=""))
    with pytest.raises(BillingValidationError):
        RevenueFactory().schedule(_basis(policy_version=0))


def test_a_withdrawn_policy_still_explains_an_old_schedule(wired):
    persistent, account = wired
    app = persistent.application
    _, schedule, _ = _active(app, account)
    policies = app.queries.dispatch(ListRecognitionPolicies(**KW))
    app.commands.dispatch(
        DeactivateRecognitionPolicy(**KW, policy_id=policies[0].id, reason="superseded")
    )
    explanation = app.queries.dispatch(ExplainRecognition(**KW, schedule_id=schedule.id))
    assert explanation.policy_version == 1
    assert app.queries.dispatch(GetRecognitionPolicyVersions(**KW, code="std"))


# ── 6. lifecycle: recognized revenue is never rewound ─────────────────────────


def test_a_fully_recognized_schedule_never_returns_to_scheduled():
    """`FULLY_RECOGNIZED → SCHEDULED` is the transition that would let a policy edit restate a
    filed number."""
    assert REVENUE_SCHEDULE_LIFECYCLE.triggers_from(ScheduleState.FULLY_RECOGNIZED) == frozenset()
    for state in TERMINAL_SCHEDULE_STATES:
        assert REVENUE_SCHEDULE_LIFECYCLE.triggers_from(state) == frozenset()


def test_a_partially_recognized_schedule_cannot_be_cancelled():
    """Once revenue is earned the way back is a reversal — a new fact, not an erasure."""
    assert (
        REVENUE_SCHEDULE_LIFECYCLE.get(
            ScheduleState.PARTIALLY_RECOGNIZED, ScheduleTrigger.CANCEL
        )
        is None
    )
    assert (
        REVENUE_SCHEDULE_LIFECYCLE.get(
            ScheduleState.PARTIALLY_RECOGNIZED, ScheduleTrigger.SUPERSEDE
        )
        is not None
    )


def test_a_posted_entry_is_never_rewound():
    """A financial fact that reached the Ledger is the Ledger's."""
    assert REVENUE_ENTRY_LIFECYCLE.get(EntryState.POSTED, EntryTrigger.RECOGNIZE) is None
    assert REVENUE_ENTRY_LIFECYCLE.triggers_from(EntryState.REVERSED) == frozenset()
    assert REVENUE_ENTRY_LIFECYCLE.get(EntryState.UNPOSTED, EntryTrigger.POST) is not None


def test_every_published_schedule_state_is_reachable():
    """The M9.16-R1 P-5 lesson, applied before it can become a finding."""
    seen, frontier = {ScheduleState.DRAFT}, [ScheduleState.DRAFT]
    while frontier:
        state = frontier.pop()
        for trigger in REVENUE_SCHEDULE_LIFECYCLE.triggers_from(state):
            target = REVENUE_SCHEDULE_LIFECYCLE.get(state, trigger).target
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(ScheduleState), f"unreachable: {set(ScheduleState) - seen}"


def test_every_published_entry_state_is_reachable():
    seen, frontier = {EntryState.PENDING}, [EntryState.PENDING]
    while frontier:
        state = frontier.pop()
        for trigger in REVENUE_ENTRY_LIFECYCLE.triggers_from(state):
            target = REVENUE_ENTRY_LIFECYCLE.get(state, trigger).target
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(EntryState), f"unreachable: {set(EntryState) - seen}"


def test_recognized_states_include_unposted():
    """An unposted recognition **has** been recognized. Excluding it would understate revenue and
    hide the repair queue in the same number."""
    assert EntryState.UNPOSTED in RECOGNIZED_ENTRY_STATES
    assert EntryState.POSTED in RECOGNIZED_ENTRY_STATES
    # **Gross of corrections.** A reversed entry was recognized — a ledger credit exists for it —
    # and the reversal carries its own negative amount. Excluding it here as well would subtract
    # the correction twice and report deferred revenue greater than the basis.
    assert EntryState.REVERSED in RECOGNIZED_ENTRY_STATES
    assert EntryState.SKIPPED not in RECOGNIZED_ENTRY_STATES
    assert EntryState.PENDING not in RECOGNIZED_ENTRY_STATES


# ── 7. paid is not earned ─────────────────────────────────────────────────────


def test_recognition_reads_no_payment_and_no_collection_state():
    """**The boundary that keeps cash and revenue apart**, asserted as an import scan."""
    for path in REVENUE_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            for name in names:
                root = name.split(".")[0].lower()
                if root in {"collections", "dataclasses", "abc", "enum", "typing"}:
                    continue  # stdlib; `collections.abc` is not the Collections domain
                for forbidden in ("payment", "collection", "dunning"):
                    assert forbidden not in name.lower(), (path.name, name)


def test_no_recognition_module_mentions_a_payment_state(wired):
    for path in REVENUE_FILES:
        code = _code_only(path.read_text())
        for forbidden in (
            "PaymentIntent", "PaymentAttempt", "AttemptState", "IntentState",
            "CollectionCase", "DunningState", "is_paid", "amount_due",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_an_unpaid_invoice_can_be_fully_recognized(wired):
    """Revenue is earned by delivering the service, not by being paid for it."""
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    document, schedule, entries = _active(
        app, account, method=RecognitionMethod.POINT_IN_TIME
    )
    assert len(entries) == 1
    receipt = app.commands.dispatch(
        RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1)
    )
    assert receipt.recognized and receipt.posted
    position = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert position.is_fully_recognized
    assert position.deferred.minor == 0
    # Nothing was ever paid: no payment intent exists for this document.
    assert document.state == "issued"


def test_a_paid_invoice_stays_deferred_until_its_periods_mature(wired):
    """The other direction. Marking a document paid changes nothing about what is earned."""
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    document, schedule, entries = _active(app, account)
    app.commands.dispatch(inv.MarkInvoicePaid(**KW, document_id=document.id))

    position = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert position.recognized.minor == 0
    assert position.deferred.minor == position.total.minor
    assert not position.is_fully_recognized


# ── 8. double recognition is structurally prevented ───────────────────────────


def test_recognizing_the_same_period_twice_is_refused(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, _, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    with pytest.raises(BillingConflict, match="already"):
        app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))


def test_scheduling_the_same_basis_twice_is_one_schedule(wired):
    persistent, account = wired
    app = persistent.application
    document = _issued(app, account)
    first = _schedule(app, document.id)
    second = _schedule(app, document.id)
    assert first.id == second.id
    entries = app.queries.dispatch(ListRecognitionEntries(**KW, schedule_id=first.id))
    assert len(entries) == first.entries_planned == 12


def test_a_period_that_has_not_matured_is_refused(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, _, entries = _active(app, account)
    with pytest.raises(BillingConflict, match="does not mature"):
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entries[-1].id, at=Y24_START + 1)
        )


def test_the_recognition_move_is_a_database_constraint_not_an_application_check():
    """`recognize` is a conditional UPDATE. An `if already_recognized` has a window, and the window
    is a double-counted month of revenue."""
    source = _inspect.getsource(
        __import__(
            "billing.persistence.revenue_repositories", fromlist=["x"]
        ).SqlRecognitionEntryRepository.recognize
    )
    assert ".update()" in source and "state == expected_state" in source
    assert "rowcount != 1" in source


# ── 9. corrections are new facts ──────────────────────────────────────────────


def test_a_reversal_leaves_the_original_entry_readable(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    receipt = app.commands.dispatch(
        RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1)
    )
    original_amount = receipt.entry.amount.minor

    adjustment = app.commands.dispatch(
        ReverseRecognition(**KW, entry_id=entries[0].id, reason="billed in error", request_key="r1")
    )
    assert adjustment.amount.minor == -original_amount
    assert adjustment.document_id == schedule.basis.document_id
    assert adjustment.policy_version == 1
    stored = app.queries.dispatch(GetRecognitionEntry(**KW, entry_id=entries[0].id))
    assert stored.amount.minor == original_amount, "the original entry was edited"
    assert stored.state == "reversed"


def test_a_reversal_conserves_the_position(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    app.commands.dispatch(
        ReverseRecognition(**KW, entry_id=entries[0].id, reason="error", request_key="r1")
    )
    p = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert p.recognized.minor + p.adjusted.minor + p.deferred.minor == p.total.minor
    assert p.adjusted.minor == -p.recognized.minor


def test_reversing_twice_is_refused(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, _, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    app.commands.dispatch(
        ReverseRecognition(**KW, entry_id=entries[0].id, reason="error", request_key="r1")
    )
    with pytest.raises(BillingConflict, match="already been reversed"):
        app.commands.dispatch(
            ReverseRecognition(**KW, entry_id=entries[0].id, reason="again", request_key="r2")
        )


def test_reversing_an_unrecognized_period_is_refused(wired):
    persistent, account = wired
    app = persistent.application
    _, _, entries = _active(app, account)
    with pytest.raises(BillingConflict, match="only a recognized amount"):
        app.commands.dispatch(
            ReverseRecognition(**KW, entry_id=entries[0].id, reason="x", request_key="r1")
        )


def test_supersession_keeps_what_was_already_recognized(wired):
    """A document revised mid-term does not undo what was earned."""
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    earned = app.queries.dispatch(
        GetRecognitionPosition(**KW, schedule_id=schedule.id)
    ).recognized.minor

    superseded = app.commands.dispatch(
        SupersedeRevenueSchedule(**KW, schedule_id=schedule.id, reason="document revised")
    )
    assert superseded.state == "superseded"
    after = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert after.recognized.minor == earned
    remaining = app.queries.dispatch(ListRecognitionEntries(**KW, schedule_id=schedule.id))
    assert all(e.state in ("recognized", "posted", "skipped") for e in remaining)


def test_cancelling_a_schedule_with_earned_revenue_is_refused(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    with pytest.raises(BillingConflict, match="reversal"):
        app.commands.dispatch(
            CancelRevenueSchedule(**KW, schedule_id=schedule.id, reason="too late")
        )


# ── 10. the ledger boundary ───────────────────────────────────────────────────


def test_recognition_writes_to_no_other_domains_repositories():
    """Every persistence call in the Application must reach `self._repos.*`."""
    tree = ast.parse(Path("billing/application/revenue_engine.py").read_text())
    writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {
            "add", "append", "save", "create", "advance", "recognize", "settle",
            "deactivate", "mark_state", "upsert", "post",
        }:
            continue
        chain = node.func.value
        parts: list[str] = []
        while isinstance(chain, ast.Attribute):
            parts.append(chain.attr)
            chain = chain.value
        if not isinstance(chain, ast.Name):
            continue
        parts.append(chain.id)
        target = ".".join(reversed(parts))
        if target.startswith("self."):
            writes.append(target)
    assert writes, "the scan found no writes at all — it is not testing anything"
    for target in writes:
        assert target.startswith("self._repos."), target


def test_recognition_imports_no_ledger_repository_or_balance():
    source = Path("billing/application/revenue_engine.py").read_text()
    for forbidden in (
        "LedgerRepositories", "LedgerRepository", "LedgerBalance", "AccountBalance",
        "CreditRepository", "billing.persistence",
    ):
        assert forbidden not in source, forbidden


def test_the_only_ledger_write_is_the_ledger_s_own_command():
    source = _code_only(Path("billing/application/revenue_engine.py").read_text())
    assert "PostJournalEntry(" in source
    assert source.count("self._ledger_post(") == 2  # recognition and its correction


def test_every_posting_names_revenue_recognition_as_its_source():
    source = _code_only(Path("billing/application/revenue_engine.py").read_text())
    assert source.count("PostingSource.REVENUE_RECOGNITION") == 2
    assert "PostingSource.MANUAL" not in source


def test_a_ledger_refusal_keeps_the_recognition_and_names_the_repair(wired):
    """**No open accounting period.** The decision is a fact and must not be lost."""
    persistent, account = wired
    app = persistent.application  # deliberately no open 2024 period
    _, schedule, entries = _active(app, account)
    receipt = app.commands.dispatch(
        RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1)
    )
    assert receipt.recognized is True
    assert receipt.posted is False
    assert receipt.entry.state == "unposted"
    queue = app.queries.dispatch(UnpostedRecognitions(**KW))
    assert [e.id for e in queue] == [entries[0].id]
    position = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert position.has_unposted and position.recognized.minor > 0


def test_the_repair_posts_and_is_idempotent(wired):
    persistent, account = wired
    app = persistent.application
    _, _, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    _open_year(app, account)
    repaired = app.commands.dispatch(PostRecognitionToLedger(**KW, entry_id=entries[0].id))
    assert repaired.posted and repaired.entry.state == "posted"
    assert repaired.ledger_entry_id
    with pytest.raises(BillingConflict, match="only an unposted"):
        app.commands.dispatch(PostRecognitionToLedger(**KW, entry_id=entries[0].id))


def test_only_a_billing_error_becomes_an_unposted_recognition():
    """A considered refusal is `UNPOSTED`; **a bug propagates**. The M9.16-R1 P-6 correction,
    applied here from the start."""
    from billing.application.revenue_engine import RevenueUseCases

    source = _code_only(textwrap.dedent(_inspect.getsource(RevenueUseCases._post)))
    assert "except BillingError as refusal:" in source
    assert "except Exception" not in source


def test_recognition_maintains_no_balance_column():
    """Deferred revenue is derived. A stored balance is a second authority on a Ledger figure."""
    from billing.persistence.revenue_models import (
        BillingRecognitionEntryRow,
        BillingRevenueScheduleRow,
    )

    for table in (BillingRevenueScheduleRow, BillingRecognitionEntryRow):
        columns = {c.name for c in table.__table__.columns}
        for banned in (
            "deferred_balance", "deferred_minor", "recognized_total", "recognized_minor",
            "balance", "outstanding",
        ):
            assert banned not in columns, (table.__tablename__, banned)


def test_deferred_is_computed_from_the_facts(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    for index in range(3):
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entries[index].id, at=entries[index].recognize_on + 1)
        )
    position = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    expected = sum(e.amount.minor for e in entries[:3])
    assert position.recognized.minor == expected
    assert position.deferred.minor == position.total.minor - expected
    summary = app.queries.dispatch(DeferredRevenueSummary(**KW, account_id=account.id))
    assert summary.deferred.minor == position.deferred.minor


# ── 11. the adversarial hunt ──────────────────────────────────────────────────


def test_hunt_recognizing_from_a_draft_document_is_refused(wired):
    """**Hunt 5.** Only an issued document states an amount that can be recognized."""
    persistent, account = wired
    app = persistent.application
    draft = inv._prepare(app, account)
    with pytest.raises(BillingConflict, match="not been issued"):
        _schedule(app, draft.id)


def test_hunt_an_unknown_document_is_a_not_found(wired):
    persistent, account = wired
    with pytest.raises(BillingNotFound):
        _schedule(persistent.application, "inv_nope")


def test_hunt_a_document_in_another_org_is_invisible(wired):
    """**Hunt 28.** Cross-org source document id."""
    persistent, account = wired
    app = persistent.application
    document = _issued(app, account)
    with pytest.raises(BillingNotFound):
        app.commands.dispatch(
            ScheduleRevenue(
                actor_id=ACTOR, org_id="org_other", document_id=document.id,
                service_start=Y24_START, service_end=Y24_END, policy_code="std",
            )
        )


def test_hunt_scheduling_with_no_published_policy_is_refused(wired):
    """**Hunt 29.** A missing policy is a configuration error, not an invented default."""
    persistent, account = wired
    app = persistent.application
    document = _issued(app, account)
    with pytest.raises(BillingConflict, match="no active recognition policy"):
        _schedule(app, document.id, policy_code="does-not-exist")


def test_hunt_a_document_revision_produces_a_different_schedule(wired):
    """**Hunt 4.** A revision is a new basis, not a mutation of the old one."""
    v1 = _basis(revision=1)
    v2 = _basis(revision=2, snapshot_digest="e" * 64)
    assert v1.schedule_id != v2.schedule_id


def test_hunt_a_far_future_instant_does_not_recognize_the_whole_schedule(wired):
    """One command recognizes one period, however far the clock has moved."""
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=10**12))
    stored = app.queries.dispatch(GetRevenueSchedule(**KW, schedule_id=schedule.id))
    assert stored.entries_recognized == 1
    assert stored.state == "partially_recognized"


def test_hunt_recognizing_every_period_completes_the_schedule(wired):
    """**Hunt 17.** And the totals reconcile exactly."""
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account)
    for entry in entries:
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entry.id, at=entry.recognize_on + 1)
        )
    stored = app.queries.dispatch(GetRevenueSchedule(**KW, schedule_id=schedule.id))
    assert stored.state == "fully_recognized"
    position = app.queries.dispatch(GetRecognitionPosition(**KW, schedule_id=schedule.id))
    assert position.is_fully_recognized
    assert position.deferred.minor == 0
    assert position.recognized.minor == position.total.minor


def test_hunt_a_completed_schedule_accepts_no_further_recognition(wired):
    persistent, account = wired
    app = persistent.application
    _open_year(app, account)
    _, schedule, entries = _active(app, account, method=RecognitionMethod.POINT_IN_TIME)
    app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 1))
    with pytest.raises(BillingConflict):
        app.commands.dispatch(RecognizeRevenue(**KW, entry_id=entries[0].id, at=Y24_START + 2))


def test_hunt_a_skipped_period_cannot_be_recognized(wired):
    persistent, account = wired
    app = persistent.application
    _, _, entries = _active(app, account)
    app.commands.dispatch(
        SkipRecognitionEntry(**KW, entry_id=entries[-1].id, reason="not required")
    )
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            RecognizeRevenue(**KW, entry_id=entries[-1].id, at=10**12)
        )


def test_hunt_the_supersession_trail_is_readable(wired):
    """**Hunt 8.** Every schedule from one document, oldest first."""
    persistent, account = wired
    app = persistent.application
    document, schedule, _ = _active(app, account)
    app.commands.dispatch(
        SupersedeRevenueSchedule(**KW, schedule_id=schedule.id, reason="revised")
    )
    trail = app.queries.dispatch(GetSchedulesForDocument(**KW, document_id=document.id))
    assert [s.id for s in trail] == [schedule.id]
    assert trail[0].state == "superseded"


def test_hunt_an_unknown_entry_is_a_not_found(wired):
    persistent, account = wired
    with pytest.raises(BillingNotFound):
        persistent.application.commands.dispatch(RecognizeRevenue(**KW, entry_id="rve_nope"))


def test_hunt_a_negative_basis_is_refused():
    """**Hunt 19.** A reduction is an adjustment against an existing schedule, not a schedule."""
    with pytest.raises(BillingValidationError):
        RevenueFactory().schedule(_basis(amount=Money(-100, INR)))


def test_hunt_a_milestone_schedule_with_no_milestones_is_refused():
    with pytest.raises(BillingValidationError):
        RevenueFactory().schedule(
            _basis(method=RecognitionMethod.SCHEDULED, milestones=())
        )


def test_hunt_milestone_recognition_conserves_and_orders():
    """Milestones are still *allocated*, so conservation is not the caller's problem."""
    basis = _basis(
        method=RecognitionMethod.SCHEDULED,
        amount=Money(100_000, INR),
        milestones=((Y24_START + 100, 30), (Y24_START + 50, 20), (Y24_START + 200, 50)),
    )
    slices = RecognitionScheduler().build(basis)
    assert sum(s.amount.minor for s in slices) == 100_000
    assert [s.recognize_on for s in slices] == sorted(s.recognize_on for s in slices)


def test_hunt_changed_current_pricing_does_not_affect_a_schedule(wired):
    """**Hunt 30.** The basis was copied from an issued snapshot; live state is never consulted."""
    persistent, account = wired
    app = persistent.application
    _, schedule, entries = _active(app, account)
    before = [e.amount.minor for e in entries]
    # Publish an entirely new price book at ten times the price, and a new pricing version with
    # it. Nothing about the already-derived schedule may move.
    app.commands.dispatch(
        inv.CreatePriceBook(**KW, code="std2", display_name="Standard v2", currency=INR)
    )
    app.commands.dispatch(
        inv.CreatePrice(
            **KW, book_code="std2", code="platform_fee",
            shape=inv.PriceShape.FLAT_RECURRING, amount=Money(9_999_00, INR),
            interval=inv.BillingInterval.MONTHLY,
        )
    )
    app.commands.dispatch(inv.PublishPricingBook(**KW, code="std2"))
    app.commands.dispatch(inv.PublishPricing(**KW, notes="v2"))
    after = app.queries.dispatch(ListRecognitionEntries(**KW, schedule_id=schedule.id))
    assert [e.amount.minor for e in after] == before


# ── 12. concurrency, with real connections ────────────────────────────────────


class _TaggedIds(SequentialIdGenerator):
    def __init__(self, tag: str) -> None:
        super().__init__()
        self._tag = tag

    def new(self, prefix: str) -> str:
        return f"{super().new(prefix)}_{self._tag}"


def _two_sessions(tmp_path, name: str = "revenue.db"):
    """Two Applications over one file-backed database, each with its own connection.

    Two *connections* rather than two threads: a thread schedule that does not actually interleave
    produces a green test that proved nothing."""
    url = f"sqlite:///{tmp_path / name}"
    first = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("a"),
        clock=FixedClock(1_000.0, step=0.0),
    )
    second = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("b"),
        clock=FixedClock(1_000.0, step=0.0), create_all=False,
    )
    assert first.database is not second.database
    return first, second


def _seeded(persistent):
    """A policy, a schedule and its entries, built directly from a basis (no invoice needed)."""
    app = persistent.application
    app.commands.dispatch(
        PublishRecognitionPolicy(**KW, code="std", version=1)
    )
    policy = app.queries.dispatch(ListRecognitionPolicies(**KW))[0]
    factory = RevenueFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    basis = _basis(policy_id=policy.id, policy_code="std", policy_version=1)
    schedule, slices = factory.schedule(basis, at=1_000.0)
    repos = persistent.application.context.revenue_engine
    stored, _ = repos.schedules.create(schedule)
    for slice_ in slices:
        repos.entries.append(factory.entry(stored, slice_))
    persistent.database.session.commit()
    return stored


def test_two_workers_cannot_create_two_schedules_for_one_basis(tmp_path):
    """**Concurrency 1.** Arbitrated by the derived primary key, across two connections."""
    first, second = _two_sessions(tmp_path)
    a = _seeded(first)
    second.database.session.expire_all()
    factory = RevenueFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0, step=0.0))
    schedule, _ = factory.schedule(
        _basis(policy_id=a.basis.policy_id, policy_code="std", policy_version=1), at=1_000.0
    )
    stored, is_new = second.application.context.revenue_engine.schedules.create(schedule)
    assert stored.id == a.id
    assert is_new is False


def test_two_workers_cannot_recognize_the_same_period(tmp_path):
    """**Concurrency 2.** The conditional `pending → recognized` picks exactly one winner, and the
    loser learns *before* it posts anything to the Ledger."""
    first, second = _two_sessions(tmp_path)
    schedule = _seeded(first)
    entries = first.application.context.revenue_engine.entries.for_schedule(ORG, schedule.id)
    target = entries[0]
    second.database.session.expire_all()

    from billing.foundation import EntryLifecycleService

    lifecycle = EntryLifecycleService()
    winner = lifecycle.apply(target, EntryTrigger.RECOGNIZE, at=Y24_START + 1)
    first.application.context.revenue_engine.entries.recognize(
        winner, expected_state=EntryState.PENDING.value
    )
    first.database.session.commit()
    second.database.session.expire_all()

    loser = lifecycle.apply(target, EntryTrigger.RECOGNIZE, at=Y24_START + 1)
    with pytest.raises(ScheduleStale, match="another worker recognized this period"):
        second.application.context.revenue_engine.entries.recognize(
            loser, expected_state=EntryState.PENDING.value
        )


def test_a_stale_worker_cannot_advance_a_superseded_schedule(tmp_path):
    """**Concurrency 5.** Compare-and-set on `version`."""
    first, second = _two_sessions(tmp_path)
    schedule = _seeded(first)
    stale = second.application.context.revenue_engine.schedules.get(ORG, schedule.id)

    lifecycle = ScheduleLifecycleService()
    moved = lifecycle.apply(schedule, ScheduleTrigger.SCHEDULE, at=1_001.0)
    moved = lifecycle.apply(moved, ScheduleTrigger.SUPERSEDE, at=1_002.0, reason="revised")
    first.application.context.revenue_engine.schedules.advance(
        moved, expected_version=schedule.version
    )
    first.database.session.commit()
    second.database.session.expire_all()

    repo = second.application.context.revenue_engine.schedules
    with pytest.raises(ScheduleStale, match="re-read and re-decide"):
        repo.advance(
            lifecycle.apply(stale, ScheduleTrigger.SCHEDULE, at=1_003.0),
            expected_version=stale.version,
        )
    assert repo.get(ORG, schedule.id).state is ScheduleState.SUPERSEDED


def test_a_superseded_schedule_cannot_be_recognized_against(tmp_path):
    """**Concurrency 8.** Two defences, either alone sufficient: the FSM has no edge out of
    `SUPERSEDED`, and the version has moved."""
    first, _ = _two_sessions(tmp_path)
    schedule = _seeded(first)
    lifecycle = ScheduleLifecycleService()
    moved = lifecycle.apply(schedule, ScheduleTrigger.SCHEDULE, at=1_001.0)
    superseded = lifecycle.apply(moved, ScheduleTrigger.SUPERSEDE, at=1_002.0)
    with pytest.raises(InvalidBillingTransition):
        lifecycle.apply(superseded, ScheduleTrigger.RECOGNIZE, at=1_003.0)


def test_restart_durability(tmp_path):
    """A second Application over the same file reads every schedule and entry."""
    first, _ = _two_sessions(tmp_path, "restart.db")
    schedule = _seeded(first)
    first.database.close()

    second = build_persistent_billing_application(
        url=f"sqlite:///{tmp_path / 'restart.db'}", authorizer=AllowAllAuthorizer(),
        ids=_TaggedIds("c"), clock=FixedClock(1_000.0, step=0.0), create_all=False,
    )
    stored = second.application.queries.dispatch(
        GetRevenueSchedule(**KW, schedule_id=schedule.id)
    )
    assert stored.id == schedule.id
    assert stored.basis.basis_digest == schedule.basis.digest
    entries = second.application.queries.dispatch(
        ListRecognitionEntries(**KW, schedule_id=schedule.id)
    )
    assert len(entries) == 12
    assert sum(e.amount.minor for e in entries) == schedule.basis.amount.minor


# ── 13. the HTTP surface ──────────────────────────────────────────────────────


def _auth(user_id: str = ACTOR, org_id: str = ORG) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + epic8_encode({"sub": user_id, "org": org_id}, SECRET, ttl_seconds=3600)
    }


@pytest.fixture
def client():
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET)
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0, step=0.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"
R = f"{PREFIX}/revenue"


def test_revenue_routes_require_identity_and_an_org(client):
    assert client.get(f"{R}/schedules").status_code == 401
    assert client.post(f"{R}/policies", json={}).status_code in (401, 422)
    assert client.get(f"{R}/deferred").status_code == 401


def test_the_revenue_prefix_collides_with_nothing(client):
    spec = client.get("/openapi.json").json()
    revenue = {p for p in spec["paths"] if p.startswith(f"{PREFIX}/revenue")}
    others = {p for p in spec["paths"] if not p.startswith(f"{PREFIX}/revenue")}
    assert revenue and not revenue & others


def test_there_is_no_delete_on_the_revenue_surface(client):
    """Financial history is corrected by a new fact, never removed."""
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if path.startswith(f"{PREFIX}/revenue"):
            assert "delete" not in operations, path


def test_publishing_a_policy_and_reading_it_back(client):
    created = client.post(
        f"{R}/policies",
        json={"code": "http", "version": 1, "method": "straight_line", "granularity": "month"},
        headers=_auth(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["method"] == "straight_line"
    listed = client.get(f"{R}/policies", headers=_auth())
    assert listed.status_code == 200 and listed.json()
    versions = client.get(f"{R}/policies/http/versions", headers=_auth())
    assert versions.status_code == 200 and versions.json()[0]["version"] == 1


def test_the_schedule_body_takes_a_document_never_an_amount(client):
    """A body carrying an amount would let its caller recognize a figure nobody billed."""
    spec = client.get("/openapi.json").json()
    body = spec["components"]["schemas"]["ScheduleRevenueBody"]["properties"]
    assert "document_id" in body
    for banned in ("amount", "amount_minor", "total", "currency"):
        assert banned not in body, banned


def test_no_stored_response_carries_a_deferred_balance(client):
    """`deferred` appears only on the *position* and *summary* responses, which are derived."""
    spec = client.get("/openapi.json").json()
    schedule = spec["components"]["schemas"]["RevenueScheduleResponse"]["properties"]
    entry = spec["components"]["schemas"]["RecognitionEntryResponse"]["properties"]
    for shape in (schedule, entry):
        for banned in ("deferred", "deferred_balance", "recognized_total", "balance"):
            assert banned not in shape, banned
    assert "deferred" in spec["components"]["schemas"]["RecognitionPositionResponse"]["properties"]


def test_controllers_decide_nothing():
    """AST: no router handler allocates, compares an amount, or reaches a ledger repository."""
    code = _code_only(Path("billing/customer/routers/revenue_engine.py").read_text())
    for forbidden in (
        "allocate", "Money(", "// ", "sum(", "PostJournalEntry", "RecognitionScheduler",
        "period_boundaries", "_repos", "amount_minor -",
    ):
        assert forbidden not in code, forbidden


# ── 14. Studio is a consumer ──────────────────────────────────────────────────


STUDIO = Path("billing/customer/studio/revenue.html")


def test_the_studio_stores_nothing():
    text = _html_code_only(STUDIO.read_text())
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in text, forbidden


def test_the_studio_allocates_nothing_and_derives_no_deferred_figure():
    """Every figure on screen is the server's. A client that divided a total by twelve would
    disagree with the ledger in the last month of every schedule."""
    text = _html_code_only(STUDIO.read_text())
    for forbidden in (
        "minor /", "minor *", "minor -", "minor +", "/ 12", "allocate", "reduce(",
    ):
        assert forbidden not in text, forbidden


def test_the_studio_calls_no_provider_and_names_no_vendor():
    text = _html_code_only(STUDIO.read_text()).lower()
    for forbidden in ("stripe", "razorpay", "http://", "https://", "provider"):
        assert forbidden not in text, forbidden


def test_the_studio_takes_its_buttons_from_the_servers_allowed_triggers():
    text = STUDIO.read_text()
    assert "allowed_triggers" in text
    assert 'allowed.has("supersede")' in text and 'allowed.has("cancel")' in text


def test_the_studio_reports_recognized_and_posted_separately():
    """Reporting both as success would show clean books while the repair queue grew."""
    text = STUDIO.read_text()
    assert "recognized but NOT posted" in text


# ── 15. architecture ──────────────────────────────────────────────────────────


def test_recognition_foundation_is_unaware_persistence_exists():
    for name in ("commercial_revenue", "revenue_rules", "revenue_factories"):
        source = Path(f"billing/foundation/{name}.py").read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "session.", "__tablename__"):
            assert forbidden not in source, (name, forbidden)


def test_recognition_computes_no_price_no_rate_no_tax_and_no_invoice_total():
    for path in REVENUE_FILES:
        code = _code_only(path.read_text())
        for forbidden in (
            "PriceBook", "PriceResolver", "RatingRun", "rate_basis_points", "TaxRate",
            "TaxTreatment", "cgst", "sgst", "igst", "InvoiceTotalsService", "discount",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_recognition_contains_no_scheduler():
    for name in ("commercial_revenue", "revenue_rules", "revenue_factories"):
        source = _code_only(Path(f"billing/foundation/{name}.py").read_text()).lower()
        for forbidden in (
            "import threading", "import asyncio", "time.sleep", "while true",
            "def run(", "def run_scheduler", "schedule.every",
        ):
            assert forbidden not in source, (name, forbidden)


def test_the_foundation_reads_no_clock_in_its_rules():
    source = _code_only(Path("billing/foundation/revenue_rules.py").read_text())
    for forbidden in ("time.time", "datetime.now", "SystemClock()"):
        assert forbidden not in source, forbidden


def test_what_is_due_is_a_query_and_not_a_command():
    command_names = {c.__name__ for c in ALL_REVENUE_COMMANDS}
    query_names = {q.__name__ for q in ALL_REVENUE_QUERIES}
    assert {"DueRecognitions", "UnpostedRecognitions"} <= query_names
    assert not any("Run" in name or "Sweep" in name for name in command_names)


def test_billing_remains_absent_from_the_runtime_composition_root():
    assert "billing" not in Path("bootstrap.py").read_text().lower()


def test_every_revenue_command_has_exactly_one_handler(wired):
    persistent, _ = wired
    app = persistent.application
    registered_commands = set(app.commands.registered)
    registered_queries = set(app.queries.registered)
    for command in ALL_REVENUE_COMMANDS:
        assert command in registered_commands, command.__name__
    for query in ALL_REVENUE_QUERIES:
        assert query in registered_queries, query.__name__


def test_migration_0028_alters_nothing():
    from billing.persistence.migrations import v0028_billing_revenue as migration

    source = _code_only(_inspect.getsource(migration))
    assert "ALTER" not in source.upper()
    assert "INSERT" not in source.upper()  # nothing is seeded — no policy, no method
    assert migration.VERSION == "0028"
    for frozen in ("cp_billing_ledger_entry", "cp_billing_invoice_snapshot"):
        assert frozen not in source


def test_the_revenue_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect as sa_inspect

    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all
    from billing.persistence.revenue_models import REVENUE_TABLES

    database = BillingDatabase()
    applied = upgrade_all(database)
    # Membership rather than "is last": M9.19 appended 0029 behind this one, and asserting a
    # tail position would make every future milestone edit an older milestone's test.
    assert "0028" in applied
    tables = set(sa_inspect(database.engine).get_table_names())
    assert {t.__tablename__ for t in REVENUE_TABLES} <= tables
    database.close()


def test_the_recognition_vocabulary_duplicates_no_existing_type():
    """`RecognitionState` is M9.2's and is reused; `ScheduleState` is new and means something
    else — where a schedule has got to, not what posture an entry carries."""
    assert {m.value for m in ScheduleState} != {m.value for m in RecognitionState}
    assert set(EARNED_SCHEDULE_STATES) <= set(ScheduleState)
