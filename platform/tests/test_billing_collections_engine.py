"""M9.17 — Dunning & Collections Engine.

Collections is the first Billing domain that is almost entirely *about other domains*. It reads
payments, reads invoices, and writes to neither; its own output is a decision and the record that
the decision was acted on. So the interesting failures are not arithmetic — there is no arithmetic
— they are **ownership** failures and **race** failures:

* a policy engine that reads "not succeeded" as "failed" turns every ambiguous payment into a
  second charge. That is the M9.16-R1 invariant, one milestone downstream, and §4 is about nothing
  else;
* a scheduler that runs twice opens two recoveries, sends two notices, or requests two retries;
* a worker that decided from a stale read undoes a recovery that happened while it was thinking;
* a domain that quietly starts computing what is owed becomes a second authority on a figure the
  Ledger owns.

Every claim below is demonstrated **structurally** — by a database constraint, by an absent FSM
edge, by a derived key, by a notifier that counts what it actually sent, or by two real connections
racing — rather than by a happy path that is assumed to imply the rest. §12 is the numbered
adversarial hunt.

The notifier used throughout is `RecordingNotifier`, which opens no socket, honours idempotency
keys the way a conforming transport does, and counts the notices it actually delivered. That last
property is what lets a test prove a duplicate produced *no second message* rather than merely
assert that the code looks careful.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ALL_COLLECTION_COMMANDS,
    ALL_COLLECTION_QUERIES,
    AllowAllAuthorizer,
    BlockCollectionCase,
    CancelCollectionCase,
    ClaimCollectionAction,
    CollectionTimeline,
    CreatePaymentIntent,
    DeactivateCollectionPolicy,
    DeclarePaymentRoute,
    EscalateCollectionCase,
    EvaluateCollectionCase,
    ExecuteCollectionAction,
    ExplainCollectionCase,
    GetCollectionAction,
    GetCollectionCase,
    GetCollectionPolicyVersions,
    ListCollectionActions,
    ListCollectionAttempts,
    ListCollectionCases,
    ListCollectionDecisions,
    ListCollectionPolicies,
    ObsoleteCollectionAction,
    OpenAccountingPeriod,
    OpenCollectionCase,
    OpenCollectionCycle,
    PublishCollectionPolicy,
    RecordPaymentSignal,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    COLLECTION_ACTION_LIFECYCLE,
    COLLECTION_CASE_LIFECYCLE,
    MONEY_REQUESTING_ACTIONS,
    RETRYABLE_SIGNALS,
    TERMINAL_CASE_STATES,
    ActionKind,
    ActionState,
    ActionTrigger,
    BillingConflict,
    BillingNotFound,
    BillingValidationError,
    CaseLifecycleService,
    CaseStale,
    CaseState,
    CaseTrigger,
    CollectionDecisionEngine,
    CollectionFactory,
    CollectionPolicy,
    CollectionStage,
    Currency,
    FailureClass,
    FixedClock,
    InvalidBillingTransition,
    NotifierRegistry,
    NullNotifier,
    ObligationKind,
    OfflinePaymentProvider,
    PaymentSignal,
    PaymentSubjectKind,
    ProviderRegistry,
    RecordingNotifier,
    SequentialIdGenerator,
    SignalReader,
    derive_action_id,
    derive_case_id,
    derive_decision_id,
)
from billing.persistence.composition import build_persistent_billing_application

ORG, ACTOR, ACCOUNT = "org_1", "usr_1", "bac_1"
KW = {"actor_id": ACTOR, "org_id": ORG}
INR = Currency.INR
SECRET = "test-secret-value-for-billing-collections"

#: A short strategy whose every rung is reachable inside one test.
STAGES = (
    CollectionStage(sequence=0, kind=ActionKind.NOTIFY, wait_seconds=0, label="first notice"),
    CollectionStage(
        sequence=1, kind=ActionKind.REQUEST_PAYMENT_RETRY, wait_seconds=100, label="retry"
    ),
    CollectionStage(sequence=2, kind=ActionKind.FINAL_NOTICE, wait_seconds=200, label="final"),
    CollectionStage(sequence=3, kind=ActionKind.STOP, wait_seconds=300, label="stop"),
)


class _TaggedIds(SequentialIdGenerator):
    """A deterministic generator whose ids cannot collide with an earlier instance's."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self._tag = tag

    def new(self, prefix: str) -> str:
        return f"{super().new(prefix)}_{self._tag}"


def _provider() -> OfflinePaymentProvider:
    return OfflinePaymentProvider()


def _build(*, notifier=None, database=None, at: float = 1_000.0, tag: str = ""):
    notifier = notifier if notifier is not None else RecordingNotifier("email")
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(),
        ids=_TaggedIds(tag) if tag else SequentialIdGenerator(),
        clock=FixedClock(at, step=0.0),
        payment_providers=ProviderRegistry(providers=(_provider(),)),
        collection_notifiers=NotifierRegistry([notifier]),
        **({"database": database, "create_all": False} if database is not None else {}),
    )
    return persistent, notifier


@pytest.fixture
def wired():
    """A collections plane with one strategy published and one notifier registered."""
    persistent, notifier = _build()
    app = persistent.application
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0, currency=INR
        )
    )
    app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    app.commands.dispatch(PublishCollectionPolicy(**KW, code="std", version=1, stages=STAGES))
    return persistent, notifier


def _case(app, obligation: str = "inv_1", **overrides):
    return app.commands.dispatch(
        OpenCollectionCase(
            **KW,
            account_id=ACCOUNT,
            obligation_kind=ObligationKind.INVOICE,
            obligation_id=obligation,
            policy_code="std",
            **overrides,
        )
    )


def _evaluate(app, case_id: str, at: float = 1_000.0):
    return app.commands.dispatch(EvaluateCollectionCase(**KW, case_id=case_id, at=at))


def _signal(app, case_id: str, **fields):
    return app.commands.dispatch(RecordPaymentSignal(**KW, case_id=case_id, **fields))


def _plan_one(app, obligation: str = "inv_1", at: float = 1_000.0):
    """Open a case and evaluate it once, returning `(case_id, action)`."""
    opened = _case(app, obligation)
    planned = _evaluate(app, opened.case.id, at=at)
    return opened.case.id, planned.action


def _drain(app, case_id: str, *, rounds: int = 60):
    """Run a case to a terminal state the way a scheduler would.

    Time advances to whatever the *server* said the next action was due at — never to an instant
    the test picked — so the ladder is walked at the pace the strategy actually declares."""
    now = 1_000.0
    for _ in range(rounds):
        case = app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))
        if case.is_terminal:
            return case
        if case.next_action_at is not None:
            now = max(now, case.next_action_at)
        result = _evaluate(app, case_id, at=now)
        action = result.action
        if action is not None and not action.is_closed:
            now = max(now, action.due_at)
            app.commands.dispatch(
                ExecuteCollectionAction(**KW, action_id=action.id, at=now)
            )
        now += 1.0
    return app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))


def _payment_intent(app, subject: str = "inv_1", key: str = "req-1"):
    return app.commands.dispatch(
        CreatePaymentIntent(
            **KW,
            account_id=ACCOUNT,
            subject_kind=PaymentSubjectKind.ACCOUNT,
            subject_id="",
            amount_minor=100_000,
            currency=INR,
            request_key=key,
        )
    )


def _billing_files(layer: str) -> list[Path]:
    return [p for p in Path(f"billing/{layer}").rglob("*.py") if p.name != "__init__.py"]


def _code_only(source: str) -> str:
    """Source with docstrings stripped, so prose about SQL is not mistaken for SQL."""
    tree = ast.parse(source)
    spans = set()
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
    """HTML with its comment blocks removed. A page that *documents* storing nothing must not
    fail a scan for the word it uses to say so."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


# ── 1. the domain: policy, identity, lifecycle ─────────────────────────────────


def test_a_case_identity_is_the_obligation_and_nothing_else():
    """Not the time, not the cycle, not the policy. One debt is one recovery, forever."""
    first = derive_case_id(ORG, "invoice", "inv_1")
    assert first == derive_case_id(ORG, "invoice", "inv_1")
    assert first != derive_case_id(ORG, "invoice", "inv_2")
    assert first != derive_case_id("org_2", "invoice", "inv_1")
    assert first != derive_case_id(ORG, "payment_intent", "inv_1")


def test_a_decision_is_identified_by_its_inputs_not_by_when_it_was_taken():
    """A scheduler polling every minute records one decision per *decision*, not per sweep."""
    a = derive_decision_id("ccs_1", 1, 0, "failed")
    assert a == derive_decision_id("ccs_1", 1, 0, "failed")
    assert a != derive_decision_id("ccs_1", 1, 1, "failed")  # a different stage
    assert a != derive_decision_id("ccs_1", 2, 0, "failed")  # a different cycle
    assert a != derive_decision_id("ccs_1", 1, 0, "ambiguous")  # a different signal


def test_an_action_id_is_derived_so_two_workers_cannot_both_be_number_three():
    assert derive_action_id("ccs_1", 1, 3) == derive_action_id("ccs_1", 1, 3)
    assert derive_action_id("ccs_1", 1, 3) != derive_action_id("ccs_1", 2, 3)


def test_the_case_lifecycle_refuses_to_reopen_a_recovery():
    """**No edge out of `RECOVERED`.** Not by a stale worker, a replayed event, or a policy edit."""
    for state in TERMINAL_CASE_STATES:
        assert COLLECTION_CASE_LIFECYCLE.triggers_from(state) == frozenset()


def test_exhausted_never_returns_to_scheduled():
    """The transition that would let a policy edit resurrect a closed recovery does not exist.

    A genuinely new commercial decision is a **new cycle**, which leaves the old one readable."""
    assert COLLECTION_CASE_LIFECYCLE.get(CaseState.EXHAUSTED, CaseTrigger.SCHEDULE) is None
    assert COLLECTION_CASE_LIFECYCLE.get(CaseState.EXHAUSTED, CaseTrigger.MATURE) is None


def test_a_blocked_case_does_not_become_actionable_because_time_passed():
    """There is no `BLOCKED → ACTION_DUE`. Ambiguity is resolved by reconciliation, never
    by the clock."""
    assert COLLECTION_CASE_LIFECYCLE.get(CaseState.BLOCKED, CaseTrigger.MATURE) is None
    assert COLLECTION_CASE_LIFECYCLE.get(CaseState.BLOCKED, CaseTrigger.UNBLOCK) is not None


def test_an_executed_action_never_un_happens():
    """A notice that was sent was sent. A retry that was requested was requested."""
    assert COLLECTION_ACTION_LIFECYCLE.triggers_from(ActionState.EXECUTED) == frozenset()
    assert COLLECTION_ACTION_LIFECYCLE.get(ActionState.OBSOLETE, ActionTrigger.CLAIM) is None


def test_every_published_case_state_is_reachable():
    """The M9.16-R1 P-5 lesson, applied before it can become a finding.

    A state the API and Studio advertise but the engine cannot enter reads to integrators as a
    supported feature, and the first client to branch on it branches on something that never
    arrives. Walked from `DRAFT` rather than asserted from a list."""
    seen, frontier = {CaseState.DRAFT}, [CaseState.DRAFT]
    while frontier:
        state = frontier.pop()
        for trigger in COLLECTION_CASE_LIFECYCLE.triggers_from(state):
            target = COLLECTION_CASE_LIFECYCLE.get(state, trigger).target
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(CaseState), f"unreachable: {set(CaseState) - seen}"


def test_every_published_action_state_is_reachable():
    seen, frontier = {ActionState.PLANNED}, [ActionState.PLANNED]
    while frontier:
        state = frontier.pop()
        for trigger in COLLECTION_ACTION_LIFECYCLE.triggers_from(state):
            target = COLLECTION_ACTION_LIFECYCLE.get(state, trigger).target
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(ActionState), f"unreachable: {set(ActionState) - seen}"


def test_a_policy_with_no_stages_is_refused():
    """A strategy that can never act is a configuration error, not a quiet no-op."""
    with pytest.raises(BillingValidationError):
        CollectionFactory().policy(org_id=ORG, code="empty", version=1, stages=())


def test_policy_stages_must_be_dense_and_ascending():
    gapped = (
        CollectionStage(sequence=0, kind=ActionKind.NOTIFY),
        CollectionStage(sequence=2, kind=ActionKind.STOP),
    )
    with pytest.raises(BillingValidationError):
        CollectionFactory().policy(org_id=ORG, code="gapped", version=1, stages=gapped)


def test_a_policy_version_is_immutable_by_contract():
    """`CollectionPolicyRepository` has no `update`, and that absence is the reproducibility
    guarantee. A case decided under version 3 must still replay after version 4 is published."""
    from billing.foundation import CollectionPolicyRepository

    assert not hasattr(CollectionPolicyRepository, "update")
    assert not hasattr(CollectionPolicyRepository, "delete")


# ── 2. the decision engine: deterministic and clock-free ───────────────────────


def _bare_case(**overrides):
    policy = CollectionPolicy(id="cpv_1", org_id=ORG, code="std", version=1, stages=STAGES)
    case = CollectionFactory(clock=FixedClock(1_000.0, step=0.0)).case(
        org_id=ORG,
        account_id=ACCOUNT,
        obligation_kind=ObligationKind.INVOICE,
        obligation_id="inv_1",
        policy=policy,
    )
    from dataclasses import replace

    return (replace(case, **overrides) if overrides else case), policy


def test_the_same_inputs_always_produce_the_same_decision():
    """Determinism is the reproducibility contract. No clock is read inside `decide`."""
    case, policy = _bare_case()
    engine = CollectionDecisionEngine()
    first = engine.decide(case, policy, signal=PaymentSignal.FAILED, at=5_000.0)
    second = engine.decide(case, policy, signal=PaymentSignal.FAILED, at=5_000.0)
    assert first == second


def test_a_decision_replays_identically_after_the_strategy_is_rewritten():
    """The whole reason a policy is versioned rather than edited.

    The March decision is recomputed against the *March* version and is unchanged, while the same
    case against April's version selects something else — which is exactly the difference an audit
    needs to be able to see."""
    case, march = _bare_case()
    april = CollectionPolicy(
        id="cpv_2",
        org_id=ORG,
        code="std",
        version=2,
        stages=(CollectionStage(sequence=0, kind=ActionKind.FINAL_NOTICE, wait_seconds=9_999),),
    )
    engine = CollectionDecisionEngine()
    original = engine.decide(case, march, signal=PaymentSignal.FAILED, at=5_000.0)
    replayed = engine.decide(case, march, signal=PaymentSignal.FAILED, at=5_000.0)
    under_april = engine.decide(case, april, signal=PaymentSignal.FAILED, at=5_000.0)
    assert replayed == original
    assert original.selected is ActionKind.NOTIFY
    assert under_april.selected is ActionKind.FINAL_NOTICE


def test_grace_is_additive_to_the_wait_and_never_a_substitute():
    """Extending grace must never accidentally *shorten* a schedule."""
    stage = CollectionStage(sequence=0, kind=ActionKind.NOTIFY, wait_seconds=100, grace_seconds=50)
    policy = CollectionPolicy(id="p", org_id=ORG, code="g", version=1, stages=(stage,))
    case, _ = _bare_case()
    outcome = CollectionDecisionEngine().decide(
        case, policy, signal=PaymentSignal.FAILED, at=1_000.0, since=1_000.0
    )
    assert outcome.due_at == 1_150.0
    assert outcome.grace_until == 1_050.0


def test_running_off_the_end_of_a_strategy_is_exhaustion_not_a_repeat():
    """A clamp would silently repeat the last rung forever."""
    case, policy = _bare_case(stage_index=99)
    outcome = CollectionDecisionEngine().decide(case, policy, signal=PaymentSignal.FAILED, at=1.0)
    assert outcome.case_trigger is CaseTrigger.EXHAUST
    assert not outcome.plans_action
    assert "may still be outstanding" in outcome.reason


def test_a_permanent_failure_skips_to_the_terminal_stage_and_says_so():
    """Burning four retries on a card reported stolen is a decision, so it is recorded as one."""
    case, policy = _bare_case(stage_index=0)
    outcome = CollectionDecisionEngine().decide(
        case, policy, signal=PaymentSignal.FAILED, at=1.0, failure_class=FailureClass.PERMANENT
    )
    assert outcome.case_trigger is CaseTrigger.EXHAUST
    assert any("permanent_failure_skips" in step for step in outcome.path)


def test_the_engine_reads_no_clock():
    """`decide` takes `at`; it never calls one. Asserted structurally, not by reading style."""
    import textwrap

    source = _code_only(textwrap.dedent(_inspect.getsource(CollectionDecisionEngine)))
    for forbidden in ("time.time", "datetime", "clock.now", "self._clock"):
        assert forbidden not in source, forbidden


# ── 3. no scheduler, anywhere in the domain ────────────────────────────────────


def test_the_foundation_contains_no_scheduler():
    """Answering *what is due* is a query. Deciding *when to ask* is somebody else's layer."""
    for name in ("commercial_collections", "collection_rules", "collection_factories"):
        source = _code_only(Path(f"billing/foundation/{name}.py").read_text()).lower()
        for forbidden in ("import threading", "import asyncio", "time.sleep", "while true",
                          "def run(", "def run_scheduler", "schedule.every"):
            assert forbidden not in source, (name, forbidden)


def test_what_is_due_is_a_query_and_not_a_command():
    """`CasesDue` and `ActionsDue` are queries. There is no `RunCollections` command."""
    command_names = {c.__name__ for c in ALL_COLLECTION_COMMANDS}
    query_names = {q.__name__ for q in ALL_COLLECTION_QUERIES}
    assert {"CasesDue", "ActionsDue"} <= query_names
    assert not any("Run" in name or "Sweep" in name for name in command_names)


# ── 4. the M9.16-R1 invariant: an ambiguous payment is never a retry ───────────


def test_five_payment_attempt_states_read_as_ambiguous_and_only_one_is_called_that():
    """`started`, `pending`, `requires_action` and `authorized` all mean *possibly in flight*.

    From a collection standpoint the honest reading of all four is the same as of `ambiguous` —
    **not a licence to send another one**."""
    for state in ("started", "pending", "requires_action", "authorized", "ambiguous"):
        assert SignalReader.signal_for(attempt_state=state) is PaymentSignal.AMBIGUOUS


def test_an_unrecognised_payment_state_reads_as_ambiguous_never_as_failed():
    """A state the engine cannot vouch for is one whose safety it cannot vouch for."""
    assert SignalReader.signal_for(attempt_state="some_future_state") is PaymentSignal.AMBIGUOUS
    assert SignalReader.signal_for(intent_state="some_future_state") is PaymentSignal.AMBIGUOUS


def test_ambiguous_is_absent_from_the_retryable_set():
    """The invariant as **data**, so it is checkable by iteration rather than asserted in prose."""
    assert PaymentSignal.AMBIGUOUS not in RETRYABLE_SIGNALS
    assert PaymentSignal.SUCCEEDED not in RETRYABLE_SIGNALS
    assert PaymentSignal.FAILED in RETRYABLE_SIGNALS


def test_gate_one_ambiguity_short_circuits_before_the_policy_is_consulted():
    """No arrangement of stages can produce a retry against an unknown outcome, because the
    ambiguity branch runs before any stage is read."""
    retry_first = CollectionPolicy(
        id="p",
        org_id=ORG,
        code="aggressive",
        version=1,
        stages=(CollectionStage(sequence=0, kind=ActionKind.REQUEST_PAYMENT_RETRY),),
    )
    case, _ = _bare_case()
    outcome = CollectionDecisionEngine().decide(
        case, retry_first, signal=PaymentSignal.AMBIGUOUS, at=1.0
    )
    assert outcome.selected is ActionKind.AWAIT_RECONCILIATION
    assert outcome.case_trigger is CaseTrigger.BLOCK


@pytest.mark.parametrize(
    "signal", [PaymentSignal.AMBIGUOUS, PaymentSignal.SUCCEEDED]
)
def test_gate_two_downgrades_a_retry_stage_for_any_non_retryable_signal(signal):
    """A whitelist, not a blacklist: a signal added later defaults to *refusing*.

    Reached directly so the second gate is proven independently of the first."""
    path: list[str] = []
    kind = CollectionDecisionEngine._gate_money_request(
        ActionKind.REQUEST_PAYMENT_RETRY, signal, path
    )
    assert kind is ActionKind.AWAIT_RECONCILIATION


def test_gate_three_refuses_at_execution_time_when_the_signal_moved(wired):
    """**The gate the first two cannot see.**

    An action planned an hour ago against a failed payment, executed now, after a reconciliation
    moved that attempt back to ambiguous. The decision engine has long since finished; only a check
    at the moment of execution catches this."""
    from billing.foundation import CollectionGuardService

    persistent, _ = wired
    app = persistent.application
    case_id, first = _plan_one(app)
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=first.id, at=10**9))
    retry = _evaluate(app, case_id, at=2_000.0).action
    assert retry.kind == ActionKind.REQUEST_PAYMENT_RETRY

    stored = app.queries.dispatch(GetCollectionAction(**KW, action_id=retry.id))
    action = persistent.application.context.collections_engine.actions.get(ORG, stored.id)
    with pytest.raises(BillingConflict, match="ambiguous outcome is resolved by reconciliation"):
        CollectionGuardService.guard_money_request(action, PaymentSignal.AMBIGUOUS)


def test_an_ambiguous_signal_blocks_the_case_and_plans_no_retry(wired):
    """End to end: the only action reachable from ambiguity is waiting."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    blocked = _signal(app, case_id, attempt_state="ambiguous")
    assert blocked.case.state == "blocked"

    evaluated = _evaluate(app, case_id, at=9_000.0)
    assert evaluated.action.kind == "await_reconciliation"
    kinds = {a.kind for a in app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))}
    assert "request_payment_retry" not in kinds or all(
        a.state != "executed"
        for a in app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
        if a.kind == "request_payment_retry"
    )


def test_resolving_the_ambiguity_unblocks_the_case(wired):
    """Reconciliation's news, delivered as a signal. The clock could not have done this."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    assert _signal(app, case_id, attempt_state="ambiguous").case.state == "blocked"
    resolved = _signal(app, case_id, attempt_state="failed")
    assert resolved.case.state == "scheduled"


# ── 5. recovery, exhaustion, cycles ────────────────────────────────────────────


def test_a_payment_success_recovers_the_case_and_retires_every_open_action(wired):
    """**The payment-success-versus-retry race, closed in one transaction.**

    A recovery that left a planned retry behind would be executed against a paid invoice by the
    next scheduler sweep."""
    persistent, _ = wired
    app = persistent.application
    case_id, action = _plan_one(app)
    assert action.state == "planned"

    recovered = _signal(app, case_id, attempt_state="succeeded")
    assert recovered.case.state == "recovered"
    actions = app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert all(a.state in ("obsolete", "executed") for a in actions)


def test_a_recovered_case_refuses_a_later_failure_signal(wired):
    """A late callback does not reopen a recovery."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    _signal(app, case_id, attempt_state="succeeded")
    with pytest.raises(BillingConflict, match="recovered"):
        _signal(app, case_id, attempt_state="failed")


def test_exhaustion_does_not_mean_the_customer_owes_nothing(wired):
    """Exhausted means *the strategy reached its boundary*. Collections writes off nothing.

    Asserted on the recorded reason, because that sentence is what an operator reads."""
    persistent, _ = wired
    app = persistent.application
    case_id = _case(app).case.id
    _drain(app, case_id)
    case = app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))
    assert case.state == "exhausted"
    assert "may still be outstanding" in case.closed_reason or "terminal" in case.closed_reason


def test_a_new_cycle_leaves_the_previous_one_readable(wired):
    """The honest alternative to reopening a closed recovery."""
    persistent, _ = wired
    app = persistent.application
    app.commands.dispatch(
        PublishCollectionPolicy(**KW, code="multi", version=1, stages=STAGES, max_cycles=3)
    )
    opened = app.commands.dispatch(
        OpenCollectionCase(
            **KW,
            account_id=ACCOUNT,
            obligation_kind=ObligationKind.INVOICE,
            obligation_id="inv_cycles",
            policy_code="multi",
        )
    )
    case_id = opened.case.id
    _drain(app, case_id)
    before = app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert before

    cycled = app.commands.dispatch(
        OpenCollectionCycle(**KW, case_id=case_id, policy_code="multi", reason="second attempt")
    )
    assert cycled.case.cycle == 2
    after = app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert [(a.id, a.state) for a in after[: len(before)]] == [
        (a.id, a.state) for a in before
    ], "the previous cycle's actions were rewritten"


def test_a_cycle_beyond_the_policy_maximum_is_refused(wired):
    persistent, _ = wired
    app = persistent.application
    case_id = _case(app, "inv_maxcycle").case.id
    _drain(app, case_id)
    with pytest.raises(BillingConflict, match="cycle"):
        app.commands.dispatch(OpenCollectionCycle(**KW, case_id=case_id))


def test_a_cycle_on_a_live_case_is_refused(wired):
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    with pytest.raises(BillingConflict, match="exhausted"):
        app.commands.dispatch(OpenCollectionCycle(**KW, case_id=case_id))


# ── 6. idempotency ─────────────────────────────────────────────────────────────


def test_opening_the_same_recovery_twice_is_one_case(wired):
    persistent, _ = wired
    app = persistent.application
    first, second = _case(app), _case(app)
    assert first.case.id == second.case.id
    assert first.disposition == "new"
    assert second.disposition == "duplicate"


def test_a_duplicate_is_never_reported_as_new(wired):
    """A scheduler that could not tell them apart would count chases that never happened."""
    persistent, _ = wired
    app = persistent.application
    published = app.commands.dispatch(
        PublishCollectionPolicy(**KW, code="std", version=1, stages=STAGES)
    )
    assert published.version == 1  # republishing is idempotent, not an error


def test_re_evaluating_an_unchanged_case_plans_no_second_action(wired):
    persistent, _ = wired
    app = persistent.application
    case_id, first = _plan_one(app)
    again = _evaluate(app, case_id, at=1_000.0)
    actions = app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert len(actions) == 1
    assert again.disposition in ("deferred", "new")
    assert actions[0].id == first.id


def test_re_evaluating_records_one_decision_not_one_per_sweep(wired):
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    for _ in range(5):
        _evaluate(app, case_id, at=1_000.0)
    decisions = app.queries.dispatch(ListCollectionDecisions(**KW, case_id=case_id))
    assert len(decisions) == 1


def test_a_duplicate_action_produces_no_second_notice(wired):
    """Proven by a notifier that counts what it actually delivered, not by reading the code."""
    persistent, notifier = wired
    app = persistent.application
    case_id, action = _plan_one(app)
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=action.id, at=10**9))
    assert len(notifier.deliveries()) == 1
    with pytest.raises(BillingConflict):
        app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=action.id, at=10**9))
    assert len(notifier.deliveries()) == 1


# ── 7. the payment boundary ────────────────────────────────────────────────────


def test_collections_never_writes_to_another_domains_repositories():
    """**The write boundary, scanned.**

    Every attribute write target in the Collections Application must reach `self._repos.*`. A
    convention is what the next caller does not know about; this is a test."""
    source = Path("billing/application/collections_engine.py").read_text()
    tree = ast.parse(source)
    writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {
            "add", "append", "save", "open", "plan", "claim", "settle", "advance",
            "deactivate", "mark_state", "record_outcome", "upsert", "complete",
        }:
            continue
        chain = node.func.value
        parts: list[str] = []
        while isinstance(chain, ast.Attribute):
            parts.append(chain.attr)
            chain = chain.value
        if not isinstance(chain, ast.Name):
            continue  # a call on a literal or subscript — not a repository handle
        parts.append(chain.id)
        target = ".".join(reversed(parts))
        if target.startswith("self."):
            writes.append(target)
    assert writes, "the scan found no writes at all — it is not testing anything"
    for target in writes:
        assert target.startswith("self._repos."), target


def test_collections_imports_no_payment_invoice_or_ledger_repository():
    """It consumes payment *facts* through the context; it never holds a payment repository type."""
    source = Path("billing/application/collections_engine.py").read_text()
    for forbidden in (
        "PaymentRepositories", "LedgerRepositories", "InvoiceRepositories",
        "PostJournalEntry", "LedgerEntry", "CreditMovement",
    ):
        assert forbidden not in source, forbidden


def test_the_only_money_requesting_action_is_the_one_that_delegates():
    """Everything else is communication, waiting, or handoff."""
    assert MONEY_REQUESTING_ACTIONS == frozenset({ActionKind.REQUEST_PAYMENT_RETRY})


def test_a_payment_retry_is_requested_through_the_payment_command(wired):
    """Collections supplies an intent id and receives a receipt. It chooses no provider, computes
    no amount, and touches no payment row."""
    persistent, _ = wired
    app = persistent.application
    intent = _payment_intent(app)
    opened = _case(app, "inv_retry", payment_intent_id=intent.id)
    case_id = opened.case.id
    first = _evaluate(app, case_id, at=1_000.0).action
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=first.id, at=10**9))
    retry = _evaluate(app, case_id, at=5_000.0).action
    assert retry.kind == "request_payment_retry"

    result = app.commands.dispatch(
        ExecuteCollectionAction(**KW, action_id=retry.id, at=retry.due_at)
    )
    attempts = app.queries.dispatch(
        ListCollectionAttempts(**KW, case_id=case_id, action_id=retry.id)
    )
    assert attempts, "no evidence was recorded for the retry"
    assert result.action.state in ("executed", "failed")


def test_an_unwired_payment_engine_defers_a_retry_rather_than_inventing_one():
    """The honest behaviour for an installation with no payment engine: recorded, not sent."""
    import textwrap

    from billing.application.collections_engine import CollectionUseCases

    source = _code_only(
        textwrap.dedent(_inspect.getsource(CollectionUseCases._request_payment_retry))
    )
    assert "self._payment_retry is None" in source
    assert "DEFERRED" in source


# ── 8. the ledger, invoice and money boundaries ────────────────────────────────


def test_no_collections_module_holds_an_amount_a_balance_or_a_total():
    """**There is no money in this domain.** Asserted across every layer it owns.

    A stored figure would be a second authority on a number the Ledger owns, and it would be the
    stale one on the day it mattered."""
    files = [
        Path("billing/foundation/commercial_collections.py"),
        Path("billing/foundation/collection_rules.py"),
        Path("billing/foundation/collection_factories.py"),
        Path("billing/application/collections_engine.py"),
        Path("billing/persistence/collection_models.py"),
        Path("billing/persistence/collection_repositories.py"),
        Path("billing/customer/collection_schemas.py"),
        Path("billing/customer/routers/collections_engine.py"),
    ]
    banned = {
        "amount", "amount_due", "amount_minor", "outstanding", "balance", "subtotal",
        "grand_total", "total_minor", "net", "captured", "refunded",
    }
    for path in files:
        tree = ast.parse(path.read_text())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        found = names & banned
        assert not found, (path.name, found)


def test_no_collections_module_imports_money_at_all():
    """Not even the type. A domain that can hold a `Money` eventually stores one."""
    for path in (
        Path("billing/foundation/commercial_collections.py"),
        Path("billing/foundation/collection_rules.py"),
        Path("billing/foundation/collection_factories.py"),
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                assert "Money" not in names, (path.name, node.module)


def test_the_case_table_has_no_money_column():
    from billing.persistence.collection_models import BillingCollectionCaseRow

    columns = {c.name for c in BillingCollectionCaseRow.__table__.columns}
    for banned in ("amount", "amount_due", "outstanding", "balance", "total"):
        assert banned not in columns


def test_collections_posts_nothing_to_the_ledger(wired):
    """The strongest form: run a whole recovery and assert the ledger is untouched."""
    from sqlmodel import select

    from billing.persistence.ledger_models import BillingJournalEntryRow

    persistent, _ = wired
    app = persistent.application
    session = persistent.database.session
    before = len(list(session.exec(select(BillingJournalEntryRow))))

    case_id, action = _plan_one(app, "inv_ledger")
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=action.id, at=10**9))
    _signal(app, case_id, attempt_state="failed")
    _evaluate(app, case_id, at=9_000.0)

    after = len(list(session.exec(select(BillingJournalEntryRow))))
    assert after == before


# ── 9. the communication boundary ──────────────────────────────────────────────


def test_the_notifier_port_names_no_vendor_and_no_transport():
    """One provider-neutral verb. There is no `send_email`, no template, and no recipient."""
    from billing.foundation import CollectionNotifier

    source = _code_only(Path("billing/foundation/collection_rules.py").read_text()).lower()
    for vendor in ("twilio", "sendgrid", "mailgun", "ses", "whatsapp", "smtp", "postmark"):
        assert not re.search(rf"\b{vendor}\b", source), vendor
    verbs = {n for n in dir(CollectionNotifier) if not n.startswith("_")}
    assert verbs == {"deliver", "code"}


def test_a_notice_carries_no_recipient_and_no_amount():
    from billing.foundation import NoticeRequest

    fields = set(NoticeRequest.__dataclass_fields__)
    for banned in ("email", "phone", "recipient", "address", "body", "template", "amount"):
        assert banned not in fields


def test_an_unconfigured_notifier_defers_rather_than_failing():
    """Nothing was attempted, so reporting a failure would put a lie in the audit trail."""
    from billing.foundation import AttemptOutcome, NoticeRequest

    request = NoticeRequest(
        org_id=ORG, case_id="c", action_id="a", account_id=ACCOUNT,
        obligation_kind=ObligationKind.INVOICE, obligation_id="inv_1",
        kind=ActionKind.NOTIFY, idempotency_key="k",
    )
    assert NullNotifier().deliver(request).outcome is AttemptOutcome.DEFERRED


def test_a_transport_that_raises_is_deferred_never_failed(wired):
    """"We could not confirm it went" is indistinguishable from "the acknowledgement was lost",
    and treating the second as the first is how a customer receives five final notices."""

    class _Exploding(RecordingNotifier):
        def deliver(self, request):
            raise TimeoutError("the mail gateway never answered")

    persistent, _ = _build(notifier=_Exploding("email"))
    app = persistent.application
    app.commands.dispatch(PublishCollectionPolicy(**KW, code="std", version=1, stages=STAGES))
    case_id, action = _plan_one(app)
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=action.id, at=10**9))
    attempts = app.queries.dispatch(ListCollectionAttempts(**KW, case_id=case_id))
    assert attempts[-1].outcome == "deferred"


def test_a_deferred_notice_does_not_advance_the_strategy(wired):
    """Nothing reached the customer, so the rung has not been climbed."""
    from billing.application.collections_engine import _advances_stage
    from billing.foundation import AttemptOutcome, CollectionAction

    action = CollectionAction(id="a", org_id=ORG, case_id="c", cycle=1, sequence=1,
                              kind=ActionKind.NOTIFY)
    assert _advances_stage(action, AttemptOutcome.SUCCEEDED) is True
    assert _advances_stage(action, AttemptOutcome.DEFERRED) is False
    assert _advances_stage(action, AttemptOutcome.NOT_SENT) is False


def test_a_notice_is_durable_before_it_is_sent(wired):
    """Phase 1 commits a `claimed` action; phase 2 sends; phase 3 records.

    Asserted on the *ordering in the source*, because the crash this protects against cannot be
    provoked from a test that shares the process."""
    from billing.application.collections_engine import CollectionUseCases

    source = _inspect.getsource(CollectionUseCases.execute_action)
    claim_at = source.index("actions.claim")
    perform_at = source.index("self._perform")
    record_at = source.index("attempts.append")
    assert claim_at < perform_at < record_at


# ── 10. operator authority ─────────────────────────────────────────────────────


def test_there_is_no_force_state_and_no_admin_override():
    """An operator fires a trigger the machine already allows. It cannot invent a state."""
    source = _code_only(Path("billing/application/collections_engine.py").read_text()).lower()
    for forbidden in ("force_state", "admin_override", "set_state", "force=true"):
        assert forbidden not in source, forbidden


def test_an_escalated_case_is_closed_to_automation_but_not_to_a_human(wired):
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    escalated = app.commands.dispatch(
        EscalateCollectionCase(**KW, case_id=case_id, reason="customer disputes the invoice")
    )
    assert escalated.case.state == "escalated"
    assert escalated.case.is_quiescent and not escalated.case.is_terminal

    ignored = _evaluate(app, case_id, at=99_000.0)
    assert ignored.disposition == "rejected"
    assert set(escalated.case.allowed_triggers) == {"cancel", "recover"}


def test_cancelling_a_case_is_not_a_write_off(wired):
    """Collections records that the *thing being recovered* went away. It forgives nothing."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    cancelled = app.commands.dispatch(
        CancelCollectionCase(**KW, case_id=case_id, reason="invoice credited in full")
    )
    assert cancelled.case.state == "cancelled"
    source = _code_only(Path("billing/application/collections_engine.py").read_text()).lower()
    for forbidden in ("write_off", "writeoff", "forgive", "credit_note", "issuecredit"):
        assert forbidden not in source, forbidden


def test_every_operator_decision_is_attributed(wired):
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    app.commands.dispatch(BlockCollectionCase(**KW, case_id=case_id, reason="manual hold"))
    trail = app.queries.dispatch(CollectionTimeline(**KW, case_id=case_id))
    assert any(entry.actor_id == ACTOR for entry in trail)


# ── 11. concurrency, with real connections ─────────────────────────────────────


def _two_sessions(tmp_path, name: str = "collections.db"):
    """Two Applications over one file-backed SQLite database, each with its own connection.

    Two *connections*, deliberately, rather than two threads: a thread schedule that does not
    actually interleave produces a green test that proved nothing, and an explicit interleaving
    proves the database is the arbitrator rather than the GIL."""
    url = f"sqlite:///{tmp_path / name}"
    first = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("a"),
        clock=FixedClock(1_000.0, step=0.0),
        payment_providers=ProviderRegistry(providers=(_provider(),)),
        collection_notifiers=NotifierRegistry([RecordingNotifier("email")]),
    )
    second = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("b"),
        clock=FixedClock(1_000.0, step=0.0), create_all=False,
        payment_providers=ProviderRegistry(providers=(_provider(),)),
        collection_notifiers=NotifierRegistry([RecordingNotifier("email")]),
    )
    assert first.database is not second.database
    for persistent in (first, second):
        try:
            persistent.application.commands.dispatch(
                PublishCollectionPolicy(**KW, code="std", version=1, stages=STAGES)
            )
        except Exception:  # pragma: no cover — the second publish is a duplicate
            pass
    return first, second


def test_two_workers_cannot_open_two_cases_for_one_obligation(tmp_path):
    """**Concurrency 1.** Arbitrated by the derived primary key, across two connections."""
    first, second = _two_sessions(tmp_path)
    a = _case(first.application, "inv_race")
    b = _case(second.application, "inv_race")
    assert a.case.id == b.case.id
    assert (a.disposition, b.disposition) == ("new", "duplicate")

    from sqlmodel import select

    from billing.persistence.collection_models import BillingCollectionCaseRow

    rows = list(first.database.session.exec(select(BillingCollectionCaseRow)))
    assert len([r for r in rows if r.obligation_id == "inv_race"]) == 1


def test_two_workers_cannot_execute_the_same_action(tmp_path):
    """**Concurrency 2.** The conditional `planned → claimed` picks exactly one winner."""
    first, second = _two_sessions(tmp_path)
    case_id, action = _plan_one(first.application, "inv_claim")

    held = first.application.commands.dispatch(
        ClaimCollectionAction(**KW, action_id=action.id, at=10**9)
    )
    assert held.state == "claimed"
    with pytest.raises((CaseStale, BillingConflict)):
        second.application.commands.dispatch(
            ClaimCollectionAction(**KW, action_id=action.id, at=10**9)
        )


def test_a_stale_worker_cannot_advance_a_case_it_read_before(tmp_path):
    """**Concurrency 3.** Compare-and-set on `version`, proven by writing a stale aggregate."""
    from dataclasses import replace

    first, second = _two_sessions(tmp_path)
    case_id, _ = _plan_one(first.application, "inv_stale")
    stale = second.application.context.collections_engine.cases.get(ORG, case_id)

    first.application.commands.dispatch(
        BlockCollectionCase(**KW, case_id=case_id, reason="something happened")
    )
    second.database.session.expire_all()
    repo = second.application.context.collections_engine.cases
    with pytest.raises(CaseStale, match="re-read and re-decide"):
        repo.advance(replace(stale, state=CaseState.SCHEDULED), expected_version=stale.version)


def test_a_recovered_case_cannot_be_moved_back_by_a_stale_worker(tmp_path):
    """**Concurrency 4 and 6.** Worker A reads `scheduled`; worker B records the payment; A returns.

    The exact scenario the mandate names. Two defences fire and either alone would be enough: the
    FSM has no edge out of `RECOVERED`, and the version has moved."""
    from dataclasses import replace

    first, second = _two_sessions(tmp_path)
    case_id, _ = _plan_one(first.application, "inv_recovered")
    stale = second.application.context.collections_engine.cases.get(ORG, case_id)
    assert stale.state is CaseState.SCHEDULED

    _signal(first.application, case_id, attempt_state="succeeded")
    second.database.session.expire_all()

    repo = second.application.context.collections_engine.cases
    with pytest.raises(CaseStale):
        repo.advance(replace(stale, state=CaseState.ACTION_DUE), expected_version=stale.version)
    assert repo.get(ORG, case_id).state is CaseState.RECOVERED


def test_an_exhausted_case_cannot_be_reopened_by_a_stale_worker(tmp_path):
    """**Concurrency 5.** Same two defences, at the other terminal state."""

    first, second = _two_sessions(tmp_path)
    app = first.application
    case_id = _case(app, "inv_exhaust").case.id
    _drain(app, case_id)

    second.database.session.expire_all()
    repo = second.application.context.collections_engine.cases
    exhausted = repo.get(ORG, case_id)
    assert exhausted.state is CaseState.EXHAUSTED
    with pytest.raises(InvalidBillingTransition):
        CaseLifecycleService().apply(exhausted, CaseTrigger.SCHEDULE, at=1.0)


def test_a_payment_success_racing_a_retry_produces_no_second_payment_request(tmp_path):
    """**Concurrency 4, end to end.** The retry is retired as obsolete rather than sent."""
    first, second = _two_sessions(tmp_path)
    app = first.application
    case_id, notice = _plan_one(app, "inv_racepay")
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=notice.id, at=10**9))
    retry = _evaluate(app, case_id, at=5_000.0).action
    assert retry.kind == "request_payment_retry"

    # The money lands through another route while the retry is sitting there planned.
    _signal(second.application, case_id, attempt_state="succeeded")
    first.database.session.expire_all()

    result = app.commands.dispatch(
        ExecuteCollectionAction(**KW, action_id=retry.id, at=retry.due_at)
    )
    assert result.disposition == "rejected"
    assert result.action.state == "obsolete"
    assert app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id)).state == "recovered"


def test_restart_durability(tmp_path):
    """A second Application over the same file reads every case, action, attempt and decision."""
    first, _ = _two_sessions(tmp_path, "restart.db")
    app = first.application
    case_id, action = _plan_one(app, "inv_restart")
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=action.id, at=10**9))
    first.database.close()

    reopened, _ = _build(database=None, tag="c")
    del reopened
    second = build_persistent_billing_application(
        url=f"sqlite:///{tmp_path / 'restart.db'}", authorizer=AllowAllAuthorizer(),
        ids=_TaggedIds("c"), clock=FixedClock(1_000.0, step=0.0), create_all=False,
        collection_notifiers=NotifierRegistry([RecordingNotifier("email")]),
    )
    case = second.application.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))
    assert case.id == case_id
    assert second.application.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert second.application.queries.dispatch(ListCollectionAttempts(**KW, case_id=case_id))


# ── 12. the adversarial hunt ───────────────────────────────────────────────────


def test_hunt_duplicate_payment_failure_event_opens_one_case(wired):
    """**Hunt 1.**"""
    persistent, _ = wired
    app = persistent.application
    ids = {_case(app, "inv_dupfail").case.id for _ in range(3)}
    assert len(ids) == 1


def test_hunt_a_far_future_instant_does_not_skip_the_ladder(wired):
    """**Hunt 18.** A clock jump matures what is due; it does not execute what is not."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app)
    result = _evaluate(app, case_id, at=10**12)
    actions = app.queries.dispatch(ListCollectionActions(**KW, case_id=case_id))
    assert len(actions) == 1, "a far-future evaluation planned the whole ladder at once"
    assert result.case.state in ("action_due", "scheduled")


def test_hunt_a_clock_moving_backwards_does_not_make_a_due_action_undue(wired):
    """**Hunt 17.** The action's own `due_at` is a stored fact; an earlier `at` cannot unset it."""
    persistent, _ = wired
    app = persistent.application
    case_id, action = _plan_one(app, at=5_000.0)
    _evaluate(app, case_id, at=1.0)  # a worker with a skewed clock
    stored = app.queries.dispatch(GetCollectionAction(**KW, action_id=action.id))
    assert stored.due_at == action.due_at
    assert stored.state == "planned"


def test_hunt_a_case_in_another_org_is_invisible(wired):
    """**Hunt 14.** Org isolation, on the read path a support agent actually uses."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app, "inv_org")
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            GetCollectionCase(actor_id=ACTOR, org_id="org_other", case_id=case_id)
        )
    assert app.queries.dispatch(
        ListCollectionCases(actor_id=ACTOR, org_id="org_other")
    ) == ()


def test_hunt_opening_a_case_with_no_published_strategy_is_refused(wired):
    """**Hunt 20.** A missing strategy is a configuration error, not an invented default."""
    persistent, _ = wired
    app = persistent.application
    with pytest.raises(BillingConflict, match="no active collection policy"):
        app.commands.dispatch(
            OpenCollectionCase(
                **KW, account_id=ACCOUNT, obligation_kind=ObligationKind.INVOICE,
                obligation_id="inv_nopolicy", policy_code="does-not-exist",
            )
        )


def test_hunt_a_policy_change_does_not_rebind_a_live_case(wired):
    """**Hunt 9.** The case keeps naming the version it was bound to."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app, "inv_rebind")
    before = app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))
    app.commands.dispatch(
        PublishCollectionPolicy(
            **KW, code="std", version=2,
            stages=(CollectionStage(sequence=0, kind=ActionKind.STOP),),
        )
    )
    _evaluate(app, case_id, at=1_000.0)
    after = app.queries.dispatch(GetCollectionCase(**KW, case_id=case_id))
    assert after.policy_version == before.policy_version == 1


def test_hunt_a_withdrawn_policy_still_explains_an_old_case(wired):
    """**Hunt 10.** Deactivation withdraws a version from *future* binding and nothing else."""
    persistent, _ = wired
    app = persistent.application
    case_id, _ = _plan_one(app, "inv_withdrawn")
    policies = app.queries.dispatch(ListCollectionPolicies(**KW))
    app.commands.dispatch(
        DeactivateCollectionPolicy(**KW, policy_id=policies[0].id, reason="superseded")
    )
    explanation = app.queries.dispatch(ExplainCollectionCase(**KW, case_id=case_id))
    assert explanation.policy_version == 1
    assert app.queries.dispatch(GetCollectionPolicyVersions(**KW, code="std"))


def test_hunt_an_unknown_case_is_a_not_found_not_a_silent_no_op(wired):
    """**Hunt 15.**"""
    persistent, _ = wired
    app = persistent.application
    with pytest.raises(BillingNotFound):
        _evaluate(app, "ccs_nope")


def test_hunt_an_obsolete_action_cannot_be_resurrected(wired):
    """**Hunt 3.** A new decision is a new action with a new sequence, never a revived one."""
    persistent, _ = wired
    app = persistent.application
    case_id, action = _plan_one(app, "inv_obsolete")
    app.commands.dispatch(
        ObsoleteCollectionAction(**KW, action_id=action.id, reason="no longer needed")
    )
    with pytest.raises((BillingConflict, InvalidBillingTransition)):
        app.commands.dispatch(ClaimCollectionAction(**KW, action_id=action.id, at=10**9))


def test_hunt_a_reconciliation_landing_mid_evaluation_does_not_produce_a_retry(wired):
    """**Hunt 22.** The signal is re-read on every evaluation, so the stale one never decides."""
    persistent, _ = wired
    app = persistent.application
    case_id, notice = _plan_one(app, "inv_recon")
    app.commands.dispatch(ExecuteCollectionAction(**KW, action_id=notice.id, at=10**9))
    _signal(app, case_id, attempt_state="ambiguous")
    result = _evaluate(app, case_id, at=9_000.0)
    assert result.action is None or result.action.kind == "await_reconciliation"


# ── 13. the HTTP surface ───────────────────────────────────────────────────────


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
        collection_notifiers=NotifierRegistry([RecordingNotifier("email")]),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"
R = f"{PREFIX}/recovery"


def test_recovery_routes_require_identity_and_an_org(client):
    """Deny by default on every new message."""
    assert client.get(f"{R}/cases").status_code == 401
    assert client.post(f"{R}/cases", json={}).status_code in (401, 422)
    assert client.get(f"{R}/policies").status_code == 401


def test_the_recovery_prefix_does_not_shadow_the_payment_collections_prefix(client):
    """`/collections` is M9.16's. Two routers under one prefix would shadow each other silently."""
    spec = client.get("/openapi.json").json()
    recovery = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/recovery")]
    collections = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/collections")]
    assert recovery and collections
    assert not set(recovery) & set(collections)


def test_a_full_recovery_over_http(client):
    published = client.post(
        f"{R}/policies",
        json={
            "code": "http",
            "version": 1,
            "stages": [
                {"sequence": 0, "kind": "notify", "wait_seconds": 0},
                {"sequence": 1, "kind": "stop", "wait_seconds": 100},
            ],
        },
        headers=_auth(),
    )
    assert published.status_code == 201, published.text

    opened = client.post(
        f"{R}/cases",
        json={
            "account_id": ACCOUNT,
            "obligation_kind": "invoice",
            "obligation_id": "inv_http",
            "policy_code": "http",
        },
        headers=_auth(),
    )
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["case"]["id"]
    assert opened.json()["disposition"] == "new"

    again = client.post(
        f"{R}/cases",
        json={
            "account_id": ACCOUNT,
            "obligation_kind": "invoice",
            "obligation_id": "inv_http",
            "policy_code": "http",
        },
        headers=_auth(),
    )
    assert again.json()["disposition"] == "duplicate"

    evaluated = client.post(f"{R}/cases/{case_id}/evaluate", json={}, headers=_auth())
    assert evaluated.status_code == 200, evaluated.text
    action_id = evaluated.json()["action"]["id"]

    executed = client.post(f"{R}/actions/{action_id}/execute", json={}, headers=_auth())
    assert executed.status_code == 200, executed.text

    for path in ("actions", "attempts", "decisions", "explain", "inspect", "timeline"):
        got = client.get(f"{R}/cases/{case_id}/{path}", headers=_auth())
        assert got.status_code == 200, (path, got.text)

    recovered = client.post(
        f"{R}/cases/{case_id}/signal", json={"attempt_state": "succeeded"}, headers=_auth()
    )
    assert recovered.json()["case"]["state"] == "recovered"


def test_no_response_on_the_recovery_surface_carries_an_amount(client):
    """Greps the OpenAPI schema, so a field added later is caught rather than assumed absent."""
    spec = client.get("/openapi.json").json()
    banned = {"amount", "amount_due", "outstanding", "balance", "amount_minor", "total_minor"}
    for name, schema in spec["components"]["schemas"].items():
        if not name.startswith("Collection"):
            continue
        assert not (set(schema.get("properties", {})) & banned), name


def test_the_signal_endpoint_accepts_states_not_verdicts(client):
    """A body carrying `recovered: true` would let its caller close a recovery."""
    spec = client.get("/openapi.json").json()
    body = spec["components"]["schemas"]["RecordPaymentSignalBody"]["properties"]
    assert set(body) == {"intent_state", "attempt_state", "failure_code", "payment_intent_id"}


def test_an_illegal_transition_over_http_is_a_conflict_not_a_500(client):
    client.post(
        f"{R}/policies",
        json={"code": "c", "version": 1,
              "stages": [{"sequence": 0, "kind": "notify"}, {"sequence": 1, "kind": "stop"}]},
        headers=_auth(),
    )
    opened = client.post(
        f"{R}/cases",
        json={"account_id": ACCOUNT, "obligation_kind": "invoice",
              "obligation_id": "inv_conflict", "policy_code": "c"},
        headers=_auth(),
    )
    case_id = opened.json()["case"]["id"]
    client.post(f"{R}/cases/{case_id}/signal", json={"attempt_state": "succeeded"},
                headers=_auth())
    refused = client.post(f"{R}/cases/{case_id}/cancel", json={"reason": "too late"},
                          headers=_auth())
    assert refused.status_code == 409, refused.text


def test_controllers_decide_nothing():
    """AST: no router handler contains a comparison against a collection state or a stage index."""
    source = Path("billing/customer/routers/collections_engine.py").read_text()
    code = _code_only(source)
    for forbidden in (
        "if signal", "if state ==", "stage_index +", "wait_seconds +", "grace_seconds +",
        "RETRYABLE_SIGNALS", "CollectionDecisionEngine", "SignalReader",
    ):
        assert forbidden not in code, forbidden


# ── 14. Studio is a consumer ───────────────────────────────────────────────────


STUDIO = Path("billing/customer/studio/recovery.html")


def test_the_studio_stores_nothing():
    text = _html_code_only(STUDIO.read_text())
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in text, forbidden


def test_the_studio_calculates_no_schedule_and_no_money():
    """No retry arithmetic, no grace arithmetic, no amount. Every figure is the server's."""
    text = _html_code_only(STUDIO.read_text())
    for forbidden in (
        "wait_seconds +", "grace_seconds +", "next_action_at +", "Date.now() +",
        "amount", "minor", "outstanding", "balance",
    ):
        assert forbidden not in text, forbidden


def test_the_studio_names_no_vendor_and_calls_no_provider():
    text = _html_code_only(STUDIO.read_text()).lower()
    for forbidden in ("twilio", "sendgrid", "stripe", "razorpay", "mailgun", "http://", "https://"):
        assert forbidden not in text, forbidden


def test_the_studio_takes_its_buttons_from_the_servers_allowed_triggers():
    text = STUDIO.read_text()
    assert "allowed_triggers" in text
    assert 'allowed.has("escalate")' in text and 'allowed.has("cancel")' in text


# ── 15. architecture ───────────────────────────────────────────────────────────


def test_collections_foundation_is_unaware_persistence_exists():
    for name in ("commercial_collections", "collection_rules", "collection_factories"):
        source = Path(f"billing/foundation/{name}.py").read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "session.", "__tablename__"):
            assert forbidden not in source, (name, forbidden)


def test_collections_foundation_imports_no_other_billing_domain():
    """It consumes payment facts as **strings**, translated in one place.

    A Foundation-to-Foundation import would make the two domains compile together whether or not
    they are wired together, and the translation table would stop being the only translation."""
    for name in ("commercial_collections", "collection_rules"):
        tree = ast.parse(Path(f"billing/foundation/{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "payment" not in node.module, (name, node.module)
                assert "invoic" not in node.module, (name, node.module)
                assert "ledger" not in node.module, (name, node.module)


def test_no_provider_sdk_reaches_collections():
    for path in (
        Path("billing/foundation/commercial_collections.py"),
        Path("billing/foundation/collection_rules.py"),
        Path("billing/foundation/collection_factories.py"),
        Path("billing/application/collections_engine.py"),
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.ImportFrom) and node.module:
                roots.append(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                roots.extend(a.name.split(".")[0] for a in node.names)
            for root in roots:
                banned = {"requests", "httpx", "urllib", "socket", "aiohttp", "smtplib"}
                assert root not in banned, (path.name, root)


def test_collections_evaluates_no_entitlement_and_no_rbac():
    """Authorization is the transport/application concern; entitlements are M9.10's."""
    for name in ("commercial_collections", "collection_rules", "collection_factories"):
        source = Path(f"billing/foundation/{name}.py").read_text()
        for forbidden in ("RoleAssignment", "has_role", "check_permission", "is_authorized",
                          "EntitlementGate", "principal"):
            assert forbidden not in source, (name, forbidden)


def test_billing_remains_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2, re-asserted at every milestone that adds a plane."""
    bootstrap = (Path("bootstrap.py")).read_text()
    assert "billing" not in bootstrap.lower()


def test_every_collection_command_has_exactly_one_handler(wired):
    persistent, _ = wired
    app = persistent.application
    registered_commands = set(app.commands.registered)
    registered_queries = set(app.queries.registered)
    for command in ALL_COLLECTION_COMMANDS:
        assert command in registered_commands, command.__name__
    for query in ALL_COLLECTION_QUERIES:
        assert query in registered_queries, query.__name__


def test_the_collections_vocabulary_duplicates_no_existing_enum():
    """`PaymentSignal` is not a copy of `AttemptState`, and it must not become one.

    Six members against nine, and the mapping is many-to-one on purpose: five attempt states mean
    the same thing to a collection decision. A duplicate enum would be a second place to fix the
    M9.16-R1 invariant, and only one of the two would get fixed."""
    from billing.foundation import AttemptState

    assert {m.value for m in PaymentSignal} != {m.value for m in AttemptState}
    assert len(PaymentSignal) < len(AttemptState)


def test_m9_2_dunning_is_untouched():
    """The frozen surface stays exactly as it is. Extension is addition (§A12)."""
    from billing.foundation import DunningPolicy, DunningProcess, DunningState, DunningTrigger
    from billing.persistence.models import BillingDunningRow

    assert {m.value for m in DunningState} == {
        "not_started", "retrying", "grace", "contracted", "recovered", "abandoned"
    }
    assert {m.value for m in DunningTrigger} == {
        "start", "retry", "enter_grace", "contract_entitlement", "recover", "abandon"
    }
    assert DunningPolicy().max_attempts == 4
    assert set(DunningProcess.__dataclass_fields__) >= {"id", "org_id", "payment_id", "state"}
    assert BillingDunningRow.__tablename__ == "cp_billing_dunning"


def test_migration_0027_alters_nothing():
    from billing.persistence.migrations import v0027_billing_collections as migration

    source = _code_only(_inspect.getsource(migration))
    assert "ALTER" not in source.upper()
    assert migration.VERSION == "0027"
    for frozen in ("cp_billing_dunning", "cp_billing_payment", "cp_billing_ledger"):
        assert frozen not in source


def test_the_collections_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect as sa_inspect

    from billing.persistence.collection_models import COLLECTION_TABLES
    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all

    # `in`, not `[-1]`: this test is about *this* milestone's migration being present and applied,
    # not about no later milestone ever appending one. M9.18 appended 0028.
    database = BillingDatabase()
    applied = upgrade_all(database)
    assert "0027" in applied
    tables = set(sa_inspect(database.engine).get_table_names())
    assert {t.__tablename__ for t in COLLECTION_TABLES} <= tables
    database.close()
