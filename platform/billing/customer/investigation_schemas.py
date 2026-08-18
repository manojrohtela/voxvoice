"""Financial Investigation & Audit Evidence wire DTOs (M9.21) — Pydantic v2, additive.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary rather than from a second copy that can drift.

Named distinctly from the frozen surfaces they sit beside: `InvestigationResponse` rather than a
second `IntegrityIncidentResponse`, which M9.20 owns for the incident this domain investigates, and
`EvidenceCaptureResponse` rather than reusing M9.20's `EvidenceGraphResponse` — that shape is a
correlation graph, and this one is a chain of custody.

## What no schema here can carry

There is **no balance, no total, no amount and no currency as a field on any shape**, and
`MoneyResponse` is deliberately not imported — a schema that cannot name money cannot leak it.
`evidence_count`, `open_question_count`, `timeline_length` and `gap_count` are counts.

The single exception is deliberate and narrow: `EvidenceCaptureResponse.body` is the **captured
representation itself**, and a snapshot of a reconciliation finding legitimately contains the
integer minor units the owning domain wrote. That is evidence of what that domain said at a moment
in time — not a figure this milestone computed, and not one anything here sums. It is opaque to
this layer: the schema does not name its keys, cannot validate them, and no endpoint reads inside
it.

There is also no price, no rate, no tax component, no usage figure and no recognized amount as a
field, and **no card number, CVV, credential, provider secret, API key or authentication token**
anywhere — a test scans every model in this file for the forbidden vocabulary.

## What the client may do

`allowed_triggers` on an investigation is the **server's** answer to "what can I do with this?". A
closed investigation returns an empty list, because the lifecycle has no edge out of it — so a
client cannot offer a reopen button for a transition the domain would refuse.

`ClosureAssessmentResponse.reasons` is the same idea for closure: the server computes what is
outstanding and says so in sentences, and the client renders them. It never decides closability
locally.

Collection responses carry `total`/`limit`/`offset`/`has_more` because pagination is mandatory at
this milestone's assumed scale.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    CaptureMode,
    ConclusionClassification,
    EvidenceAvailability,
    EvidenceDomain,
    EvidenceSourceType,
    HypothesisStatus,
    IncidentSeverity,
    InvestigationPriority,
    InvestigationStatus,
    ObservationKind,
    PackageCompleteness,
    QuestionKind,
    QuestionStatus,
    RootCause,
)


def _fields(view) -> dict:
    """An Application view as a plain mapping. Views are slotted, so `vars()` is unavailable."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


# ── inbound ─────────────────────────────────────────────────────────────────────


class OpenInvestigationBody(BaseModel):
    """Open a case against an incident M9.20 already declared.

    There is no `severity` field: severity belongs to the incident, and letting a caller supply one
    would create a second copy that could disagree with it."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    summary: str = ""
    priority: InvestigationPriority = InvestigationPriority.NORMAL


class TriageInvestigationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: InvestigationPriority = InvestigationPriority.NORMAL
    note: str = ""


class InvestigationNoteBody(BaseModel):
    """Named for its milestone rather than `NoteBody`, because five other Billing surfaces already
    own a `ReasonBody`/`NoteBody` and a sixth identical name makes OpenAPI qualify *somebody else's*
    schema. The same rule this module's docstring states, applied to its smallest shapes."""

    model_config = ConfigDict(extra="forbid")

    note: str = ""


class InvestigationReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class CaptureEvidenceBody(BaseModel):
    """Capture one source.

    `mode` is the milestone's central distinction and the caller states it explicitly: a snapshot
    asserts what a source said and can back a conclusion, a live reference points at it and cannot.
    There is no `body` field — a caller does not supply the representation, because evidence a
    caller could author is not evidence."""

    model_config = ConfigDict(extra="forbid")

    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1)
    mode: CaptureMode = CaptureMode.EVIDENCE_SNAPSHOT
    note: str = ""


class CaptureIncidentEvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_findings: bool = True
    include_repairs: bool = True


class MarkUnavailableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1)
    reason: str = ""


class RedactEvidenceBody(BaseModel):
    """Narrow a captured representation. The original keeps its identity, digest and author."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    reason: str = ""
    keep_fields: list[str] = Field(default_factory=list)


class RecordQuestionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: QuestionKind
    text: str = Field(min_length=1)
    required: bool = False


class AnswerQuestionBody(BaseModel):
    """`unanswerable` is a real outcome, not a failure — a required question that evidence cannot
    settle must be closable without inventing an answer."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    answer: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    unanswerable: bool = False


class RecordObservationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ObservationKind
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class SettleHypothesisBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    supported: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class RecordConclusionBody(BaseModel):
    """`evidenced_root_cause` is accepted **only** on a contradiction, and is reported to the
    owning M9.20 workflow rather than applied. Nothing on this shape changes an incident."""

    model_config = ConfigDict(extra="forbid")

    classification: ConclusionClassification
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    evidenced_root_cause: RootCause | None = None
    supersedes: str = ""


# ── outbound ────────────────────────────────────────────────────────────────────


class InvestigationResponse(BaseModel):
    """One investigation. **No amount, no total, no currency** — every integer here is a count,
    and `incident_severity` is a label M9.20 decided."""

    id: str
    org_id: str
    incident_id: str
    status: InvestigationStatus
    priority: InvestigationPriority
    incident_severity: IncidentSeverity
    summary: str
    opened_at: float
    opened_by: str
    last_activity_at: float
    evidence_count: int
    open_question_count: int
    unavailable_evidence_count: int
    conclusion_id: str
    closed_at: float | None = None
    closed_by: str = ""
    closed_reason: str = ""
    version: int = 0
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> InvestigationResponse:
        data = _fields(view)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class InvestigationPageResponse(BaseModel):
    investigations: list[InvestigationResponse]
    total: int
    offset: int
    limit: int
    has_more: bool

    @classmethod
    def of(cls, view) -> InvestigationPageResponse:
        return cls(
            investigations=[InvestigationResponse.of(i) for i in view.investigations],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
            has_more=view.has_more,
        )


class EvidenceCaptureResponse(BaseModel):
    """One link in the chain of custody.

    `mode` and `content_digest` together are what let a client tell a historical claim from a
    pointer: a live reference has an empty digest and a `null` body, and `supports_conclusion` is
    the server's own answer to "can this back a confirmed finding?" so the client never derives it.

    `body` is the captured representation, opaque to this layer — see the module docstring."""

    id: str
    locator: str
    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str
    revision: str
    mode: CaptureMode
    availability: EvidenceAvailability
    content_digest: str
    supports_conclusion: bool
    captured_at: float
    captured_by: str
    previous_evidence_id: str = ""
    unavailable_reason: str = ""
    redaction_reason: str = ""
    sequence: int = 0
    body: dict[str, Any] | None = None

    @classmethod
    def of(cls, view) -> EvidenceCaptureResponse:
        return cls(**_fields(view))


class EvidencePageResponse(BaseModel):
    evidence: list[EvidenceCaptureResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def of(cls, view) -> EvidencePageResponse:
        return cls(
            evidence=[EvidenceCaptureResponse.of(e) for e in view.evidence],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )


class CustodyLinkResponse(BaseModel):
    evidence_id: str
    locator: str
    mode: CaptureMode
    availability: EvidenceAvailability
    content_digest: str
    captured_at: float
    captured_by: str
    previous_evidence_id: str = ""
    reason: str = ""


class CustodyChainResponse(BaseModel):
    """Every capture of one source, oldest first. `changed` is the answer to *did this move
    while we were looking at it* — computed from digests, never from an amount."""

    locator: str
    changed: bool
    links: list[CustodyLinkResponse]

    @classmethod
    def of(cls, view) -> CustodyChainResponse:
        return cls(
            locator=view.locator,
            changed=view.changed,
            links=[CustodyLinkResponse(**_fields(link)) for link in view.links],
        )


class QuestionResponse(BaseModel):
    id: str
    kind: QuestionKind
    text: str
    status: QuestionStatus
    required: bool
    answer: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    asked_at: float = 0.0
    asked_by: str = ""
    answered_at: float | None = None
    answered_by: str = ""

    @classmethod
    def of(cls, view) -> QuestionResponse:
        data = _fields(view)
        data["evidence_ids"] = list(view.evidence_ids)
        return cls(**data)


class ObservationResponse(BaseModel):
    """`kind` is the distinction that stops a guess reading as a finding, and it is on the wire
    for exactly that reason: a client renders a hypothesis differently from an observed fact."""

    id: str
    kind: ObservationKind
    statement: str
    statement_digest: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    hypothesis_status: HypothesisStatus = HypothesisStatus.NOT_APPLICABLE
    recorded_at: float = 0.0
    recorded_by: str = ""
    settled_at: float | None = None
    settled_by: str = ""
    settling_evidence_ids: list[str] = Field(default_factory=list)
    settled_reason: str = ""
    sequence: int = 0

    @classmethod
    def of(cls, view) -> ObservationResponse:
        data = _fields(view)
        data["evidence_ids"] = list(view.evidence_ids)
        data["settling_evidence_ids"] = list(view.settling_evidence_ids)
        return cls(**data)


class ConclusionResponse(BaseModel):
    """`incident_root_cause` is what M9.20 said when this was written; `evidenced_root_cause` is
    what the evidence indicates when the two disagree. **Neither is written back.**"""

    id: str
    classification: ConclusionClassification
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    incident_root_cause: RootCause = RootCause.UNKNOWN
    evidenced_root_cause: RootCause | None = None
    supersedes: str = ""
    recorded_at: float = 0.0
    recorded_by: str = ""
    version: int = 0

    @classmethod
    def of(cls, view) -> ConclusionResponse:
        data = _fields(view)
        data["evidence_ids"] = list(view.evidence_ids)
        return cls(**data)


class TimelineEntryResponse(BaseModel):
    sequence: int
    kind: str
    at: float
    actor_id: str = ""
    subject: str = ""
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    detail: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def of(cls, view) -> TimelineEntryResponse:
        data = _fields(view)
        data["evidence_ids"] = list(view.evidence_ids)
        data["detail"] = dict(view.detail)
        return cls(**data)


class TimelinePageResponse(BaseModel):
    entries: list[TimelineEntryResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def of(cls, view) -> TimelinePageResponse:
        return cls(
            entries=[TimelineEntryResponse.of(e) for e in view.entries],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )


class EvidenceGapResponse(BaseModel):
    """Evidence the package expected and does not have. **Named, never omitted.**"""

    locator: str
    reason: str
    availability: EvidenceAvailability
    last_known_available_at: float = 0.0
    blocks_conclusion: bool = False
    evidence_id: str = ""


class ClosureAssessmentResponse(BaseModel):
    """What is outstanding, **before** anybody presses close. The client renders `reasons`; it
    never computes closability locally."""

    investigation_id: str
    may_close: bool
    unresolved_required_questions: list[str] = Field(default_factory=list)
    open_hypotheses: list[str] = Field(default_factory=list)
    blocking_gaps: list[EvidenceGapResponse] = Field(default_factory=list)
    conclusion_id: str = ""
    conclusion_classification: ConclusionClassification | None = None
    reasons: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> ClosureAssessmentResponse:
        return cls(
            investigation_id=view.investigation_id,
            may_close=view.may_close,
            unresolved_required_questions=list(view.unresolved_required_questions),
            open_hypotheses=list(view.open_hypotheses),
            blocking_gaps=[EvidenceGapResponse(**_fields(g)) for g in view.blocking_gaps],
            conclusion_id=view.conclusion_id,
            conclusion_classification=view.conclusion_classification,
            reasons=list(view.reasons),
        )


class AuditPackageResponse(BaseModel):
    """An immutable, content-addressed export.

    `manifest_digest` is recomputable from `manifest` by anyone holding this response — which is
    what makes the export auditable rather than merely downloadable. `completeness` is decided on
    evidence availability, and `gaps` names what is missing."""

    id: str
    org_id: str
    investigation_id: str
    incident_id: str
    manifest_digest: str
    completeness: PackageCompleteness
    evidence_count: int
    observation_count: int
    question_count: int
    conclusion_count: int
    timeline_length: int
    created_at: float
    created_by: str = ""
    gaps: list[EvidenceGapResponse] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, view) -> AuditPackageResponse:
        data = _fields(view)
        data["gaps"] = [EvidenceGapResponse(**_fields(g)) for g in view.gaps]
        data["manifest"] = dict(view.manifest)
        return cls(**data)


class EvidenceChainNodeResponse(BaseModel):
    kind: str
    identity: str
    label: str
    available: bool = True


class EvidenceChainEdgeResponse(BaseModel):
    source: str
    target: str
    relation: str


class EvidenceChainResponse(BaseModel):
    """The incident → finding → evidence → repair chain. A node whose source could not be read is
    **present and marked**, never dropped."""

    investigation_id: str
    incident_id: str
    nodes: list[EvidenceChainNodeResponse]
    edges: list[EvidenceChainEdgeResponse]
    unavailable: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> EvidenceChainResponse:
        return cls(
            investigation_id=view.investigation_id,
            incident_id=view.incident_id,
            nodes=[EvidenceChainNodeResponse(**_fields(n)) for n in view.nodes],
            edges=[EvidenceChainEdgeResponse(**_fields(e)) for e in view.edges],
            unavailable=list(view.unavailable),
        )


class InvestigationPostureResponse(BaseModel):
    """The org's investigation picture. **Counts, never money.**"""

    org_id: str
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    blocked: int = 0
    concluded_awaiting_closure: int = 0
    unavailable_evidence: int = 0
    packages_exported: int = 0
    incomplete_packages: int = 0

    @classmethod
    def of(cls, view) -> InvestigationPostureResponse:
        data = _fields(view)
        data["by_status"] = dict(view.by_status)
        data["by_priority"] = dict(view.by_priority)
        return cls(**data)
