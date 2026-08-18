"""M9.4 — Billing Persistence layer: integration tests.

Every test runs against a **real database** (SQLite in-memory, the same SQLModel/SQLAlchemy
stack as Postgres/Supabase) — no repository fakes, because the point of this milestone is
precisely the part a fake cannot prove: that revenue records survive a commit, vanish on a
rollback, and come back field-for-field identical.

Groups:
  1. round-trip + mapping fidelity (all thirty aggregates)
  2. organization isolation
  3. append-only / immutability enforcement
  4. transactions: atomicity, durability, event publication ordering
  5. cross-instance durability
  6. migration chain
  7. architecture compliance: dependency direction, no leakage, layers unchanged
"""

from __future__ import annotations

import ast
import inspect as pyinspect
from dataclasses import replace
from pathlib import Path

import pytest

from billing.application import (
    AllowAllAuthorizer,
    ApplicationEventType,
    DenyAllAuthorizer,
    DraftInvoice,
    GetAccount,
    GrantEntitlement,
    IssueInvoice,
    ListInvoices,
    OpenBillingAccount,
    PublishEntitlementSnapshot,
    RecordingApplicationEventEmitter,
    RecordUsage,
    UnitOfWorkClosedError,
)
from billing.foundation import (
    AccountKind,
    AccountStatus,
    BillingAccount,
    BillingAddress,
    BillingContact,
    BillingEvent,
    BillingEventType,
    BillingPeriod,
    BillingRun,
    BillingRunStage,
    ChargeLine,
    ChargeLineKind,
    CommercialCatalog,
    Commitment,
    CommitmentKind,
    Contract,
    ContractState,
    ContractTerm,
    CreditGrant,
    CreditKind,
    CreditMovement,
    CreditNote,
    Currency,
    Dispute,
    DisputeStage,
    DunningProcess,
    DunningState,
    EffectiveRange,
    Entitlement,
    EntitlementGate,
    EntitlementGrant,
    EntitlementSnapshot,
    Feature,
    FeatureKind,
    FixedClock,
    GateClassification,
    GrantSource,
    GstSplit,
    ImmutabilityViolation,
    Invoice,
    InvoiceNumber,
    InvoiceState,
    InvoiceTotals,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    License,
    LicenseKind,
    LicenseState,
    MeterAggregation,
    MeterDefinition,
    MeterSource,
    Money,
    Payment,
    PaymentInstrument,
    PaymentMethodKind,
    PaymentState,
    Plan,
    PlanFeature,
    Price,
    PriceBook,
    PriceBookAudience,
    PriceShape,
    PriceTier,
    ProviderReport,
    Quantity,
    RecognitionState,
    ReconciliationFinding,
    ReconciliationVerdict,
    Refund,
    RefundApproval,
    RefundDestination,
    RefundReason,
    RefundState,
    Rounding,
    Scope,
    SequentialIdGenerator,
    Subscription,
    SubscriptionItem,
    SubscriptionState,
    TaxComponent,
    TaxDetermination,
    TaxpayerKind,
    TaxProfile,
    TaxRegime,
    TaxRegistration,
    TaxTreatment,
    Tier,
    UsageAggregate,
    UsageQuarantine,
    UsageRecord,
    UsageSource,
    UsageStatus,
)
from billing.foundation import BillingInterval as Interval
from billing.persistence import (
    MIGRATIONS,
    SQL_REPOSITORIES,
    BillingDatabase,
    BillingUnitOfWorkFactory,
    SqlBillingAccountRepository,
    SqlBillingEventRepository,
    SqlBillingRunRepository,
    SqlCatalogSnapshotRepository,
    SqlContractRepository,
    SqlCreditNoteRepository,
    SqlCreditRepository,
    SqlDisputeRepository,
    SqlDunningRepository,
    SqlEntitlementGrantRepository,
    SqlEntitlementSnapshotRepository,
    SqlFeatureRepository,
    SqlInvoiceNumberAllocator,
    SqlInvoiceRepository,
    SqlLedgerRepository,
    SqlLicenseRepository,
    SqlMeterRepository,
    SqlPaymentInstrumentRepository,
    SqlPaymentRepository,
    SqlPlanRepository,
    SqlPriceBookRepository,
    SqlReconciliationRepository,
    SqlRefundRepository,
    SqlSubscriptionRepository,
    SqlTaxDeterminationRepository,
    SqlTaxProfileRepository,
    SqlUsageAggregateRepository,
    SqlUsageQuarantineRepository,
    SqlUsageRecordRepository,
    build_persistent_billing_application,
)
from billing.persistence.codec import decode, encode

INR = Currency.INR
PERIOD = BillingPeriod(0.0, 2_592_000.0)
PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"


@pytest.fixture()
def db() -> BillingDatabase:
    database = BillingDatabase()
    database.create_all()
    yield database
    database.close()


def _commit(database: BillingDatabase) -> None:
    database.session.commit()


# ── sample aggregates: one of every persisted type ─────────────────────────────


def _account(org="org_1", account_id="acc_1") -> BillingAccount:
    return BillingAccount(
        id=account_id,
        org_id=org,
        display_name="Acme",
        currency=INR,
        kind=AccountKind.PARTNER,
        status=AccountStatus.ACTIVE,
        contact=BillingContact(email="ap@acme.test", name="AP", phone="+91"),
        address=BillingAddress(line1="1 Road", city="Pune", region="MH", country="IN"),
        created_at=1.0,
    )


def _plan() -> Plan:
    return Plan(
        id="pln_1",
        org_id="",
        code="pro",
        display_name="Pro",
        tier=Tier.PRO,
        published=True,
        features=(PlanFeature("voice_minutes", allowance=1000, allows_overage=True),),
        metered_codes=("voice_minutes",),
    )


def _meter() -> MeterDefinition:
    return MeterDefinition(
        id="mtr_1",
        org_id="",
        code="voice_minutes",
        display_name="Voice minutes",
        unit="minute",
        aggregation=MeterAggregation.SUM,
        source=MeterSource.RUNTIME_SESSION,
        rounding=Rounding.HALF_EVEN,
    )


def _feature() -> Feature:
    return Feature(id="ftr_1", code="sso", display_name="SSO", kind=FeatureKind.FLAG)


def _price_book() -> PriceBook:
    return PriceBook(
        id="pbk_1",
        org_id="",
        code="in_public",
        display_name="India public",
        currency=INR,
        audience=PriceBookAudience.PUBLIC,
        published=True,
        prices=(
            Price(
                id="prc_1",
                price_book_id="pbk_1",
                code="pro_monthly",
                shape=PriceShape.TIERED_GRADUATED,
                currency=INR,
                effective=EffectiveRange(0.0, None),
                interval=Interval.MONTHLY,
                amount=Money(99900, INR),
                tiers=(
                    PriceTier(1000, Money(200, INR), Money(0, INR)),
                    PriceTier(None, Money(150, INR)),
                ),
            ),
        ),
    )


def _subscription() -> Subscription:
    return Subscription(
        id="sub_1",
        org_id="org_1",
        account_id="acc_1",
        plan_code="pro",
        plan_version=1,
        price_book_id="pbk_1",
        price_book_version=1,
        currency=INR,
        interval=Interval.MONTHLY,
        state=SubscriptionState.ACTIVE,
        items=(SubscriptionItem("pro_monthly", 1, quantity=3, meter_code="voice_minutes"),),
        current_period=PERIOD,
        started_at=1.0,
    )


def _grant(grant_id="ent_1") -> EntitlementGrant:
    return EntitlementGrant(
        id=grant_id,
        org_id="org_1",
        account_id="acc_1",
        entitlement=Entitlement(
            "voice_minutes", FeatureKind.QUOTA, allowance=1000, allows_overage=True
        ),
        source=GrantSource.PLAN,
        source_ref="pln_1",
        granted_at=1.0,
    )


def _snapshot(version=1) -> EntitlementSnapshot:
    return EntitlementSnapshot(
        id=f"ess_{version}",
        org_id="org_1",
        account_id="acc_1",
        version=version,
        issued_at=1.0,
        entitlements=(Entitlement("voice_minutes", FeatureKind.QUOTA, allowance=1000),),
        gates=(EntitlementGate("voice_minutes", FeatureKind.QUOTA, GateClassification.FAIL_OPEN),),
        subscription_id="sub_1",
    )


def _usage(event_id="evt_1", record_id="usg_1") -> UsageRecord:
    return UsageRecord(
        id=record_id,
        org_id="org_1",
        account_id="acc_1",
        meter_code="voice_minutes",
        quantity=Quantity(600, "minute"),
        scope=Scope("org_1"),
        period=PERIOD,
        source=UsageSource(event_id, "metric.session.completed", 1.0),
        status=UsageStatus.RECORDED,
        subscription_id="sub_1",
    )


def _aggregate(sealed=False) -> UsageAggregate:
    return UsageAggregate(
        id="uga_1",
        org_id="org_1",
        account_id="acc_1",
        meter_code="voice_minutes",
        scope=Scope("org_1"),
        period=PERIOD,
        aggregation=MeterAggregation.SUM,
        quantity=Quantity(1200, "minute"),
        record_count=2,
        sealed=sealed,
    )


def _ledger(entry_id="led_1") -> LedgerEntry:
    return LedgerEntry(
        id=entry_id,
        org_id="org_1",
        account_id="acc_1",
        entry_type=LedgerEntryType.CHARGE,
        direction=LedgerDirection.DEBIT,
        amount=Money(99900, INR),
        recorded_at=2.0,
        recognition=RecognitionState.DEFERRED,
        invoice_id="inv_1",
    )


def _line(line_id="chl_1") -> ChargeLine:
    return ChargeLine(
        id=line_id,
        org_id="org_1",
        account_id="acc_1",
        kind=ChargeLineKind.OVERAGE,
        description="Voice overage",
        amount=Money(40000, INR),
        period=PERIOD,
        meter_code="voice_minutes",
        quantity=Quantity(200, "minute"),
        unit_amount=Money(200, INR),
        usage_record_ids=("usg_1", "usg_2"),
    )


def _invoice(state=InvoiceState.DRAFT, invoice_id="inv_1") -> Invoice:
    number = (
        InvoiceNumber("VOX", "IN", 1, "VOX/IN/000001") if state is not InvoiceState.DRAFT else None
    )
    return Invoice(
        id=invoice_id,
        org_id="org_1",
        account_id="acc_1",
        currency=INR,
        period=PERIOD,
        state=state,
        number=number,
        lines=(_line(),),
        totals=InvoiceTotals(
            Money(40000, INR),
            Money(0, INR),
            Money(7200, INR),
            Money(0, INR),
            Money(47200, INR),
            Money(47200, INR),
        ),
        subscription_id="sub_1",
        evidence=("tax_det_1",),
    )


def _payment(payment_id="pay_1") -> Payment:
    return Payment(
        id=payment_id,
        org_id="org_1",
        account_id="acc_1",
        invoice_id="inv_1",
        amount=Money(47200, INR),
        state=PaymentState.CAPTURED,
        method=PaymentMethodKind.UPI,
        provider_code="razorpay",
        attempt=2,
        requested_at=3.0,
        reports=(ProviderReport("razorpay", "pay_x", "captured", Money(47200, INR), 4.0, True),),
    )


def _refund(refund_id="ref_1", key="rk_1") -> Refund:
    return Refund(
        id=refund_id,
        org_id="org_1",
        account_id="acc_1",
        payment_id="pay_1",
        invoice_id="inv_1",
        amount=Money(10000, INR),
        reason=RefundReason.GOODWILL,
        destination=RefundDestination.CREDIT_BALANCE,
        state=RefundState.APPROVED,
        request_key=key,
        approval=RefundApproval("usr_2", 5.0, "approved"),
    )


def _tax_profile() -> TaxProfile:
    return TaxProfile(
        id="txp_1",
        org_id="org_1",
        account_id="acc_1",
        regime=TaxRegime.INDIA_GST,
        taxpayer_kind=TaxpayerKind.BUSINESS,
        country="IN",
        place_of_supply="MH",
        registrations=(TaxRegistration(TaxRegime.INDIA_GST, "27AAA", "IN", True, 1.0),),
        updated_at=1.0,
    )


def _determination() -> TaxDetermination:
    return TaxDetermination(
        id="txd_1",
        org_id="org_1",
        account_id="acc_1",
        invoice_id="inv_1",
        regime=TaxRegime.INDIA_GST,
        treatment=TaxTreatment.STANDARD,
        taxable_amount=Money(40000, INR),
        tax_total=Money(7200, INR),
        components=(
            TaxComponent("CGST", 900, Money(3600, INR)),
            TaxComponent("SGST", 900, Money(3600, INR)),
        ),
        gst_split=GstSplit.INTRA_STATE,
        place_of_supply="MH",
    )


def _contract() -> Contract:
    return Contract(
        id="ctr_1",
        org_id="org_1",
        account_id="acc_1",
        code="ACME-2026",
        state=ContractState.ACTIVE,
        term=ContractTerm(0.0, 31_536_000.0, Interval.YEARLY, True, 2_592_000, 500),
        commitment=Commitment(
            CommitmentKind.MINIMUM_SPEND, Money(1_000_000, INR), Money(250_000, INR)
        ),
        entitlement_overrides=("sso",),
        signed_at=1.0,
    )


def _license(license_id="lic_1") -> License:
    return License(
        id=license_id,
        org_id="org_1",
        account_id="acc_1",
        kind=LicenseKind.SELF_HOSTED,
        state=LicenseState.ACTIVE,
        contract_id="ctr_1",
        entitlement_codes=("sso", "voice_minutes"),
        seat_limit=50,
    )


def _event(event_id="bev_1") -> BillingEvent:
    return BillingEvent(
        id=event_id,
        type=BillingEventType.INVOICE_ISSUED,
        ts=6.0,
        org_id="org_1",
        subject_id="inv_1",
        actor_id="usr_1",
        correlation_id="cor_1",
        data={"total_minor": 47200, "lines": 2},
    )


# ── 1. round-trip + mapping fidelity ───────────────────────────────────────────


def test_account_round_trip(db):
    repo = SqlBillingAccountRepository(db)
    original = _account()
    repo.add(original)
    _commit(db)
    assert repo.get("org_1", "acc_1") == original


@pytest.mark.parametrize(
    "aggregate",
    [
        _account(),
        _plan(),
        _meter(),
        _feature(),
        _price_book(),
        _subscription(),
        _grant(),
        _snapshot(),
        _usage(),
        _aggregate(),
        _ledger(),
        _invoice(InvoiceState.ISSUED),
        _payment(),
        _refund(),
        _tax_profile(),
        _determination(),
        _contract(),
        _license(),
        _event(),
        CommercialCatalog(plans=(_plan(),), features=(_feature(),), meters=(_meter(),), version=3),
        CreditGrant("crd_1", "org_1", "acc_1", CreditKind.PREPAID, Money(50000, INR), 1.0, 9.0),
        CreditMovement("crm_1", "org_1", "acc_1", "crd_1", Money(-10000, INR), 2.0, "inv_1"),
        CreditNote("cnt_1", "org_1", "acc_1", "inv_1", INR, Money(10000, INR), "goodwill", 7.0),
        BillingRun("brn_1", "org_1", "acc_1", PERIOD, BillingRunStage.RATED, "inv_1", 1.0, None),
        PaymentInstrument(
            "pmi_1", "org_1", "acc_1", PaymentMethodKind.CARD, "stripe", "tok", "4242", True
        ),
        DunningProcess("dun_1", "org_1", "acc_1", "pay_1", DunningState.RETRYING, 2, 1.0, 9.0),
        ReconciliationFinding(
            "pay_1",
            "razorpay",
            ReconciliationVerdict.AMOUNT_DIVERGED,
            Money(47200, INR),
            Money(47000, INR),
            8.0,
        ),
        Dispute("dsp_1", "org_1", "acc_1", "pay_1", Money(47200, INR), DisputeStage.WON, "stripe"),
        UsageQuarantine(
            "uqr_1", UsageSource("evt_9", "x", 1.0), "voice_minutes", "bad", 2.0, "org_1"
        ),
    ],
)
def test_codec_round_trip_is_lossless(aggregate):
    """`decode(type(a), encode(a)) == a` for every persisted aggregate. A mapping layer that
    is merely plausible is how revenue records quietly lose a field."""
    assert decode(type(aggregate), encode(aggregate)) == aggregate


def test_codec_encodes_to_json_portable_primitives():
    """Payloads must survive a JSON column on SQLite *and* Postgres — so enums become their
    values and tuples become lists, with no Python-only type left in the tree."""
    import json

    payload = encode(_invoice(InvoiceState.ISSUED))
    assert json.loads(json.dumps(payload)) == payload


def test_codec_tolerates_added_and_removed_fields():
    """Forward-tolerant, backward-safe: an unknown key from a newer writer is ignored, and a
    key absent because it did not exist yet falls back to the dataclass default. That is what
    lets the payload absorb additive Foundation change without a migration."""
    payload = encode(_account())
    payload["a_field_from_the_future"] = 42
    del payload["closed_at"]  # optional, added later
    restored = decode(BillingAccount, payload)
    assert restored.closed_at is None and restored.id == "acc_1"


def test_codec_refuses_to_invent_a_required_value():
    payload = encode(_account())
    del payload["currency"]  # required, no default
    with pytest.raises(KeyError):
        decode(BillingAccount, payload)


def test_every_repository_contract_has_one_implementation(db):
    """The 29 Foundation contracts, each implemented exactly once and instantiable."""
    from billing.foundation import repositories as contracts

    declared = {
        name
        for name, obj in vars(contracts).items()
        if isinstance(obj, type)
        and not name.startswith("_")
        and obj.__module__ == contracts.__name__
    }
    implemented = {contract.__name__ for contract, _ in SQL_REPOSITORIES}
    assert declared == implemented, declared ^ implemented
    assert len(SQL_REPOSITORIES) == 29
    for contract, implementation in SQL_REPOSITORIES:
        instance = implementation(db)
        assert isinstance(instance, contract)
        assert not pyinspect.isabstract(implementation)


def test_catalog_publish_and_version_lookup(db):
    repo = SqlCatalogSnapshotRepository(db)
    v1 = CommercialCatalog(plans=(_plan(),), version=1)
    v2 = CommercialCatalog(plans=(_plan(),), features=(_feature(),), version=2)
    repo.publish(v1)
    repo.publish(v2)
    _commit(db)
    assert repo.latest().version == 2
    assert repo.version(1) == v1  # an earlier version stays readable, unaltered


def test_price_book_and_plan_lookups(db):
    SqlPlanRepository(db).add(_plan())
    SqlMeterRepository(db).add(_meter())
    SqlFeatureRepository(db).add(_feature())
    SqlPriceBookRepository(db).add(_price_book())
    _commit(db)
    assert SqlPlanRepository(db).published() == (_plan(),)
    assert SqlMeterRepository(db).get("voice_minutes") == _meter()
    assert SqlFeatureRepository(db).all() == (_feature(),)
    assert SqlPriceBookRepository(db).get("pbk_1", 1) == _price_book()
    assert SqlPriceBookRepository(db).by_code("in_public") == (_price_book(),)


def test_whole_aggregate_replace_keeps_one_row(db):
    repo = SqlSubscriptionRepository(db)
    repo.add(_subscription())
    _commit(db)
    cancelled = replace(_subscription(), state=SubscriptionState.CANCELLED, cancelled_at=9.0)
    repo.replace(cancelled)
    _commit(db)
    assert repo.by_account("org_1", "acc_1") == (cancelled,)


def test_billing_run_upsert_is_keyed_by_account_and_period(db):
    repo = SqlBillingRunRepository(db)
    run = BillingRun("brn_1", "org_1", "acc_1", PERIOD, BillingRunStage.RATED)
    repo.upsert(run)
    repo.upsert(replace(run, stage=BillingRunStage.TAXED))
    _commit(db)
    # Re-running a period resumes rather than duplicates.
    assert repo.get("org_1", "acc_1", PERIOD).stage is BillingRunStage.TAXED


def test_dunning_and_reconciliation_and_disputes_round_trip(db):
    dunning = DunningProcess("dun_1", "org_1", "acc_1", "pay_1", DunningState.RETRYING, 2)
    SqlDunningRepository(db).upsert(dunning)
    SqlReconciliationRepository(db).append(
        ReconciliationFinding("pay_1", "razorpay", ReconciliationVerdict.MATCHED)
    )
    diverged = ReconciliationFinding(
        "pay_2", "razorpay", ReconciliationVerdict.AMOUNT_DIVERGED, Money(1, INR), Money(2, INR)
    )
    SqlReconciliationRepository(db).append(diverged)
    dispute = Dispute("dsp_1", "org_1", "acc_1", "pay_1", Money(47200, INR))
    SqlDisputeRepository(db).add(dispute)
    _commit(db)
    assert SqlDunningRepository(db).for_payment("org_1", "pay_1") == dunning
    assert SqlReconciliationRepository(db).divergent() == (diverged,)  # matched ones excluded
    assert SqlDisputeRepository(db).for_payment("org_1", "pay_1") == (dispute,)


def test_instrument_removal_touches_no_revenue_record(db):
    instrument = PaymentInstrument("pmi_1", "org_1", "acc_1", PaymentMethodKind.CARD, "stripe")
    SqlPaymentInstrumentRepository(db).add(instrument)
    SqlPaymentRepository(db).add(_payment())
    SqlLedgerRepository(db).append(_ledger())
    _commit(db)

    SqlPaymentInstrumentRepository(db).remove("org_1", "pmi_1")
    _commit(db)

    assert SqlPaymentInstrumentRepository(db).for_account("org_1", "acc_1") == ()
    assert SqlPaymentRepository(db).get("org_1", "pay_1") == _payment()
    assert len(SqlLedgerRepository(db).for_account("org_1", "acc_1")) == 1


def test_credits_and_notes_and_tax_round_trip(db):
    grant = CreditGrant("crd_1", "org_1", "acc_1", CreditKind.PREPAID, Money(50000, INR), 1.0)
    movement = CreditMovement("crm_1", "org_1", "acc_1", "crd_1", Money(-10000, INR), 2.0)
    SqlCreditRepository(db).grant(grant)
    SqlCreditRepository(db).append_movement(movement)
    note = CreditNote("cnt_1", "org_1", "acc_1", "inv_1", INR, Money(10000, INR), "goodwill", 7.0)
    SqlCreditNoteRepository(db).add(note)
    SqlTaxProfileRepository(db).upsert(_tax_profile())
    SqlTaxDeterminationRepository(db).append(_determination())
    _commit(db)

    assert SqlCreditRepository(db).grants_for("org_1", "acc_1") == (grant,)
    assert SqlCreditRepository(db).movements_for("org_1", "acc_1") == (movement,)
    assert SqlCreditNoteRepository(db).for_invoice("org_1", "inv_1") == (note,)
    assert SqlTaxProfileRepository(db).for_account("org_1", "acc_1") == _tax_profile()
    assert SqlTaxDeterminationRepository(db).for_invoice("org_1", "inv_1") == _determination()


def test_contracts_licenses_and_events_round_trip(db):
    SqlContractRepository(db).add(_contract())
    SqlLicenseRepository(db).add(_license())
    SqlBillingEventRepository(db).append(_event())
    _commit(db)
    assert SqlContractRepository(db).get("org_1", "ctr_1") == _contract()
    assert SqlLicenseRepository(db).for_account("org_1", "acc_1") == (_license(),)
    assert SqlBillingEventRepository(db).for_subject("org_1", "inv_1") == (_event(),)


def test_account_hierarchy_is_a_billing_relationship(db):
    repo = SqlBillingAccountRepository(db)
    parent = _account()
    child = replace(
        _account(org="org_2", account_id="acc_2"), parent_account_id="acc_1", kind=AccountKind.CHILD
    )
    repo.add(parent)
    repo.add(child)
    _commit(db)
    # The child lives in its own organization — a billing hierarchy is not an identity one.
    assert repo.children_of("org_1", "acc_1") == (child,)


# ── 2. organization isolation ──────────────────────────────────────────────────


def test_org_isolation_across_every_org_scoped_read(db):
    """A caller in org_2 cannot read org_1's revenue — not by policy, but because every
    org-owned query filters on `org_id` by construction (§A11.2)."""
    SqlBillingAccountRepository(db).add(_account())
    SqlSubscriptionRepository(db).add(_subscription())
    SqlInvoiceRepository(db).add(_invoice())
    SqlLedgerRepository(db).append(_ledger())
    SqlPaymentRepository(db).add(_payment())
    SqlRefundRepository(db).add(_refund())
    SqlEntitlementGrantRepository(db).add(_grant())
    SqlEntitlementSnapshotRepository(db).publish(_snapshot())
    SqlContractRepository(db).add(_contract())
    SqlLicenseRepository(db).add(_license())
    SqlTaxProfileRepository(db).upsert(_tax_profile())
    SqlUsageRecordRepository(db).append(_usage())
    _commit(db)

    other = "org_intruder"
    assert SqlBillingAccountRepository(db).get(other, "acc_1") is None
    assert SqlBillingAccountRepository(db).by_org(other) is None
    assert SqlSubscriptionRepository(db).get(other, "sub_1") is None
    assert SqlSubscriptionRepository(db).by_account(other, "acc_1") == ()
    assert SqlInvoiceRepository(db).get(other, "inv_1") is None
    assert SqlInvoiceRepository(db).for_account(other, "acc_1") == ()
    assert SqlLedgerRepository(db).for_account(other, "acc_1") == ()
    assert SqlLedgerRepository(db).for_invoice(other, "inv_1") == ()
    assert SqlPaymentRepository(db).get(other, "pay_1") is None
    assert SqlPaymentRepository(db).for_invoice(other, "inv_1") == ()
    assert SqlRefundRepository(db).get(other, "ref_1") is None
    assert SqlRefundRepository(db).by_request_key(other, "rk_1") is None
    assert SqlEntitlementGrantRepository(db).active_for(other, "acc_1") == ()
    assert SqlEntitlementSnapshotRepository(db).latest(other, "acc_1") is None
    assert SqlContractRepository(db).for_account(other, "acc_1") == ()
    assert SqlLicenseRepository(db).for_account(other, "acc_1") == ()
    assert SqlTaxProfileRepository(db).for_account(other, "acc_1") is None
    assert SqlUsageRecordRepository(db).for_period(other, "acc_1", "voice_minutes", PERIOD) == ()


def test_org_isolation_survives_identical_ids_in_two_orgs(db):
    """Two organizations holding the same account id still never see each other's rows."""
    repo = SqlSubscriptionRepository(db)
    mine = _subscription()
    theirs = replace(_subscription(), id="sub_2", org_id="org_2")
    repo.add(mine)
    repo.add(theirs)
    _commit(db)
    assert repo.by_account("org_1", "acc_1") == (mine,)
    assert repo.by_account("org_2", "acc_1") == (theirs,)


# ── 3. append-only + immutability enforcement ──────────────────────────────────


APPEND_ONLY = (
    SqlLedgerRepository,
    SqlCreditRepository,
    SqlUsageRecordRepository,
    SqlUsageQuarantineRepository,
    SqlCreditNoteRepository,
    SqlTaxDeterminationRepository,
    SqlReconciliationRepository,
    SqlBillingEventRepository,
)


@pytest.mark.parametrize("repository", APPEND_ONLY)
def test_append_only_repositories_expose_no_mutation_path(repository):
    """An append-only posture that ships with `update()` is not append-only. These classes
    have no update and no delete method to call at all (ADR-0023 RS1)."""
    surface = {n for n in dir(repository) if not n.startswith("_")}
    assert not surface & {"update", "replace", "replace_draft", "delete", "remove", "upsert"}


@pytest.mark.parametrize("repository", APPEND_ONLY)
def test_append_only_repositories_never_delete_or_update_in_sql(repository):
    """Source-level proof, not just surface-level: no `session.delete` and no attribute
    write-back anywhere in an append-only implementation."""
    source = pyinspect.getsource(repository)
    assert "session.delete" not in source
    assert "update_row" not in source


def test_ledger_is_append_only_and_ordered(db):
    repo = SqlLedgerRepository(db)
    charge = _ledger("led_1")
    reversal = replace(
        _ledger("led_2"),
        entry_type=LedgerEntryType.ADJUSTMENT,
        direction=LedgerDirection.CREDIT,
        recorded_at=3.0,
        reverses_entry_id="led_1",
    )
    repo.append(charge)
    repo.append(reversal)
    _commit(db)
    entries = repo.for_account("org_1", "acc_1")
    # Correction is a new, later entry that references the original — never an edit of it.
    assert entries == (charge, reversal)
    assert entries[0] == _ledger("led_1")


def test_issued_invoice_cannot_be_replaced(db):
    repo = SqlInvoiceRepository(db)
    repo.add(_invoice(InvoiceState.ISSUED))
    _commit(db)
    tampered = replace(_invoice(InvoiceState.ISSUED), totals=None)
    with pytest.raises(ImmutabilityViolation):
        repo.replace_draft(tampered)
    db.session.rollback()
    assert repo.get("org_1", "inv_1") == _invoice(InvoiceState.ISSUED)


def test_draft_invoice_may_be_replaced_then_frozen_on_issue(db):
    repo = SqlInvoiceRepository(db)
    repo.add(_invoice(InvoiceState.DRAFT))
    revised = replace(_invoice(InvoiceState.DRAFT), lines=(_line("chl_1"), _line("chl_2")))
    repo.replace_draft(revised)
    _commit(db)
    assert len(repo.get("org_1", "inv_1").lines) == 2

    repo.replace_draft(replace(revised, state=InvoiceState.ISSUED))
    _commit(db)
    with pytest.raises(ImmutabilityViolation):
        repo.replace_draft(revised)


def test_entitlement_snapshots_are_versioned_and_immutable(db):
    repo = SqlEntitlementSnapshotRepository(db)
    repo.publish(_snapshot(1))
    repo.publish(_snapshot(2))
    _commit(db)
    assert repo.latest("org_1", "acc_1").version == 2
    assert repo.version("org_1", "acc_1", 1) == _snapshot(1)  # v1 unchanged by v2
    assert not hasattr(repo, "replace") and not hasattr(repo, "update")


def test_republishing_a_snapshot_version_is_rejected_by_storage(db):
    repo = SqlEntitlementSnapshotRepository(db)
    repo.publish(_snapshot(1))
    _commit(db)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        repo.publish(replace(_snapshot(1), id="ess_other"))
    db.session.rollback()


def test_entitlement_revocation_is_recorded_not_deleted(db):
    repo = SqlEntitlementGrantRepository(db)
    repo.add(_grant())
    _commit(db)
    revoked = repo.revoke("org_1", "ent_1", 9.0)
    _commit(db)
    assert revoked.revoked_at == 9.0
    assert repo.active_for("org_1", "acc_1") == ()  # no longer active …
    # … but the fact and its timing survive: why an entitlement went away is itself a fact.
    stored = SqlEntitlementGrantRepository(db)
    assert stored.revoke("org_1", "ent_1", 9.0).granted_at == 1.0


def test_usage_append_is_idempotent_on_source_event(db):
    repo = SqlUsageRecordRepository(db)
    first = repo.append(_usage("evt_1", "usg_1"))
    second = repo.append(_usage("evt_1", "usg_2"))  # same source event, replayed
    _commit(db)
    # Exactly-once billing over at-least-once delivery: the replay returns the first write.
    assert second == first
    assert len(repo.for_period("org_1", "acc_1", "voice_minutes", PERIOD)) == 1
    assert repo.exists("evt_1", "voice_minutes")
    assert not repo.exists("evt_1", "other_meter")


def test_usage_idempotency_is_enforced_by_the_database_too(db):
    """Defence in depth: even a caller that bypasses `append`'s check hits a unique
    constraint. Double-billing must not be one lost race away."""
    from sqlalchemy.exc import IntegrityError

    from billing.persistence import mapping as mp

    db.session.add(mp.usage_record_row(_usage("evt_1", "usg_1")))
    db.session.add(mp.usage_record_row(_usage("evt_1", "usg_2")))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_sealed_usage_period_cannot_be_reopened(db):
    repo = SqlUsageAggregateRepository(db)
    repo.upsert(_aggregate(sealed=False))
    repo.upsert(_aggregate(sealed=True))
    _commit(db)
    with pytest.raises(ImmutabilityViolation):
        repo.upsert(_aggregate(sealed=False))  # sealing is a one-way door (M7)
    db.session.rollback()
    assert repo.get("org_1", "acc_1", "voice_minutes", PERIOD).sealed


def test_usage_aggregate_is_derived_and_recomputable(db):
    repo = SqlUsageAggregateRepository(db)
    repo.upsert(_aggregate())
    recomputed = replace(_aggregate(), quantity=Quantity(1800, "minute"), record_count=3)
    repo.upsert(recomputed)
    _commit(db)
    assert repo.for_period("org_1", "acc_1", PERIOD) == (recomputed,)


def test_invoice_numbers_are_gapless_and_never_reused(db):
    allocator = SqlInvoiceNumberAllocator(db)
    issued = [allocator.allocate("VOX", "IN") for _ in range(5)]
    _commit(db)
    assert issued == [1, 2, 3, 4, 5]
    # A different legal entity/jurisdiction keeps its own independent sequence.
    assert SqlInvoiceNumberAllocator(db).allocate("VOX", "AE") == 1
    assert SqlInvoiceNumberAllocator(db).allocate("VOX", "IN") == 6


def test_invoice_sequence_survives_a_new_allocator_instance(db):
    SqlInvoiceNumberAllocator(db).allocate("VOX", "IN")
    _commit(db)
    # The counter is a storage fact, not an in-process one — a restart continues the series.
    assert SqlInvoiceNumberAllocator(db).allocate("VOX", "IN") == 2


def test_quarantined_usage_is_appended_not_billed(db):
    repo = SqlUsageQuarantineRepository(db)
    quarantined = UsageQuarantine(
        "uqr_1", UsageSource("evt_9", "metric.x", 1.0), "unknown", "no such meter", 2.0, "org_1"
    )
    repo.append(quarantined)
    _commit(db)
    assert repo.pending() == (quarantined,)
    assert SqlUsageRecordRepository(db).for_period("org_1", "acc_1", "unknown", PERIOD) == ()


# ── 4. transactions ────────────────────────────────────────────────────────────


def _uow(db):
    emitter = RecordingApplicationEventEmitter()
    return BillingUnitOfWorkFactory(db, emitter).new(), emitter


def test_commit_makes_repository_writes_durable(db):
    uow, emitter = _uow(db)
    repo = SqlBillingAccountRepository(db)
    with uow:
        repo.add(_account())
    assert repo.get("org_1", "acc_1") == _account()


def test_rollback_undoes_repository_writes(db):
    """The durable atomicity that replaces M9.3's honest limitation: the data goes back too,
    not just the events."""
    uow, emitter = _uow(db)
    repo = SqlBillingAccountRepository(db)
    with pytest.raises(RuntimeError):
        with uow:
            repo.add(_account())
            raise RuntimeError("orchestration failed")
    assert repo.get("org_1", "acc_1") is None


def test_rollback_is_all_or_nothing_across_repositories(db):
    """Several repositories, one transaction: an invoice, its ledger entries, its tax
    evidence, and its fact stream either all land or none do."""
    uow, _ = _uow(db)
    with pytest.raises(RuntimeError):
        with uow:
            SqlInvoiceRepository(db).add(_invoice(InvoiceState.ISSUED))
            SqlLedgerRepository(db).append(_ledger())
            SqlTaxDeterminationRepository(db).append(_determination())
            SqlBillingEventRepository(db).append(_event())
            raise RuntimeError("tax adapter unavailable")
    assert SqlInvoiceRepository(db).get("org_1", "inv_1") is None
    assert SqlLedgerRepository(db).for_account("org_1", "acc_1") == ()
    assert SqlTaxDeterminationRepository(db).for_invoice("org_1", "inv_1") is None
    assert SqlBillingEventRepository(db).for_subject("org_1", "inv_1") == ()


def _app_event(event_id="bap_1"):
    from billing.application import ApplicationEvent

    return ApplicationEvent(
        id=event_id,
        type=ApplicationEventType.ACCOUNT_PROVISIONED,
        ts=1.0,
        use_case="open_billing_account",
        outcome="completed",
        org_id="org_1",
    )


def test_events_publish_only_after_a_durable_commit(db):
    uow, emitter = _uow(db)
    event = _app_event()
    with uow:
        SqlBillingAccountRepository(db).add(_account())
        uow.stage(event)
        assert emitter.events == ()  # invisible until commit
        assert uow.staged == (event,)
    assert emitter.events == (event,)


def test_rollback_discards_staged_events(db):
    uow, emitter = _uow(db)
    with pytest.raises(RuntimeError):
        with uow:
            uow.stage(_app_event())
            raise RuntimeError("boom")
    # No trace claiming an orchestration succeeded when it did not.
    assert emitter.events == ()
    assert uow.staged == ()


def test_unit_of_work_never_suppresses_an_exception(db):
    uow, _ = _uow(db)
    with pytest.raises(ValueError):
        with uow:
            raise ValueError("must propagate")


def test_closed_unit_of_work_cannot_be_reused(db):
    uow, _ = _uow(db)
    with uow:
        SqlBillingAccountRepository(db).add(_account())
    with pytest.raises(UnitOfWorkClosedError):
        uow.stage(_app_event("bap_2"))
    with pytest.raises(UnitOfWorkClosedError):
        uow.__enter__()


def test_billing_facts_commit_with_the_records_they_describe(db):
    """The event log and the ledger can never disagree about whether something happened."""
    from billing.persistence import SqlBillingEventStore

    store = SqlBillingEventStore(db)
    uow, _ = _uow(db)
    with pytest.raises(RuntimeError):
        with uow:
            SqlLedgerRepository(db).append(_ledger())
            store.emit(_event())
            raise RuntimeError("failed after emitting")
    assert store.for_subject("org_1", "inv_1") == ()
    assert SqlLedgerRepository(db).for_account("org_1", "acc_1") == ()


# ── 5. cross-instance durability ───────────────────────────────────────────────


def test_data_survives_a_new_application_over_the_same_database():
    """Proof the records are in the database and not in some object's memory: a second,
    independently wired Application reads what the first one wrote."""
    first = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    account = first.application.commands.dispatch(
        OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name="Acme", currency=INR)
    )

    second = build_persistent_billing_application(
        database=first.database,
        create_all=False,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    view = second.application.queries.dispatch(
        GetAccount(actor_id="usr_2", org_id="org_1", account_id=account.id)
    )
    assert view.display_name == "Acme"
    first.database.close()


def test_durable_billing_fact_stream_is_readable_after_the_fact():
    app = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    account = app.application.commands.dispatch(
        OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name="Acme", currency=INR)
    )
    facts = app.events.for_subject("org_1", account.id)
    assert [f.type for f in facts] == [BillingEventType.ACCOUNT_CREATED]
    assert facts[0].actor_id == "usr_1"  # who changed what, when
    app.database.close()


def test_full_journey_is_durable_end_to_end():
    """The M9.3 journey, now over a real database, read back through the query surface."""
    app = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    application = app.application
    account = application.commands.dispatch(
        OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name="Acme", currency=INR)
    )
    application.commands.dispatch(
        GrantEntitlement(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            entitlement=Entitlement(
                "voice_minutes", FeatureKind.QUOTA, allowance=1000, allows_overage=True
            ),
        )
    )
    snapshot = application.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            gates=(
                EntitlementGate("voice_minutes", FeatureKind.QUOTA, GateClassification.FAIL_OPEN),
            ),
        )
    )
    assert snapshot.allowance("voice_minutes") == 1000

    for i in range(2):
        application.commands.dispatch(
            RecordUsage(
                actor_id="sys",
                org_id="org_1",
                account_id=account.id,
                meter_code="voice_minutes",
                units=600,
                unit="minute",
                scope=Scope("org_1"),
                period=PERIOD,
                event_id=f"evt_{i}",
                event_type="metric.session.completed",
                observed_at=float(i),
            )
        )
    draft = application.commands.dispatch(
        DraftInvoice(
            actor_id="usr_1",
            org_id="org_1",
            account_id=account.id,
            currency=INR,
            period=PERIOD,
            lines=(_line(),),
        )
    )
    issued = application.commands.dispatch(
        IssueInvoice(actor_id="usr_1", org_id="org_1", invoice_id=draft.id, jurisdiction="IN")
    )
    assert issued.number is not None

    # Everything is readable from a second Application over the same storage.
    reopened = build_persistent_billing_application(
        database=app.database,
        create_all=False,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    invoices = reopened.application.queries.dispatch(
        ListInvoices(actor_id="usr_1", org_id="org_1", account_id=account.id)
    )
    assert len(invoices) == 1 and invoices[0].state == "issued"
    app.database.close()


def test_a_rejected_command_leaves_no_durable_trace():
    """Deny-by-default survives production wiring: an unauthorized command writes nothing."""
    app = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    from billing.application import BillingAuthorizationError

    with pytest.raises(BillingAuthorizationError):
        app.application.commands.dispatch(
            OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name="Acme", currency=INR)
        )
    assert SqlBillingAccountRepository(app.database).by_org("org_1") is None
    app.database.close()


def test_composition_root_defaults_to_denying():
    """No authorizer supplied ⇒ the Application's deny-all stands. A build that forgets to
    wire RBAC is inert, not open."""
    app = build_persistent_billing_application(ids=SequentialIdGenerator(), clock=FixedClock())
    from billing.application import BillingAuthorizationError

    with pytest.raises(BillingAuthorizationError):
        app.application.commands.dispatch(
            OpenBillingAccount(actor_id="usr_1", org_id="org_1", display_name="Acme", currency=INR)
        )
    app.database.close()


# ── 6. migration chain ─────────────────────────────────────────────────────────


def test_migration_chain_is_ordered_and_contiguous():
    from billing.persistence.migrations import CHAIN_STARTS_AFTER

    versions = [module.VERSION for module in MIGRATIONS]
    assert versions == sorted(versions)
    assert versions[:3] == ["0013", "0014", "0015"]  # M9.6 appended 0016
    # It continues the one platform chain rather than starting a second one.
    assert int(versions[0]) == int(CHAIN_STARTS_AFTER) + 1
    for previous, nxt in zip(versions[:-1], versions[1:], strict=True):
        assert int(nxt) == int(previous) + 1


def test_migration_chain_continues_the_customer_platform_chain():
    from api.persistence.migrations import MIGRATIONS as CP_MIGRATIONS

    cp_last = max(getattr(m, "VERSION", "0001") for m in CP_MIGRATIONS)
    assert cp_last == "0012"
    assert MIGRATIONS[0].VERSION == "0013"


def test_migrations_build_every_billing_table_and_only_those():
    from sqlalchemy import inspect

    from billing.persistence.migrations import upgrade_all
    from billing.persistence.models import BILLING_TABLE_NAMES

    database = BillingDatabase()  # no create_all — the migrations do the work
    applied = upgrade_all(database)
    assert applied[:3] == ("0013", "0014", "0015")
    tables = set(inspect(database.engine).get_table_names())
    # M9.6 appended the catalog tables; the M9.4 set must still be present in full.
    assert set(BILLING_TABLE_NAMES) <= tables
    assert all(name.startswith("cp_billing_") for name in tables)
    database.close()


def test_migrations_are_idempotent():
    from sqlalchemy import inspect

    from billing.persistence.migrations import upgrade_all

    database = BillingDatabase()
    upgrade_all(database)
    before = set(inspect(database.engine).get_table_names())
    upgrade_all(database)  # re-applying must be a no-op, not an error
    assert set(inspect(database.engine).get_table_names()) == before
    database.close()


def test_migrations_reuse_no_legacy_table():
    """Strict namespace isolation: Billing creates its own tables and touches none of the
    Epic 5–8 ones."""
    from api.persistence.models import CP_TABLES
    from billing.persistence.models import BILLING_TABLE_NAMES

    legacy = {t.__tablename__ for t in CP_TABLES}
    assert not legacy & set(BILLING_TABLE_NAMES)
    for module in MIGRATIONS:
        source = pyinspect.getsource(module)
        assert "ALTER TABLE" not in source.upper()
        for table in legacy:
            assert table not in source


def test_migration_modules_contain_no_application_logic():
    for module in MIGRATIONS:
        exported = {
            name
            for name, obj in vars(module).items()
            if callable(obj) and getattr(obj, "__module__", "") == module.__name__
        }
        assert exported == {"upgrade", "downgrade"}


def test_create_all_touches_only_billing_tables():
    from sqlalchemy import inspect

    from billing.persistence.models import BILLING_TABLE_NAMES

    database = BillingDatabase()
    database.create_all()
    created = set(inspect(database.engine).get_table_names())
    assert set(BILLING_TABLE_NAMES) <= created
    assert all(name.startswith("cp_billing_") for name in created)
    database.close()


# ── 7. architecture compliance ─────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _billing_files(sub: str) -> list[Path]:
    return sorted((BILLING / sub).rglob("*.py"))


def test_persistence_imports_only_its_allowed_dependencies():
    allowed = {
        "billing",
        "core",
        "sqlmodel",
        "sqlalchemy",
        "__future__",
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "types",
        "collections",
        "json",
    }
    for path in _billing_files("persistence"):
        assert _imports(path) <= allowed, (path.name, _imports(path) - allowed)


def test_persistence_never_imports_a_forbidden_package():
    forbidden = {
        "fastapi",
        "starlette",
        "pydantic",
        "httpx",
        "requests",
        "api",
        "actuator",
        "runtime",
        "providers",
        "provider",
        "agent",
        "tool",
        "skill",
        "workflow",
        "mcp",
        "loop",
        "hooks",
        "integration",
        "bridge",
        "memory",
        "knowledge",
        "context",
        "prompt",
        "reasoning",
        "pipeline",
        "events",
        "gateway",
        "replay",
        "observability",
        "telemetry",
        "tenant",
        "studio",
        "product",
    }
    for path in _billing_files("persistence"):
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_dependency_direction_is_one_way():
    """persistence → application → foundation → core, and never the reverse."""
    for path in _billing_files("foundation"):
        found = _imports(path)
        assert "sqlmodel" not in found and "sqlalchemy" not in found
        assert not any(
            "persistence" in n or "application" in n for n in (a for a in _module_paths(path))
        )
    for path in _billing_files("application"):
        found = _imports(path)
        assert "sqlmodel" not in found and "sqlalchemy" not in found
        assert not any("persistence" in n for n in _module_paths(path))


def _module_paths(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_foundation_is_unaware_persistence_exists():
    """Not one mention of a session, a table, a row, or SQL anywhere in the pure domain.

    `select(` is checked **structurally** rather than as a substring. M9.16's `PaymentRouter.select`
    picks a payment provider from operator-declared policies and touches nothing resembling a
    database, and a scan that flagged it would be reporting a domain method for the crime of
    sharing a verb with SQL. What actually matters is whether a SQL `select` was *imported*, which
    is stricter: it also catches `from sqlalchemy import select as pick`, which the substring scan
    never would."""
    for path in _billing_files("foundation"):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "session.", "__tablename__"):
            assert forbidden not in source, (path.name, forbidden)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in {"sqlmodel", "sqlalchemy"}, (path.name, node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in {"sqlmodel", "sqlalchemy"}, (path.name, alias.name)


def test_application_is_unaware_persistence_exists():
    for path in _billing_files("application"):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "__tablename__", "billing.persistence"):
            assert forbidden not in source, (path.name, forbidden)


def test_no_persistence_type_leaks_out_of_the_package():
    """Repositories return Foundation aggregates; no row type crosses the boundary."""
    import billing.persistence as p
    from billing.persistence import models

    row_types = {t.__name__ for t in models.BILLING_TABLES}
    assert not row_types & set(p.__all__)
    assert "models" not in p.__all__ and "mapping" not in p.__all__ and "codec" not in p.__all__


def test_repositories_return_frozen_foundation_aggregates(db):
    """Round-tripped values are the Foundation's own immutable types — a caller cannot
    mutate persisted state by holding a returned object."""
    SqlBillingAccountRepository(db).add(_account())
    SqlInvoiceRepository(db).add(_invoice())
    _commit(db)
    account = SqlBillingAccountRepository(db).get("org_1", "acc_1")
    invoice = SqlInvoiceRepository(db).get("org_1", "inv_1")
    assert isinstance(account, BillingAccount) and isinstance(invoice, Invoice)
    with pytest.raises((AttributeError, TypeError)):
        account.display_name = "changed"  # type: ignore[misc]
    assert isinstance(invoice.lines, tuple)


def test_repositories_never_commit_or_roll_back():
    """Transaction ownership lives in exactly one place. A repository that commits would
    silently make a half-finished orchestration durable."""
    source = (BILLING / "persistence" / "repositories.py").read_text()
    assert "commit()" not in source
    assert "rollback()" not in source


def test_repositories_contain_no_business_rule():
    """Mapping only. The three raises that do exist are storage-integrity guards
    (immutability), not commercial policy — no pricing, tax, proration, or entitlement
    decision appears here."""
    source = (BILLING / "persistence" / "repositories.py").read_text().lower()
    for term in ("proration", "tax_rate", "discount(", "rating", "def calculate", "def compute"):
        assert term not in source, term


def test_frozen_layers_are_unchanged_by_this_milestone():
    """M9.2 and M9.3 remain byte-for-byte unchanged: they gained no import, no method, and
    no knowledge of storage."""
    from billing import application, foundation

    # Surfaces grow additively; nothing is ever removed (§A12 — M9.6 added the catalog).
    assert len(foundation.__all__) >= 203
    assert len(application.__all__) >= 100
    assert "UnitOfWork" in application.__all__  # the abstraction M9.4 implements
    assert not any("Sql" in name for name in foundation.__all__ + application.__all__)


def test_billing_is_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2: a total Billing outage cannot affect a conversation or a deployment,
    because Billing is not wired into the runtime at all."""
    bootstrap = (PLATFORM / "bootstrap.py").read_text()
    assert "billing" not in bootstrap.lower()
