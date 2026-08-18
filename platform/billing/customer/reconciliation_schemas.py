"""Reconciliation & Financial Integrity wire DTOs (M9.19) — Pydantic v2, additive.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared**.

Named distinctly from the frozen surfaces they sit beside: `IntegrityFindingResponse` rather than a
second `ReconciliationResponse`, which M9.11 owns for usage and M9.16 owns for the payment↔provider
comparison this domain deliberately does not perform.

## What no schema here can carry

There is **no balance, no total, and no monetary sum on any shape**. `IntegrityPostureResponse`
carries counts — how many open, how many critical, how many per owner — and no amount at all. The
two `amount` fields on `FactRefResponse` are copies of the two figures that disagreed, carried so
the divergence is legible on screen; nothing adds them, and there is no field for a difference.

A "total divergent value" would be a figure M9.19 computed, and the moment such a number is on the
wire somebody reports it — at which point reconciliation has become a second set of books.

There is also no price, no rate, no tax component, no usage figure and no recognized amount. Every
number here was decided by the domain that owns it and is reproduced verbatim.

## What the client may do

`allowed_triggers` on a finding is the **server's** answer to "what can I do with this?". A resolved
finding returns an empty list, because the lifecycle has no edge out of it — so a client cannot
offer a reopen button for a transition the domain would refuse. The client renders the list; it
never computes it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import DivergenceKind, FindingSeverity, RelationshipKind, RepairOwner

from .schemas import MoneyResponse


def _fields(view) -> dict:
    """An Application view as a plain mapping. Views are slotted, so `vars()` is unavailable."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


# ── inbound ─────────────────────────────────────────────────────────────────────


class ReconcileAccountBody(BaseModel):
    """Reconcile one account's payments against the ledger, or against its documents."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64)
    period_key: str = Field(default="", max_length=32)


class ReconcileDocumentBody(BaseModel):
    """Reconcile one document against the ledger, its tax determination, or its recognition."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=64)
    assessment_id: str = Field(default="", max_length=64)


class ReconcileScheduleBody(BaseModel):
    """Reconcile one revenue schedule's recognized periods against the ledger."""

    model_config = ConfigDict(extra="forbid")

    schedule_id: str = Field(min_length=1, max_length=64)


class FindingNoteBody(BaseModel):
    """Acknowledge or close. Both are attributed from the verified token, never from the body."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class RepairRequestBody(BaseModel):
    """Ask the **owning domain** to fix it. `request_key` makes the ask idempotent, which matters
    because the dispatched repair may move money."""

    model_config = ConfigDict(extra="forbid")

    request_key: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=500)


class ResolveFindingBody(BaseModel):
    """Close a divergence **with evidence**.

    There is no `force` field and there never will be. The server re-reads both sides and refuses
    unless they now agree — ADR-0023 PY5 enforced at the boundary the client cannot reach past."""

    model_config = ConfigDict(extra="forbid")

    request_key: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=500)


# ── outbound ────────────────────────────────────────────────────────────────────


class FactRefResponse(BaseModel):
    """One side of a comparison. `amount` is a **copy of the figure that disagreed**, never a
    sum."""

    identity: str
    kind: str
    amount: MoneyResponse | None = None
    account_id: str = ""
    period_key: str = ""
    source: str = ""
    source_id: str = ""
    state: str = ""
    summary: str = ""

    @classmethod
    def of(cls, view) -> FactRefResponse | None:
        if view is None:
            return None
        data = _fields(view)
        data["amount"] = (
            MoneyResponse(minor=view.amount.minor, currency=view.amount.currency)
            if view.amount
            else None
        )
        return cls(**data)


class IntegrityRunResponse(BaseModel):
    """One evaluation. `evidence_digest` is what makes it reproducible: recompute it from the
    facts and it either matches or the evidence changed."""

    id: str
    org_id: str
    relationship: RelationshipKind
    subject_id: str
    period_key: str
    evidence_digest: str
    state: str
    compared: int
    matched: int
    diverged: int
    max_severity: FindingSeverity
    is_clean: bool
    started_at: float
    completed_at: float | None = None
    triggered_by: str = ""
    note: str = ""

    @classmethod
    def of(cls, view) -> IntegrityRunResponse:
        return cls(**_fields(view))


class IntegrityFindingResponse(BaseModel):
    """One divergence, explainable from this payload alone.

    `severity` is written once at detection and never changes — a resolution records that a critical
    divergence was fixed, it does not make it retroactively a warning."""

    id: str
    org_id: str
    run_id: str
    relationship: RelationshipKind
    subject_key: str
    divergence: DivergenceKind
    severity: FindingSeverity
    owner: RepairOwner
    state: str
    verdict: str
    expected: str
    observed: str
    detail: str
    account_id: str
    detected_at: float
    source: FactRefResponse | None = None
    counterpart: FactRefResponse | None = None
    repair_request_id: str = ""
    resolution_id: str = ""
    resolved_at: float | None = None
    resolved_by: str = ""
    version: int = 0
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> IntegrityFindingResponse:
        data = _fields(view)
        data["source"] = FactRefResponse.of(view.source)
        data["counterpart"] = FactRefResponse.of(view.counterpart)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class ReconciliationReceiptResponse(BaseModel):
    """What one reconciliation returns: the run, plus what it found."""

    run: IntegrityRunResponse
    findings: list[IntegrityFindingResponse]
    is_new: bool

    @classmethod
    def of(cls, view) -> ReconciliationReceiptResponse:
        return cls(
            run=IntegrityRunResponse.of(view.run),
            findings=[IntegrityFindingResponse.of(f) for f in view.findings],
            is_new=view.is_new,
        )


class RepairRequestResponse(BaseModel):
    """A request, not a repair. `accepted` says the owner took it — **not** that the books agree;
    only a later run says that."""

    id: str
    finding_id: str
    owner: RepairOwner
    action: str
    request_key: str
    dispatched_command: str
    accepted: bool
    detail: str
    requested_at: float
    requested_by: str

    @classmethod
    def of(cls, view) -> RepairRequestResponse:
        return cls(**_fields(view))


class IntegrityResolutionResponse(BaseModel):
    """Evidence that a divergence was addressed, digested over the **resulting** facts."""

    id: str
    finding_id: str
    run_id: str
    owner: RepairOwner
    action: str
    evidence_digest: str
    resolved_at: float
    resolved_by: str
    note: str = ""
    resulting_source: FactRefResponse | None = None
    resulting_counterpart: FactRefResponse | None = None

    @classmethod
    def of(cls, view) -> IntegrityResolutionResponse:
        data = _fields(view)
        data["resulting_source"] = FactRefResponse.of(view.resulting_source)
        data["resulting_counterpart"] = FactRefResponse.of(view.resulting_counterpart)
        return cls(**data)


class FindingExplanationResponse(BaseModel):
    """*Why does this finding exist?* — assembled from stored evidence, with no domain re-read."""

    finding_id: str
    relationship: RelationshipKind
    divergence: DivergenceKind
    severity: FindingSeverity
    owner: RepairOwner
    state: str
    narrative: list[str]
    source_fact: str
    counterpart_fact: str
    expected: str
    observed: str
    matching_identity: str
    authoritative_side: str
    evidence_digest: str
    detected_at: float
    resolution: str = ""

    @classmethod
    def of(cls, view) -> FindingExplanationResponse:
        data = _fields(view)
        data["narrative"] = list(view.narrative)
        return cls(**data)


class RunInspectionResponse(BaseModel):
    """A structural read of one run: are its counts self-consistent, is it scoped, is it done?"""

    run_id: str
    relationship: RelationshipKind
    state: str
    compared: int
    matched: int
    diverged: int
    findings_recorded: int
    is_counted_correctly: bool
    is_scoped: bool
    is_complete: bool
    unresolved: int
    notes: list[str]

    @classmethod
    def of(cls, view) -> RunInspectionResponse:
        data = _fields(view)
        data["notes"] = list(view.notes)
        return cls(**data)


class IntegrityPostureResponse(BaseModel):
    """The org's integrity at a glance. **Counts only — there is no amount on this shape.**"""

    org_id: str
    runs: int
    findings_open: int
    findings_acknowledged: int
    findings_repair_requested: int
    findings_resolved: int
    findings_closed: int
    critical: int
    error: int
    warning: int
    is_clean: bool
    by_relationship: dict[str, int]
    by_owner: dict[str, int]

    @classmethod
    def of(cls, view) -> IntegrityPostureResponse:
        return cls(**_fields(view))


class RelationshipResponse(BaseModel):
    """One row of the reconciliation matrix, as data.

    Served rather than hard-coded in the client for the same reason it is a table rather than six
    functions in the engine: a client that hard-coded six pairs would drift from the engine the
    first time one of them changed."""

    relationship: RelationshipKind
    source_kind: str
    counterpart_kind: str
    authoritative: str
    matching_identity: str
    compares: list[str]
    mode: str
    owner: RepairOwner
    allows_partial: bool
    flags_orphans: bool
    summary: str

    @classmethod
    def of(cls, view) -> RelationshipResponse:
        data = _fields(view)
        data["compares"] = list(view.compares)
        return cls(**data)


class IntegrityActivityResponse(BaseModel):
    id: str
    run_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    finding_id: str
    detail: dict[str, str]

    @classmethod
    def of(cls, view) -> IntegrityActivityResponse:
        data = _fields(view)
        data["detail"] = dict(view.detail)
        return cls(**data)


class IntegritySearchResponse(BaseModel):
    findings: list[IntegrityFindingResponse]
    total: int
    truncated: bool

    @classmethod
    def of(cls, view) -> IntegritySearchResponse:
        return cls(
            findings=[IntegrityFindingResponse.of(f) for f in view.findings],
            total=view.total,
            truncated=view.truncated,
        )


__all__ = [
    "FactRefResponse",
    "FindingExplanationResponse",
    "FindingNoteBody",
    "IntegrityActivityResponse",
    "IntegrityFindingResponse",
    "IntegrityPostureResponse",
    "IntegrityResolutionResponse",
    "IntegrityRunResponse",
    "IntegritySearchResponse",
    "ReconcileAccountBody",
    "ReconcileDocumentBody",
    "ReconcileScheduleBody",
    "ReconciliationReceiptResponse",
    "RelationshipResponse",
    "RepairRequestBody",
    "RepairRequestResponse",
    "ResolveFindingBody",
    "RunInspectionResponse",
]
