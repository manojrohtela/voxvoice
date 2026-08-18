"""Tax Determination repositories (M9.15) — additive to the M9.4 persistence layer.

Seven SQL implementations of the M9.15 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

Three expose **no update or delete method at all**: declared rates, assessments, and the audit
trail. Not by convention — there is no method to call. `retire` and `mark_state` are the two narrow
exceptions and neither amends a figure: the first sets a timestamp, the second moves a lifecycle
state along an FSM that has no edge out of `final`.

## The idempotent determination

`SqlTaxAssessmentRepository.append` is defended the same three ways M9.11–M9.14 defend theirs: a
**derived primary key** (the basis digest, so two workers determining the same facts compute the
same id and collide rather than both succeeding), a **read-before-write**, and a **`SAVEPOINT`**
around the insert whose `IntegrityError` becomes a read of the winner — so losing a race does not
destroy the caller's transaction along with the failed insert.

`current_for_subject` is the one query with a rule in it, and the rule is declared rather than
emergent: **a finalized determination stands over any provisional one**, and among provisionals the
newest version wins. Ordering by recency alone would let a stale draft shadow the determination an
invoice was actually issued with.

No repository here resolves a place of supply, decides a treatment, chooses a rate, computes a tax,
or rounds one. Those are Foundation decisions, and a repository that made one would put tax logic
where nobody would look for it."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    AssessmentState,
    DeclaredTaxRate,
    TaxActivity,
    TaxActivityRepository,
    TaxAssessment,
    TaxAssessmentRepository,
    TaxClassification,
    TaxClassificationRepository,
    TaxExemption,
    TaxExemptionRepository,
    TaxJurisdiction,
    TaxJurisdictionRepository,
    TaxNexus,
    TaxNexusRepository,
    TaxRateRepository,
)

from . import tax_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _jurisdiction_projection(j: TaxJurisdiction) -> dict:
    return {
        "id": j.id,
        "org_id": j.org_id,
        "code": j.code,
        "display_name": j.display_name,
        "country": j.country,
        "region": j.region,
        "level": j.level.value,
        "regime": j.regime.value,
        "parent_code": j.parent_code,
        "is_active": j.is_active,
        "declared_at": j.declared_at,
        "declared_by": j.declared_by,
    }


def _classification_projection(c: TaxClassification) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "code": c.code,
        "display_name": c.display_name,
        "default_treatment": c.default_treatment.value,
        "is_digital_service": c.is_digital_service,
        "declared_at": c.declared_at,
        "declared_by": c.declared_by,
    }


def _nexus_projection(n: TaxNexus) -> dict:
    return {
        "id": n.id,
        "org_id": n.org_id,
        "entity_code": n.entity_code,
        "jurisdiction_code": n.jurisdiction_code,
        "kind": n.kind.value,
        "registration_number": n.registration.number if n.registration else "",
        "registration_regime": n.registration.regime.value if n.registration else "",
        "effective_from": n.effective_from,
        "effective_until": n.effective_until,
        "declared_at": n.declared_at,
        "declared_by": n.declared_by,
    }


def _exemption_projection(e: TaxExemption) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "account_id": e.account_id,
        "jurisdiction_code": e.jurisdiction_code,
        "kind": e.kind.value,
        "certificate_ref": e.certificate_ref,
        "effective_from": e.effective_from,
        "effective_until": e.effective_until,
        "revoked_at": e.revoked_at,
        "recorded_at": e.recorded_at,
        "recorded_by": e.recorded_by,
    }


def _rate_projection(r: DeclaredTaxRate) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "jurisdiction_code": r.jurisdiction_code,
        "tax_type": r.tax_type,
        "name": r.name,
        "rate_basis_points": r.rate_basis_points,
        "classification_code": r.classification_code,
        "treatment": r.treatment.value,
        "effective_from": r.effective_from,
        "effective_until": r.effective_until,
        "reference": r.reference,
        "source_version": r.source_version,
        "retired_at": r.retired_at,
        "declared_at": r.declared_at,
        "declared_by": r.declared_by,
    }


def _assessment_projection(a: TaxAssessment) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "account_id": a.account_id,
        "subject_id": a.subject_id,
        "subject_kind": a.subject_kind.value,
        "state": a.state.value,
        "treatment": a.treatment.value,
        "regime": a.regime.value,
        "currency": a.currency.value,
        "version": a.version,
        "jurisdiction_code": a.place_of_supply.resolved_code,
        "gst_split": a.place_of_supply.gst_split.value,
        "taxable_base_minor": a.taxable_base.minor,
        "tax_total_minor": a.tax_total.minor,
        "withholding_minor": a.withholding_minor,
        "line_count": len(a.lines),
        "component_count": len(a.assessed_components),
        "nexus_id": a.nexus_id,
        "registration_used": a.registration_used,
        "exemption_id": a.exemption_id,
        "is_overridden": a.is_overridden,
        "is_sourced": a.is_sourced,
        "supersedes_assessment_id": a.supersedes_assessment_id,
        "digest": a.digest,
        "assessed_at": a.assessed_at,
        "assessed_by": a.assessed_by,
        "finalized_at": a.finalized_at,
    }


def _activity_projection(a: TaxActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "account_id": a.account_id,
        "subject_id": a.subject_id,
        "assessment_id": a.assessment_id,
        "jurisdiction_code": a.jurisdiction_code,
        "amount_minor": a.amount_minor,
        "currency": a.currency.value if a.currency else "",
        "reason": a.reason,
    }


class SqlTaxJurisdictionRepository(_Repo, TaxJurisdictionRepository):
    """Declared jurisdictions. `replace` retires by flag; no row is ever removed."""

    def add(self, jurisdiction: TaxJurisdiction) -> TaxJurisdiction:
        self._add(
            rows.BillingTaxJurisdictionRow(
                payload=encode(jurisdiction), **_jurisdiction_projection(jurisdiction)
            )
        )
        return jurisdiction

    def get(self, org_id: str, jurisdiction_id: str) -> TaxJurisdiction | None:
        row = self._row(org_id, jurisdiction_id)
        return from_row(TaxJurisdiction, row) if row else None

    def by_code(self, org_id: str, code: str) -> TaxJurisdiction | None:
        row = self._one(
            select(rows.BillingTaxJurisdictionRow).where(
                rows.BillingTaxJurisdictionRow.org_id == org_id,
                rows.BillingTaxJurisdictionRow.code == code,
            )
        )
        return from_row(TaxJurisdiction, row) if row else None

    def all(self, org_id: str, include_retired: bool = False) -> tuple[TaxJurisdiction, ...]:
        statement = select(rows.BillingTaxJurisdictionRow).where(
            rows.BillingTaxJurisdictionRow.org_id == org_id
        )
        if not include_retired:
            statement = statement.where(rows.BillingTaxJurisdictionRow.is_active.is_(True))  # type: ignore[attr-defined]
        found = self._all(statement.order_by(rows.BillingTaxJurisdictionRow.code))
        return tuple(from_row(TaxJurisdiction, r) for r in found)

    def replace(self, jurisdiction: TaxJurisdiction) -> TaxJurisdiction:
        existing = self._row(jurisdiction.org_id, jurisdiction.id)
        if existing is None:
            return self.add(jurisdiction)
        update_row(existing, jurisdiction, _jurisdiction_projection(jurisdiction))
        self._session.flush()
        return jurisdiction

    def _row(self, org_id: str, jurisdiction_id: str):
        return self._one(
            select(rows.BillingTaxJurisdictionRow).where(
                rows.BillingTaxJurisdictionRow.org_id == org_id,
                rows.BillingTaxJurisdictionRow.id == jurisdiction_id,
            )
        )


class SqlTaxClassificationRepository(_Repo, TaxClassificationRepository):
    """What is supplied, in the authority's vocabulary. No delete."""

    def add(self, classification: TaxClassification) -> TaxClassification:
        self._add(
            rows.BillingTaxClassificationRow(
                payload=encode(classification), **_classification_projection(classification)
            )
        )
        return classification

    def by_code(self, org_id: str, code: str) -> TaxClassification | None:
        row = self._one(
            select(rows.BillingTaxClassificationRow).where(
                rows.BillingTaxClassificationRow.org_id == org_id,
                rows.BillingTaxClassificationRow.code == code,
            )
        )
        return from_row(TaxClassification, row) if row else None

    def all(self, org_id: str) -> tuple[TaxClassification, ...]:
        found = self._all(
            select(rows.BillingTaxClassificationRow)
            .where(rows.BillingTaxClassificationRow.org_id == org_id)
            .order_by(rows.BillingTaxClassificationRow.code)
        )
        return tuple(from_row(TaxClassification, r) for r in found)


class SqlTaxNexusRepository(_Repo, TaxNexusRepository):
    """Supplier obligations. `replace` ends a nexus by timestamp; no row is removed."""

    def add(self, nexus: TaxNexus) -> TaxNexus:
        self._add(rows.BillingTaxNexusRow(payload=encode(nexus), **_nexus_projection(nexus)))
        return nexus

    def get(self, org_id: str, nexus_id: str) -> TaxNexus | None:
        row = self._row(org_id, nexus_id)
        return from_row(TaxNexus, row) if row else None

    def for_entity(self, org_id: str, entity_code: str) -> tuple[TaxNexus, ...]:
        found = self._all(
            select(rows.BillingTaxNexusRow)
            .where(
                rows.BillingTaxNexusRow.org_id == org_id,
                rows.BillingTaxNexusRow.entity_code == entity_code,
            )
            .order_by(rows.BillingTaxNexusRow.jurisdiction_code, rows.BillingTaxNexusRow.id)
        )
        return tuple(from_row(TaxNexus, r) for r in found)

    def all(self, org_id: str) -> tuple[TaxNexus, ...]:
        found = self._all(
            select(rows.BillingTaxNexusRow)
            .where(rows.BillingTaxNexusRow.org_id == org_id)
            .order_by(rows.BillingTaxNexusRow.entity_code, rows.BillingTaxNexusRow.id)
        )
        return tuple(from_row(TaxNexus, r) for r in found)

    def replace(self, nexus: TaxNexus) -> TaxNexus:
        existing = self._row(nexus.org_id, nexus.id)
        if existing is None:
            return self.add(nexus)
        update_row(existing, nexus, _nexus_projection(nexus))
        self._session.flush()
        return nexus

    def _row(self, org_id: str, nexus_id: str):
        return self._one(
            select(rows.BillingTaxNexusRow).where(
                rows.BillingTaxNexusRow.org_id == org_id,
                rows.BillingTaxNexusRow.id == nexus_id,
            )
        )


class SqlTaxExemptionRepository(_Repo, TaxExemptionRepository):
    """Customer exemptions. Revoked by timestamp, never deleted."""

    def add(self, exemption: TaxExemption) -> TaxExemption:
        self._add(
            rows.BillingTaxExemptionRow(
                payload=encode(exemption), **_exemption_projection(exemption)
            )
        )
        return exemption

    def get(self, org_id: str, exemption_id: str) -> TaxExemption | None:
        row = self._row(org_id, exemption_id)
        return from_row(TaxExemption, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[TaxExemption, ...]:
        found = self._all(
            select(rows.BillingTaxExemptionRow)
            .where(
                rows.BillingTaxExemptionRow.org_id == org_id,
                rows.BillingTaxExemptionRow.account_id == account_id,
            )
            .order_by(rows.BillingTaxExemptionRow.effective_from, rows.BillingTaxExemptionRow.id)
        )
        return tuple(from_row(TaxExemption, r) for r in found)

    def all(self, org_id: str) -> tuple[TaxExemption, ...]:
        found = self._all(
            select(rows.BillingTaxExemptionRow)
            .where(rows.BillingTaxExemptionRow.org_id == org_id)
            .order_by(rows.BillingTaxExemptionRow.account_id, rows.BillingTaxExemptionRow.id)
        )
        return tuple(from_row(TaxExemption, r) for r in found)

    def replace(self, exemption: TaxExemption) -> TaxExemption:
        existing = self._row(exemption.org_id, exemption.id)
        if existing is None:
            return self.add(exemption)
        update_row(existing, exemption, _exemption_projection(exemption))
        self._session.flush()
        return exemption

    def _row(self, org_id: str, exemption_id: str):
        return self._one(
            select(rows.BillingTaxExemptionRow).where(
                rows.BillingTaxExemptionRow.org_id == org_id,
                rows.BillingTaxExemptionRow.id == exemption_id,
            )
        )


class SqlTaxRateRepository(_Repo, TaxRateRepository):
    """**Append-only rate evidence.** No update method; `retire` sets a timestamp.

    `for_jurisdiction` returns every rate ever declared there, live or not — an audit reads the
    retired ones too, because the question is what was in force on a date, not what is in force
    now."""

    def append(self, rate: DeclaredTaxRate) -> DeclaredTaxRate:
        existing = self._row(rate.org_id, rate.id)
        if existing is not None:
            return from_row(DeclaredTaxRate, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingTaxRateRow(payload=encode(rate), **_rate_projection(rate))
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(rate.org_id, rate.id)
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(DeclaredTaxRate, winner)
        return rate

    def get(self, org_id: str, rate_id: str) -> DeclaredTaxRate | None:
        row = self._row(org_id, rate_id)
        return from_row(DeclaredTaxRate, row) if row else None

    def for_jurisdiction(self, org_id: str, jurisdiction_code: str) -> tuple[DeclaredTaxRate, ...]:
        found = self._all(
            select(rows.BillingTaxRateRow)
            .where(
                rows.BillingTaxRateRow.org_id == org_id,
                rows.BillingTaxRateRow.jurisdiction_code == jurisdiction_code,
            )
            .order_by(rows.BillingTaxRateRow.effective_from, rows.BillingTaxRateRow.name)
        )
        return tuple(from_row(DeclaredTaxRate, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[DeclaredTaxRate, ...]:
        found = self._all(
            select(rows.BillingTaxRateRow)
            .where(rows.BillingTaxRateRow.org_id == org_id)
            .order_by(
                rows.BillingTaxRateRow.jurisdiction_code,
                rows.BillingTaxRateRow.effective_from,
                rows.BillingTaxRateRow.name,
            )
            .limit(limit)
        )
        return tuple(from_row(DeclaredTaxRate, r) for r in found)

    def retire(self, rate: DeclaredTaxRate) -> DeclaredTaxRate:
        """Set the retirement timestamp. The declared rate itself is never amended: an issued
        invoice must stay explainable against the rate in force when it was issued."""
        existing = self._row(rate.org_id, rate.id)
        if existing is None:
            return self.append(rate)
        existing.retired_at = rate.retired_at
        existing.payload = encode(rate)
        self._session.flush()
        return rate

    def _row(self, org_id: str, rate_id: str):
        return self._one(
            select(rows.BillingTaxRateRow).where(
                rows.BillingTaxRateRow.org_id == org_id,
                rows.BillingTaxRateRow.id == rate_id,
            )
        )


class SqlTaxAssessmentRepository(_Repo, TaxAssessmentRepository):
    """**Append-only determinations**, idempotent on the derived basis id.

    See the module docstring for why the savepoint in `append` matters and why
    `current_for_subject` prefers a finalized determination over a newer provisional one."""

    def append(self, assessment: TaxAssessment) -> TaxAssessment:
        existing = self._row(assessment.org_id, assessment.id)
        if existing is not None:
            # The common case: re-determining facts that have not moved. One row, not two answers.
            return from_row(TaxAssessment, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingTaxAssessmentRow(
                        payload=encode(assessment), **_assessment_projection(assessment)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(assessment.org_id, assessment.id)
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(TaxAssessment, winner)
        return assessment

    def get(self, org_id: str, assessment_id: str) -> TaxAssessment | None:
        row = self._row(org_id, assessment_id)
        return from_row(TaxAssessment, row) if row else None

    def for_subject(self, org_id: str, subject_id: str) -> tuple[TaxAssessment, ...]:
        found = self._all(
            select(rows.BillingTaxAssessmentRow)
            .where(
                rows.BillingTaxAssessmentRow.org_id == org_id,
                rows.BillingTaxAssessmentRow.subject_id == subject_id,
            )
            .order_by(rows.BillingTaxAssessmentRow.version, rows.BillingTaxAssessmentRow.id)
        )
        return tuple(from_row(TaxAssessment, r) for r in found)

    def current_for_subject(self, org_id: str, subject_id: str) -> TaxAssessment | None:
        """The determination that stands.

        **A finalized determination beats any provisional one**, and among provisionals the newest
        version wins. Ordering by recency alone would let a stale draft shadow the determination an
        invoice was actually issued with — which is the figure a customer paid."""
        candidates = [
            a
            for a in self.for_subject(org_id, subject_id)
            if a.state not in (AssessmentState.SUPERSEDED, AssessmentState.DISCARDED)
        ]
        if not candidates:
            return None
        final = [a for a in candidates if a.state is AssessmentState.FINAL]
        return sorted(final or candidates, key=lambda a: (a.version, a.id))[-1]

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[TaxAssessment, ...]:
        found = self._all(
            select(rows.BillingTaxAssessmentRow)
            .where(
                rows.BillingTaxAssessmentRow.org_id == org_id,
                rows.BillingTaxAssessmentRow.account_id == account_id,
            )
            .order_by(rows.BillingTaxAssessmentRow.assessed_at, rows.BillingTaxAssessmentRow.id)
            .limit(limit)
        )
        return tuple(from_row(TaxAssessment, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[TaxAssessment, ...]:
        found = self._all(
            select(rows.BillingTaxAssessmentRow)
            .where(rows.BillingTaxAssessmentRow.org_id == org_id)
            .order_by(
                rows.BillingTaxAssessmentRow.assessed_at.desc(),  # type: ignore[attr-defined]
                rows.BillingTaxAssessmentRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(TaxAssessment, r) for r in found)

    def mark_state(self, assessment: TaxAssessment) -> TaxAssessment:
        """Lifecycle state only. Figures, components, provenance, and the digest are never
        amended — the FSM's absent edge out of `final` is what actually enforces that, and this
        method exists only so the state it computes can be stored."""
        existing = self._row(assessment.org_id, assessment.id)
        if existing is None:
            return self.append(assessment)
        update_row(existing, assessment, _assessment_projection(assessment))
        self._session.flush()
        return assessment

    def _row(self, org_id: str, assessment_id: str):
        return self._one(
            select(rows.BillingTaxAssessmentRow).where(
                rows.BillingTaxAssessmentRow.org_id == org_id,
                rows.BillingTaxAssessmentRow.id == assessment_id,
            )
        )


class SqlTaxActivityRepository(_Repo, TaxActivityRepository):
    """Append-only tax audit trail."""

    def append(self, activity: TaxActivity) -> TaxActivity:
        self._add(
            rows.BillingTaxActivityRow(payload=encode(activity), **_activity_projection(activity))
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[TaxActivity, ...]:
        statement = select(rows.BillingTaxActivityRow).where(
            rows.BillingTaxActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(rows.BillingTaxActivityRow.account_id == account_id)
        found = self._all(
            statement.order_by(
                rows.BillingTaxActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingTaxActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(TaxActivity, r) for r in found)

    def for_subject(self, org_id: str, subject_id: str) -> tuple[TaxActivity, ...]:
        found = self._all(
            select(rows.BillingTaxActivityRow)
            .where(
                rows.BillingTaxActivityRow.org_id == org_id,
                rows.BillingTaxActivityRow.subject_id == subject_id,
            )
            .order_by(rows.BillingTaxActivityRow.at, rows.BillingTaxActivityRow.id)
        )
        return tuple(from_row(TaxActivity, r) for r in found)
