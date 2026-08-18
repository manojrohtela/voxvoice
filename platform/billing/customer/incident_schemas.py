"""Financial Integrity Correlation wire DTOs (M9.20) — Pydantic v2, additive.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary.

Named distinctly from the frozen surfaces they sit beside: `IntegrityIncidentResponse` rather than a
second `IntegrityFindingResponse`, which M9.19 owns for the single divergence this domain groups.

## What no schema here can carry

There is **no balance, no total, no amount and no currency on any shape**. Not one field, and
`MoneyResponse` is deliberately not imported — a schema that could not name money cannot leak it.
`finding_count` and `unresolved_findings` are counts; `impact` is a *classification*, never a
figure.

A "divergent total" on the wire would be a number M9.20 computed, and the moment it exists somebody
reports it beside a figure the Ledger owns. Every amount an operator needs is on M9.19's finding,
reachable by `finding_id`, where the domain that decided it put it.

There is also no price, no rate, no tax component, no usage figure and no recognized amount.

## What the client may do

`allowed_triggers` on an incident is the **server's** answer to "what can I do with this?". A
resolved incident returns an empty list, because the lifecycle has no edge out of it — so a client
cannot offer a reopen button for a transition the domain would refuse. The client renders the list;
it never computes it.

Collection responses carry `total`/`limit`/`offset`/`has_more` because pagination is mandatory at
this milestone's assumed scale, and a response that omitted them would invite a client to page by
fetching everything.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AnchorKind,
    EdgeKind,
    ImpactClass,
    IncidentSeverity,
    IncidentStatus,
    RelationshipKind,
    RepairOwner,
    RootCause,
)


def _fields(view) -> dict:
    """An Application view as a plain mapping. Views are slotted, so `vars()` is unavailable."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


# ── inbound ─────────────────────────────────────────────────────────────────────


class CorrelateFindingsBody(BaseModel):
    """Correlate reconciliation findings into incidents.

    Takes finding ids or a run id — never a correlation key: the key is *derived* from immutable
    evidence, and letting a caller supply one would let it assert a correlation nobody can prove."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[str] = Field(default_factory=list, max_length=500)
    run_id: str = Field(default="", max_length=64)
    limit: int = Field(default=200, ge=1, le=500)


class IncidentNoteBody(BaseModel):
    """Acknowledge, investigate, verify. Attributed from the verified token, never from the body."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class IncidentReasonBody(BaseModel):
    """Close. A reason is required in substance even though the field permits an empty string —
    the audit trail records what was given."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class RequestIncidentRepairBody(BaseModel):
    """Ask the **owning domain** to fix the underlying fact.

    `request_key` makes the ask idempotent, which matters because the dispatched repair may move
    money two layers down."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default="", max_length=64)
    request_key: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=500)


class ObserveRepairBody(BaseModel):
    """Record that the owner reported back. **Not evidence the incident is resolved.**"""

    model_config = ConfigDict(extra="forbid")

    repair_id: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)


class ResolveIncidentBody(BaseModel):
    """Close an incident **with evidence**.

    There is no `force` field and there never will be. The server re-reads every attached finding
    and refuses unless all are settled and none was unreadable — ADR-0023 PY5 at incident altitude,
    enforced at a boundary the client cannot reach past."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class MergeIncidentsBody(BaseModel):
    """**A merge as a new fact.** Neither original incident is rewritten."""

    model_config = ConfigDict(extra="forbid")

    merged_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


class CorrectCorrelationBody(BaseModel):
    """**A correction as a new fact.** The previous correlation stays in the trail."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


# ── outbound ────────────────────────────────────────────────────────────────────


class IntegrityIncidentResponse(BaseModel):
    """One incident. **No amount, no total, no currency** — `finding_count` is a count and
    `impact` is a classification."""

    id: str
    org_id: str
    correlation_key: str
    anchor_kind: AnchorKind
    anchor_value: str
    status: IncidentStatus
    severity: IncidentSeverity
    root_cause: RootCause
    impact: ImpactClass
    finding_count: int
    unresolved_findings: int
    relationships: list[RelationshipKind]
    owners: list[RepairOwner]
    account_id: str
    evidence_digest: str
    first_detected_at: float
    last_observed_at: float
    canonical_id: str
    version: int
    closed_at: float | None = None
    closed_reason: str = ""
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> IntegrityIncidentResponse:
        data = _fields(view)
        data["relationships"] = list(view.relationships)
        data["owners"] = list(view.owners)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class IncidentPageResponse(BaseModel):
    """**Paginated.** `total` comes from a database count, not from the length of this page."""

    incidents: list[IntegrityIncidentResponse]
    total: int
    limit: int
    offset: int
    has_more: bool

    @classmethod
    def of(cls, view) -> IncidentPageResponse:
        return cls(
            incidents=[IntegrityIncidentResponse.of(i) for i in view.incidents],
            total=view.total,
            limit=view.limit,
            offset=view.offset,
            has_more=view.has_more,
        )


class CorrelationReceiptResponse(BaseModel):
    """What one correlation pass did. `already_attached` is not a failure — it is idempotency."""

    incidents: list[IntegrityIncidentResponse]
    correlated: int
    attached: int
    already_attached: int
    incidents_opened: int

    @classmethod
    def of(cls, view) -> CorrelationReceiptResponse:
        return cls(
            incidents=[IntegrityIncidentResponse.of(i) for i in view.incidents],
            correlated=view.correlated,
            attached=view.attached,
            already_attached=view.already_attached,
            incidents_opened=view.incidents_opened,
        )


class FindingLinkResponse(BaseModel):
    """One finding's attachment, **with the anchor that proved it belongs**.

    `is_attached` false means the link was detached by a correlation correction. The row survives,
    because "this was once believed to belong here" is exactly what an auditor asks about."""

    id: str
    incident_id: str
    finding_id: str
    run_id: str
    relationship: RelationshipKind
    divergence: str
    severity: str
    anchor_kind: AnchorKind
    anchor_value: str
    edge: EdgeKind
    subject_key: str
    attached_at: float
    attached_by: str
    is_attached: bool
    detached_at: float | None = None
    detached_reason: str = ""

    @classmethod
    def of(cls, view) -> FindingLinkResponse:
        return cls(**_fields(view))


class IncidentEventResponse(BaseModel):
    """One line of the audit trail. **The source of explanation — not an application log.**"""

    id: str
    incident_id: str
    sequence: int
    kind: str
    at: float
    actor_id: str
    finding_id: str
    subject: str
    summary: str
    detail: dict[str, str]

    @classmethod
    def of(cls, view) -> IncidentEventResponse:
        data = _fields(view)
        data["detail"] = {k: str(v) for k, v in view.detail.items()}
        return cls(**data)


class EvidenceNodeResponse(BaseModel):
    id: str
    kind: str
    label: str
    detail: str


class EvidenceEdgeResponse(BaseModel):
    """One connection **and the anchor that proved it**. An edge with no anchor cannot exist."""

    source_id: str
    target_id: str
    kind: EdgeKind
    anchor: AnchorKind
    evidence: str


class EvidenceGraphResponse(BaseModel):
    """The incident as a graph of evidence rather than a flat list of findings."""

    incident_id: str
    nodes: list[EvidenceNodeResponse]
    edges: list[EvidenceEdgeResponse]
    is_connected: bool

    @classmethod
    def of(cls, view) -> EvidenceGraphResponse:
        return cls(
            incident_id=view.incident_id,
            nodes=[EvidenceNodeResponse(**_fields(n)) for n in view.nodes],
            edges=[EvidenceEdgeResponse(**_fields(e)) for e in view.edges],
            is_connected=view.is_connected,
        )


class IncidentRepairResponse(BaseModel):
    """A reference, not a repair. `observed` says the owner reported back — **not** that the books
    agree; only verification says that."""

    id: str
    incident_id: str
    owner: RepairOwner
    action: str
    request_key: str
    finding_id: str
    reference: str
    observed: bool
    observed_at: float | None
    requested_at: float
    requested_by: str
    detail: str

    @classmethod
    def of(cls, view) -> IncidentRepairResponse:
        return cls(**_fields(view))


class IncidentMergeResponse(BaseModel):
    """A merge decision. Both originals stay readable at their original severity."""

    id: str
    canonical_id: str
    merged_id: str
    reason: str
    evidence: str
    anchor_kind: AnchorKind
    decided_at: float
    decided_by: str

    @classmethod
    def of(cls, view) -> IncidentMergeResponse:
        return cls(**_fields(view))


class CorrelationCorrectionResponse(BaseModel):
    """Both keys, deliberately: an auditor asks what was believed and why it changed."""

    id: str
    finding_id: str
    previous_incident_id: str
    previous_key: str
    corrected_incident_id: str
    corrected_key: str
    reason: str
    evidence: str
    corrected_at: float
    corrected_by: str

    @classmethod
    def of(cls, view) -> CorrelationCorrectionResponse:
        return cls(**_fields(view))


class IncidentExplanationResponse(BaseModel):
    """*What happened?* — assembled from stored evidence, with no domain re-read."""

    incident_id: str
    correlation_key: str
    anchor_kind: AnchorKind
    anchor_value: str
    status: IncidentStatus
    severity: IncidentSeverity
    root_cause: RootCause
    impact: ImpactClass
    narrative: list[str]
    findings: list[str]
    relationships: list[str]
    owners: list[str]
    first_detected_at: float
    last_observed_at: float
    evidence_digest: str
    canonical_id: str

    @classmethod
    def of(cls, view) -> IncidentExplanationResponse:
        data = _fields(view)
        for key in ("narrative", "findings", "relationships", "owners"):
            data[key] = list(getattr(view, key))
        return cls(**data)


class IncidentInspectionResponse(BaseModel):
    """A structural read: counted correctly, scoped, connected, severity monotonic."""

    incident_id: str
    status: IncidentStatus
    severity: IncidentSeverity
    finding_count: int
    links_recorded: int
    events_recorded: int
    is_counted_correctly: bool
    is_scoped: bool
    is_connected: bool
    is_severity_monotonic: bool
    unresolved_findings: int
    notes: list[str]

    @classmethod
    def of(cls, view) -> IncidentInspectionResponse:
        data = _fields(view)
        data["notes"] = list(view.notes)
        return cls(**data)


class VerificationResponse(BaseModel):
    """What a verification pass found. `is_clean` requires nothing divergent **and nothing
    unreadable** — a finding that could not be re-read is not evidence of agreement."""

    incident_id: str
    checked: int
    still_divergent: int
    resolved_findings: int
    unreadable: int
    is_clean: bool
    detail: list[str]

    @classmethod
    def of(cls, view) -> VerificationResponse:
        data = _fields(view)
        data["detail"] = list(view.detail)
        return cls(**data)


class IncidentPostureResponse(BaseModel):
    """The org's incident picture. **Counts only — there is no amount on this shape.**"""

    org_id: str
    incidents: int
    open_incidents: int
    acknowledged: int
    investigating: int
    repair_in_progress: int
    verifying: int
    resolved: int
    closed: int
    merged: int
    critical: int
    error: int
    warning: int
    is_clean: bool
    by_root_cause: dict[str, int]
    by_impact: dict[str, int]
    by_owner: dict[str, int]

    @classmethod
    def of(cls, view) -> IncidentPostureResponse:
        return cls(**_fields(view))


__all__ = [
    "CorrectCorrelationBody",
    "CorrelateFindingsBody",
    "CorrelationCorrectionResponse",
    "CorrelationReceiptResponse",
    "EvidenceEdgeResponse",
    "EvidenceGraphResponse",
    "EvidenceNodeResponse",
    "FindingLinkResponse",
    "IncidentEventResponse",
    "IncidentExplanationResponse",
    "IncidentInspectionResponse",
    "IncidentMergeResponse",
    "IncidentNoteBody",
    "IncidentPageResponse",
    "IncidentPostureResponse",
    "IncidentReasonBody",
    "IncidentRepairResponse",
    "IntegrityIncidentResponse",
    "MergeIncidentsBody",
    "ObserveRepairBody",
    "RequestIncidentRepairBody",
    "ResolveIncidentBody",
    "VerificationResponse",
]
