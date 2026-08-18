"""Compliance & Regulatory Reporting wire DTOs (M9.22) — Pydantic v2, additive.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary rather than from a second copy that can drift.

**Every shape here is named for this milestone**, and that is a rule with a history: M9.20's D-2 and
M9.21's D-2 were both a generic name (`derive_correction_id`, `ReasonBody`) colliding with a
neighbour's, and M9.22 opened with seven more. `ReportDefinitionResponse`, not `DefinitionResponse`;
`ReportPeriodBody`, not `PeriodBody`. A test asserts no schema in this file forces OpenAPI to
qualify anybody's name.

## Money on the wire

Unlike M9.20 and M9.21, a report legitimately carries a figure — a filing with no figures reports
nothing. It is carried as **integer minor units plus an explicit currency**, exactly as the owning
domain wrote it, and `MoneyResponse` is deliberately not imported so no shape here can be mistaken
for a monetary value this milestone computed.

Two rules hold on every monetary field:

* `amount_minor` is `null`, never `0`, when the source could not be read. A regulator cannot tell a
  zero that means *nothing happened* from a zero that means *we could not look*, so the wire keeps
  them different;
* there is no converted figure and no rate anywhere. A field whose sources span currencies comes
  back `UNSUPPORTED` with the reason, not a total.

## What no schema here can carry

No balance, no computed total across reports, no tax rate, no price, no FX rate. And **no card
number, CVV, bank credential, provider secret, API key or authentication token** — a test scans
every model in this file for the forbidden vocabulary.

## What the client may do

`allowed_triggers` on a report is the **server's** answer to "what can I do with this?". A finalized
report returns an empty list, because the lifecycle has no edge out of it.
`ReportValidationResponse` carries every check by name, so a client renders why a filing is
blocked rather than deriving it.

Collection responses carry `total`/`limit`/`offset`/`has_more` because pagination is mandatory.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    CurrencyBasis,
    EvidenceCompleteness,
    EvidenceDomain,
    EvidenceSourceType,
    ExceptionSeverity,
    ExceptionState,
    PeriodBasis,
    ReportCompleteness,
    ReportedIntegrity,
    ReportExceptionKind,
    ReportFrequency,
    ReportingPeriodState,
    ReportScope,
    ReportStatus,
    ReportTransformation,
    SourceStatus,
    ValueStatus,
)


def _fields(view) -> dict:
    """An Application view as a plain mapping. Views are slotted, so `vars()` is unavailable."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


# ── inbound ─────────────────────────────────────────────────────────────────────


class ReportFieldSpecBody(BaseModel):
    """One field of a report definition, **bound to the source that answers it**.

    There is no `source: str` and no formula: `transformation` is drawn from the closed
    `ReportTransformation` set, so a regulatory rule cannot be expressed as free text and hidden
    in a service."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = ""
    domain: EvidenceDomain
    source_type: EvidenceSourceType
    transformation: ReportTransformation = ReportTransformation.COPY
    mandatory: bool = True
    source_attribute: str = ""
    classification_map: list[tuple[str, str]] = Field(default_factory=list)
    note: str = ""


class PublishReportDefinitionBody(BaseModel):
    """Publish a reporting rule at a version. **Immutable thereafter** — re-publishing the same
    version with different content is refused, because filings already bound to it must keep
    meaning what they meant."""

    model_config = ConfigDict(extra="forbid")

    report_code: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    title: str = ""
    jurisdiction: str = ""
    frequency: ReportFrequency = ReportFrequency.MONTHLY
    period_basis: PeriodBasis = PeriodBasis.CALENDAR_MONTH
    fields: list[ReportFieldSpecBody] = Field(default_factory=list)
    required_sources: list[EvidenceDomain] = Field(default_factory=list)
    currency_basis: CurrencyBasis = CurrencyBasis.NOT_APPLICABLE
    #: Required under `SINGLE_DECLARED` and refused otherwise. There is **no conversion**: a source
    #: in another currency raises `UNSUPPORTED_CURRENCY` rather than being converted.
    declared_currency: str | None = None
    integrity_blocks_finalization: bool = False


class OpenReportingPeriodBody(BaseModel):
    """`basis` is required and part of the identity: a calendar month is not an accounting period
    is not a tax period, and Billing already owns four of these."""

    model_config = ConfigDict(extra="forbid")

    basis: PeriodBasis
    period_key: str = Field(min_length=1)
    start: float
    end: float
    aligned_source_id: str = ""


class CreateReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_code: str = Field(min_length=1)
    #: `0` resolves the latest published version **once, at creation**, and the run stores it. No
    #: later version reinterprets the filing.
    definition_version: int = Field(default=0, ge=0)
    period_id: str = Field(min_length=1)
    #: Empty means organization-wide. Because every period-scoped read Billing publishes is keyed
    #: by account, an org-wide run reports its sources as unavailable rather than aggregating
    #: across accounts on a regulator's behalf.
    account_id: str = ""


class CorrectReportBody(BaseModel):
    """File a correction — **a new report beside the original**, never an edit of it."""

    model_config = ConfigDict(extra="forbid")

    definition_version: int = Field(default=0, ge=0)
    reason: str = ""


class AcknowledgeExceptionBody(BaseModel):
    """Record that somebody saw it. **This does not unblock a filing.**"""

    model_config = ConfigDict(extra="forbid")

    exception_id: str = Field(min_length=1)
    reason: str = ""


class RecordSourceUnavailableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: EvidenceDomain
    reason: str = ""


# ── outbound ────────────────────────────────────────────────────────────────────


class ReportDefinitionResponse(BaseModel):
    """What a report means, at one version. `definition_digest` is what lets a re-publication be
    proven identical rather than assumed."""

    id: str
    report_code: str
    version: int
    title: str
    jurisdiction: str
    frequency: ReportFrequency
    period_basis: PeriodBasis
    currency_basis: CurrencyBasis
    declared_currency: str | None
    integrity_blocks_finalization: bool
    definition_digest: str
    published_at: float
    published_by: str
    fields: list[dict[str, Any]] = Field(default_factory=list)
    required_sources: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> ReportDefinitionResponse:
        data = _fields(view)
        data["fields"] = [dict(f) for f in view.fields]
        data["required_sources"] = list(view.required_sources)
        return cls(**data)


class ReportPeriodResponse(BaseModel):
    id: str
    basis: PeriodBasis
    period_key: str
    start: float
    end: float
    state: ReportingPeriodState
    aligned_source_id: str
    opened_at: float
    closed_at: float | None
    version: int

    @classmethod
    def of(cls, view) -> ReportPeriodResponse:
        return cls(**_fields(view))


class ReportFieldValueResponse(BaseModel):
    """One computed field.

    `amount_minor` is **`null`, never `0`**, when the source could not be read — the whole point of
    this milestone in one field."""

    key: str
    status: ValueStatus
    transformation: ReportTransformation
    amount_minor: int | None = None
    currency: str | None = None
    number: int | None = None
    text: str = ""
    source_locators: list[str] = Field(default_factory=list)
    detail: str = ""

    @classmethod
    def of(cls, view) -> ReportFieldValueResponse:
        data = _fields(view)
        data["source_locators"] = list(view.source_locators)
        return cls(**data)


class ReportResponse(BaseModel):
    """One report run. Every integer outside `values` is a count."""

    id: str
    org_id: str
    report_code: str
    definition_id: str
    definition_version: int
    period_id: str
    manifest_id: str
    manifest_digest: str
    account_id: str
    scope: ReportScope
    status: ReportStatus
    completeness: ReportCompleteness
    evidence_completeness: EvidenceCompleteness
    integrity_posture: ReportedIntegrity
    report_digest: str
    snapshot_id: str
    exception_count: int
    blocking_exception_count: int
    source_count: int
    corrects: str
    corrected_by: str
    created_at: float
    created_by: str
    finalized_at: float | None
    finalized_by: str
    version: int
    values: list[ReportFieldValueResponse] = Field(default_factory=list)
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> ReportResponse:
        data = _fields(view)
        data["values"] = [ReportFieldValueResponse.of(v) for v in view.values]
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class ReportPageResponse(BaseModel):
    reports: list[ReportResponse]
    total: int
    offset: int
    limit: int
    has_more: bool

    @classmethod
    def of(cls, view) -> ReportPageResponse:
        return cls(
            reports=[ReportResponse.of(r) for r in view.reports],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
            has_more=view.has_more,
        )


class ReportSourceResponse(BaseModel):
    """One pinned source. `status` distinguishes resolved, empty and **unavailable** — and the
    third never becomes zero, never disappears, and is what an examiner reads."""

    locator: str
    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str
    status: SourceStatus
    revision: str
    digest: str
    resolved_at: float
    fact_count: int
    unavailable_reason: str

    @classmethod
    def of(cls, view) -> ReportSourceResponse:
        return cls(**_fields(view))


class ReportSourcePageResponse(BaseModel):
    sources: list[ReportSourceResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def of(cls, view) -> ReportSourcePageResponse:
        return cls(
            sources=[ReportSourceResponse.of(s) for s in view.sources],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )


class ReportExceptionResponse(BaseModel):
    """A **named** problem. There is no generic failure on this surface.

    `blocks_finalization` is the server's own answer and stays `true` after acknowledgement —
    somebody seeing a problem is a different fact from the problem being gone."""

    id: str
    kind: ReportExceptionKind
    severity: ExceptionSeverity
    subject: str
    diagnostic: str
    state: ExceptionState
    field_key: str
    source_locator: str
    blocks_finalization: bool
    created_at: float
    acknowledged_at: float | None
    acknowledged_by: str
    acknowledgement_reason: str

    @classmethod
    def of(cls, view) -> ReportExceptionResponse:
        return cls(**_fields(view))


class ReportExceptionPageResponse(BaseModel):
    exceptions: list[ReportExceptionResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def of(cls, view) -> ReportExceptionPageResponse:
        return cls(
            exceptions=[ReportExceptionResponse.of(e) for e in view.exceptions],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )


class ReportValidationCheckResponse(BaseModel):
    name: str
    passed: bool
    subject: str
    detail: str


class ReportValidationResponse(BaseModel):
    """Every check by name, so a client renders **why** a filing is blocked rather than deriving
    it. None of these compares two financial domains — that is M9.19's question."""

    report_id: str
    passed: bool
    definition_version: int
    validated_at: float
    validated_by: str
    checks: list[ReportValidationCheckResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> ReportValidationResponse:
        return cls(
            report_id=view.report_id,
            passed=view.passed,
            definition_version=view.definition_version,
            validated_at=view.validated_at,
            validated_by=view.validated_by,
            checks=[ReportValidationCheckResponse(**_fields(c)) for c in view.checks],
        )


class ReportSnapshotResponse(BaseModel):
    """The finalized filing. `report_digest` is recomputable from `representation`, which is what
    makes a submission defensible rather than merely archived."""

    id: str
    report_id: str
    definition_id: str
    definition_version: int
    manifest_id: str
    manifest_digest: str
    report_digest: str
    completeness: ReportCompleteness
    evidence_completeness: EvidenceCompleteness
    validation_passed: bool
    finalized_at: float
    finalized_by: str
    representation: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, view) -> ReportSnapshotResponse:
        data = _fields(view)
        data["representation"] = dict(view.representation)
        return cls(**data)


class SubmissionPackageResponse(BaseModel):
    """A bundle prepared **for** a submission this platform never performs.

    `submitted_at` and `submission_reference` are always `null`/empty in M9.22 — §24 defers
    transmission to a future adapter, and there is no network client anywhere in this milestone."""

    id: str
    report_id: str
    snapshot_id: str
    definition_id: str
    definition_version: int
    report_digest: str
    manifest_digest: str
    package_digest: str
    completeness: ReportCompleteness
    evidence_completeness: EvidenceCompleteness
    validation_passed: bool
    prepared_at: float
    prepared_by: str
    submitted_at: float | None = None
    submission_reference: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, view) -> SubmissionPackageResponse:
        data = _fields(view)
        data["artifact"] = dict(view.artifact)
        return cls(**data)


class ReportSourceTraceResponse(BaseModel):
    field_key: str
    label: str
    domain: str
    source_type: str
    transformation: str
    status: str
    locators: list[str] = Field(default_factory=list)
    detail: str = ""


class ReportProvenanceResponse(BaseModel):
    """**The §25 answer**: which definition, which version, which period, which sources at which
    revisions, what was available, what was not, what validation ran, who finalized it and when —
    assembled from stored rows, never from current source state or an application log."""

    report_id: str
    report_code: str
    definition_id: str
    definition_version: int
    period_key: str
    period_basis: str
    manifest_id: str
    manifest_digest: str
    report_digest: str
    validation_passed: bool
    finalized_at: float | None
    finalized_by: str
    corrects: str
    corrected_by: str
    traces: list[ReportSourceTraceResponse] = Field(default_factory=list)
    unavailable_sources: list[str] = Field(default_factory=list)
    exception_kinds: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> ReportProvenanceResponse:
        data = _fields(view)
        data["traces"] = [
            ReportSourceTraceResponse(**{**_fields(t), "locators": list(t.locators)})
            for t in view.traces
        ]
        data["unavailable_sources"] = list(view.unavailable_sources)
        data["exception_kinds"] = list(view.exception_kinds)
        return cls(**data)


class ReportDiffResponse(BaseModel):
    """What changed between a filing and its correction — the question a correction exists to
    answer, and one no other surface can."""

    earlier_report_id: str
    later_report_id: str
    changed_fields: list[str] = Field(default_factory=list)
    added_fields: list[str] = Field(default_factory=list)
    removed_fields: list[str] = Field(default_factory=list)
    changed_sources: list[str] = Field(default_factory=list)
    definition_version_changed: bool = False

    @classmethod
    def of(cls, view) -> ReportDiffResponse:
        data = _fields(view)
        for key in ("changed_fields", "added_fields", "removed_fields", "changed_sources"):
            data[key] = list(getattr(view, key))
        return cls(**data)


class ReportingPostureResponse(BaseModel):
    """The org's compliance picture. **Counts, never money.**"""

    org_id: str
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_completeness: dict[str, int] = Field(default_factory=dict)
    finalized: int = 0
    blocked: int = 0
    open_exceptions: int = 0
    blocking_exceptions: int = 0
    submission_packages: int = 0

    @classmethod
    def of(cls, view) -> ReportingPostureResponse:
        data = _fields(view)
        data["by_status"] = dict(view.by_status)
        data["by_completeness"] = dict(view.by_completeness)
        return cls(**data)
