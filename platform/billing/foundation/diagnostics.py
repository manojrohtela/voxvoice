"""BillingDiagnostics (M9.2) — a deterministic, read-only finding set.

Mirrors `product.diagnostics` / `api.foundation.diagnostics` exactly (the established
configuration-diagnostics shape): each finding is addressable (a `path` into the input),
coded, severity-typed, and carries a remediation hint. Findings are canonically ordered,
so identical input always yields an identical, diffable report.

Validation produces these and **never raises**; factories and services decide whether to
fail fast on error-severity findings (§A2.6).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class BillingDiagnosticCode(StrEnum):
    """The Billing Foundation diagnostic vocabulary (M9.2). One closed set so reports
    are uniform; grows additively per later Epic 9 milestone."""

    MISSING_REQUIRED_FIELD = "missing_required_field"
    EMPTY_IDENTITY = "empty_identity"
    INVALID_CODE = "invalid_code"
    INVALID_NAME = "invalid_name"
    DUPLICATE_ID = "duplicate_id"
    DUPLICATE_CODE = "duplicate_code"
    CROSS_ORG_REFERENCE = "cross_org_reference"
    # money / pricing
    NEGATIVE_AMOUNT = "negative_amount"
    CURRENCY_MISMATCH = "currency_mismatch"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE_SHAPE = "invalid_price_shape"
    OVERLAPPING_PRICE_TIERS = "overlapping_price_tiers"
    NON_CONTIGUOUS_PRICE_TIERS = "non_contiguous_price_tiers"
    INVALID_EFFECTIVE_RANGE = "invalid_effective_range"
    # catalog / metering
    UNKNOWN_METER = "unknown_meter"
    DUPLICATE_METER = "duplicate_meter"
    MISSING_METER_SOURCE = "missing_meter_source"
    INVALID_AGGREGATION = "invalid_aggregation"
    # subscription / entitlement
    INVALID_PERIOD = "invalid_period"
    UNKNOWN_PLAN = "unknown_plan"
    UNKNOWN_PRICE = "unknown_price"
    INVALID_SEAT_COUNT = "invalid_seat_count"
    DOWNGRADE_VIOLATES_USAGE = "downgrade_violates_usage"
    UNCLASSIFIED_GATE = "unclassified_gate"
    DUPLICATE_ENTITLEMENT = "duplicate_entitlement"
    # invoicing / payment / tax / refund
    EMPTY_INVOICE = "empty_invoice"
    INVOICE_ALREADY_ISSUED = "invoice_already_issued"
    INVALID_INVOICE_NUMBER = "invalid_invoice_number"
    REFUND_EXCEEDS_CAPTURED = "refund_exceeds_captured"
    MISSING_TAX_PROFILE = "missing_tax_profile"
    INVALID_TAX_REGISTRATION = "invalid_tax_registration"
    # contracts / licensing
    INVALID_CONTRACT_TERM = "invalid_contract_term"
    INVALID_LICENSE_WINDOW = "invalid_license_window"
    # commercial catalog (M9.6 — additive; no existing member changed)
    DUPLICATE_PRODUCT_CODE = "duplicate_product_code"
    DUPLICATE_PLAN_CODE = "duplicate_plan_code"
    DUPLICATE_FEATURE_CODE = "duplicate_feature_code"
    DUPLICATE_EDITION_CODE = "duplicate_edition_code"
    DUPLICATE_BUNDLE_CODE = "duplicate_bundle_code"
    BUNDLE_CYCLE = "bundle_cycle"
    UNKNOWN_BUNDLE_MEMBER = "unknown_bundle_member"
    EDITION_CONFLICT = "edition_conflict"
    UNKNOWN_EDITION_PRODUCT = "unknown_edition_product"
    UNKNOWN_FEATURE_REFERENCE = "unknown_feature_reference"
    UNPUBLISHABLE_LIFECYCLE_STATE = "unpublishable_lifecycle_state"
    SNAPSHOT_INTEGRITY = "snapshot_integrity"
    EMPTY_CATALOG = "empty_catalog"
    # pricing engine (M9.7 — additive; no existing member changed)
    DUPLICATE_PRICE_CODE = "duplicate_price_code"
    DUPLICATE_PRICE_BOOK_CODE = "duplicate_price_book_code"
    OVERLAPPING_EFFECTIVE_RANGE = "overlapping_effective_range"
    UNKNOWN_PRICE_BOOK = "unknown_price_book"
    UNKNOWN_CATALOG_VERSION = "unknown_catalog_version"
    INCOMPLETE_PRICING = "incomplete_pricing"
    INVALID_DISCOUNT = "invalid_discount"
    DISCOUNT_CONFLICT = "discount_conflict"
    PROMOTION_CONFLICT = "promotion_conflict"
    COUPON_CONFLICT = "coupon_conflict"
    PRICE_BOOK_IMMUTABLE = "price_book_immutable"
    EMPTY_PRICE_BOOK = "empty_price_book"
    PRICING_SNAPSHOT_INTEGRITY = "pricing_snapshot_integrity"
    # billing accounts (M9.8 — additive; no existing member changed)
    DUPLICATE_ACCOUNT_NUMBER = "duplicate_account_number"
    DUPLICATE_LEGAL_IDENTIFIER = "duplicate_legal_identifier"
    INVALID_HIERARCHY = "invalid_hierarchy"
    ACCOUNT_CYCLE = "account_cycle"
    PAYER_LOOP = "payer_loop"
    HIERARCHY_TOO_DEEP = "hierarchy_too_deep"
    CROSS_CURRENCY_HIERARCHY = "cross_currency_hierarchy"
    INVALID_ADDRESS = "invalid_address"
    MISSING_BILLING_ADDRESS = "missing_billing_address"
    INVALID_CONTACT = "invalid_contact"
    INVALID_LEGAL_ENTITY = "invalid_legal_entity"
    INVALID_CREDIT_LIMIT = "invalid_credit_limit"
    INVALID_COMMERCIAL_STATUS = "invalid_commercial_status"
    UNKNOWN_ACCOUNT = "unknown_account"
    INVALID_BILLING_PREFERENCE = "invalid_billing_preference"
    # subscription engine (M9.9 — additive; no existing member changed)
    DUPLICATE_SUBSCRIPTION_NUMBER = "duplicate_subscription_number"
    DUPLICATE_SUBSCRIPTION_ITEM = "duplicate_subscription_item"
    INVALID_SUBSCRIPTION_ITEM = "invalid_subscription_item"
    EMPTY_SUBSCRIPTION = "empty_subscription"
    UNKNOWN_BILLING_ACCOUNT = "unknown_billing_account"
    INACTIVE_BILLING_ACCOUNT = "inactive_billing_account"
    UNKNOWN_PRICING_VERSION = "unknown_pricing_version"
    INVALID_BILLING_CYCLE = "invalid_billing_cycle"
    INVALID_TRIAL_POLICY = "invalid_trial_policy"
    INVALID_RENEWAL_POLICY = "invalid_renewal_policy"
    INVALID_PAUSE_POLICY = "invalid_pause_policy"
    INVALID_CANCELLATION = "invalid_cancellation"
    INVALID_SCHEDULED_CHANGE = "invalid_scheduled_change"
    SCHEDULED_CHANGE_IN_PAST = "scheduled_change_in_past"
    CONFLICTING_SCHEDULED_CHANGE = "conflicting_scheduled_change"
    SUBSCRIPTION_SNAPSHOT_INTEGRITY = "subscription_snapshot_integrity"
    # entitlement engine (M9.10 — additive; no existing member changed. `unclassified_gate` and
    # `duplicate_entitlement` were declared at M9.2 and are reused, so there is still exactly one
    # code per finding rather than two that mean the same thing)
    DUPLICATE_ENTITLEMENT_CODE = "duplicate_entitlement_code"
    UNKNOWN_ENTITLEMENT_DEFINITION = "unknown_entitlement_definition"
    RETIRED_ENTITLEMENT_DEFINITION = "retired_entitlement_definition"
    UNKNOWN_ENTITLEMENT_BUNDLE = "unknown_entitlement_bundle"
    DUPLICATE_ENTITLEMENT_BUNDLE_CODE = "duplicate_entitlement_bundle_code"
    ENTITLEMENT_BUNDLE_CYCLE = "entitlement_bundle_cycle"
    ENTITLEMENT_BUNDLE_TOO_DEEP = "entitlement_bundle_too_deep"
    EMPTY_ENTITLEMENT_BUNDLE = "empty_entitlement_bundle"
    INVALID_ENTITLEMENT_VALUE = "invalid_entitlement_value"
    INVALID_ALLOWANCE = "invalid_allowance"
    MISSING_QUOTA_METER = "missing_quota_meter"
    INVALID_GRANT_WINDOW = "invalid_grant_window"
    OVERLAPPING_GRANT_WINDOW = "overlapping_grant_window"
    CONFLICTING_GRANT = "conflicting_grant"
    INVALID_PRECEDENCE_OVERRIDE = "invalid_precedence_override"
    CLOSED_GRANT_MODIFIED = "closed_grant_modified"
    UNKNOWN_SUBSCRIPTION_REFERENCE = "unknown_subscription_reference"
    INVALID_STALENESS_BUDGET = "invalid_staleness_budget"
    INVALID_SCHEDULED_ENTITLEMENT_CHANGE = "invalid_scheduled_entitlement_change"
    CONFLICTING_SCHEDULED_ENTITLEMENT_CHANGE = "conflicting_scheduled_entitlement_change"
    ENTITLEMENT_SNAPSHOT_INTEGRITY = "entitlement_snapshot_integrity"
    # metering engine (M9.11 — additive; no existing member changed. `unknown_meter`,
    # `duplicate_meter`, `missing_meter_source`, `invalid_aggregation`, `invalid_quantity` and
    # `invalid_period` were declared at M9.2 and are reused)
    DUPLICATE_METER_CODE = "duplicate_meter_code"
    RETIRED_METER = "retired_meter"
    NON_AUTHORITATIVE_SOURCE = "non_authoritative_source"
    INVALID_NORMALIZATION = "invalid_normalization"
    INVALID_LATE_ARRIVAL_WINDOW = "invalid_late_arrival_window"
    INVALID_UNIT = "invalid_unit"
    UNIT_MISMATCH = "unit_mismatch"
    EMPTY_SOURCE_EVENT = "empty_source_event"
    FUTURE_SOURCE_EVENT = "future_source_event"
    UNATTRIBUTABLE_USAGE = "unattributable_usage"
    DUPLICATE_USAGE_RECORD = "duplicate_usage_record"
    USAGE_PERIOD_SEALED = "usage_period_sealed"
    INVALID_USAGE_PERIOD = "invalid_usage_period"
    INVALID_AGGREGATION_WINDOW = "invalid_aggregation_window"
    INVALID_CORRECTION = "invalid_correction"
    UNKNOWN_USAGE_RECORD = "unknown_usage_record"
    SEAL_DIGEST_MISMATCH = "seal_digest_mismatch"
    USAGE_RECONCILIATION_GAP = "usage_reconciliation_gap"
    QUARANTINE_BACKLOG = "quarantine_backlog"
    METERING_PROVENANCE_INTEGRITY = "metering_provenance_integrity"
    # rating engine (M9.12 — additive; no existing member changed. `invalid_quantity`,
    # `invalid_period`, `illegal_transition`, and `missing_required_field` were declared earlier
    # and are reused rather than restated)
    UNPRICED_SUBSCRIPTION_ITEM = "unpriced_subscription_item"
    MISSING_PRICE_RESOLUTION = "missing_price_resolution"
    CHARGE_BREAKDOWN_MISMATCH = "charge_breakdown_mismatch"
    MISSING_CHARGE_PROVENANCE = "missing_charge_provenance"
    NEGATIVE_CHARGE = "negative_charge"
    RATING_CURRENCY_MISMATCH = "rating_currency_mismatch"
    RATING_BASIS_MISMATCH = "rating_basis_mismatch"
    RATING_DIGEST_MISMATCH = "rating_digest_mismatch"
    FINALIZED_RATING_MODIFIED = "finalized_rating_modified"
    PREVIEW_NOT_PERSISTABLE = "preview_not_persistable"
    INVALID_COMMITMENT = "invalid_commitment"
    DUPLICATE_COMMITMENT_CODE = "duplicate_commitment_code"
    FIXED_DISCOUNT_ON_METERED_RATE = "fixed_discount_on_metered_rate"
    UNRATED_USAGE = "unrated_usage"
    RATING_PERIOD_NOT_SEALED = "rating_period_not_sealed"
    # credit & revenue ledger (M9.13 — additive; no existing member changed. `invalid_period`,
    # `illegal_transition`, and `missing_required_field` were declared earlier and are reused)
    INVALID_LEDGER_AMOUNT = "invalid_ledger_amount"
    INVALID_LEDGER_CORRECTION = "invalid_ledger_correction"
    NO_OPEN_ACCOUNTING_PERIOD = "no_open_accounting_period"
    CLOSED_PERIOD_POSTING = "closed_period_posting"
    DUPLICATE_LEDGER_POSTING = "duplicate_ledger_posting"
    UNTRACED_LEDGER_ENTRY = "untraced_ledger_entry"
    LEDGER_SEQUENCE_GAP = "ledger_sequence_gap"
    LEDGER_CURRENCY_MISMATCH = "ledger_currency_mismatch"
    BALANCE_CHECKPOINT_DRIFT = "balance_checkpoint_drift"
    LEDGER_CLOSE_DIGEST_MISMATCH = "ledger_close_digest_mismatch"
    TRIAL_BALANCE_MISMATCH = "trial_balance_mismatch"
    INVALID_CREDIT_AMOUNT = "invalid_credit_amount"
    CREDIT_OVERDRAWN = "credit_overdrawn"
    EXPIRED_CREDIT_APPLIED = "expired_credit_applied"
    UNPOSTED_RATED_CHARGE = "unposted_rated_charge"
    UNFINALIZED_RATING_RUN = "unfinalized_rating_run"
    # invoice engine (M9.14 — additive; no existing member changed. `empty_invoice`,
    # `invoice_already_issued`, `invalid_invoice_number`, `missing_required_field`,
    # `illegal_transition` and `invalid_period` were declared earlier and are **reused rather than
    # restated** — a second code for one finding is the drift §A2.5 prevents, and in a `StrEnum` a
    # repeated *name* is not even an alias: it is a `TypeError` at import, which is how this one was
    # found)
    INVOICE_NOT_NUMBERED = "invoice_not_numbered"
    UNSEALED_INVOICE = "unsealed_invoice"
    INVOICE_CURRENCY_MISMATCH = "invoice_currency_mismatch"
    MISSING_SUPPLIER_IDENTITY = "missing_supplier_identity"
    MISSING_CUSTOMER_IDENTITY = "missing_customer_identity"
    MISSING_TAX_EVIDENCE = "missing_tax_evidence"
    MISSING_LEGAL_ENTITY = "missing_legal_entity"
    SNAPSHOT_DOCUMENT_MISMATCH = "snapshot_document_mismatch"
    INVOICE_NUMBER_GAP = "invoice_number_gap"
    DUPLICATE_INVOICE_NUMBER = "duplicate_invoice_number"
    UNBILLED_LEDGER_ENTRY = "unbilled_ledger_entry"
    ORPHAN_INVOICE_LINE = "orphan_invoice_line"
    INVOICE_TOTALS_MISMATCH = "invoice_totals_mismatch"
    ISSUED_INVOICE_MODIFIED = "issued_invoice_modified"
    INVALID_CORRECTION_DOCUMENT = "invalid_correction_document"
    # tax determination engine (M9.15 — additive; no existing member changed. `missing_tax_profile`,
    # `invalid_tax_registration`, `missing_tax_evidence`, `missing_required_field`,
    # `illegal_transition` and `invalid_period` were declared earlier and are **reused rather than
    # restated**. Note what is absent: there is no code for "wrong rate", because Billing does not
    # know what the right rate is — every finding here is about *evidence*, not about arithmetic.)
    INVALID_JURISDICTION_CODE = "invalid_jurisdiction_code"
    DUPLICATE_JURISDICTION_CODE = "duplicate_jurisdiction_code"
    UNKNOWN_TAX_JURISDICTION = "unknown_tax_jurisdiction"
    UNKNOWN_TAX_CLASSIFICATION = "unknown_tax_classification"
    INVALID_TAX_RATE = "invalid_tax_rate"
    INVALID_TAX_RATE_WINDOW = "invalid_tax_rate_window"
    MISSING_RATE_PROVENANCE = "missing_rate_provenance"
    NO_TAX_RATE_SOURCE = "no_tax_rate_source"
    STALE_TAX_RATE = "stale_tax_rate"
    UNRESOLVED_PLACE_OF_SUPPLY = "unresolved_place_of_supply"
    NO_TAX_NEXUS = "no_tax_nexus"
    TAX_WITHOUT_REGISTRATION = "tax_without_registration"
    EXPIRED_TAX_EXEMPTION = "expired_tax_exemption"
    UNDECLARED_TAX_OVERRIDE = "undeclared_tax_override"
    TAX_CURRENCY_MISMATCH = "tax_currency_mismatch"
    UNEXPLAINABLE_TAX = "unexplainable_tax"
    FINALIZED_ASSESSMENT_MODIFIED = "finalized_assessment_modified"
    # payment engine (M9.16 — additive; no existing member changed. `missing_required_field`,
    # `illegal_transition`, `currency_mismatch`, `negative_amount`, `duplicate_id` and
    # `cross_org_reference` were declared earlier and are **reused rather than restated**. Note
    # what is absent, again: there is no code for "wrong amount", because Payments does not decide
    # amounts — every finding here is about *evidence, identity, or safety*, never arithmetic.)
    UNROUTABLE_PAYMENT = "unroutable_payment"
    UNKNOWN_PAYMENT_PROVIDER = "unknown_payment_provider"
    PAYMENT_AMOUNT_MISMATCH = "payment_amount_mismatch"
    UNPAYABLE_SUBJECT = "unpayable_subject"
    DUPLICATE_PAYMENT_ATTEMPT = "duplicate_payment_attempt"
    UNRESOLVED_PAYMENT_ATTEMPT = "unresolved_payment_attempt"
    OPEN_PROVIDER_OPERATION = "open_provider_operation"
    AMBIGUOUS_PROVIDER_OUTCOME = "ambiguous_provider_outcome"
    UNVERIFIED_PROVIDER_EVENT = "unverified_provider_event"
    UNCORRELATED_PROVIDER_EVENT = "uncorrelated_provider_event"
    CONFLICTING_PROVIDER_EVENT = "conflicting_provider_event"
    SETTLED_ATTEMPT_MODIFIED = "settled_attempt_modified"
    UNPOSTED_PAYMENT_CAPTURE = "unposted_payment_capture"
    REFUND_EXCEEDS_CAPTURE = "refund_exceeds_capture"
    UNREFUNDABLE_ATTEMPT = "unrefundable_attempt"
    PAYMENT_DIVERGENCE_OPEN = "payment_divergence_open"
    INSTRUMENT_DATA_PRESENT = "instrument_data_present"
    # dunning & collections (M9.17 — additive; no existing member changed. `missing_required_field`
    # and `cross_org_reference` are reused rather than restated. Note the absence again: there is
    # no code for "wrong amount due", because Collections does not compute amounts. Every finding
    # here is about *strategy shape, timing, or safety* — never arithmetic.)
    EMPTY_COLLECTION_STRATEGY = "empty_collection_strategy"
    COLLECTION_STAGE_GAP = "collection_stage_gap"
    UNBOUNDED_COLLECTION_STRATEGY = "unbounded_collection_strategy"
    AMBIGUOUS_PAYMENT_IN_COLLECTION = "ambiguous_payment_in_collection"
    RETRY_AGAINST_UNKNOWN_OUTCOME = "retry_against_unknown_outcome"
    STALE_COLLECTION_DECISION = "stale_collection_decision"
    COLLECTION_POLICY_MUTATED = "collection_policy_mutated"
    ORPHANED_COLLECTION_CASE = "orphaned_collection_case"
    # revenue recognition (M9.18 — additive; no existing member changed. `missing_required_field`,
    # `negative_amount` and `cross_org_reference` are reused rather than restated. What is new here
    # is the one class of finding this domain owns: **the money did not survive the split**, and
    # **the source was not pinned**, which is the only way a historical recognition stops being
    # reproducible.)
    RECOGNITION_NOT_CONSERVED = "recognition_not_conserved"
    UNPINNED_RECOGNITION_SOURCE = "unpinned_recognition_source"
    UNPINNED_RECOGNITION_POLICY = "unpinned_recognition_policy"
    EMPTY_RECOGNITION_SCHEDULE = "empty_recognition_schedule"
    RECOGNITION_EXCEEDS_BASIS = "recognition_exceeds_basis"
    UNPOSTED_RECOGNITION = "unposted_recognition"
    CLOSED_PERIOD_RECOGNITION = "closed_period_recognition"


@dataclass(frozen=True, slots=True, order=True)
class Diagnostic:
    """One finding. `order=True` over (path, code, severity) gives deterministic sorting;
    `message`/`remediation` are excluded from ordering so wording never reorders a report."""

    path: str
    code: BillingDiagnosticCode
    severity: Severity
    message: str = field(compare=False)
    remediation: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class BillingDiagnostics:
    """An immutable, deterministically-ordered collection of findings."""

    findings: tuple[Diagnostic, ...] = ()

    @classmethod
    def of(cls, findings: Iterable[Diagnostic]) -> BillingDiagnostics:
        return cls(tuple(sorted(findings)))

    @classmethod
    def empty(cls) -> BillingDiagnostics:
        return cls(())

    def merged(self, other: BillingDiagnostics) -> BillingDiagnostics:
        return BillingDiagnostics.of(self.findings + other.findings)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)

    @property
    def is_valid(self) -> bool:
        return not self.has_errors

    def summary(self) -> str:
        if not self.findings:
            return "billing input valid: 0 findings"
        parts = [f"{len(self.errors)} error(s)", f"{len(self.warnings)} warning(s)"]
        first = self.errors[0] if self.errors else self.findings[0]
        return f"billing input invalid: {', '.join(parts)}; first: {first.path} {first.code.value}"
