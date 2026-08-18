"""Revenue Recognition wire DTOs (M9.18) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared**.

Named distinctly from the frozen surfaces they sit beside: `RecognitionEntryResponse` rather than a
second `LedgerEntryResponse`, which M9.5/M9.13 own for the financial fact this domain *causes* but
does not record.

## What no schema here can carry

There is **no `deferred_balance` field on any stored shape**. `RecognitionPositionResponse` carries
`deferred`, and it is a *derived* figure the server computed from the entries on this request — the
same number is never written to a column. That distinction is the whole of ADR-0023 RS6 on the
wire: recognition is a projection over the ledger, not a second set of books.

There is no tax rate, no jurisdiction, no tax component, no price, no discount and no payment
reference. `ScheduleRevenueBody` takes a **document id**, not an amount: the figure is read from the
snapshot that document pinned at issue, so this surface cannot be used to recognize a number nobody
billed.

`basis_digest` is on every schedule response deliberately. It is the one field that makes a
recognition falsifiable years later — recompute it from the evidence and it either matches or the
evidence changed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AdjustmentKind,
    PeriodGranularity,
    RecognitionMethod,
    RemainderRule,
)

from .schemas import MoneyResponse


def _fields(view) -> dict:
    """An Application view as a plain mapping. Views are slotted, so `vars()` is unavailable."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


def _money(view) -> MoneyResponse:
    return MoneyResponse(minor=view.minor, currency=view.currency)


# ── inbound ─────────────────────────────────────────────────────────────────────


class PublishRecognitionPolicyBody(BaseModel):
    """Publish one **immutable** recognition strategy version.

    There is no PUT and no PATCH on a policy anywhere in this surface. Changing a method or a
    remainder rule publishes version *n+1*; a schedule built under *n* keeps naming it **inside its
    basis digest**, which is what makes a historical recognition replay to the same answer."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "subscription",
                "version": 1,
                "method": "straight_line",
                "granularity": "month",
                "remainder": "largest_remainder",
            }
        }
    )

    code: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    method: RecognitionMethod = RecognitionMethod.STRAIGHT_LINE
    granularity: PeriodGranularity = PeriodGranularity.MONTH
    remainder: RemainderRule = RemainderRule.LARGEST_REMAINDER
    description: str = ""


class ScheduleRevenueBody(BaseModel):
    """Derive a schedule from an **issued** document.

    Takes a document id, never an amount. The figure comes from the snapshot the document pinned at
    issue, so a caller cannot recognize revenue nobody was billed for. The service window is the one
    input a document does not always carry, and both ends are inside the basis digest.

    `milestones` is `[[recognize_on, weight], ...]` and applies only to `scheduled` recognition —
    still *allocated* by the server, so conservation is not the caller's problem."""

    document_id: str = Field(min_length=1)
    service_start: float
    service_end: float
    policy_code: str = Field(min_length=1)
    method: RecognitionMethod | None = None
    milestones: list[tuple[float, int]] = Field(default_factory=list)
    subscription_id: str = ""


class RecognizeRevenueBody(BaseModel):
    """`at` is explicit so a recognition is reproducible against a chosen instant."""

    at: float | None = None


class ReverseRecognitionBody(BaseModel):
    """Correct a recognized amount with **a new fact**. The original entry is never edited."""

    kind: AdjustmentKind = AdjustmentKind.REVERSAL
    reason: str = Field(default="", max_length=500)
    request_key: str = ""
    amount_minor: int = 0  # ignored for a reversal, which is exactly the negative of the original


class SupersedeScheduleBody(BaseModel):
    successor_schedule_id: str = ""
    reason: str = Field(default="", max_length=500)


class RecognitionReasonBody(BaseModel):
    """The attribution every operator decision carries."""

    reason: str = Field(default="", max_length=500)


# ── outbound ────────────────────────────────────────────────────────────────────


class RecognitionPolicyResponse(BaseModel):
    id: str
    code: str
    version: int
    method: str
    granularity: str
    remainder: str
    is_active: bool
    description: str
    declared_at: float
    declared_by: str

    @classmethod
    def of(cls, view) -> RecognitionPolicyResponse:
        return cls(**_fields(view))


class RecognitionBasisResponse(BaseModel):
    """The immutable evidence. **`basis_digest` is what makes a recognition falsifiable.**"""

    document_id: str
    revision: int
    snapshot_digest: str
    basis_digest: str
    amount: MoneyResponse
    service_start: float
    service_end: float
    method: str
    granularity: str
    remainder: str
    policy_code: str
    policy_version: int
    subscription_id: str

    @classmethod
    def of(cls, view) -> RecognitionBasisResponse:
        data = _fields(view)
        data["amount"] = _money(view.amount)
        return cls(**data)


class RevenueScheduleResponse(BaseModel):
    id: str
    account_id: str
    state: str
    basis: RecognitionBasisResponse
    entries_planned: int
    entries_recognized: int
    version: int
    superseded_by: str
    created_at: float
    closed_at: float | None
    closed_reason: str
    is_terminal: bool
    allowed_triggers: list[str]

    @classmethod
    def of(cls, view) -> RevenueScheduleResponse:
        data = _fields(view)
        data["basis"] = RecognitionBasisResponse.of(view.basis)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class RecognitionEntryResponse(BaseModel):
    id: str
    schedule_id: str
    sequence: int
    period_start: float
    period_end: float
    amount: MoneyResponse
    recognize_on: float
    state: str
    recognition: str
    ledger_entry_id: str
    ledger_period_key: str
    ledger_disposition: str
    recognized_at: float | None
    posted_at: float | None
    note: str
    allowed_triggers: list[str]

    @classmethod
    def of(cls, view) -> RecognitionEntryResponse:
        data = _fields(view)
        data["amount"] = _money(view.amount)
        data["allowed_triggers"] = list(view.allowed_triggers)
        return cls(**data)


class RecognitionAdjustmentResponse(BaseModel):
    id: str
    schedule_id: str
    entry_id: str
    kind: str
    amount: MoneyResponse
    reason: str
    document_id: str
    revision: int
    policy_code: str
    policy_version: int
    effective_at: float
    ledger_entry_id: str
    created_at: float
    created_by: str

    @classmethod
    def of(cls, view) -> RecognitionAdjustmentResponse:
        data = _fields(view)
        data["amount"] = _money(view.amount)
        return cls(**data)


class RecognitionPositionResponse(BaseModel):
    """**Derived on this request.** `deferred` is total − recognized − adjusted, and there is no
    column anywhere holding it."""

    schedule_id: str
    currency: str
    total: MoneyResponse
    recognized: MoneyResponse
    adjusted: MoneyResponse
    deferred: MoneyResponse
    entries: int
    recognized_entries: int
    posted_entries: int
    unposted_entries: int
    reversed_entries: int
    is_fully_recognized: bool
    has_unposted: bool

    @classmethod
    def of(cls, view) -> RecognitionPositionResponse:
        data = _fields(view)
        for name in ("total", "recognized", "adjusted", "deferred"):
            data[name] = _money(getattr(view, name))
        return cls(**data)


class RecognitionExplanationResponse(BaseModel):
    schedule_id: str
    state: str
    document_id: str
    revision: int
    snapshot_digest: str
    basis_digest: str
    policy_code: str
    policy_version: int
    method: str
    granularity: str
    remainder: str
    service_start: float
    service_end: float
    total_minor: int
    recognized_minor: int
    deferred_minor: int
    currency: str
    reason: str
    path: list[str]

    @classmethod
    def of(cls, view) -> RecognitionExplanationResponse:
        data = _fields(view)
        data["path"] = list(view.path)
        return cls(**data)


class RecognitionInspectionResponse(BaseModel):
    schedule: RevenueScheduleResponse
    position: RecognitionPositionResponse
    adjustments: int
    due_now: int
    next_recognize_on: float | None

    @classmethod
    def of(cls, view) -> RecognitionInspectionResponse:
        return cls(
            schedule=RevenueScheduleResponse.of(view.schedule),
            position=RecognitionPositionResponse.of(view.position),
            adjustments=view.adjustments,
            due_now=view.due_now,
            next_recognize_on=view.next_recognize_on,
        )


class RecognitionReceiptResponse(BaseModel):
    """What one recognition did.

    `recognized` and `posted` are separate answers on purpose: a recognition whose ledger posting
    was refused is a successful recognition *and* an unposted fact, and a client that could not tell
    them apart would report clean books while the repair queue grew."""

    entry: RecognitionEntryResponse
    schedule: RevenueScheduleResponse
    recognized: bool
    posted: bool
    ledger_entry_id: str
    ledger_outcome: str
    ledger_disposition: str
    note: str

    @classmethod
    def of(cls, view) -> RecognitionReceiptResponse:
        return cls(
            entry=RecognitionEntryResponse.of(view.entry),
            schedule=RevenueScheduleResponse.of(view.schedule),
            recognized=view.recognized,
            posted=view.posted,
            ledger_entry_id=view.ledger_entry_id,
            ledger_outcome=view.ledger_outcome,
            ledger_disposition=view.ledger_disposition,
            note=view.note,
        )


class RecognitionActivityResponse(BaseModel):
    id: str
    schedule_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    entry_id: str

    @classmethod
    def of(cls, view) -> RecognitionActivityResponse:
        return cls(**_fields(view))


class DeferredRevenueResponse(BaseModel):
    """An account's deferred revenue, summed from schedules at read time."""

    account_id: str
    currency: str
    total: MoneyResponse
    recognized: MoneyResponse
    deferred: MoneyResponse
    schedules: int
    unposted_entries: int

    @classmethod
    def of(cls, view) -> DeferredRevenueResponse:
        data = _fields(view)
        for name in ("total", "recognized", "deferred"):
            data[name] = _money(getattr(view, name))
        return cls(**data)


class RevenueSearchResponse(BaseModel):
    schedules: list[RevenueScheduleResponse]
    total: int
    truncated: bool

    @classmethod
    def of(cls, view) -> RevenueSearchResponse:
        return cls(
            schedules=[RevenueScheduleResponse.of(s) for s in view.schedules],
            total=view.total,
            truncated=view.truncated,
        )


__all__ = [
    "DeferredRevenueResponse",
    "PublishRecognitionPolicyBody",
    "RecognitionActivityResponse",
    "RecognitionAdjustmentResponse",
    "RecognitionBasisResponse",
    "RecognitionEntryResponse",
    "RecognitionExplanationResponse",
    "RecognitionInspectionResponse",
    "RecognitionPolicyResponse",
    "RecognitionPositionResponse",
    "RecognitionReasonBody",
    "RecognitionReceiptResponse",
    "RecognizeRevenueBody",
    "ReverseRecognitionBody",
    "RevenueScheduleResponse",
    "RevenueSearchResponse",
    "ScheduleRevenueBody",
    "SupersedeScheduleBody",
]
