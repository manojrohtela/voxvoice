"""Period Scope & Consolidation wire schemas (M9.23).

Pydantic v2 request bodies and response models for the `/consolidations` surface. Transport shapes
only: no rule is decided here, no money is computed here, and every `of()` is a field-for-field
projection of an Application view.

## The one wire rule that is load-bearing

`amount_minor` is `int | None` on every response that carries one, and **`None` is the normal wire
representation of a gap**. A source that could not be read serialises as `null`, never as `0` —
because a client that received `0` could not tell "the domain had nothing" from "we could not
look", and that distinction is the whole point of the milestone.

Model names are prefixed to keep them distinct from every other bounded context's. M9.21 shipped a
defect where `ReasonBody` collided with the Ledger's and FastAPI silently qualified the OpenAPI
schema name; a test now asserts no generated schema name contains `billing__customer__`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from billing.foundation import EvidenceDomain, PeriodBasis, ScopeCurrencyMode, ScopeMode


class PublishScopeDefinitionBody(BaseModel):
    """A scope rule, at a version. Immutable once published."""

    scope_code: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    mode: ScopeMode
    title: str = Field(default="", max_length=200)
    anchor_account_id: str = Field(default="", max_length=64)
    member_account_ids: list[str] = Field(default_factory=list)
    excluded_account_ids: list[str] = Field(default_factory=list)
    currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED
    declared_currency: str | None = None
    period_basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD
    required_domains: list[EvidenceDomain] = Field(default_factory=list)
    integrity_blocks_finalization: bool = False


class OpenConsolidationPeriodBody(BaseModel):
    basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD
    period_key: str = Field(min_length=1, max_length=64)
    start: float = 0.0
    end: float = 0.0
    aligned_source_id: str = Field(default="", max_length=64)


class ResolveScopeBody(BaseModel):
    scope_definition_id: str = Field(min_length=1, max_length=64)


class OpenConsolidationBody(BaseModel):
    scope_snapshot_id: str = Field(min_length=1, max_length=64)
    period_id: str = Field(min_length=1, max_length=64)


class CollectSourcesBody(BaseModel):
    """One batch. Bounded so a hundred-thousand-account scope is many requests, not one timeout."""

    batch_size: int = Field(default=100, ge=1, le=500)


class ConsolidationReasonBody(BaseModel):
    """Prefixed deliberately: a bare `ReasonBody` already exists in the Ledger's schemas."""

    reason: str = Field(default="", max_length=500)


class ConsolidationNoteBody(BaseModel):
    note: str = Field(default="", max_length=500)


class ScopeDefinitionResponse(BaseModel):
    id: str
    scope_code: str
    version: int
    mode: str
    title: str
    anchor_account_id: str
    member_account_ids: list[str]
    excluded_account_ids: list[str]
    currency_mode: str
    declared_currency: str | None
    period_basis: str
    required_domains: list[str]
    integrity_blocks_finalization: bool
    definition_digest: str
    #: The mode's proven semantics, served **from the server's own rule table** so a client never
    #: has to infer what a scope mode means.
    inclusion_rule: str
    exclusion_rule: str
    hierarchy: str

    @classmethod
    def of(cls, view) -> ScopeDefinitionResponse:
        return cls(
            id=view.id,
            scope_code=view.scope_code,
            version=view.version,
            mode=view.mode,
            title=view.title,
            anchor_account_id=view.anchor_account_id,
            member_account_ids=list(view.member_account_ids),
            excluded_account_ids=list(view.excluded_account_ids),
            currency_mode=view.currency_mode,
            declared_currency=view.declared_currency,
            period_basis=view.period_basis,
            required_domains=list(view.required_domains),
            integrity_blocks_finalization=view.integrity_blocks_finalization,
            definition_digest=view.definition_digest,
            inclusion_rule=view.inclusion_rule,
            exclusion_rule=view.exclusion_rule,
            hierarchy=view.hierarchy,
        )


class ExcludedAccountResponse(BaseModel):
    """An account the scope considered and did not include. **Never an absence** — every exclusion
    carries a reason, because a silently dropped account and a deliberately excluded one look
    identical in a total."""

    account_id: str
    reason: str
    detail: str

    @classmethod
    def of(cls, view) -> ExcludedAccountResponse:
        return cls(account_id=view.account_id, reason=view.reason, detail=view.detail)


class ScopeSnapshotResponse(BaseModel):
    id: str
    scope_definition_id: str
    scope_version: int
    mode: str
    hierarchy: str
    member_count: int
    excluded: list[ExcludedAccountResponse]
    currency_mode: str
    declared_currency: str | None
    currencies: list[str]
    membership_digest: str
    snapshot_digest: str
    effective_at: float

    @classmethod
    def of(cls, view) -> ScopeSnapshotResponse:
        return cls(
            id=view.id,
            scope_definition_id=view.scope_definition_id,
            scope_version=view.scope_version,
            mode=view.mode,
            hierarchy=view.hierarchy,
            member_count=view.member_count,
            excluded=[ExcludedAccountResponse.of(e) for e in view.excluded],
            currency_mode=view.currency_mode,
            declared_currency=view.declared_currency,
            currencies=list(view.currencies),
            membership_digest=view.membership_digest,
            snapshot_digest=view.snapshot_digest,
            effective_at=view.effective_at,
        )


class ScopeMemberResponse(BaseModel):
    account_id: str
    inclusion: str
    account_revision: str
    currency: str | None
    depth: int
    hierarchy_path: list[str]

    @classmethod
    def of(cls, view) -> ScopeMemberResponse:
        return cls(
            account_id=view.account_id,
            inclusion=view.inclusion,
            account_revision=view.account_revision,
            currency=view.currency,
            depth=view.depth,
            hierarchy_path=list(view.hierarchy_path),
        )


class ScopeMemberPage(BaseModel):
    members: list[ScopeMemberResponse]
    total: int
    page: int
    page_size: int


class ConsolidationPeriodResponse(BaseModel):
    id: str
    basis: str
    period_key: str
    start: float
    end: float
    aligned_source_id: str

    @classmethod
    def of(cls, view) -> ConsolidationPeriodResponse:
        return cls(
            id=view.id,
            basis=view.basis,
            period_key=view.period_key,
            start=view.start,
            end=view.end,
            aligned_source_id=view.aligned_source_id,
        )


class ConsolidatedAmountResponse(BaseModel):
    """One aggregate: one domain, one currency, integer minor units.

    **Never a combined figure across currencies, and never a converted one.**"""

    domain: str
    currency: str
    amount_minor: int
    contributing_accounts: int
    contributing_records: int

    @classmethod
    def of(cls, view) -> ConsolidatedAmountResponse:
        return cls(
            domain=view.domain,
            currency=view.currency,
            amount_minor=view.amount_minor,
            contributing_accounts=view.contributing_accounts,
            contributing_records=view.contributing_records,
        )


class ConsolidationResponse(BaseModel):
    id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_id: str
    period_basis: str
    period_key: str
    status: str
    completeness: str
    currency_mode: str
    declared_currency: str | None
    amounts: list[ConsolidatedAmountResponse]
    member_count: int
    collected_count: int
    source_count: int
    unavailable_source_count: int
    exception_count: int
    blocking_exception_count: int
    consolidation_digest: str
    corrects: str
    superseded_by: str
    correction_reason: str
    #: **The server's answer, and the only lifecycle authority a client has.** Studio renders
    #: exactly these actions; it never derives which transitions are legal.
    allowed_triggers: list[str]
    version: int
    finalized_at: float | None

    @classmethod
    def of(cls, view) -> ConsolidationResponse:
        return cls(
            id=view.id,
            scope_snapshot_id=view.scope_snapshot_id,
            scope_definition_id=view.scope_definition_id,
            scope_version=view.scope_version,
            period_id=view.period_id,
            period_basis=view.period_basis,
            period_key=view.period_key,
            status=view.status,
            completeness=view.completeness,
            currency_mode=view.currency_mode,
            declared_currency=view.declared_currency,
            amounts=[ConsolidatedAmountResponse.of(a) for a in view.amounts],
            member_count=view.member_count,
            collected_count=view.collected_count,
            source_count=view.source_count,
            unavailable_source_count=view.unavailable_source_count,
            exception_count=view.exception_count,
            blocking_exception_count=view.blocking_exception_count,
            consolidation_digest=view.consolidation_digest,
            corrects=view.corrects,
            superseded_by=view.superseded_by,
            correction_reason=view.correction_reason,
            allowed_triggers=list(view.allowed_triggers),
            version=view.version,
            finalized_at=view.finalized_at,
        )


class ConsolidationPage(BaseModel):
    consolidations: list[ConsolidationResponse]
    total: int
    pages: int


class SourceBindingResponse(BaseModel):
    """One pinned source.

    `amount_minor` is `null` for anything but a resolved source. **That is deliberate and
    load-bearing**: a client cannot mistake "could not read" for "nothing happened", and no
    downstream sum can treat a gap as nought."""

    id: str
    account_id: str
    domain: str
    artifact: str
    read_name: str
    status: str
    completeness: str
    period_key: str
    #: False when the owning domain publishes only an account-keyed read — the figure covers the
    #: account, not the period, and the wire says so rather than implying otherwise.
    period_applied: bool
    source_revision: str
    source_digest: str
    record_count: int
    amount_minor: int | None
    currency: str | None
    detail: str

    @classmethod
    def of(cls, view) -> SourceBindingResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            domain=view.domain,
            artifact=view.artifact,
            read_name=view.read_name,
            status=view.status,
            completeness=view.completeness,
            period_key=view.period_key,
            period_applied=view.period_applied,
            source_revision=view.source_revision,
            source_digest=view.source_digest,
            record_count=view.record_count,
            amount_minor=view.amount_minor,
            currency=view.currency,
            detail=view.detail,
        )


class SourceBindingPage(BaseModel):
    sources: list[SourceBindingResponse]
    total: int
    page: int
    page_size: int


class ConsolidationExceptionResponse(BaseModel):
    """A named problem. **There is no generic kind** — no `failed`, no `error`, no `other`."""

    id: str
    kind: str
    severity: str
    state: str
    subject: str
    diagnostic: str
    account_id: str
    domain: str | None
    period_key: str
    created_at: float
    acknowledged_at: float | None
    acknowledged_by: str
    acknowledgement_note: str
    #: **Stays true after acknowledgement.** Somebody having seen a gap is a different fact from
    #: the gap being closed, and only the second unblocks a consolidation.
    is_blocking: bool

    @classmethod
    def of(cls, view) -> ConsolidationExceptionResponse:
        return cls(
            id=view.id,
            kind=view.kind,
            severity=view.severity,
            state=view.state,
            subject=view.subject,
            diagnostic=view.diagnostic,
            account_id=view.account_id,
            domain=view.domain,
            period_key=view.period_key,
            created_at=view.created_at,
            acknowledged_at=view.acknowledged_at,
            acknowledged_by=view.acknowledged_by,
            acknowledgement_note=view.acknowledgement_note,
            is_blocking=view.is_blocking,
        )


class ConsolidationCheckResponse(BaseModel):
    name: str
    passed: bool
    detail: str


class ConsolidationValidationResponse(BaseModel):
    consolidation_id: str
    passed: bool
    checks: list[ConsolidationCheckResponse]
    validated_at: float
    validated_by: str

    @classmethod
    def of(cls, view) -> ConsolidationValidationResponse:
        return cls(
            consolidation_id=view.consolidation_id,
            passed=view.passed,
            checks=[
                ConsolidationCheckResponse(name=c.name, passed=c.passed, detail=c.detail)
                for c in view.checks
            ],
            validated_at=view.validated_at,
            validated_by=view.validated_by,
        )


class ConsolidationResultResponse(BaseModel):
    """The finalized artifact, content-addressed on pinned state only."""

    id: str
    consolidation_id: str
    consolidation_digest: str
    scope_snapshot_id: str
    scope_snapshot_digest: str
    period_key: str
    representation: str
    source_digest: str
    completeness: str
    finalized_at: float
    finalized_by: str

    @classmethod
    def of(cls, view) -> ConsolidationResultResponse:
        return cls(
            id=view.id,
            consolidation_id=view.consolidation_id,
            consolidation_digest=view.consolidation_digest,
            scope_snapshot_id=view.scope_snapshot_id,
            scope_snapshot_digest=view.scope_snapshot_digest,
            period_key=view.period_key,
            representation=view.representation,
            source_digest=view.source_digest,
            completeness=view.completeness,
            finalized_at=view.finalized_at,
            finalized_by=view.finalized_by,
        )


class AccountContributionResponse(BaseModel):
    account_id: str
    inclusion: str
    resolved_sources: int
    empty_sources: int
    unavailable_sources: int
    record_count: int
    currency: str | None
    contributed: bool


class ConsolidationProvenanceResponse(BaseModel):
    """**Which accounts made up this number, and which did not.**"""

    consolidation_id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_basis: str
    period_key: str
    hierarchy: str
    contributions: list[AccountContributionResponse]
    excluded: list[ExcludedAccountResponse]
    domains: list[str]
    consolidation_digest: str
    corrects: str
    superseded_by: str

    @classmethod
    def of(cls, view) -> ConsolidationProvenanceResponse:
        return cls(
            consolidation_id=view.consolidation_id,
            scope_snapshot_id=view.scope_snapshot_id,
            scope_definition_id=view.scope_definition_id,
            scope_version=view.scope_version,
            period_basis=view.period_basis,
            period_key=view.period_key,
            hierarchy=view.hierarchy,
            contributions=[
                AccountContributionResponse(
                    account_id=c.account_id,
                    inclusion=c.inclusion,
                    resolved_sources=c.resolved_sources,
                    empty_sources=c.empty_sources,
                    unavailable_sources=c.unavailable_sources,
                    record_count=c.record_count,
                    currency=c.currency,
                    contributed=c.contributed,
                )
                for c in view.contributions
            ],
            excluded=[ExcludedAccountResponse.of(e) for e in view.excluded],
            domains=list(view.domains),
            consolidation_digest=view.consolidation_digest,
            corrects=view.corrects,
            superseded_by=view.superseded_by,
        )


class ConsolidationEventResponse(BaseModel):
    id: str
    kind: str
    sequence: int
    subject: str
    detail: str
    actor: str
    occurred_at: float

    @classmethod
    def of(cls, view) -> ConsolidationEventResponse:
        return cls(
            id=view.id,
            kind=view.kind,
            sequence=view.sequence,
            subject=view.subject,
            detail=view.detail,
            actor=view.actor,
            occurred_at=view.occurred_at,
        )


class ConsolidationPostureResponse(BaseModel):
    """Counts and labels. **No money.**"""

    total_runs: int
    by_status: dict[str, int]
    by_completeness: dict[str, int]
    open_exceptions: int
    blocking_exceptions: int
    scope_definitions: int

    @classmethod
    def of(cls, view) -> ConsolidationPostureResponse:
        return cls(
            total_runs=view.total_runs,
            by_status=dict(view.by_status),
            by_completeness=dict(view.by_completeness),
            open_exceptions=view.open_exceptions,
            blocking_exceptions=view.blocking_exceptions,
            scope_definitions=view.scope_definitions,
        )


__all__ = [
    "AccountContributionResponse",
    "CollectSourcesBody",
    "ConsolidatedAmountResponse",
    "ConsolidationEventResponse",
    "ConsolidationExceptionResponse",
    "ConsolidationNoteBody",
    "ConsolidationPage",
    "ConsolidationPeriodResponse",
    "ConsolidationPostureResponse",
    "ConsolidationProvenanceResponse",
    "ConsolidationReasonBody",
    "ConsolidationResponse",
    "ConsolidationResultResponse",
    "ConsolidationValidationResponse",
    "ExcludedAccountResponse",
    "OpenConsolidationBody",
    "OpenConsolidationPeriodBody",
    "PublishScopeDefinitionBody",
    "ResolveScopeBody",
    "ScopeDefinitionResponse",
    "ScopeMemberPage",
    "ScopeMemberResponse",
    "ScopeSnapshotResponse",
    "SourceBindingPage",
    "SourceBindingResponse",
]
