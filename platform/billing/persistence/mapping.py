"""Row ↔ aggregate mapping (M9.4).

The boundary translation, and the *only* place both representations are visible at once:

    Foundation aggregate  →  persistence row  →  Foundation aggregate

`to_row` writes the canonically encoded aggregate into the JSON payload and *derives* the
projected columns from that same aggregate — projections are an index over the payload,
never an independent copy that could disagree with it (§A2.5, applied inside a row).
`from_row` reconstructs the aggregate from the payload alone, so what a repository returns
is exactly what a caller handed it, field for field.

Mapping is all this layer does: no rule, no defaulting of domain values, no derivation the
Foundation would otherwise own. `update_row` refreshes an existing row in place for the
whole-aggregate `replace`/`upsert` contracts, keeping row identity (and therefore the
enclosing transaction's dirty-tracking) intact.

No SQLModel type crosses out of this package, and no Foundation type is constructed here
other than by decoding what the Foundation itself produced.
"""

from __future__ import annotations

from typing import Any, TypeVar

from billing.foundation import (
    BillingAccount,
    BillingEvent,
    BillingRun,
    CommercialCatalog,
    Contract,
    CreditGrant,
    CreditMovement,
    CreditNote,
    Dispute,
    DunningProcess,
    EntitlementGrant,
    EntitlementSnapshot,
    Feature,
    Invoice,
    LedgerEntry,
    License,
    MeterDefinition,
    Payment,
    PaymentInstrument,
    Plan,
    PriceBook,
    ReconciliationFinding,
    Refund,
    Subscription,
    TaxDetermination,
    TaxProfile,
    UsageAggregate,
    UsageQuarantine,
    UsageRecord,
)

from . import models as rows
from .codec import decode, encode

A = TypeVar("A")


# ── generic direction helpers ──────────────────────────────────────────────────


def from_row(aggregate_cls: type[A], row: Any) -> A:
    """Row → aggregate. Reads the payload only; projections are never trusted as truth."""
    return decode(aggregate_cls, row.payload)


def _row(row_cls: type, aggregate: Any, projections: dict[str, Any]) -> Any:
    return row_cls(payload=encode(aggregate), **projections)


def update_row(row: Any, aggregate: Any, projections: dict[str, Any]) -> Any:
    """Refresh an existing row in place (whole-aggregate replacement, never partial)."""
    row.payload = encode(aggregate)
    for key, value in projections.items():
        setattr(row, key, value)
    return row


# ── projections: derived from the aggregate, one function per table ────────────


def account_projection(a: BillingAccount) -> dict[str, Any]:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "parent_account_id": a.parent_account_id,
        "status": a.status.value,
        "currency": a.currency.value,
        "created_at": a.created_at,
    }


def plan_projection(p: Plan) -> dict[str, Any]:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "code": p.code,
        "version": p.version,
        "published": p.published,
        "tier": p.tier.value,
    }


def feature_projection(f: Feature) -> dict[str, Any]:
    return {"id": f.id, "code": f.code, "kind": f.kind.value}


def meter_projection(m: MeterDefinition) -> dict[str, Any]:
    return {"id": m.id, "org_id": m.org_id, "code": m.code, "version": m.version}


def catalog_projection(c: CommercialCatalog, *, org_id: str) -> dict[str, Any]:
    return {"org_id": org_id, "version": c.version}


def price_book_projection(b: PriceBook) -> dict[str, Any]:
    return {
        "id": b.id,
        "org_id": b.org_id,
        "code": b.code,
        "version": b.version,
        "published": b.published,
        "currency": b.currency.value,
    }


def subscription_projection(s: Subscription) -> dict[str, Any]:
    return {
        "id": s.id,
        "org_id": s.org_id,
        "account_id": s.account_id,
        "state": s.state.value,
        "plan_code": s.plan_code,
        "plan_version": s.plan_version,
        "contract_id": s.contract_id,
    }


def grant_projection(g: EntitlementGrant) -> dict[str, Any]:
    return {
        "id": g.id,
        "org_id": g.org_id,
        "account_id": g.account_id,
        "feature_code": g.entitlement.code,
        "source": g.source.value,
        "granted_at": g.granted_at,
        "revoked_at": g.revoked_at,
    }


def snapshot_projection(s: EntitlementSnapshot) -> dict[str, Any]:
    return {
        "id": s.id,
        "org_id": s.org_id,
        "account_id": s.account_id,
        "version": s.version,
        "issued_at": s.issued_at,
    }


def usage_record_projection(u: UsageRecord) -> dict[str, Any]:
    return {
        "id": u.id,
        "org_id": u.org_id,
        "account_id": u.account_id,
        "meter_code": u.meter_code,
        "source_event_id": u.source.event_id,
        "period_start": u.period.start,
        "period_end": u.period.end,
        "status": u.status.value,
    }


def usage_aggregate_projection(a: UsageAggregate) -> dict[str, Any]:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "meter_code": a.meter_code,
        "period_start": a.period.start,
        "period_end": a.period.end,
        "sealed": a.sealed,
    }


def quarantine_projection(q: UsageQuarantine) -> dict[str, Any]:
    return {
        "id": q.id,
        "org_id": q.org_id,
        "meter_code": q.meter_code,
        "received_at": q.received_at,
    }


def ledger_projection(e: LedgerEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "account_id": e.account_id,
        "invoice_id": e.invoice_id,
        "payment_id": e.payment_id,
        "entry_type": e.entry_type.value,
        "direction": e.direction.value,
        "amount_minor": e.amount.minor,
        "currency": e.amount.currency.value,
        "recorded_at": e.recorded_at,
    }


def credit_grant_projection(g: CreditGrant) -> dict[str, Any]:
    return {
        "id": g.id,
        "org_id": g.org_id,
        "account_id": g.account_id,
        "kind": g.kind.value,
        "amount_minor": g.amount.minor,
        "currency": g.amount.currency.value,
        "granted_at": g.granted_at,
        "expires_at": g.expires_at,
    }


def credit_movement_projection(m: CreditMovement) -> dict[str, Any]:
    return {
        "id": m.id,
        "org_id": m.org_id,
        "account_id": m.account_id,
        "grant_id": m.grant_id,
        "invoice_id": m.invoice_id,
        "amount_minor": m.amount.minor,
        "currency": m.amount.currency.value,
        "occurred_at": m.occurred_at,
    }


def invoice_projection(i: Invoice) -> dict[str, Any]:
    return {
        "id": i.id,
        "org_id": i.org_id,
        "account_id": i.account_id,
        "state": i.state.value,
        "subscription_id": i.subscription_id,
        "invoice_number": i.number.formatted if i.number is not None else "",
        "period_start": i.period.start,
        "period_end": i.period.end,
        "issued_at": i.issued_at,
    }


def credit_note_projection(n: CreditNote) -> dict[str, Any]:
    return {
        "id": n.id,
        "org_id": n.org_id,
        "account_id": n.account_id,
        "invoice_id": n.invoice_id,
        "amount_minor": n.amount.minor,
        "currency": n.amount.currency.value,
        "issued_at": n.issued_at,
    }


def run_projection(r: BillingRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "account_id": r.account_id,
        "period_start": r.period.start,
        "period_end": r.period.end,
        "stage": r.stage.value,
        "invoice_id": r.invoice_id,
    }


def payment_projection(p: Payment) -> dict[str, Any]:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "account_id": p.account_id,
        "invoice_id": p.invoice_id,
        "state": p.state.value,
        "provider_code": p.provider_code,
        "amount_minor": p.amount.minor,
        "currency": p.amount.currency.value,
        "requested_at": p.requested_at,
    }


def instrument_projection(i: PaymentInstrument) -> dict[str, Any]:
    return {
        "id": i.id,
        "org_id": i.org_id,
        "account_id": i.account_id,
        "provider_code": i.provider_code,
        "kind": i.kind.value,
        "is_default": i.is_default,
    }


def dunning_projection(d: DunningProcess) -> dict[str, Any]:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "account_id": d.account_id,
        "payment_id": d.payment_id,
        "state": d.state.value,
        "next_attempt_at": d.next_attempt_at,
    }


def reconciliation_projection(f: ReconciliationFinding) -> dict[str, Any]:
    return {
        "payment_id": f.payment_id,
        "provider_code": f.provider_code,
        "verdict": f.verdict.value,
        "observed_at": f.observed_at,
    }


def refund_projection(r: Refund) -> dict[str, Any]:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "account_id": r.account_id,
        "payment_id": r.payment_id,
        "invoice_id": r.invoice_id,
        "request_key": r.request_key,
        "state": r.state.value,
        "amount_minor": r.amount.minor,
        "currency": r.amount.currency.value,
    }


def dispute_projection(d: Dispute) -> dict[str, Any]:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "account_id": d.account_id,
        "payment_id": d.payment_id,
        "stage": d.stage.value,
        "amount_minor": d.amount.minor,
        "currency": d.amount.currency.value,
    }


def tax_profile_projection(p: TaxProfile) -> dict[str, Any]:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "account_id": p.account_id,
        "regime": p.regime.value,
        "country": p.country,
        "updated_at": p.updated_at,
    }


def tax_determination_projection(d: TaxDetermination) -> dict[str, Any]:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "account_id": d.account_id,
        "invoice_id": d.invoice_id,
        "regime": d.regime.value,
        "treatment": d.treatment.value,
        "determined_at": d.determined_at,
    }


def contract_projection(c: Contract) -> dict[str, Any]:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "account_id": c.account_id,
        "code": c.code,
        "state": c.state.value,
    }


def license_projection(licence: License) -> dict[str, Any]:
    return {
        "id": licence.id,
        "org_id": licence.org_id,
        "account_id": licence.account_id,
        "contract_id": licence.contract_id,
        "kind": licence.kind.value,
        "state": licence.state.value,
    }


def event_projection(e: BillingEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type.value,
        "ts": e.ts,
        "org_id": e.org_id,
        "subject_id": e.subject_id,
        "actor_id": e.actor_id,
        "correlation_id": e.correlation_id,
    }


# ── aggregate → row constructors ───────────────────────────────────────────────


def account_row(a: BillingAccount) -> rows.BillingAccountRow:
    return _row(rows.BillingAccountRow, a, account_projection(a))


def plan_row(p: Plan) -> rows.BillingPlanRow:
    return _row(rows.BillingPlanRow, p, plan_projection(p))


def feature_row(f: Feature) -> rows.BillingFeatureRow:
    return _row(rows.BillingFeatureRow, f, feature_projection(f))


def meter_row(m: MeterDefinition) -> rows.BillingMeterRow:
    return _row(rows.BillingMeterRow, m, meter_projection(m))


def catalog_row(c: CommercialCatalog, *, org_id: str) -> rows.BillingCatalogSnapshotRow:
    return _row(rows.BillingCatalogSnapshotRow, c, catalog_projection(c, org_id=org_id))


def price_book_row(b: PriceBook) -> rows.BillingPriceBookRow:
    return _row(rows.BillingPriceBookRow, b, price_book_projection(b))


def subscription_row(s: Subscription) -> rows.BillingSubscriptionRow:
    return _row(rows.BillingSubscriptionRow, s, subscription_projection(s))


def grant_row(g: EntitlementGrant) -> rows.BillingEntitlementGrantRow:
    return _row(rows.BillingEntitlementGrantRow, g, grant_projection(g))


def snapshot_row(s: EntitlementSnapshot) -> rows.BillingEntitlementSnapshotRow:
    return _row(rows.BillingEntitlementSnapshotRow, s, snapshot_projection(s))


def usage_record_row(u: UsageRecord) -> rows.BillingUsageRecordRow:
    return _row(rows.BillingUsageRecordRow, u, usage_record_projection(u))


def usage_aggregate_row(a: UsageAggregate) -> rows.BillingUsageAggregateRow:
    return _row(rows.BillingUsageAggregateRow, a, usage_aggregate_projection(a))


def quarantine_row(q: UsageQuarantine) -> rows.BillingUsageQuarantineRow:
    return _row(rows.BillingUsageQuarantineRow, q, quarantine_projection(q))


def ledger_row(e: LedgerEntry) -> rows.BillingLedgerEntryRow:
    return _row(rows.BillingLedgerEntryRow, e, ledger_projection(e))


def credit_grant_row(g: CreditGrant) -> rows.BillingCreditGrantRow:
    return _row(rows.BillingCreditGrantRow, g, credit_grant_projection(g))


def credit_movement_row(m: CreditMovement) -> rows.BillingCreditMovementRow:
    return _row(rows.BillingCreditMovementRow, m, credit_movement_projection(m))


def invoice_row(i: Invoice) -> rows.BillingInvoiceRow:
    return _row(rows.BillingInvoiceRow, i, invoice_projection(i))


def credit_note_row(n: CreditNote) -> rows.BillingCreditNoteRow:
    return _row(rows.BillingCreditNoteRow, n, credit_note_projection(n))


def run_row(r: BillingRun) -> rows.BillingRunRow:
    return _row(rows.BillingRunRow, r, run_projection(r))


def payment_row(p: Payment) -> rows.BillingPaymentRow:
    return _row(rows.BillingPaymentRow, p, payment_projection(p))


def instrument_row(i: PaymentInstrument) -> rows.BillingPaymentInstrumentRow:
    return _row(rows.BillingPaymentInstrumentRow, i, instrument_projection(i))


def dunning_row(d: DunningProcess) -> rows.BillingDunningRow:
    return _row(rows.BillingDunningRow, d, dunning_projection(d))


def reconciliation_row(f: ReconciliationFinding) -> rows.BillingReconciliationRow:
    return _row(rows.BillingReconciliationRow, f, reconciliation_projection(f))


def refund_row(r: Refund) -> rows.BillingRefundRow:
    return _row(rows.BillingRefundRow, r, refund_projection(r))


def dispute_row(d: Dispute) -> rows.BillingDisputeRow:
    return _row(rows.BillingDisputeRow, d, dispute_projection(d))


def tax_profile_row(p: TaxProfile) -> rows.BillingTaxProfileRow:
    return _row(rows.BillingTaxProfileRow, p, tax_profile_projection(p))


def tax_determination_row(d: TaxDetermination) -> rows.BillingTaxDeterminationRow:
    return _row(rows.BillingTaxDeterminationRow, d, tax_determination_projection(d))


def contract_row(c: Contract) -> rows.BillingContractRow:
    return _row(rows.BillingContractRow, c, contract_projection(c))


def license_row(licence: License) -> rows.BillingLicenseRow:
    return _row(rows.BillingLicenseRow, licence, license_projection(licence))


def event_row(e: BillingEvent) -> rows.BillingEventRow:
    return _row(rows.BillingEventRow, e, event_projection(e))
