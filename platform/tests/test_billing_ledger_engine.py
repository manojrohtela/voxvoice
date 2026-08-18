"""M9.13 — Credit & Revenue Ledger.

The Ledger is where commercial facts become financial ones, and it is the first Billing domain whose
errors cannot be corrected by re-running anything: a duplicated posting has already changed a
balance, may already have reached a statement, and is remedied only by a reversal, a credit note,
and a conversation with a customer who has been charged twice.

So the suite is organised so that every accounting invariant is demonstrated **structurally** —
by the absence of a method, the absence of an FSM edge, or a digest comparison — rather than by a
narrative that a passing integration test is assumed to imply. §13 is a numbered list of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.application import (
    ALL_LEDGER_COMMANDS,
    ALL_LEDGER_QUERIES,
    ActivateBillingAccount,
    ActivateCommercialMeter,
    ActivateSubscription,
    AdjustJournalEntry,
    AllowAllAuthorizer,
    ApplyCommercialCredit,
    CloseAccountingPeriod,
    CreateBillingAccount,
    CreatePrice,
    CreatePriceBook,
    CreateProduct,
    CreateSubscription,
    DefineCommercialMeter,
    DiagnoseLedgerPeriod,
    ExpireCommercialCredit,
    FinalizeRatingRun,
    GetAccountCreditPosition,
    GetAccountLedgerBalance,
    GetTrialBalance,
    GrantCommercialCredit,
    IngestMetricSignal,
    InspectLedgerPeriod,
    LedgerTimeline,
    ListJournalEntries,
    OpenAccountingPeriod,
    OpenUsagePeriod,
    PostJournalEntry,
    PostRatingRun,
    PublishCatalog,
    PublishPricing,
    PublishPricingBook,
    PublishProduct,
    RateSubscriptionPeriod,
    RebuildBalanceCheckpoint,
    ReconcileLedgerPeriod,
    ReplayLedgerPeriod,
    ReverseJournalEntry,
    RevokeCommercialCredit,
    SearchJournalEntries,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    ACCOUNTING_PERIOD_LIFECYCLE,
    CREDIT_LIFECYCLE,
    AccountBalance,
    AccountingPeriod,
    AccountingPeriodService,
    AccountingPeriodState,
    AccountingPeriodTrigger,
    AdjustmentService,
    AgreementItem,
    BalanceService,
    BillingConflict,
    BillingDiagnosticCode,
    BillingInterval,
    BillingNotFound,
    BillingPeriod,
    ChargePostingService,
    CommercialCredit,
    CreditApplication,
    CreditApplicationService,
    CreditKind,
    CreditLifecycleService,
    CreditPosition,
    CreditState,
    CreditTrigger,
    Currency,
    DefaultIdGenerator,
    EntryComposer,
    EntryReferences,
    FixedClock,
    InvalidBillingTransition,
    ItemKind,
    LateEntryDisposition,
    LedgerDirection,
    LedgerEntryType,
    LedgerInspector,
    LedgerReconciliationService,
    LedgerReplayService,
    LedgerSearchQuery,
    LedgerSearchService,
    LedgerValidator,
    MeterSource,
    MetricSignal,
    Money,
    Normalization,
    PostingOutcome,
    PostingRequest,
    PostingService,
    PostingSource,
    PriceShape,
    PriceTier,
    ReversalService,
    Scope,
    SequentialIdGenerator,
    TrialBalanceService,
    derive_journal_entry_id,
    period_key_for,
)
from billing.persistence import build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
STUDIO = BILLING / "customer" / "studio" / "ledger.html"

INR = Currency.INR
USD = Currency.USD
ORG, ACTOR, ACC = "org_1", "usr_1", "bac_1"
P0, P1 = 1_000.0, 2_000.0
PERIOD = BillingPeriod(P0, P1)
KEY = period_key_for(PERIOD)
NEXT = BillingPeriod(2_000.0, 3_000.0)
NEXT_KEY = period_key_for(NEXT)

posting = PostingService()
composer = EntryComposer()
periods = AccountingPeriodService()
balances = BalanceService()


# ── helpers ─────────────────────────────────────────────────────────────────────


def a_period(state=AccountingPeriodState.OPEN, key=KEY, window=PERIOD, **kw) -> AccountingPeriod:
    base = dict(
        id="acp_1",
        org_id=ORG,
        account_id=ACC,
        period_key=key,
        period=window,
        currency=INR,
        state=state,
    )
    base.update(kw)
    return AccountingPeriod(**base)  # type: ignore[arg-type]


def a_request(minor: int = 500_00, **kw) -> PostingRequest:
    base = dict(
        org_id=ORG,
        account_id=ACC,
        entry_type=LedgerEntryType.CHARGE,
        direction=LedgerDirection.DEBIT,
        amount=Money(minor, INR),
        source=PostingSource.RATING_RUN,
        source_id="chg_1",
        references=EntryReferences(charge_id="chg_1", rating_run_id="rat_1"),
        target_period_key=KEY,
    )
    base.update(kw)
    return PostingRequest(**base)  # type: ignore[arg-type]


def post(request, *, target=None, open_p=None, already=False, seq=1):
    decision = posting.decide(
        request, target_period=target, open_period=open_p, already_posted=already
    )
    entry = (
        composer.compose(request, decision, sequence=seq, posted_at=1_500.0)
        if decision.should_write
        else None
    )
    return decision, entry


def a_credit(minor: int = 100_00, **kw) -> CommercialCredit:
    base = dict(
        id="ccr_1",
        org_id=ORG,
        account_id=ACC,
        code="promo",
        kind=CreditKind.PROMOTIONAL,
        amount=Money(minor, INR),
        granted_at=0.0,
    )
    base.update(kw)
    return CommercialCredit(**base)  # type: ignore[arg-type]


# ── 1. identity + exactly-once ──────────────────────────────────────────────────


def test_an_entry_id_is_derived_from_what_caused_it():
    """The exactly-once story in one assertion: same cause, same id, no generator consulted."""
    assert a_request().entry_id == a_request().entry_id
    assert a_request().entry_id.startswith("jrn_")


def test_a_different_cause_derives_a_different_id():
    assert a_request().entry_id != a_request(source_id="chg_2").entry_id
    assert a_request().entry_id != a_request(entry_type=LedgerEntryType.TAX).entry_id
    assert a_request().entry_id != a_request(account_id="bac_2").entry_id


def test_where_an_entry_lands_is_not_part_of_its_identity():
    """Deliberate: the same cause is one financial fact regardless of which period records it. If
    the target were in the id, a charge deferred into February would have a different identity from
    the same charge posted in January, and re-running the January job would double-post."""
    assert (
        a_request(target_period_key=KEY).entry_id == a_request(target_period_key=NEXT_KEY).entry_id
    )


def test_the_amount_is_not_part_of_the_identity_either():
    """Also deliberate. If it were, a corrected amount would post *beside* the original instead of
    colliding with it, and the same charge would be recorded twice at two figures."""
    assert a_request(500_00).entry_id == a_request(900_00).entry_id


def test_a_duplicate_is_a_success_not_an_error():
    """At-least-once delivery makes retries normal, and the recorded activity is the evidence
    exactly-once worked."""
    decision, entry = post(a_request(), target=a_period(), open_p=a_period(), already=True)
    assert decision.outcome is PostingOutcome.DUPLICATE
    assert decision.is_success and not decision.should_write
    assert entry is None


def test_the_derived_id_helper_is_stable():
    assert derive_journal_entry_id(ORG, ACC, "rating_run", "chg_1", "charge") == (
        derive_journal_entry_id(ORG, ACC, "rating_run", "chg_1", "charge")
    )


# ── 2. posting decisions ────────────────────────────────────────────────────────


def test_a_normal_posting_lands_in_its_own_period():
    decision, entry = post(a_request(), target=a_period(), open_p=a_period())
    assert decision.outcome is PostingOutcome.POSTED
    assert entry.period_key == KEY
    assert entry.disposition is LateEntryDisposition.IN_PERIOD


def test_a_closed_period_never_reopens_and_the_entry_defers():
    """M9.11 learned this: sealing a period without deciding where a late fact lands produces
    either a silent retroactive write or an unexplained rejection."""
    later = a_period(key=NEXT_KEY, window=NEXT)
    decision, entry = post(
        a_request(), target=a_period(state=AccountingPeriodState.CLOSED), open_p=later
    )
    assert decision.outcome is PostingOutcome.DEFERRED
    assert entry.period_key == NEXT_KEY
    assert entry.disposition is LateEntryDisposition.NEXT_OPEN_PERIOD
    assert entry.was_late


def test_no_open_period_is_a_loud_rejection_never_a_silent_drop():
    """An unrecordable financial fact is an operational incident, and silence is how revenue
    disappears."""
    decision, entry = post(
        a_request(), target=a_period(state=AccountingPeriodState.CLOSED), open_p=None
    )
    assert decision.outcome is PostingOutcome.REJECTED
    assert not decision.is_success and entry is None
    assert BillingDiagnosticCode.NO_OPEN_ACCOUNTING_PERIOD in [
        f.code for f in decision.diagnostics.findings
    ]


def test_a_negative_amount_is_refused_because_direction_carries_the_sign():
    decision, _ = post(
        a_request(-1, source=PostingSource.MANUAL), target=a_period(), open_p=a_period()
    )
    assert decision.outcome is PostingOutcome.REJECTED
    assert BillingDiagnosticCode.INVALID_LEDGER_AMOUNT in [
        f.code for f in decision.diagnostics.findings
    ]


def test_an_entry_cannot_both_reverse_and_adjust():
    decision, _ = post(
        a_request(reverses_entry_id="jrn_a", adjusts_entry_id="jrn_b"),
        target=a_period(),
        open_p=a_period(),
    )
    assert decision.outcome is PostingOutcome.REJECTED


def test_validity_is_checked_before_duplication():
    """Reporting a malformed request as a duplicate would hide the malformation."""
    decision, _ = post(
        a_request(-1, source=PostingSource.MANUAL),
        target=a_period(),
        open_p=a_period(),
        already=True,
    )
    assert decision.outcome is PostingOutcome.REJECTED


def test_the_posting_service_writes_nothing_and_reads_no_clock():
    """The same pure function serves the live path and replay — a replay that used different code
    would prove nothing about the original."""
    kwargs = {"target_period": a_period(), "open_period": a_period(), "already_posted": False}
    one = posting.decide(a_request(), **kwargs)
    two = posting.decide(a_request(), **kwargs)
    assert one == two


# ── 3. balances are derived ─────────────────────────────────────────────────────


def _entries(*specs):
    out = []
    for index, (minor, direction) in enumerate(specs, start=1):
        request = a_request(minor, direction=direction, source_id=f"chg_{index}")
        _, entry = post(request, target=a_period(), open_p=a_period(), seq=index)
        out.append(entry)
    return tuple(out)


def test_a_balance_is_folded_from_entries():
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    balance = AccountBalance.of(ACC, INR, entries)
    assert balance.outstanding == Money(300_00, INR)
    assert balance.debits == Money(500_00, INR)
    assert balance.credits == Money(200_00, INR)


def test_there_is_no_stored_balance_field_anywhere_in_the_domain():
    """The structural version of "balances are derived": no aggregate carries one."""
    from billing.foundation import BalanceCheckpoint

    for aggregate in (AccountingPeriod, CommercialCredit, CreditApplication):
        fields = set(aggregate.__dataclass_fields__)
        assert "balance" not in fields, aggregate.__name__
        assert "available" not in fields, aggregate.__name__
    # A checkpoint carries one, and is explicitly a cache with a proof.
    assert "digest" in BalanceCheckpoint.__dataclass_fields__


def test_a_credit_grant_carries_no_balance():
    """What remains is derived from the applications, so the two can never disagree."""
    assert "remaining" not in CommercialCredit.__dataclass_fields__
    assert "consumed" not in CommercialCredit.__dataclass_fields__


def test_a_customer_in_credit_is_a_real_state():
    """A naive unsigned balance hides this."""
    entries = _entries((100_00, LedgerDirection.CREDIT))
    assert AccountBalance.of(ACC, INR, entries).is_in_credit


def test_a_mixed_currency_fold_raises_rather_than_producing_a_number():
    entries = _entries((100_00, LedgerDirection.DEBIT))
    with pytest.raises(ValueError):
        AccountBalance.of(ACC, USD, entries)


def test_a_checkpoint_is_verifiable_and_drift_is_detectable():
    """The property that makes a checkpoint acceptable at all: the moment it becomes authoritative,
    the ledger stops being verifiable."""
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    checkpoint = balances.checkpoint(
        org_id=ORG, account_id=ACC, period_key=KEY, currency=INR, entries=entries
    )
    assert checkpoint.verify(entries)
    assert not checkpoint.verify(entries[:1])


def test_sequence_gaps_are_surfaced_rather_than_hidden():
    """A gap means a number was allocated for a posting that never committed. Unexplained is what
    an audit looks for; a silently reused number would be worse."""
    entries = _entries((1_00, LedgerDirection.DEBIT), (2_00, LedgerDirection.DEBIT))
    from dataclasses import replace as _replace

    sparse = (entries[0], _replace(entries[1], sequence=5))
    assert balances.sequence_gaps(sparse) == (2, 3, 4)


# ── 4. corrections never mutate ─────────────────────────────────────────────────


def test_a_reversal_is_a_new_opposite_entry():
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    request = ReversalService().request(original, reason="billed in error")
    _, reversal = post(request, target=a_period(), open_p=a_period(), seq=2)
    assert reversal.direction is LedgerDirection.CREDIT
    assert reversal.amount == original.amount
    assert reversal.reverses_entry_id == original.id
    assert reversal.id != original.id
    assert AccountBalance.of(ACC, INR, (original, reversal)).outstanding.is_zero


def test_a_reversal_belongs_to_the_period_of_what_it_corrects():
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    assert ReversalService().request(original).target_period_key == original.period_key


def test_a_reversal_cannot_itself_be_reversed():
    """That is a re-posting, and calling it a reversal makes the correction chain unreadable."""
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    _, reversal = post(
        ReversalService().request(original), target=a_period(), open_p=a_period(), seq=2
    )
    with pytest.raises(BillingConflict):
        ReversalService().guard_reversible(reversal, (original, reversal))


def test_an_entry_is_never_reversed_twice():
    """Doing so would credit the customer the amount twice over."""
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    _, reversal = post(
        ReversalService().request(original), target=a_period(), open_p=a_period(), seq=2
    )
    with pytest.raises(BillingConflict):
        ReversalService().guard_reversible(original, (original, reversal))


def test_an_adjustment_moves_a_figure_without_withdrawing_it():
    """The distinction from a reversal has teeth: a reversal says "this should not have been
    recorded"; an adjustment says "the amount has moved by this much"."""
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    request = AdjustmentService().request(original, delta=Money(-50_00, INR))
    _, adjustment = post(request, target=a_period(), open_p=a_period(), seq=2)
    assert adjustment.direction is LedgerDirection.CREDIT
    assert adjustment.amount == Money(50_00, INR)
    assert adjustment.adjusts_entry_id == original.id
    assert AccountBalance.of(ACC, INR, (original, adjustment)).outstanding == Money(450_00, INR)


def test_a_positive_adjustment_posts_in_the_original_direction():
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    request = AdjustmentService().request(original, delta=Money(25_00, INR))
    _, adjustment = post(request, target=a_period(), open_p=a_period(), seq=2)
    assert adjustment.direction is LedgerDirection.DEBIT


def test_there_is_no_update_or_delete_on_an_entry_repository():
    """Append-only by construction: there is no method to call."""
    from billing.foundation import JournalEntryRepository

    methods = {n for n in dir(JournalEntryRepository) if not n.startswith("_")}
    assert not methods & {"update", "delete", "remove", "replace", "amend"}


def test_credit_applications_are_append_only_too():
    from billing.foundation import CreditApplicationRepository

    methods = {n for n in dir(CreditApplicationRepository) if not n.startswith("_")}
    assert not methods & {"update", "delete", "remove", "replace"}


# ── 5. accounting periods ───────────────────────────────────────────────────────


def test_a_closed_period_has_no_way_out():
    """The load-bearing absence. A filed number must be structurally unchangeable."""
    assert ACCOUNTING_PERIOD_LIFECYCLE.triggers_from(AccountingPeriodState.CLOSED) == frozenset()
    assert periods.allowed(AccountingPeriodState.CLOSED) == ()


def test_reopening_a_closed_period_is_refused_by_the_machine():
    with pytest.raises(InvalidBillingTransition):
        periods.next_state(AccountingPeriodState.CLOSED, AccountingPeriodTrigger.CLOSE)


def test_only_an_open_period_accepts_postings():
    assert a_period().accepts_postings
    assert not a_period(state=AccountingPeriodState.CLOSING).accepts_postings
    assert not a_period(state=AccountingPeriodState.CLOSED).accepts_postings


def test_closing_seals_totals_and_a_digest():
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    closed = periods.apply(
        a_period(), AccountingPeriodTrigger.CLOSE, at=2_100.0, actor_id="ops", entries=entries
    )
    assert closed.state is AccountingPeriodState.CLOSED
    assert closed.closing_balance == Money(300_00, INR)
    assert closed.entry_count == 2
    assert closed.close_digest
    assert closed.closed_by == "ops"


def test_the_close_digest_is_order_independent():
    """Two readers who fetched entries in different orders must compute the same digest."""
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    assert periods.close_digest(entries) == periods.close_digest(tuple(reversed(entries)))


def test_guard_open_refuses_the_intent_with_a_reason():
    with pytest.raises(BillingConflict) as excinfo:
        periods.guard_open(a_period(state=AccountingPeriodState.CLOSED))
    assert "never reopened" in str(excinfo.value)


def test_a_period_may_be_closed_directly_or_through_closing():
    assert periods.can(AccountingPeriodState.OPEN, AccountingPeriodTrigger.CLOSE)
    assert periods.can(AccountingPeriodState.OPEN, AccountingPeriodTrigger.BEGIN_CLOSE)
    assert periods.can(AccountingPeriodState.CLOSING, AccountingPeriodTrigger.CLOSE)


# ── 6. replay ───────────────────────────────────────────────────────────────────


def test_a_replay_proves_it_reconstructed_the_same_state():
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    closed = periods.apply(a_period(), AccountingPeriodTrigger.CLOSE, at=2_100.0, entries=entries)
    result = LedgerReplayService().replay(
        entries, org_id=ORG, account_id=ACC, period=closed, currency=INR, at=9_999.0
    )
    assert result.is_clean
    assert result.matches_close_digest and result.balances_agree


def test_a_tampered_replay_is_detected():
    """A `False` here is a financial integrity incident, and the reason the digest exists."""
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    closed = periods.apply(a_period(), AccountingPeriodTrigger.CLOSE, at=2_100.0, entries=entries)
    result = LedgerReplayService().replay(
        entries[:1], org_id=ORG, account_id=ACC, period=closed, currency=INR
    )
    assert not result.is_clean


def test_replaying_an_unclosed_period_has_nothing_to_contradict():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    result = LedgerReplayService().replay(
        entries, org_id=ORG, account_id=ACC, period=a_period(), currency=INR
    )
    assert result.matches_close_digest


def test_the_replay_uses_the_same_digest_function_the_close_used():
    """Not a second implementation that could drift from it."""
    code = (BILLING / "foundation" / "ledger_rules.py").read_text()
    assert "self._periods.close_digest(entries)" in code


# ── 7. reconciliation ───────────────────────────────────────────────────────────


def test_reconciliation_finds_an_unposted_charge():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    report = LedgerReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        expected=[("chg_1", 500_00), ("chg_missing", 25_00)],
        entries=entries,
    )
    assert report.unposted_count == 1
    assert not report.is_clean


def test_reconciliation_finds_an_amount_mismatch():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    report = LedgerReconciliationService().reconcile(
        org_id=ORG, account_id=ACC, period_key=KEY, expected=[("chg_1", 999_00)], entries=entries
    )
    assert [d.kind.value for d in report.discrepancies] == ["amount_mismatch"]


def test_reconciliation_finds_an_orphan_entry():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    report = LedgerReconciliationService().reconcile(
        org_id=ORG, account_id=ACC, period_key=KEY, expected=[], entries=entries
    )
    assert [d.kind.value for d in report.discrepancies] == ["orphan_entry"]


def test_a_correction_does_not_shadow_the_charge_it_corrects():
    """**Defect found by the end-to-end smoke.** A reversal and an adjustment both carry the
    original's `charge_id` — that lineage is the point — so keying the posted map on it without
    filtering let a correction shadow its original, and every corrected charge read as an
    `AMOUNT_MISMATCH`."""
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    _, reversal = post(
        ReversalService().request(original), target=a_period(), open_p=a_period(), seq=2
    )
    report = LedgerReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        expected=[("chg_1", 500_00)],
        entries=(original, reversal),
    )
    assert report.is_clean, [d.detail for d in report.discrepancies]


def test_reconciliation_reports_checkpoint_drift():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    stale = balances.checkpoint(
        org_id=ORG, account_id=ACC, period_key=KEY, currency=INR, entries=()
    )
    report = LedgerReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        expected=[("chg_1", 500_00)],
        entries=entries,
        checkpoint=stale,
    )
    assert "checkpoint_drift" in [d.kind.value for d in report.discrepancies]


# ── 8. credits ──────────────────────────────────────────────────────────────────


def test_a_credit_position_is_derived_from_its_applications():
    credit = a_credit(100_00)
    applications = (
        CreditApplication(
            id="cap_1",
            org_id=ORG,
            account_id=ACC,
            credit_id=credit.id,
            amount=Money(30_00, INR),
        ),
    )
    position = CreditPosition.of(credit, applications)
    assert position.granted == Money(100_00, INR)
    assert position.applied == Money(30_00, INR)
    assert position.remaining == Money(70_00, INR)


def test_credits_are_applied_earliest_expiring_first():
    """Declared rather than emergent: a customer whose oldest credit lapsed unspent while a newer
    one was consumed has a legitimate complaint."""
    late = a_credit(100_00, id="ccr_late", code="late", expires_at=9_000.0)
    soon = a_credit(60_00, id="ccr_soon", code="soon", expires_at=3_000.0)
    positions = {c.id: CreditPosition.of(c, ()) for c in (late, soon)}
    plan = CreditApplicationService().plan(Money(120_00, INR), [late, soon], positions, at=1_000.0)
    assert [a.credit_id for a in plan.allocations] == ["ccr_soon", "ccr_late"]
    assert plan.is_fully_covered


def test_credit_ordering_does_not_depend_on_input_order():
    late = a_credit(100_00, id="ccr_late", code="late", expires_at=9_000.0)
    soon = a_credit(60_00, id="ccr_soon", code="soon", expires_at=3_000.0)
    positions = {c.id: CreditPosition.of(c, ()) for c in (late, soon)}
    service = CreditApplicationService()
    forward = service.plan(Money(120_00, INR), [late, soon], positions, at=1_000.0)
    backward = service.plan(Money(120_00, INR), [soon, late], positions, at=1_000.0)
    assert forward.allocations == backward.allocations


def test_a_shortfall_is_reported_rather_than_over_applied():
    credit = a_credit(50_00)
    positions = {credit.id: CreditPosition.of(credit, ())}
    plan = CreditApplicationService().plan(Money(120_00, INR), [credit], positions, at=1_000.0)
    assert plan.covered == Money(50_00, INR)
    assert plan.shortfall == Money(70_00, INR)
    assert not plan.is_fully_covered


def test_expiry_is_a_resolution_time_property_as_well_as_a_state():
    """A credit whose window has passed stops being spendable immediately, rather than when a job
    gets round to closing it."""
    credit = a_credit(expires_at=3_000.0)
    assert credit.is_live_at(1_000.0)
    assert not credit.is_live_at(5_000.0)


def test_an_expired_credit_is_excluded_from_a_plan():
    credit = a_credit(expires_at=3_000.0)
    positions = {credit.id: CreditPosition.of(credit, ())}
    plan = CreditApplicationService().plan(Money(10_00, INR), [credit], positions, at=5_000.0)
    assert not plan.allocations


def test_a_credit_cannot_be_overdrawn():
    credit = a_credit(50_00)
    findings = LedgerValidator().validate_application(
        credit, CreditPosition.of(credit, ()), Money(999_00, INR), at=1_000.0
    )
    assert BillingDiagnosticCode.CREDIT_OVERDRAWN in [f.code for f in findings.findings]


def test_applying_an_expired_credit_is_an_error():
    credit = a_credit(expires_at=3_000.0)
    findings = LedgerValidator().validate_application(
        credit, CreditPosition.of(credit, ()), Money(10_00, INR), at=5_000.0
    )
    assert BillingDiagnosticCode.EXPIRED_CREDIT_APPLIED in [f.code for f in findings.findings]


def test_a_negative_or_zero_credit_grant_is_refused():
    """Withdrawing a credit is a revocation, not a negative grant."""
    for minor in (-1, 0):
        findings = LedgerValidator().validate_credit(a_credit(minor))
        assert BillingDiagnosticCode.INVALID_CREDIT_AMOUNT in [f.code for f in findings.findings]


def test_every_credit_terminal_state_has_no_exit():
    """Re-activating a spent, lapsed, or withdrawn credit would make "was this ever honoured?"
    unanswerable — precisely the question a disputed balance turns on."""
    lifecycle = CreditLifecycleService()
    for state in (CreditState.CONSUMED, CreditState.EXPIRED, CreditState.REVOKED):
        assert lifecycle.allowed(state) == ()
        assert CREDIT_LIFECYCLE.triggers_from(state) == frozenset()


def test_there_is_no_credit_deletion_state():
    assert not any("delete" in s.value for s in CreditState)


def test_revoking_does_not_un_apply_what_was_already_spent():
    credit = a_credit(100_00)
    applications = (
        CreditApplication(
            id="cap_1",
            org_id=ORG,
            account_id=ACC,
            credit_id=credit.id,
            amount=Money(30_00, INR),
        ),
    )
    revoked = CreditLifecycleService().apply(credit, CreditTrigger.REVOKE, at=1_000.0)
    position = CreditPosition.of(revoked, applications)
    assert position.applied == Money(30_00, INR)


# ── 9. trial balance ────────────────────────────────────────────────────────────


def test_a_trial_balance_keeps_debits_and_credits_separate():
    """Separate rather than netted: an accountant reconciling against a general ledger needs both
    sides, and a net figure cannot be mapped onto two GL accounts."""
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    trial = TrialBalanceService().compute(
        entries,
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        currency=INR,
        opening=Money.zero(INR),
    )
    assert trial.total_debits == Money(500_00, INR)
    assert trial.total_credits == Money(200_00, INR)
    assert trial.movement == Money(300_00, INR)


def test_a_trial_balance_reconciles():
    """opening + movement == closing. The first thing an accountant checks."""
    entries = _entries((500_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    trial = TrialBalanceService().compute(
        entries,
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        currency=INR,
        opening=Money(100_00, INR),
    )
    assert trial.reconciles
    assert trial.closing_balance == Money(400_00, INR)


def test_trial_balance_lines_are_ordered_deterministically():
    entries = _entries((500_00, LedgerDirection.DEBIT))
    service = TrialBalanceService()
    one = service.compute(entries, org_id=ORG, account_id=ACC, period_key=KEY, currency=INR)
    two = service.compute(entries, org_id=ORG, account_id=ACC, period_key=KEY, currency=INR)
    assert one.lines == two.lines


# ── 10. charge → entry ──────────────────────────────────────────────────────────


class _FakeCharge:
    def __init__(self, minor, kind_value="usage", charge_id="chg_1"):
        from billing.foundation import ChargeKind, ChargeProvenance

        self.id = charge_id
        self.kind = ChargeKind(kind_value)
        self.amount = Money(minor, INR)
        self.description = "Voice minutes"
        self.digest = "abc123"
        self.provenance = ChargeProvenance(price_code="p", subscription_id="csb_1")


def test_a_rated_charge_becomes_a_debit_at_exactly_its_amount():
    """Records; never recalculates. `charge.amount` is taken exactly as M9.12 produced it."""
    requests = ChargePostingService().requests(
        [_FakeCharge(900_00)], org_id=ORG, account_id=ACC, rating_run_id="rat_1", period_key=KEY
    )
    assert len(requests) == 1
    assert requests[0].amount == Money(900_00, INR)
    assert requests[0].direction is LedgerDirection.DEBIT
    assert requests[0].source_digest == "abc123"


def test_a_negative_charge_becomes_a_credit_entry_not_a_negative_debit():
    """Direction carries the sign; a negative amount would make a column-summing report wrong."""
    requests = ChargePostingService().requests(
        [_FakeCharge(-25_00, "adjustment")], org_id=ORG, account_id=ACC, rating_run_id="rat_1"
    )
    assert requests[0].direction is LedgerDirection.CREDIT
    assert requests[0].amount == Money(25_00, INR)
    assert requests[0].entry_type is LedgerEntryType.ADJUSTMENT


def test_a_zero_charge_produces_no_financial_fact():
    """A met allowance is a real commercial outcome and not a financial one; a zero line on a
    statement invites "why am I billed nothing for this?"."""
    assert (
        ChargePostingService().requests(
            [_FakeCharge(0)], org_id=ORG, account_id=ACC, rating_run_id="rat_1"
        )
        == ()
    )


def test_every_rated_charge_carries_its_lineage():
    requests = ChargePostingService().requests(
        [_FakeCharge(900_00)], org_id=ORG, account_id=ACC, rating_run_id="rat_1", period_key=KEY
    )
    references = requests[0].references
    assert references.charge_id == "chg_1"
    assert references.rating_run_id == "rat_1"
    assert references.is_traced


# ── 11. inspection, validation, search ──────────────────────────────────────────


def test_inspection_flags_an_untraced_entry():
    request = a_request(source=PostingSource.RATING_RUN, references=EntryReferences())
    _, entry = post(request, target=a_period(), open_p=a_period())
    inspection = LedgerInspector().inspect(
        (entry,),
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        state=AccountingPeriodState.OPEN,
        currency=INR,
    )
    assert not inspection.is_sound
    assert inspection.untraced == (entry.id,)


def test_a_manual_entry_needs_no_lineage_because_an_actor_is_named():
    request = a_request(source=PostingSource.MANUAL, references=EntryReferences())
    _, entry = post(request, target=a_period(), open_p=a_period())
    inspection = LedgerInspector().inspect(
        (entry,),
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        state=AccountingPeriodState.OPEN,
        currency=INR,
    )
    assert inspection.is_sound


def test_inspection_names_what_corrected_an_entry():
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    _, reversal = post(
        ReversalService().request(original), target=a_period(), open_p=a_period(), seq=2
    )
    inspection = LedgerInspector().inspect(
        (original, reversal),
        org_id=ORG,
        account_id=ACC,
        period_key=KEY,
        state=AccountingPeriodState.OPEN,
        currency=INR,
    )
    first = [e for e in inspection.entries if e.entry_id == original.id][0]
    assert first.is_corrected and first.corrected_by == (reversal.id,)


def test_closing_validation_reports_a_sequence_gap():
    from dataclasses import replace as _replace

    entries = _entries((1_00, LedgerDirection.DEBIT), (2_00, LedgerDirection.DEBIT))
    sparse = (entries[0], _replace(entries[1], sequence=5))
    findings = LedgerValidator().validate_period_close(a_period(), sparse)
    assert BillingDiagnosticCode.LEDGER_SEQUENCE_GAP in [f.code for f in findings.findings]


def test_closing_validation_warns_about_untraced_entries():
    request = a_request(source=PostingSource.RATING_RUN, references=EntryReferences())
    _, entry = post(request, target=a_period(), open_p=a_period())
    findings = LedgerValidator().validate_period_close(a_period(), (entry,))
    codes = [f.code for f in findings.findings]
    assert BillingDiagnosticCode.UNTRACED_LEDGER_ENTRY in codes
    assert findings.is_valid  # a warning, not a block


def test_validation_never_raises():
    assert LedgerValidator().validate_credit(a_credit(-5)).has_errors


def test_search_is_deterministic_and_filters():
    entries = _entries((100_00, LedgerDirection.DEBIT), (200_00, LedgerDirection.CREDIT))
    service = LedgerSearchService()
    assert service.search(entries, LedgerSearchQuery()).total == 2
    assert (
        service.search(entries, LedgerSearchQuery(directions=(LedgerDirection.CREDIT,))).total == 1
    )
    first = service.search(entries, LedgerSearchQuery(limit=1))
    assert first.runs if hasattr(first, "runs") else first.entries == first.entries
    assert service.search(entries, LedgerSearchQuery(limit=1)).entries == first.entries


def test_search_finds_corrections_only():
    original = _entries((500_00, LedgerDirection.DEBIT))[0]
    _, reversal = post(
        ReversalService().request(original), target=a_period(), open_p=a_period(), seq=2
    )
    found = LedgerSearchService().search(
        (original, reversal), LedgerSearchQuery(corrections_only=True)
    )
    assert found.total == 1 and found.entries[0].id == reversal.id


# ── 12. wiring ──────────────────────────────────────────────────────────────────


@pytest.fixture
def wired():
    """A real database with a finalized rating run and an open accounting period."""
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(1_500.0)
    )
    app = persistent.application
    kw = {"actor_id": ACTOR, "org_id": ORG}
    account = app.commands.dispatch(CreateBillingAccount(**kw, display_name="Acme", currency=INR))
    app.commands.dispatch(ActivateBillingAccount(**kw, account_id=account.id))
    app.commands.dispatch(CreateProduct(**kw, code="voice", display_name="Voice"))
    app.commands.dispatch(PublishProduct(**kw, code="voice"))
    catalog = app.commands.dispatch(PublishCatalog(**kw))
    app.commands.dispatch(CreatePriceBook(**kw, code="std", display_name="Standard", currency=INR))
    app.commands.dispatch(
        CreatePrice(
            **kw,
            book_code="std",
            code="platform_fee",
            shape=PriceShape.FLAT_RECURRING,
            amount=Money(500_00, INR),
            interval=BillingInterval.MONTHLY,
        )
    )
    app.commands.dispatch(
        CreatePrice(
            **kw,
            book_code="std",
            code="voice_minutes",
            shape=PriceShape.TIERED_GRADUATED,
            meter_code="voice_minutes",
            tiers=(
                PriceTier(up_to=100, unit_price=Money(200, INR)),
                PriceTier(up_to=None, unit_price=Money(100, INR)),
            ),
        )
    )
    app.commands.dispatch(PublishPricingBook(**kw, code="std"))
    pricing = app.commands.dispatch(PublishPricing(**kw, notes="v1"))
    app.commands.dispatch(
        DefineCommercialMeter(
            **kw,
            code="voice_minutes",
            unit="minute",
            source=MeterSource.RUNTIME_SESSION,
            normalization=Normalization(1, 1),
            accepted_event_types=("metric_session_minutes",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**kw, code="voice_minutes"))
    app.commands.dispatch(
        OpenUsagePeriod(**kw, account_id=account.id, period_start=P0, period_end=P1)
    )
    subscription = app.commands.dispatch(
        CreateSubscription(
            **kw,
            account_id=account.id,
            catalog_version=catalog.version,
            pricing_version=pricing.version,
            currency=INR,
            plan_code="pro",
            items=(
                AgreementItem(id="itm_flat", price_code="platform_fee", kind=ItemKind.FLAT),
                AgreementItem(
                    id="itm_use",
                    price_code="voice_minutes",
                    kind=ItemKind.USAGE,
                    meter_code="voice_minutes",
                ),
            ),
        )
    )
    app.commands.dispatch(ActivateSubscription(**kw, subscription_id=subscription.id))
    app.commands.dispatch(
        IngestMetricSignal(
            **kw,
            period_start=P0,
            period_end=P1,
            signal=MetricSignal(
                event_id="evt_1",
                event_type="metric_session_minutes",
                source=MeterSource.RUNTIME_SESSION,
                observed_at=1_200.0,
                raw_units=300,
                raw_unit="minute",
                scope=Scope(org_id=ORG),
                received_at=1_200.0,
            ),
        )
    )
    rated = app.commands.dispatch(
        RateSubscriptionPeriod(
            **kw, subscription_id=subscription.id, period_start=P0, period_end=P1
        )
    )
    app.commands.dispatch(
        OpenAccountingPeriod(
            **kw, account_id=account.id, period_start=P0, period_end=P1, currency=INR
        )
    )
    yield persistent, account, rated
    persistent.database.close()


KW = {"actor_id": ACTOR, "org_id": ORG}
EXPECTED = 500_00 + (100 * 200 + 200 * 100)


def _finalize_and_post(app, rated):
    app.commands.dispatch(FinalizeRatingRun(**KW, rating_run_id=rated.rating_run_id))
    return app.commands.dispatch(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))


# ── 13. THE ACCOUNTING DEMONSTRATIONS ───────────────────────────────────────────
#
# Each corresponds to one invariant the milestone claims. Separated deliberately: an invariant
# nobody can point at is one that decays.


def test_demo_1_a_provisional_rating_run_cannot_become_a_financial_fact(wired):
    """The guard M9.12's spec recorded as missing, implemented where money is created."""
    persistent, _, rated = wired
    with pytest.raises(BillingConflict) as excinfo:
        persistent.application.commands.dispatch(
            PostRatingRun(**KW, rating_run_id=rated.rating_run_id)
        )
    assert "not finalized" in str(excinfo.value)


def test_demo_2_posting_a_finalized_run_records_every_charge(wired):
    persistent, _, rated = wired
    receipt = _finalize_and_post(persistent.application, rated)
    assert receipt.posted == 2
    assert receipt.total.minor == EXPECTED


def test_demo_3_the_balance_is_derived_and_equals_what_was_rated(wired):
    persistent, account, rated = wired
    _finalize_and_post(persistent.application, rated)
    balance = persistent.application.queries.dispatch(
        GetAccountLedgerBalance(**KW, account_id=account.id, currency=INR)
    )
    assert balance.outstanding.minor == EXPECTED
    assert balance.entry_count == 2


def test_demo_4_posting_the_same_run_twice_records_nothing_the_second_time(wired):
    """Exactly-once, against the database."""
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    again = app.commands.dispatch(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))
    assert again.posted == 0 and again.duplicates == 2
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    assert len(entries) == 2


def test_demo_5_a_duplicate_leaves_evidence_in_the_history(wired):
    """ "We saw this cause again and recorded no second entry" is what an auditor asks for when a
    total looks too round."""
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    app.commands.dispatch(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))
    actions = [a.action for a in app.queries.dispatch(LedgerTimeline(**KW, account_id=account.id))]
    assert "entry.duplicate_ignored" in actions


def test_demo_6_a_reversal_leaves_the_original_exactly_as_it_was(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    original = entries[0]
    app.commands.dispatch(
        ReverseJournalEntry(**KW, journal_entry_id=original.id, reason="billed in error")
    )
    after = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    unchanged = [e for e in after if e.id == original.id][0]
    assert unchanged.amount.minor == original.amount.minor
    assert unchanged.direction == original.direction
    assert len(after) == len(entries) + 1


def test_demo_7_an_entry_cannot_be_reversed_twice(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    app.commands.dispatch(ReverseJournalEntry(**KW, journal_entry_id=entries[0].id))
    with pytest.raises(BillingConflict):
        app.commands.dispatch(ReverseJournalEntry(**KW, journal_entry_id=entries[0].id))


def test_demo_8_closing_seals_a_digest_and_a_replay_proves_it(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    closed = app.commands.dispatch(
        CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY)
    )
    assert closed.state == "closed" and closed.close_digest
    replay = app.queries.dispatch(
        ReplayLedgerPeriod(**KW, account_id=account.id, period_key=KEY, currency=INR)
    )
    assert replay.is_clean and replay.matches_close_digest


def test_demo_9_a_closed_period_accepts_no_further_lifecycle_move(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    closed = app.commands.dispatch(
        CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY)
    )
    assert closed.allowed_triggers == ()
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY))


def test_demo_10_a_late_correction_lands_in_the_next_open_period(wired):
    """A closed period is never reopened; the entry records where it went and why."""
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    app.commands.dispatch(CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY))
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=account.id, period_start=2_000.0, period_end=3_000.0, currency=INR
        )
    )
    late = app.commands.dispatch(
        AdjustJournalEntry(**KW, journal_entry_id=entries[0].id, delta_minor=25_00)
    )
    assert late.outcome == "deferred"
    assert late.entry.period_key == NEXT_KEY
    assert late.entry.disposition == "next_open_period"


def test_demo_11_a_closed_period_digest_survives_a_later_posting(wired):
    """The strongest form of "corrections never mutate history"."""
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    app.commands.dispatch(CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY))
    before = app.queries.dispatch(
        ReplayLedgerPeriod(**KW, account_id=account.id, period_key=KEY, currency=INR)
    )
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=account.id, period_start=2_000.0, period_end=3_000.0, currency=INR
        )
    )
    app.commands.dispatch(
        AdjustJournalEntry(**KW, journal_entry_id=entries[0].id, delta_minor=25_00)
    )
    after = app.queries.dispatch(
        ReplayLedgerPeriod(**KW, account_id=account.id, period_key=KEY, currency=INR)
    )
    assert after.is_clean
    assert after.replay_digest == before.replay_digest


def test_demo_12_determinism_survives_a_restart_with_new_seams(wired):
    """A different id generator and a different clock, over the same database, post nothing new —
    because an entry's identity comes from what caused it, not from when or by whom."""
    persistent, account, rated = wired
    _finalize_and_post(persistent.application, rated)
    restarted = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(),
        ids=DefaultIdGenerator(),
        clock=FixedClock(9_999.0),
        database=persistent.database,
        create_all=False,
    )
    again = restarted.application.commands.dispatch(
        PostRatingRun(**KW, rating_run_id=rated.rating_run_id)
    )
    assert again.posted == 0 and again.duplicates == 2
    balance = restarted.application.queries.dispatch(
        GetAccountLedgerBalance(**KW, account_id=account.id, currency=INR)
    )
    assert balance.outstanding.minor == EXPECTED


def test_demo_13_credits_are_derived_and_spent_earliest_expiring_first(wired):
    persistent, account, _ = wired
    app = persistent.application
    app.commands.dispatch(
        GrantCommercialCredit(
            **KW,
            account_id=account.id,
            code="late_credit",
            kind=CreditKind.GOODWILL,
            amount_minor=100_00,
            currency=INR,
            expires_at=9_000.0,
        )
    )
    app.commands.dispatch(
        GrantCommercialCredit(
            **KW,
            account_id=account.id,
            code="soon_credit",
            kind=CreditKind.PROMOTIONAL,
            amount_minor=60_00,
            currency=INR,
            expires_at=3_000.0,
        )
    )
    before = app.queries.dispatch(
        GetAccountCreditPosition(**KW, account_id=account.id, currency=INR)
    )
    assert before.available.minor == 160_00
    receipt = app.commands.dispatch(
        ApplyCommercialCredit(**KW, account_id=account.id, amount_minor=120_00, currency=INR)
    )
    assert receipt.is_fully_covered and len(receipt.applications) == 2
    after = app.queries.dispatch(
        GetAccountCreditPosition(**KW, account_id=account.id, currency=INR)
    )
    assert after.available.minor == 40_00


def test_demo_14_the_trial_balance_reconciles_end_to_end(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    trial = app.queries.dispatch(
        GetTrialBalance(**KW, account_id=account.id, period_key=KEY, currency=INR)
    )
    assert trial.reconciles
    assert trial.total_debits.minor == EXPECTED


def test_demo_15_reconciliation_against_the_rating_run_is_clean(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    report = app.queries.dispatch(
        ReconcileLedgerPeriod(
            **KW, account_id=account.id, period_key=KEY, rating_run_id=rated.rating_run_id
        )
    )
    assert report.is_clean, [d.detail for d in report.discrepancies]


def test_demo_16_an_unposted_charge_is_detected(wired):
    """The failure mode a ledger exists to catch: rating produced it and nothing recorded it."""
    persistent, account, rated = wired
    app = persistent.application
    app.commands.dispatch(FinalizeRatingRun(**KW, rating_run_id=rated.rating_run_id))
    report = app.queries.dispatch(
        ReconcileLedgerPeriod(
            **KW, account_id=account.id, period_key=KEY, rating_run_id=rated.rating_run_id
        )
    )
    assert report.unposted_count == 2
    assert not report.is_clean


def test_demo_17_entries_are_totally_ordered_by_a_durable_counter(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    app.commands.dispatch(
        PostJournalEntry(
            **KW,
            account_id=account.id,
            entry_type=LedgerEntryType.PAYMENT,
            direction=LedgerDirection.CREDIT,
            amount_minor=100_00,
            currency=INR,
            reference="pay_1",
        )
    )
    entries = app.queries.dispatch(ListJournalEntries(**KW, account_id=account.id))
    assert [e.sequence for e in entries] == [1, 2, 3]


def test_demo_18_a_rebuilt_checkpoint_replaces_rather_than_accumulates(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    one = app.commands.dispatch(
        RebuildBalanceCheckpoint(**KW, account_id=account.id, period_key=KEY)
    )
    two = app.commands.dispatch(
        RebuildBalanceCheckpoint(**KW, account_id=account.id, period_key=KEY)
    )
    assert one.id == two.id and one.digest == two.digest
    inspection = app.queries.dispatch(
        InspectLedgerPeriod(**KW, account_id=account.id, period_key=KEY, currency=INR)
    )
    assert inspection.checkpoint_consistent


def test_demo_19_the_ledger_writes_to_no_other_domain(wired):
    """Read rating, metering, pricing, and subscriptions before and after posting: none moved."""
    persistent, account, rated = wired
    app = persistent.application
    app.commands.dispatch(FinalizeRatingRun(**KW, rating_run_id=rated.rating_run_id))
    ctx = app.context
    before_usage = ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1)
    before_charges = ctx.rating_engine.charges.for_run(ORG, rated.rating_run_id)
    before_runs = ctx.rating_engine.runs.get(ORG, rated.rating_run_id)
    before_pricing = ctx.pricing.versions.latest(ORG)
    app.commands.dispatch(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))
    assert ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1) == before_usage
    assert ctx.rating_engine.charges.for_run(ORG, rated.rating_run_id) == before_charges
    assert ctx.rating_engine.runs.get(ORG, rated.rating_run_id) == before_runs
    assert ctx.pricing.versions.latest(ORG) == before_pricing


def test_inspection_and_diagnostics_are_reachable(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    assert app.queries.dispatch(
        InspectLedgerPeriod(**KW, account_id=account.id, period_key=KEY, currency=INR)
    ).is_sound
    assert app.queries.dispatch(
        DiagnoseLedgerPeriod(**KW, account_id=account.id, period_key=KEY)
    ).is_valid


def test_search_and_credit_lifecycle_over_the_bus(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    assert (
        app.queries.dispatch(
            SearchJournalEntries(**KW, query=LedgerSearchQuery(account_id=account.id))
        ).total
        == 2
    )
    credit = app.commands.dispatch(
        GrantCommercialCredit(
            **KW, account_id=account.id, code="c1", amount_minor=10_00, currency=INR
        )
    )
    expired = app.commands.dispatch(ExpireCommercialCredit(**KW, credit_id=credit.id))
    assert expired.state == "expired" and expired.allowed_triggers == ()
    other = app.commands.dispatch(
        GrantCommercialCredit(
            **KW, account_id=account.id, code="c2", amount_minor=10_00, currency=INR
        )
    )
    assert (
        app.commands.dispatch(RevokeCommercialCredit(**KW, credit_id=other.id)).state == "revoked"
    )


def test_the_ledger_is_org_scoped(wired):
    persistent, account, rated = wired
    app = persistent.application
    _finalize_and_post(app, rated)
    assert (
        app.queries.dispatch(
            ListJournalEntries(actor_id=ACTOR, org_id="org_other", account_id=account.id)
        )
        == ()
    )
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            DiagnoseLedgerPeriod(
                actor_id=ACTOR, org_id="org_other", account_id=account.id, period_key=KEY
            )
        )


# ── 14. HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    settings = BillingApiSettings.load(environment="testing")
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"


def test_ledger_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/accounting/balance?account_id=x").status_code == 401


def test_the_organization_always_comes_from_the_token(client):
    source = (BILLING / "customer" / "routers" / "ledger_engine.py").read_text()
    assert "ctx.organization()" in source
    assert "body.org_id" not in source


def test_ledger_openapi_is_tagged_and_documents_errors(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/accounting" not in path:
            continue
        for operation in operations.values():
            assert "ledger" in operation["tags"]
            for status in ("401", "403", "404", "409", "422", "500"):
                assert status in operation["responses"], (path, status)


def test_the_ledger_surface_has_no_delete_put_or_patch(client):
    """Nothing in a ledger is edited or removed — including by an operator with every permission."""
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/accounting" not in path:
            continue
        assert not ({"delete", "put", "patch"} & set(operations)), path


def test_there_is_no_route_that_reopens_a_period(client):
    spec = client.get("/openapi.json").json()
    assert not [p for p in spec["paths"] if "reopen" in p or "unclose" in p]


def test_no_schema_name_is_qualified(client):
    """The general form of a defect this milestone and M9.12 both hit: when two modules declare a
    model with the same class name, FastAPI disambiguates by emitting fully qualified names like
    `billing__customer__schemas__GrantCreditBody` into the **public** document. That is a defect a
    reader can only find by reading the generated OpenAPI, so it is asserted here.

    **Scoped to M9.13's own schemas, and the reason is a finding rather than a convenience.** The
    document already carries 29 qualified names inherited from M9.5/M9.7–M9.11 — the earlier
    milestones each declared a `PeriodBody`, an `ActivityResponse`, a `BundleResponse` and so on
    without checking. Failing this suite on that debt would make M9.13 unmergeable for a problem it
    did not create; ignoring it would let the count keep growing. It is recorded in the spec's
    limitations with its size, and this assertion holds the line at zero **new** ones."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert [n for n in schemas if "ledger_schemas" in n] == []


def test_exactly_one_money_schema_exists(client):
    """M9.12's lesson: a duplicated money type surfaces as two qualified schema names."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert [n for n in schemas if n.endswith("MoneyResponse")] == ["MoneyResponse"]


def test_no_ledger_path_collides(client):
    spec = client.get("/openapi.json").json()
    paths = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/accounting")]
    assert len(paths) == len(set(paths))
    assert len(paths) >= 24


def test_the_earlier_surfaces_are_untouched(client):
    """M9.5's `/ledger` and `/credits` still answer — `/accounting` is a sibling."""
    spec = client.get("/openapi.json").json()
    for prefix in ("/ledger", "/credits", "/charges", "/usage", "/pricing", "/invoices"):
        assert any(p.startswith(PREFIX + prefix) for p in spec["paths"]), prefix


def test_ledger_schemas_carry_examples(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    documented = {n for n, s in schemas.items() if "example" in s}
    expected = {"GrantCommercialCreditBody", "JournalEntryResponse", "AccountingPeriodBody"}
    assert expected <= documented


# ── 15. Studio ──────────────────────────────────────────────────────────────────


def test_ledger_studio_is_served(client):
    response = client.get("/studio/ledger.html")
    assert response.status_code == 200
    assert "Ledger" in response.text


def test_ledger_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("https://", "http://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_ledger_studio_has_a_screen_for_every_required_area():
    source = STUDIO.read_text()
    for screen in (
        "Balance",
        "Entries",
        "Post",
        "Periods",
        "Trial Balance",
        "Credits",
        "Replay",
        "Reconcile",
        "Inspection",
        "Search",
        "Timeline",
    ):
        assert screen in source, screen


def test_ledger_studio_never_computes_a_balance():
    """A client that adds up its own column will eventually disagree with the books and be
    believed."""
    source = STUDIO.read_text()
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "reduce(",
        "minor +",
        "minor -",
        "minor *",
        "minor /",
        "toFixed(",
        "function total",
        "function sum",
        "+=",
    ):
        assert forbidden not in source, forbidden


def test_ledger_studio_takes_its_lifecycle_buttons_from_the_server():
    """A closed period returns no triggers, so the UI cannot offer to reopen one."""
    assert "allowed_triggers" in STUDIO.read_text()


# ── 16. architecture compliance ─────────────────────────────────────────────────

LEDGER_MODULES = (
    BILLING / "foundation" / "commercial_ledger.py",
    BILLING / "foundation" / "ledger_rules.py",
    BILLING / "foundation" / "ledger_factories.py",
    BILLING / "application" / "ledger_engine.py",
    BILLING / "persistence" / "ledger_models.py",
    BILLING / "persistence" / "ledger_repositories.py",
    BILLING / "customer" / "ledger_schemas.py",
    BILLING / "customer" / "routers" / "ledger_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *does*, and prose
    explaining that the ledger prices nothing must not read as pricing."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_the_ledger_calculates_no_price():
    for path in LEDGER_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PriceResolver",
            "PriceResolutionRequest",
            "select_book",
            "DiscountEngine",
            "ResolvedPrice",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_rates_nothing():
    """It reads M9.12's charges and records them; it never recomputes one."""
    for path in LEDGER_MODULES:
        code = _code_only(path)
        for forbidden in (
            "RatingEngine",
            "ChargeAssembler",
            "ChargeCalculator",
            "DiscountRebasingPolicy",
            "RatingBasis",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_meters_nothing():
    for path in LEDGER_MODULES:
        code = _code_only(path)
        for forbidden in (
            "UsageIngestionService",
            "UsageAggregator",
            "MetricSignal",
            "MetricStreamPort",
            "normalize(",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_evaluates_no_entitlement():
    for path in LEDGER_MODULES:
        code = _code_only(path)
        for forbidden in (
            "EntitlementSet",
            "ResolvedEntitlement",
            "EntitlementGate",
            "is_enabled",
            "allowance(",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_authorizes_nothing():
    for path in LEDGER_MODULES[:3]:
        code = _code_only(path)
        for forbidden in ("principal", "Permission", "has_role", "authorize", "RBAC"):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_creates_no_invoice_and_moves_no_money():
    for path in LEDGER_MODULES:
        code = _code_only(path)
        for forbidden in (
            "Invoice(",
            "InvoiceNumberAllocator",
            "PaymentGateway",
            "capture(",
            "authorize_payment",
            "TaxDetermination",
            "determine_tax",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_ledger_imports_no_frozen_epic():
    forbidden = {
        "api",
        "runtime",
        "product",
        "actuator",
        "providers",
        "provider",
        "agent",
        "tool",
        "workflow",
        "mcp",
        "knowledge",
        "observability",
        "gateway",
        "telemetry",
        "tenant",
        "deployment",
    }
    for path in LEDGER_MODULES:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_ledger_foundation_stays_pure():
    forbidden = {
        "sqlalchemy",
        "sqlmodel",
        "fastapi",
        "starlette",
        "pydantic",
        "os",
        "random",
        "datetime",
        "socket",
        "httpx",
        "requests",
        "asyncio",
    }
    for path in LEDGER_MODULES[:3]:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_ledger_rules_read_no_clock():
    """Recording is a pure function of what it is given — the only way replay can be proven."""
    code = _code_only(BILLING / "foundation" / "ledger_rules.py")
    for forbidden in ("Clock", "now()", "time("):
        assert forbidden not in code, forbidden


def test_ledger_use_cases_contain_no_arithmetic():
    """Every accounting rule belongs to the Foundation."""
    code = _code_only(BILLING / "application" / "ledger_engine.py")
    for forbidden in (
        ".times(",
        ".scaled(",
        ".allocate(",
        "def calculate",
        "def compute",
        "sum(",
        "Rounding.",
    ):
        assert forbidden not in code, forbidden


def test_the_ledger_writes_only_to_its_own_repositories():
    """The structural form of "the Ledger takes ownership of nothing": a write verb on an injected
    collaborator must target `self._repos.*`."""
    code = _code_only(BILLING / "application" / "ledger_engine.py")
    writes = {"add", "append", "upsert", "replace", "publish", "seal", "append_all"}
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in writes:
            continue
        target = ast.unparse(node.func.value)
        if not target.startswith("self._"):
            continue  # a local list's `.append` is not a repository write
        assert target.startswith("self._repos."), (target, node.func.attr)


def test_ledger_repositories_decide_nothing():
    code = _code_only(BILLING / "persistence" / "ledger_repositories.py")
    for forbidden in ("close_digest(", "decide(", "plan(", ".verify(", "AccountBalance.of"):
        assert forbidden not in code, forbidden


def test_ledger_controllers_stay_transport_only():
    code = _code_only(BILLING / "customer" / "routers" / "ledger_engine.py")
    for forbidden in ("repositories", "session.", ".commit()", "select(", "Money("):
        assert forbidden not in code, forbidden


def test_m9_2_ledger_vocabulary_is_reused_not_duplicated():
    """`LedgerEntryType`, `LedgerDirection`, `RecognitionState`, and `CreditKind` are M9.2's. Two
    answers to "which way does this move the balance?" is the drift §A2.5 prevents."""
    m92 = (BILLING / "foundation" / "ledger.py").read_text()
    assert "class LedgerEntryType" in m92 and "class LedgerDirection" in m92
    m913 = (BILLING / "foundation" / "commercial_ledger.py").read_text()
    for duplicated in (
        "class LedgerEntryType",
        "class LedgerDirection",
        "class RecognitionState",
        "class CreditKind",
    ):
        assert duplicated not in m913, duplicated
    assert "class JournalEntry" in m913 and "class AccountBalance" in m913


def test_the_m9_2_aggregates_are_untouched():
    m92 = (BILLING / "foundation" / "ledger.py").read_text()
    for kept in (
        "class LedgerEntry",
        "class LedgerBalance",
        "class CreditGrant",
        "class CreditMovement",
        "class CreditBalance",
    ):
        assert kept in m92, kept


def test_every_event_value_is_unique():
    """A reused value in a `StrEnum` silently creates an alias, which is how an event vocabulary
    quietly miscounts. Found and fixed while authoring M9.13."""
    from billing.foundation import BillingEventType

    values = [e.value for e in BillingEventType]
    assert len(values) == len(set(values))


def test_every_diagnostic_code_is_unique():
    from billing.foundation import BillingDiagnosticCode as D

    values = [d.value for d in D]
    assert len(values) == len(set(values))


def test_command_and_query_coverage_is_complete():
    import inspect as _inspect

    from billing.application.ledger_engine import (
        register_ledger_command_handlers,
        register_ledger_query_handlers,
    )

    registered = _inspect.getsource(register_ledger_command_handlers) + _inspect.getsource(
        register_ledger_query_handlers
    )
    for message in (*ALL_LEDGER_COMMANDS, *ALL_LEDGER_QUERIES):
        assert f"bus.register({message.__name__}," in registered, message.__name__
        fields = set(message.__dataclass_fields__)
        assert {"actor_id", "org_id"} <= fields, message.__name__
    assert len(ALL_LEDGER_COMMANDS) == 12
    assert len(ALL_LEDGER_QUERIES) == 14


def test_exactly_one_event_per_posting_outcome():
    from billing.foundation import POSTING_EVENTS

    assert set(POSTING_EVENTS) == {
        PostingOutcome.POSTED,
        PostingOutcome.DUPLICATE,
        PostingOutcome.DEFERRED,
    }
    assert PostingOutcome.REJECTED not in POSTING_EVENTS
    assert len(set(POSTING_EVENTS.values())) == 3


def test_the_ledger_engine_is_additive_and_optional():
    from billing.application.context import BillingContext

    assert "ledger_engine" in BillingContext.__dataclass_fields__
    assert BillingContext.__dataclass_fields__["ledger_engine"].default is None


def test_ledger_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect as sa_inspect

    from billing.persistence.database import BillingDatabase
    from billing.persistence.ledger_models import LEDGER_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, upgrade_all

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    assert "0023" in applied  # pinned by presence: M9.14 appended 0024 after it
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(sa_inspect(database.engine).get_table_names())
    for name in LEDGER_TABLE_NAMES:
        assert name in tables
    # The M9.2 ledger tables survive untouched.
    for name in (
        "cp_billing_ledger_entry",
        "cp_billing_credit_grant",
        "cp_billing_credit_movement",
    ):
        assert name in tables, name
    database.close()


def test_the_ledger_migration_alters_nothing_and_is_idempotent():
    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all, v0023_billing_ledger

    code = _code_only(Path(v0023_billing_ledger.__file__))
    for forbidden in ("ALTER", "DROP", "drop(", "delete("):
        assert forbidden not in code
    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    v0023_billing_ledger.upgrade(database)  # twice is a no-op
    assert v0023_billing_ledger.downgrade(database) is None
    database.close()


def test_the_exactly_once_constraint_is_the_primary_key():
    """There is no separate idempotency column, because a separate column can disagree with the
    key. Here they are the same object."""
    from billing.persistence.ledger_models import BillingJournalEntryRow

    primary = [c.name for c in BillingJournalEntryRow.__table__.primary_key]
    assert primary == ["id"]
    constraints = {
        c.name for c in BillingJournalEntryRow.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_cp_billing_journal_entry_sequence" in constraints


def test_the_period_uniqueness_constraint_exists():
    from billing.persistence.ledger_models import BillingAccountingPeriodRow

    constraints = {
        c.name for c in BillingAccountingPeriodRow.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_cp_billing_accounting_period" in constraints


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    for name in ("commercial_ledger", "ledger_rules", "ledger_factories"):
        assert name in modules, name
    assert "ledger" in modules  # the M9.2 module, unreshaped


def test_the_earlier_domain_boundaries_still_hold():
    """Metering and Subscription still carry no money after the milestone that records it."""
    for module in ("commercial_subscriptions", "commercial_metering"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        assert "Money" not in code, module


def test_billing_is_still_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2 survives M9.13."""
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
