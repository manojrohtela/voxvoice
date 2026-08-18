"""Billing domain events (M9.2) — the `billing.*` vocabulary.

ADR-0023 §6/§7: Billing is one more **producer** on the §A5 envelope. It emits `billing.*`
facts and **never emits or mutates `metric.*`** (that stream is owned by runtime producers
and is consumed read-only, M1–M8). It keeps no private audit log — the Epic 8 Audit
Platform records what Billing emits (§A5/§A11.2).

Like `api.foundation.events`, this uses a billing-local envelope rather than
`core.events.Event`: the runtime envelope carries `session_id`/`seq` (bus-assigned,
per-conversation) which are meaningless for revenue facts, and reusing it would force
empty fields. Structurally bridgeable onto the audit log (id/type/ts/org/actor present),
and it touches no frozen Epic file.

`EventEmitter` is the seam; a recording emitter is the deterministic test/audit default.
Emission is the only "effect" the Foundation has, and it is injected, never ambient.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BillingEventType(StrEnum):
    """The billing domain-event vocabulary. Dotted, lowercase, stable, additive (AR-32).
    One producer per type (AR-2) — every member below is produced by Billing alone."""

    # billing accounts (M9.5)
    ACCOUNT_CREATED = "billing.account.created"
    ACCOUNT_UPDATED = "billing.account.updated"
    ACCOUNT_PARENT_CHANGED = "billing.account.parent_changed"
    ACCOUNT_CLOSED = "billing.account.closed"
    # commercial catalog (M9.6)
    PLAN_PUBLISHED = "billing.plan.published"
    PLAN_DEPRECATED = "billing.plan.deprecated"
    METER_DEFINED = "billing.meter.defined"
    # pricing (M9.7)
    PRICE_BOOK_PUBLISHED = "billing.price_book.published"
    PRICE_VERSION_PUBLISHED = "billing.price.version_published"
    # subscriptions (M9.8)
    SUBSCRIPTION_CREATED = "billing.subscription.created"
    SUBSCRIPTION_ACTIVATED = "billing.subscription.activated"
    SUBSCRIPTION_TRIAL_STARTED = "billing.subscription.trial_started"
    SUBSCRIPTION_TRIAL_CONVERTED = "billing.subscription.trial_converted"
    SUBSCRIPTION_PLAN_CHANGED = "billing.subscription.plan_changed"
    SUBSCRIPTION_SEATS_CHANGED = "billing.subscription.seats_changed"
    SUBSCRIPTION_RENEWED = "billing.subscription.renewed"
    SUBSCRIPTION_PAST_DUE = "billing.subscription.past_due"
    SUBSCRIPTION_SUSPENDED = "billing.subscription.suspended"
    SUBSCRIPTION_PAUSED = "billing.subscription.paused"
    SUBSCRIPTION_RESUMED = "billing.subscription.resumed"
    SUBSCRIPTION_CANCELLED = "billing.subscription.cancelled"
    SUBSCRIPTION_EXPIRED = "billing.subscription.expired"
    # entitlements (M9.9)
    ENTITLEMENT_GRANTED = "billing.entitlement.granted"
    ENTITLEMENT_REVOKED = "billing.entitlement.revoked"
    ENTITLEMENT_SNAPSHOT_PUBLISHED = "billing.entitlement.snapshot_published"
    # metering (M9.10)
    USAGE_RECORDED = "billing.usage.recorded"
    USAGE_QUARANTINED = "billing.usage.quarantined"
    USAGE_PERIOD_SEALED = "billing.usage.period_sealed"
    # rating (M9.11)
    CHARGES_RATED = "billing.charges.rated"
    # ledger + credits (M9.12)
    LEDGER_ENTRY_RECORDED = "billing.ledger.entry_recorded"
    CREDIT_GRANTED = "billing.credit.granted"
    CREDIT_CONSUMED = "billing.credit.consumed"
    CREDIT_EXPIRED = "billing.credit.expired"
    # invoicing (M9.13)
    BILLING_RUN_STARTED = "billing.run.started"
    BILLING_RUN_COMPLETED = "billing.run.completed"
    INVOICE_DRAFTED = "billing.invoice.drafted"
    INVOICE_ISSUED = "billing.invoice.issued"
    INVOICE_VOIDED = "billing.invoice.voided"
    INVOICE_PAID = "billing.invoice.paid"
    INVOICE_OVERDUE = "billing.invoice.overdue"
    CREDIT_NOTE_ISSUED = "billing.credit_note.issued"
    # payments + dunning (M9.14)
    PAYMENT_REQUESTED = "billing.payment.requested"
    PAYMENT_AUTHORIZED = "billing.payment.authorized"
    PAYMENT_CAPTURED = "billing.payment.captured"
    PAYMENT_SETTLED = "billing.payment.settled"
    PAYMENT_FAILED = "billing.payment.failed"
    PAYMENT_DISPUTED = "billing.payment.disputed"
    DUNNING_STARTED = "billing.dunning.started"
    DUNNING_RECOVERED = "billing.dunning.recovered"
    DUNNING_ABANDONED = "billing.dunning.abandoned"
    RECONCILIATION_DIVERGED = "billing.reconciliation.diverged"
    # tax (M9.15)
    TAX_PROFILE_UPDATED = "billing.tax.profile_updated"
    TAX_DETERMINED = "billing.tax.determined"
    # refunds (M9.16)
    REFUND_REQUESTED = "billing.refund.requested"
    REFUND_APPROVED = "billing.refund.approved"
    REFUND_REJECTED = "billing.refund.rejected"
    REFUND_COMPLETED = "billing.refund.completed"
    # commerce + licensing (M9.20 / M9.21)
    ORDER_CREATED = "billing.order.created"
    ORDER_COMPLETED = "billing.order.completed"
    ORDER_CANCELLED = "billing.order.cancelled"
    PAYABLE_RECORDED = "billing.payable.recorded"
    CONTRACT_ACTIVATED = "billing.contract.activated"
    CONTRACT_RENEWED = "billing.contract.renewed"
    CONTRACT_ENDED = "billing.contract.ended"
    LICENSE_ISSUED = "billing.license.issued"
    LICENSE_REVOKED = "billing.license.revoked"
    # commercial catalog (M9.6 — additive; no existing member changed)
    PRODUCT_CREATED = "billing.product.created"
    PRODUCT_PUBLISHED = "billing.product.published"
    PRODUCT_DEPRECATED = "billing.product.deprecated"
    PRODUCT_ARCHIVED = "billing.product.archived"
    PRODUCT_RESTORED = "billing.product.restored"
    PRODUCT_DUPLICATED = "billing.product.duplicated"
    PLAN_ARCHIVED = "billing.plan.archived"
    EDITION_CREATED = "billing.edition.created"
    BUNDLE_CREATED = "billing.bundle.created"
    FEATURE_CREATED = "billing.feature.created"
    FEATURE_GROUP_CREATED = "billing.feature_group.created"
    CATALOG_PUBLISHED = "billing.catalog.published"
    CATALOG_ROLLED_BACK = "billing.catalog.rolled_back"
    # pricing engine (M9.7 — additive; `price_book.published` and `price.version_published`
    # were already declared at M9.2 and are reused unchanged)
    PRICE_BOOK_CREATED = "billing.price_book.created"
    PRICE_BOOK_ARCHIVED = "billing.price_book.archived"
    PRICE_BOOK_RESTORED = "billing.price_book.restored"
    PRICE_BOOK_DUPLICATED = "billing.price_book.duplicated"
    PRICE_CREATED = "billing.price.created"
    PRICE_UPDATED = "billing.price.updated"
    DISCOUNT_CREATED = "billing.discount.created"
    PROMOTION_CREATED = "billing.promotion.created"
    COUPON_CREATED = "billing.coupon.created"
    PRICING_PUBLISHED = "billing.pricing.published"
    PRICING_ROLLED_BACK = "billing.pricing.rolled_back"
    # billing accounts (M9.8 — additive; `account.created`, `account.updated`,
    # `account.parent_changed` and `account.closed` were declared at M9.2 and are reused)
    ACCOUNT_ACTIVATED = "billing.account.activated"
    ACCOUNT_SUSPENDED = "billing.account.suspended"
    ACCOUNT_RESUMED = "billing.account.resumed"
    ACCOUNT_ARCHIVED = "billing.account.archived"
    ACCOUNT_RESTORED = "billing.account.restored"
    ACCOUNT_PAYER_CHANGED = "billing.account.payer_changed"
    ACCOUNT_PREFERENCES_UPDATED = "billing.account.preferences_updated"
    ACCOUNT_CONTACTS_UPDATED = "billing.account.contacts_updated"
    ACCOUNT_ADDRESSES_UPDATED = "billing.account.addresses_updated"
    ACCOUNT_CREDIT_UPDATED = "billing.account.credit_updated"
    ACCOUNT_NOTE_ADDED = "billing.account.note_added"
    # subscription engine (M9.9 — additive; the M9.2 `subscription.*` facts above are reused
    # unchanged, so exactly one event still exists per business fact)
    SUBSCRIPTION_REACTIVATED = "billing.subscription.reactivated"
    SUBSCRIPTION_ARCHIVED = "billing.subscription.archived"
    SUBSCRIPTION_TRIAL_EXPIRED = "billing.subscription.trial_expired"
    SUBSCRIPTION_ITEM_ADDED = "billing.subscription.item_added"
    SUBSCRIPTION_ITEM_UPDATED = "billing.subscription.item_updated"
    SUBSCRIPTION_ITEM_REMOVED = "billing.subscription.item_removed"
    SUBSCRIPTION_CHANGE_SCHEDULED = "billing.subscription.change_scheduled"
    SUBSCRIPTION_CHANGE_CANCELLED = "billing.subscription.change_cancelled"
    SUBSCRIPTION_UPGRADED = "billing.subscription.upgraded"
    SUBSCRIPTION_DOWNGRADED = "billing.subscription.downgraded"
    SUBSCRIPTION_RENEWAL_POLICY_CHANGED = "billing.subscription.renewal_policy_changed"
    SUBSCRIPTION_VERSION_PUBLISHED = "billing.subscription.version_published"
    SUBSCRIPTION_ROLLED_BACK = "billing.subscription.rolled_back"
    # entitlement engine (M9.10 — additive; `entitlement.granted`, `entitlement.revoked` and
    # `entitlement.snapshot_published` were declared at M9.2 and are reused unchanged, so there
    # is still exactly one event per business fact)
    ENTITLEMENT_PROFILE_OPENED = "billing.entitlement.profile_opened"
    ENTITLEMENT_DEFINED = "billing.entitlement.defined"
    ENTITLEMENT_DEFINITION_UPDATED = "billing.entitlement.definition_updated"
    ENTITLEMENT_DEFINITION_RETIRED = "billing.entitlement.definition_retired"
    ENTITLEMENT_BUNDLE_CREATED = "billing.entitlement.bundle_created"
    ENTITLEMENT_BUNDLE_UPDATED = "billing.entitlement.bundle_updated"
    ENTITLEMENT_BUNDLE_GRANTED = "billing.entitlement.bundle_granted"
    ENTITLEMENT_GRANT_ACTIVATED = "billing.entitlement.grant_activated"
    ENTITLEMENT_GRANT_ADJUSTED = "billing.entitlement.grant_adjusted"
    ENTITLEMENT_GRANT_SUSPENDED = "billing.entitlement.grant_suspended"
    ENTITLEMENT_GRANT_RESUMED = "billing.entitlement.grant_resumed"
    ENTITLEMENT_GRANT_EXPIRED = "billing.entitlement.grant_expired"
    ENTITLEMENT_GRANT_ARCHIVED = "billing.entitlement.grant_archived"
    ENTITLEMENT_CHANGE_SCHEDULED = "billing.entitlement.change_scheduled"
    ENTITLEMENT_CHANGE_CANCELLED = "billing.entitlement.change_cancelled"
    ENTITLEMENT_VERSION_PUBLISHED = "billing.entitlement.version_published"
    ENTITLEMENT_ROLLED_BACK = "billing.entitlement.rolled_back"
    # metering engine (M9.11 — additive; `usage.recorded`, `usage.quarantined` and
    # `usage.period_sealed` were declared at M9.2 and are reused unchanged, so there is still
    # exactly one event per business fact. **Billing emits no `metric.*` — it consumes that
    # stream read-only and produces only `billing.*` facts about commercial usage** (ADR-0023 §6).
    METER_CREATED = "billing.meter.created"
    METER_VERSIONED = "billing.meter.versioned"
    METER_ACTIVATED = "billing.meter.activated"
    METER_DEPRECATED = "billing.meter.deprecated"
    METER_REINSTATED = "billing.meter.reinstated"
    METER_RETIRED = "billing.meter.retired"
    USAGE_DUPLICATE_IGNORED = "billing.usage.duplicate_ignored"
    USAGE_ADJUSTED = "billing.usage.adjusted"
    USAGE_CORRECTED = "billing.usage.corrected"
    USAGE_QUARANTINE_RESOLVED = "billing.usage.quarantine_resolved"
    USAGE_QUARANTINE_DISCARDED = "billing.usage.quarantine_discarded"
    USAGE_PERIOD_OPENED = "billing.usage.period_opened"
    USAGE_PERIOD_CLOSING = "billing.usage.period_closing"
    USAGE_AGGREGATES_REBUILT = "billing.usage.aggregates_rebuilt"
    USAGE_PERIOD_REPLAYED = "billing.usage.period_replayed"
    USAGE_PERIOD_RECONCILED = "billing.usage.period_reconciled"
    # rating engine (M9.12 — additive; nothing existing changed. **Rating emits no price fact and
    # no usage fact**: it consumes both read-only and produces only `billing.rating.*` facts about
    # charges it calculated (ADR-0023 §6). One event per business fact — note that a rerate whose
    # inputs are unchanged emits `rating.unchanged`, because "we rated this again and nothing
    # moved" is the evidence determinism held.)
    COMMITMENT_DEFINED = "billing.rating.commitment_defined"
    COMMITMENT_RETIRED = "billing.rating.commitment_retired"
    RATING_COMPLETED = "billing.rating.completed"
    RATING_UNCHANGED = "billing.rating.unchanged"
    RATING_RERATED = "billing.rating.rerated"
    RATING_ADJUSTED = "billing.rating.adjusted"
    RATING_FINALIZED = "billing.rating.finalized"
    RATING_SUPERSEDED = "billing.rating.superseded"
    RATING_DISCARDED = "billing.rating.discarded"
    RATING_SNAPSHOT_TAKEN = "billing.rating.snapshot_taken"
    # credit & revenue ledger (M9.13 — additive; nothing existing changed. **The Ledger emits no
    # pricing, rating, invoice, payment or tax fact** — it records what other domains decided and
    # produces only `billing.ledger.*` and `billing.credit.*` facts (ADR-0023 §6). Note the
    # duplicate-ignored fact: "we saw this cause again and recorded no second entry" is a distinct
    # business fact and the evidence exactly-once posting worked.
    #
    # `credit.granted`, `credit.consumed` and `credit.expired` were declared at M9.2 and are
    # **reused unchanged** — they are the same business facts, and a second name for one fact is
    # the drift §A2.5 prevents. In a `StrEnum` it would not even be a second member: reusing a
    # value silently creates an alias, which is how an event vocabulary quietly miscounts.
    ACCOUNTING_PERIOD_OPENED = "billing.ledger.period_opened"
    ACCOUNTING_PERIOD_CLOSING = "billing.ledger.period_closing"
    ACCOUNTING_PERIOD_CLOSED = "billing.ledger.period_closed"
    JOURNAL_ENTRY_POSTED = "billing.ledger.entry_posted"
    JOURNAL_ENTRY_DUPLICATE_IGNORED = "billing.ledger.entry_duplicate_ignored"
    JOURNAL_ENTRY_DEFERRED = "billing.ledger.entry_deferred"
    JOURNAL_ENTRY_REVERSED = "billing.ledger.entry_reversed"
    JOURNAL_ENTRY_ADJUSTED = "billing.ledger.entry_adjusted"
    RATING_RUN_POSTED = "billing.ledger.rating_run_posted"
    BALANCE_CHECKPOINT_REBUILT = "billing.ledger.checkpoint_rebuilt"
    COMMERCIAL_CREDIT_REVOKED = "billing.credit.revoked"
    # invoice engine (M9.14 — additive; nothing existing changed. **The Invoice Engine emits no
    # pricing, rating, metering, ledger, payment or tax fact** — it documents what other domains
    # decided and produces only `billing.invoice.*`, `billing.credit_note.*` and
    # `billing.debit_note.*` facts (ADR-0023 §6).
    #
    # `invoice.drafted`, `invoice.issued`, `invoice.voided`, `invoice.paid`, `invoice.overdue` and
    # `credit_note.issued` were declared at M9.2 and are **reused unchanged** — they are the same
    # business facts. In a `StrEnum` a reused value would not even be a second member: it silently
    # creates an alias, which is how an event vocabulary quietly miscounts. `test_every_event_
    # value_is_unique` exists because M9.13 found exactly that.
    INVOICE_PREPARED = "billing.invoice.prepared"
    INVOICE_REVISED = "billing.invoice.revised"
    INVOICE_SUBMITTED_FOR_APPROVAL = "billing.invoice.submitted_for_approval"
    INVOICE_NUMBER_ALLOCATED = "billing.invoice.number_allocated"
    INVOICE_SENT_TO_COLLECTION = "billing.invoice.sent_to_collection"
    INVOICE_CREDITED = "billing.invoice.credited"
    INVOICE_RENDERED = "billing.invoice.rendered"
    INVOICE_DELIVERY_RECORDED = "billing.invoice.delivery_recorded"
    INVOICE_ATTACHMENT_RECORDED = "billing.invoice.attachment_recorded"
    DEBIT_NOTE_ISSUED = "billing.debit_note.issued"
    # tax determination engine (M9.15 — additive; nothing existing changed. **The Tax Engine
    # emits no pricing, rating, ledger, invoice or payment fact** — it determines the tax
    # treatment of facts other domains already decided, and produces only `billing.tax.*`
    # facts (ADR-0023 §6).
    #
    # `tax.profile_updated` and `tax.determined` were declared at M9.2 and are **reused unchanged**:
    # they are the same business facts, and in a `StrEnum` a repeated value silently creates an
    # alias while a repeated *name* is a `TypeError` at import — both are how a vocabulary quietly
    # miscounts, and M9.13 and M9.14 each found one.
    #
    # Note `tax.assessment_unchanged`: "we determined this again and reached the same conclusion" is
    # a distinct business fact and the evidence determination is deterministic — exactly what an
    # auditor asks for when a rate has changed and a figure has not.
    TAX_JURISDICTION_DECLARED = "billing.tax.jurisdiction_declared"
    TAX_JURISDICTION_RETIRED = "billing.tax.jurisdiction_retired"
    TAX_CLASSIFICATION_DECLARED = "billing.tax.classification_declared"
    TAX_NEXUS_REGISTERED = "billing.tax.nexus_registered"
    TAX_NEXUS_ENDED = "billing.tax.nexus_ended"
    TAX_EXEMPTION_RECORDED = "billing.tax.exemption_recorded"
    TAX_EXEMPTION_REVOKED = "billing.tax.exemption_revoked"
    TAX_RATE_DECLARED = "billing.tax.rate_declared"
    TAX_RATE_RETIRED = "billing.tax.rate_retired"
    TAX_ASSESSMENT_UNCHANGED = "billing.tax.assessment_unchanged"
    TAX_ASSESSMENT_FINALIZED = "billing.tax.assessment_finalized"
    TAX_ASSESSMENT_SUPERSEDED = "billing.tax.assessment_superseded"
    TAX_ASSESSMENT_DISCARDED = "billing.tax.assessment_discarded"
    TAX_TREATMENT_OVERRIDDEN = "billing.tax.treatment_overridden"
    # ── payment engine (M9.16, additive) ────────────────────────────────────────
    # `payment.requested`, `payment.authorized`, `payment.captured`, `payment.settled`,
    # `payment.failed`, `payment.disputed`, `reconciliation.diverged`, and the four
    # `refund.*` facts were declared at M9.2 and are **reused unchanged** — they are the same
    # business facts, and the execution of a payment does not make its capture a different event.
    #
    # What is new is what M9.2 had no vocabulary for, because it had no execution: the
    # *objective* being opened and closed, one *attempt* among several, an outcome nobody knows,
    # the resolution of one, and everything about the provider edge — what was sent, what arrived,
    # and what was refused. Note `payment.attempt_ambiguous` in particular: "we asked a provider to
    # move money and never found out whether it did" is a distinct fact, it is the one an operator
    # must act on, and a vocabulary that folded it into `payment.failed` would be a vocabulary that
    # reports a second charge as a retry.
    PAYMENT_INTENT_CREATED = "billing.payment.intent_created"
    PAYMENT_INTENT_CANCELLED = "billing.payment.intent_cancelled"
    PAYMENT_INTENT_EXPIRED = "billing.payment.intent_expired"
    PAYMENT_ATTEMPT_STARTED = "billing.payment.attempt_started"
    PAYMENT_ATTEMPT_AMBIGUOUS = "billing.payment.attempt_ambiguous"
    PAYMENT_ATTEMPT_RESOLVED = "billing.payment.attempt_resolved"
    PAYMENT_ATTEMPT_CANCELLED = "billing.payment.attempt_cancelled"
    PAYMENT_ACTION_REQUIRED = "billing.payment.action_required"
    PAYMENT_OPERATION_SENT = "billing.payment.operation_sent"
    PAYMENT_OPERATION_REPLAYED = "billing.payment.operation_replayed"
    PAYMENT_EVENT_RECEIVED = "billing.payment.event_received"
    PAYMENT_EVENT_APPLIED = "billing.payment.event_applied"
    PAYMENT_EVENT_REJECTED = "billing.payment.event_rejected"
    PAYMENT_POSTED_TO_LEDGER = "billing.payment.posted_to_ledger"
    PAYMENT_ROUTE_DECLARED = "billing.payment.route_declared"
    PAYMENT_ROUTE_RETIRED = "billing.payment.route_retired"
    REFUND_FAILED = "billing.refund.failed"
    RECONCILIATION_MATCHED = "billing.reconciliation.matched"
    RECONCILIATION_RESOLVED = "billing.reconciliation.resolved"
    # dunning & collections (M9.17 — additive). Three members are **deliberately not** declared
    # here: `DUNNING_STARTED`, `DUNNING_RECOVERED` and `DUNNING_ABANDONED` were declared at M9.2
    # and mean exactly what a case opening, recovering and exhausting mean. Publishing
    # `billing.collection.opened` beside `billing.dunning.started` would give one business fact two
    # names and every consumer a choice to get wrong (AR-2: one producer, one type). What is new
    # below is what M9.2 had no concept of — a versioned strategy, a decided-but-not-yet-executed
    # action, and a recovery blocked on an outcome nobody knows yet.
    COLLECTION_POLICY_PUBLISHED = "billing.collection.policy_published"
    COLLECTION_CYCLE_OPENED = "billing.collection.cycle_opened"
    COLLECTION_ACTION_PLANNED = "billing.collection.action_planned"
    COLLECTION_ACTION_EXECUTED = "billing.collection.action_executed"
    COLLECTION_ACTION_OBSOLETE = "billing.collection.action_obsolete"
    COLLECTION_NOTICE_SENT = "billing.collection.notice_sent"
    COLLECTION_RETRY_REQUESTED = "billing.collection.retry_requested"
    COLLECTION_CASE_BLOCKED = "billing.collection.blocked"
    COLLECTION_CASE_ESCALATED = "billing.collection.escalated"
    COLLECTION_CASE_CANCELLED = "billing.collection.cancelled"
    # revenue recognition (M9.18 — additive). Note what is *not* declared: there is no
    # `billing.revenue.posted`. A recognition that reaches the Ledger is the **Ledger's** fact and
    # M9.13 already publishes it; a second type here would let one posting be counted twice.
    RECOGNITION_POLICY_PUBLISHED = "billing.revenue.policy_published"
    REVENUE_SCHEDULED = "billing.revenue.scheduled"
    REVENUE_RECOGNIZED = "billing.revenue.recognized"
    REVENUE_SCHEDULE_COMPLETED = "billing.revenue.completed"
    REVENUE_SCHEDULE_SUPERSEDED = "billing.revenue.superseded"
    REVENUE_SCHEDULE_CANCELLED = "billing.revenue.cancelled"
    REVENUE_ADJUSTED = "billing.revenue.adjusted"
    REVENUE_UNPOSTED = "billing.revenue.unposted"

    # reconciliation & financial integrity (M9.19 — additive). Note what is *not* declared:
    #
    #   * there is no `billing.integrity.matched`. Agreement is a counter on the run, not news —
    #     publishing it would emit one event per compared fact and drown the stream in non-events;
    #   * there is no `billing.integrity.repaired`. M9.19 repairs nothing, so it has nothing to
    #     announce. The owning domain publishes its own repair (`billing.ledger.entry_recorded`,
    #     `billing.revenue.recognized`), and a second type here would let one repair be counted
    #     twice by anyone aggregating the stream — the same rule that kept `billing.revenue.posted`
    #     out of M9.18.
    #
    # `INTEGRITY_CRITICAL_DIVERGENCE` is separate from the general one deliberately: an alerting
    # rule that pages on a fabricated or duplicated financial fact must not also page on a period
    # disposition M9.13 made on purpose.
    INTEGRITY_RUN_COMPLETED = "billing.integrity.run_completed"
    INTEGRITY_DIVERGENCE_DETECTED = "billing.integrity.divergence_detected"
    INTEGRITY_CRITICAL_DIVERGENCE = "billing.integrity.critical_divergence"
    INTEGRITY_FINDING_ACKNOWLEDGED = "billing.integrity.acknowledged"
    INTEGRITY_REPAIR_REQUESTED = "billing.integrity.repair_requested"
    INTEGRITY_FINDING_RESOLVED = "billing.integrity.resolved"
    INTEGRITY_FINDING_CLOSED = "billing.integrity.closed"

    # financial integrity correlation (M9.20 — additive). Note what is *not* declared:
    #
    #   * there is no `billing.incident.finding_reconciled`. M9.19 already publishes what a
    #     reconciliation found; a second type here would let one divergence be counted twice by
    #     anyone aggregating the stream;
    #   * there is no `billing.incident.repaired`. M9.20 repairs nothing — the owning domain
    #     publishes its own repair, and republishing it would make one fix look like two;
    #   * there is no event for a **rejected** command. A refusal is not a business fact, and
    #     emitting one would make an audit trail in which nothing happened indistinguishable from
    #     one in which something did.
    #
    # Investigation states (acknowledged, investigating, verifying) are deliberately absent too:
    # this stream is the platform's business-fact feed, not a mirror of the audit trail. The full
    # sequence lives in `cp_billing_integrity_event`, which is where an auditor reads it.
    INCIDENT_OPENED = "billing.incident.opened"
    INCIDENT_FINDING_CORRELATED = "billing.incident.finding_correlated"
    INCIDENT_RESOLVED = "billing.incident.resolved"
    INCIDENT_CLOSED = "billing.incident.closed"
    INCIDENT_MERGED = "billing.incident.merged"
    INCIDENT_CORRELATION_CORRECTED = "billing.incident.correlation_corrected"

    # ── M9.21 · financial investigation & audit evidence ────────────────────────
    #
    # Five, against twenty timeline kinds — the same posture M9.20 takes and for the same reason.
    # An external consumer needs to know that Billing opened an investigation, that it got stuck
    # because evidence could not be retrieved, what it concluded, that an auditable package was
    # produced and that the case closed. It does not need one message per investigator keystroke;
    # the full sequence lives in `cp_billing_investigation_event`, which is where an examiner reads
    # it.
    #
    # `INVESTIGATION_EVIDENCE_UNAVAILABLE` is on the bus deliberately: an audit that cannot retrieve
    # its evidence is a fact the platform outside Billing needs to hear about, and burying it in a
    # table nobody watches is how "could not retrieve" quietly becomes "fine".
    #
    # There is no event for a repair or a reconciliation here — the owning domain publishes its own,
    # and republishing it would make one occurrence look like two.
    INVESTIGATION_OPENED = "billing.investigation.opened"
    INVESTIGATION_EVIDENCE_UNAVAILABLE = "billing.investigation.evidence_unavailable"
    INVESTIGATION_CONCLUDED = "billing.investigation.concluded"
    INVESTIGATION_CLOSED = "billing.investigation.closed"
    AUDIT_PACKAGE_CREATED = "billing.investigation.audit_package_created"

    # ── M9.22 · compliance & regulatory reporting ───────────────────────────────
    #
    # Five, against sixteen trail kinds — the posture M9.20 and M9.21 both take. An external
    # consumer needs to know that a reporting rule was published, that a filing was made or
    # corrected, that a package was prepared for submission, and that a *blocking* problem stopped
    # one. It does not need one message per field computation; the full sequence lives in
    # `cp_billing_report_event`.
    #
    # Only a **blocking** exception reaches the bus. Publishing advisory ones would train
    # consumers to ignore the feed, which is how the blocking one gets missed.
    #
    # There is no `billing.report.submitted`: M9.22 sends nothing to a regulator, and an event
    # claiming otherwise would make a deferred boundary look like a shipped one.
    REPORT_DEFINITION_PUBLISHED = "billing.report.definition_published"
    REPORT_EXCEPTION_RAISED = "billing.report.exception_raised"
    REPORT_FINALIZED = "billing.report.finalized"
    REPORT_CORRECTION_OPENED = "billing.report.correction_opened"
    SUBMISSION_PACKAGE_CREATED = "billing.report.submission_package_created"


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """One immutable billing fact. Org-scoped (§A11.2) and actor-attributed so the audit
    trail answers *who changed what, when* without a second mechanism."""

    id: str
    type: BillingEventType
    ts: float
    org_id: str
    subject_id: str = ""  # the aggregate this fact is about
    actor_id: str = ""  # the principal that caused it ("" = system/scheduled)
    correlation_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class EventEmitter(ABC):
    """The emission seam. The Foundation depends on this ABC only; the control-plane
    Foundation service supplies an implementation that mirrors events into the durable
    Epic 8 audit stream (M9.3+). The Foundation never dispatches, queues, or persists."""

    @abstractmethod
    def emit(self, event: BillingEvent) -> None: ...


class NullEventEmitter(EventEmitter):
    """Drops events — for pure-domain unit tests that assert on return values only."""

    def emit(self, event: BillingEvent) -> None:  # noqa: D102
        return None


class RecordingEventEmitter(EventEmitter):
    """Deterministic, append-only in-memory recorder — the test/audit default. Insertion
    order is the emission order; there is no update or delete path (append-only, §A5)."""

    def __init__(self) -> None:
        self._events: list[BillingEvent] = []

    def emit(self, event: BillingEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[BillingEvent, ...]:
        return tuple(self._events)

    def of_type(self, event_type: BillingEventType) -> tuple[BillingEvent, ...]:
        return tuple(e for e in self._events if e.type is event_type)
