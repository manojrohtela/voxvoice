"""Tax factories + repository contracts (M9.15).

`TaxFactory` is the one place a jurisdiction, a classification, a nexus, an exemption, a declared
rate, an assessment, or an audit entry is constructed, following the M9.2 factory discipline
exactly: validate structurally, **fail fast** on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and clock
seams, and emit the corresponding `billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all. One is easy to
miss and deliberate: re-determining a subject whose basis has not moved emits
`billing.tax.assessment_unchanged`, not `billing.tax.determined` — "we determined this again and
reached the same conclusion" is a distinct fact and the evidence determination is deterministic. It
is exactly what an auditor asks for when a rate has changed and a figure has not.

No I/O, no persistence, no networking, and **no rate**. Emission is the only effect and it is
injected.

---

## Why assessment ids are derived rather than minted

An assessment's id is a hash of its **basis** — the immutable inputs the determination read. Three
consequences, all of them the reason:

* **Re-determining is idempotent.** Assessing the same facts twice is one assessment, so a retried
  invoice run does not produce two competing determinations for one document.
* **A re-assessment cannot overwrite what it revises.** A changed input derives a *different* id, so
  the previous determination survives beside the new one — and the question an audit asks is not
  "what is the tax?" but "what tax did you charge, and what did you change it to?"
* **Replay is a no-op rather than a rewrite.** Reprocessing a period recomputes the same ids and
  writes nothing new, which is what makes a reproduction drill safe to run in production.

## Why rates are repository rows and not constants

`DeclaredTaxRate` rows are **evidence configuration**, effective-dated and append-only. Nothing in
this package knows any jurisdiction's percentage, and `TaxRateRepository` has no `update`:
superseding a rate appends a new row beside the one it replaces, because an issued invoice must
stay explainable against the rate in force when it was issued (ADR-0023 TX2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from .commercial_tax import (
    AssessmentState,
    DeclaredTaxRate,
    ExemptionKind,
    JurisdictionLevel,
    NexusKind,
    TaxAction,
    TaxActivity,
    TaxAssessment,
    TaxClassification,
    TaxExemption,
    TaxJurisdiction,
    TaxNexus,
    TaxOverride,
    derive_rate_id,
    jurisdiction_key,
)
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock
from .tax import TaxRegime, TaxRegistration, TaxTreatment
from .tax_rules import TaxValidator

PREFIX_TAX_JURISDICTION = "txj"
PREFIX_TAX_CLASSIFICATION = "txc"
PREFIX_TAX_NEXUS = "txn"
PREFIX_TAX_EXEMPTION = "txe"
PREFIX_TAX_ACTIVITY = "txl"

#: Which `billing.*` fact each assessment lifecycle move publishes. A table so "exactly one event
#: per business fact" is checkable by iteration rather than asserted in prose.
ASSESSMENT_EVENTS: dict[AssessmentState, BillingEventType] = {
    AssessmentState.FINAL: BillingEventType.TAX_ASSESSMENT_FINALIZED,
    AssessmentState.SUPERSEDED: BillingEventType.TAX_ASSESSMENT_SUPERSEDED,
    AssessmentState.DISCARDED: BillingEventType.TAX_ASSESSMENT_DISCARDED,
}


@dataclass(frozen=True, slots=True)
class TaxFactory:
    """Constructs tax aggregates from injected seams. Stateless; holds references only."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: TaxValidator = TaxValidator()

    # ── configuration ───────────────────────────────────────────────────────────

    def jurisdiction(
        self,
        *,
        org_id: str,
        country: str,
        region: str = "",
        display_name: str = "",
        level: JurisdictionLevel = JurisdictionLevel.COUNTRY,
        regime: TaxRegime = TaxRegime.NONE,
        parent_code: str = "",
        notes: str = "",
        actor_id: str = "",
    ) -> TaxJurisdiction:
        """Declare a place with taxing authority — never its percentage.

        The code is **derived** from country and region rather than accepted from a caller, so
        `IN:KA` and `in:ka` cannot both exist. A duplicate jurisdiction is a duplicate nexus, and a
        duplicate nexus is a supplier registered twice in the same place."""
        now = self.clock.now()
        code = jurisdiction_key(country, region)
        declared = TaxJurisdiction(
            id=self.ids.new(PREFIX_TAX_JURISDICTION),
            org_id=org_id,
            code=code,
            display_name=display_name or code,
            country=country.upper(),
            region=region.upper(),
            level=level,
            regime=regime,
            parent_code=parent_code,
            declared_at=now,
            declared_by=actor_id,
            notes=notes,
        )
        self._fail_fast(self.validator.validate_jurisdiction(declared))
        self._emit(
            BillingEventType.TAX_JURISDICTION_DECLARED,
            org_id,
            declared.id,
            actor_id,
            code=code,
            regime=regime.value,
            level=level.value,
        )
        return declared

    def classification(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        default_treatment: TaxTreatment = TaxTreatment.STANDARD,
        codes: tuple[tuple[str, str], ...] = (),
        is_digital_service: bool = False,
        notes: str = "",
        actor_id: str = "",
    ) -> TaxClassification:
        """Declare what is being supplied, in the vocabulary a tax authority uses."""
        classification = TaxClassification(
            id=self.ids.new(PREFIX_TAX_CLASSIFICATION),
            org_id=org_id,
            code=code,
            display_name=display_name,
            default_treatment=default_treatment,
            codes=codes,
            is_digital_service=is_digital_service,
            declared_at=self.clock.now(),
            declared_by=actor_id,
            notes=notes,
        )
        self._emit(
            BillingEventType.TAX_CLASSIFICATION_DECLARED,
            org_id,
            classification.id,
            actor_id,
            code=code,
            treatment=default_treatment.value,
        )
        return classification

    def nexus(
        self,
        *,
        org_id: str,
        entity_code: str,
        jurisdiction_code: str,
        kind: NexusKind = NexusKind.REGISTRATION,
        registration: TaxRegistration | None = None,
        effective_from: float | None = None,
        effective_until: float | None = None,
        established_reason: str = "",
        actor_id: str = "",
    ) -> TaxNexus:
        """Record the supplier's obligation in one jurisdiction, and the registration that
        discharges it."""
        now = self.clock.now()
        registered = TaxNexus(
            id=self.ids.new(PREFIX_TAX_NEXUS),
            org_id=org_id,
            entity_code=entity_code,
            jurisdiction_code=jurisdiction_code,
            kind=kind,
            registration=registration,
            effective_from=effective_from if effective_from is not None else now,
            effective_until=effective_until,
            established_reason=established_reason,
            declared_at=now,
            declared_by=actor_id,
        )
        self._emit(
            BillingEventType.TAX_NEXUS_REGISTERED,
            org_id,
            registered.id,
            actor_id,
            entity_code=entity_code,
            jurisdiction_code=jurisdiction_code,
            kind=kind.value,
            registration=registration.number if registration else "",
        )
        return registered

    def exemption(
        self,
        *,
        org_id: str,
        account_id: str,
        jurisdiction_code: str,
        kind: ExemptionKind = ExemptionKind.CERTIFICATE,
        certificate_ref: str = "",
        reason: str = "",
        effective_from: float | None = None,
        effective_until: float | None = None,
        evidence_ref: str = "",
        actor_id: str = "",
    ) -> TaxExemption:
        now = self.clock.now()
        recorded = TaxExemption(
            id=self.ids.new(PREFIX_TAX_EXEMPTION),
            org_id=org_id,
            account_id=account_id,
            jurisdiction_code=jurisdiction_code,
            kind=kind,
            certificate_ref=certificate_ref,
            reason=reason,
            effective_from=effective_from if effective_from is not None else now,
            effective_until=effective_until,
            evidence_ref=evidence_ref,
            recorded_at=now,
            recorded_by=actor_id,
        )
        self._emit(
            BillingEventType.TAX_EXEMPTION_RECORDED,
            org_id,
            recorded.id,
            actor_id,
            account_id=account_id,
            jurisdiction_code=jurisdiction_code,
            kind=kind.value,
        )
        return recorded

    def rate(
        self,
        *,
        org_id: str,
        jurisdiction_code: str,
        tax_type: str,
        name: str,
        rate_basis_points: int,
        classification_code: str = "",
        treatment: TaxTreatment = TaxTreatment.STANDARD,
        effective_from: float | None = None,
        effective_until: float | None = None,
        reference: str = "",
        source_version: str = "",
        actor_id: str = "",
    ) -> DeclaredTaxRate:
        """Declare an effective-dated rate as **Billing evidence**.

        Nothing in this package knows what the rate should be. An operator declares it with the
        notification that set it and the window it applies in, and that declaration is itself
        evidence a challenge can examine. The id is derived from the jurisdiction, tax type,
        classification, and effective start, so re-declaring the same rate is one row."""
        from_ts = effective_from if effective_from is not None else self.clock.now()
        declared = DeclaredTaxRate(
            id=derive_rate_id(
                org_id, jurisdiction_code, tax_type, name, classification_code, from_ts
            ),
            org_id=org_id,
            jurisdiction_code=jurisdiction_code,
            tax_type=tax_type,
            name=name,
            rate_basis_points=rate_basis_points,
            classification_code=classification_code,
            treatment=treatment,
            effective_from=from_ts,
            effective_until=effective_until,
            reference=reference,
            source_version=source_version,
            declared_at=self.clock.now(),
            declared_by=actor_id,
        )
        self._fail_fast(self.validator.validate_rate(declared))
        self._emit(
            BillingEventType.TAX_RATE_DECLARED,
            org_id,
            declared.id,
            actor_id,
            jurisdiction_code=jurisdiction_code,
            tax_type=tax_type,
            rate_basis_points=rate_basis_points,
            reference=reference,
        )
        return declared

    # ── determination ───────────────────────────────────────────────────────────

    def emit_assessment(
        self, assessment: TaxAssessment, *, unchanged: bool = False, actor_id: str = ""
    ) -> None:
        """Emit exactly the one fact this determination represents.

        `unchanged=True` publishes `billing.tax.assessment_unchanged` rather than
        `billing.tax.determined`: re-determining a subject whose basis has not moved is a distinct
        business fact, and it is the evidence determination is deterministic."""
        self._emit(
            BillingEventType.TAX_ASSESSMENT_UNCHANGED
            if unchanged
            else BillingEventType.TAX_DETERMINED,
            assessment.org_id,
            assessment.id,
            actor_id,
            account_id=assessment.account_id,
            subject=assessment.subject_id,
            subject_kind=assessment.subject_kind.value,
            treatment=assessment.treatment.value,
            jurisdiction_code=assessment.place_of_supply.resolved_code,
            tax_total_minor=assessment.tax_total.minor,
            currency=assessment.currency.value,
            digest=assessment.digest,
        )

    def emit_transition(self, assessment: TaxAssessment, *, actor_id: str = "", **data) -> None:
        event_type = ASSESSMENT_EVENTS.get(assessment.state)
        if event_type is None:
            return
        self._emit(
            event_type,
            assessment.org_id,
            assessment.id,
            actor_id,
            account_id=assessment.account_id,
            subject=assessment.subject_id,
            state=assessment.state.value,
            **data,
        )

    def override(
        self,
        assessment: TaxAssessment,
        *,
        treatment: TaxTreatment,
        reason: str,
        actor_id: str,
        reference: str = "",
    ) -> TaxOverride:
        """A human decision to depart from what determination concluded.

        Always attributed, always reasoned, and it keeps the original conclusion beside it — an
        override that silently replaced a figure would be the one thing an audit cannot forgive."""
        return TaxOverride(
            treatment=treatment,
            reason=reason,
            actor_id=actor_id,
            at=self.clock.now(),
            supersedes_treatment=assessment.treatment,
            reference=reference,
        )

    def emit_override(self, assessment: TaxAssessment, *, actor_id: str = "") -> None:
        override = assessment.override
        self._emit(
            BillingEventType.TAX_TREATMENT_OVERRIDDEN,
            assessment.org_id,
            assessment.id,
            actor_id,
            account_id=assessment.account_id,
            subject=assessment.subject_id,
            treatment=assessment.treatment.value,
            supersedes=(
                override.supersedes_treatment.value
                if override and override.supersedes_treatment
                else ""
            ),
            reason=override.reason if override else "",
        )

    def retire(self, aggregate, *, at: float | None = None, actor_id: str = "", reason: str = ""):
        """End a jurisdiction, a nexus, an exemption, or a rate — never delete one.

        Retirement is a timestamp, so a document issued while the thing was live stays explainable
        after it stops being. Deleting it would make "was this customer exempt on 4 March?"
        unanswerable, which is precisely the question an audit asks."""
        now = at if at is not None else self.clock.now()
        if isinstance(aggregate, TaxJurisdiction):
            return replace(aggregate, is_active=False, notes=reason or aggregate.notes)
        if isinstance(aggregate, TaxNexus):
            return replace(aggregate, effective_until=now)
        if isinstance(aggregate, TaxExemption):
            return replace(aggregate, revoked_at=now, revoked_reason=reason)
        if isinstance(aggregate, DeclaredTaxRate):
            return replace(aggregate, retired_at=now)
        raise TypeError(f"cannot retire {type(aggregate).__name__}")

    def emit_retirement(
        self, event_type: BillingEventType, aggregate, *, actor_id: str = "", reason: str = ""
    ) -> None:
        self._emit(
            event_type,
            aggregate.org_id,
            aggregate.id,
            actor_id,
            reason=reason,
        )

    # ── history ─────────────────────────────────────────────────────────────────

    def activity(
        self,
        *,
        org_id: str,
        action: TaxAction,
        actor_id: str = "",
        account_id: str = "",
        subject_id: str = "",
        assessment_id: str = "",
        jurisdiction_code: str = "",
        amount_minor: int = 0,
        currency: Currency | None = None,
        reason: str = "",
        detail: str = "",
    ) -> TaxActivity:
        return TaxActivity(
            id=self.ids.new(PREFIX_TAX_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self.clock.now(),
            account_id=account_id,
            subject_id=subject_id,
            assessment_id=assessment_id,
            jurisdiction_code=jurisdiction_code,
            amount_minor=amount_minor,
            currency=currency,
            reason=reason,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject_id: str, actor_id: str, **data
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class TaxJurisdictionRepository(ABC):
    """Declared jurisdictions. Org-scoped by construction (§A11.2).

    `replace` retires — it flips `is_active` and never removes a row, because a document issued
    while a jurisdiction was live must stay explainable after it is retired."""

    @abstractmethod
    def add(self, jurisdiction: TaxJurisdiction) -> TaxJurisdiction: ...
    @abstractmethod
    def get(self, org_id: str, jurisdiction_id: str) -> TaxJurisdiction | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> TaxJurisdiction | None: ...
    @abstractmethod
    def all(self, org_id: str, include_retired: bool = False) -> tuple[TaxJurisdiction, ...]: ...
    @abstractmethod
    def replace(self, jurisdiction: TaxJurisdiction) -> TaxJurisdiction: ...


class TaxClassificationRepository(ABC):
    """What is supplied, in the authority's vocabulary. No delete: a classification an issued
    invoice referenced must remain readable."""

    @abstractmethod
    def add(self, classification: TaxClassification) -> TaxClassification: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> TaxClassification | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[TaxClassification, ...]: ...


class TaxNexusRepository(ABC):
    """Supplier obligations. `replace` ends a nexus by setting its effective end — never by
    removing the row, because "were we registered there in March?" is an audit question."""

    @abstractmethod
    def add(self, nexus: TaxNexus) -> TaxNexus: ...
    @abstractmethod
    def get(self, org_id: str, nexus_id: str) -> TaxNexus | None: ...
    @abstractmethod
    def for_entity(self, org_id: str, entity_code: str) -> tuple[TaxNexus, ...]: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[TaxNexus, ...]: ...
    @abstractmethod
    def replace(self, nexus: TaxNexus) -> TaxNexus: ...


class TaxExemptionRepository(ABC):
    """Customer exemptions. Revoked by timestamp, never deleted."""

    @abstractmethod
    def add(self, exemption: TaxExemption) -> TaxExemption: ...
    @abstractmethod
    def get(self, org_id: str, exemption_id: str) -> TaxExemption | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[TaxExemption, ...]: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[TaxExemption, ...]: ...
    @abstractmethod
    def replace(self, exemption: TaxExemption) -> TaxExemption: ...


class TaxRateRepository(ABC):
    """**Effective-dated rate evidence. Append-only.**

    There is no `update` method — not by convention, but because none exists to call. Superseding a
    rate appends a new row beside the one it replaces, so an issued invoice stays explainable
    against the rate that was in force when it was issued (ADR-0023 TX2). `retire` sets a timestamp;
    it removes nothing."""

    @abstractmethod
    def append(self, rate: DeclaredTaxRate) -> DeclaredTaxRate:
        """Store, or return what is already stored for this cause. Never raises on a duplicate."""

    @abstractmethod
    def get(self, org_id: str, rate_id: str) -> DeclaredTaxRate | None: ...
    @abstractmethod
    def for_jurisdiction(self, org_id: str, jurisdiction_code: str) -> tuple[DeclaredTaxRate, ...]:
        """Every rate ever declared for a jurisdiction, live or not — what an audit reads."""

    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[DeclaredTaxRate, ...]: ...
    @abstractmethod
    def retire(self, rate: DeclaredTaxRate) -> DeclaredTaxRate:
        """Set the retirement timestamp. The rate row itself is never amended or removed."""


class TaxAssessmentRepository(ABC):
    """**Append-only determinations.**

    No update method and no delete method. A correction is a *new* assessment carrying
    `supersedes_assessment_id`; `mark_state` is the one narrow exception and it moves the lifecycle
    only — the figures, the evidence, and the digest are never amended, which the FSM's absent edge
    out of `final` is what actually enforces."""

    @abstractmethod
    def append(self, assessment: TaxAssessment) -> TaxAssessment:
        """Store, or return what is already stored for this basis. Never writes twice for one."""

    @abstractmethod
    def get(self, org_id: str, assessment_id: str) -> TaxAssessment | None: ...
    @abstractmethod
    def for_subject(self, org_id: str, subject_id: str) -> tuple[TaxAssessment, ...]:
        """Every determination made for a subject, oldest first — the supersession chain."""

    @abstractmethod
    def current_for_subject(self, org_id: str, subject_id: str) -> TaxAssessment | None:
        """The determination that stands: the finalized one, or the newest provisional."""

    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[TaxAssessment, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[TaxAssessment, ...]: ...
    @abstractmethod
    def mark_state(self, assessment: TaxAssessment) -> TaxAssessment:
        """Lifecycle state only. Figures and evidence are never amended."""


class TaxActivityRepository(ABC):
    """Append-only tax audit trail."""

    @abstractmethod
    def append(self, activity: TaxActivity) -> TaxActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[TaxActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""

    @abstractmethod
    def for_subject(self, org_id: str, subject_id: str) -> tuple[TaxActivity, ...]: ...


__all__ = [
    "ASSESSMENT_EVENTS",
    "PREFIX_TAX_ACTIVITY",
    "PREFIX_TAX_CLASSIFICATION",
    "PREFIX_TAX_EXEMPTION",
    "PREFIX_TAX_JURISDICTION",
    "PREFIX_TAX_NEXUS",
    "TaxActivityRepository",
    "TaxAssessmentRepository",
    "TaxClassificationRepository",
    "TaxExemptionRepository",
    "TaxFactory",
    "TaxJurisdictionRepository",
    "TaxNexusRepository",
    "TaxRateRepository",
]
