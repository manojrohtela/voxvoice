"""Entitlement Engine wire DTOs (M9.10) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused so
validation and OpenAPI enums come from the domain vocabulary.

**Not one field here carries money, and not one carries a principal.** An allowance says how much
is included, never what it costs; and nothing on this wire names a user, a role, or a scope,
because entitlement is commercial capability and authorization is decided elsewhere (ADR-0023
§11.3). Both absences are asserted by tests, because "we simply did not add the field" is a
property that decays the moment nobody is checking.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    EntitlementChangeKind,
    EntitlementValue,
    EntitlementWindow,
    FeatureKind,
    GateClassification,
    GrantSource,
    GrantState,
)

# ── shared bodies ───────────────────────────────────────────────────────────────


class ValueBody(BaseModel):
    enabled: bool = True
    allowance: int | None = Field(
        default=None, ge=0, description="Null = unlimited; 0 = explicitly none — opposite answers"
    )
    allows_overage: bool = False

    def to_value(self) -> EntitlementValue:
        return EntitlementValue(
            enabled=self.enabled, allowance=self.allowance, allows_overage=self.allows_overage
        )


class WindowBody(BaseModel):
    starts_at: float | None = Field(
        default=None, description="Half-open [starts_at, ends_at); null = unbounded"
    )
    ends_at: float | None = None

    def to_window(self) -> EntitlementWindow:
        return EntitlementWindow(starts_at=self.starts_at, ends_at=self.ends_at)


class BundleEntryBody(BaseModel):
    code: str
    value: ValueBody = Field(default_factory=ValueBody)


# ── requests ────────────────────────────────────────────────────────────────────


class DefineEntitlementBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "voice.minutes",
                "display_name": "Voice minutes",
                "kind": "quota",
                "classification": "fail_open",
                "meter_code": "runtime.session.minutes",
                "default": {"enabled": True, "allowance": 0},
            }
        }
    )
    code: str
    display_name: str = ""
    kind: FeatureKind = FeatureKind.FLAG
    classification: GateClassification = Field(
        default=GateClassification.FAIL_OPEN,
        description="What an enforcing plane must do when it cannot see fresh data. Required by "
        "ADR-0023 E3 — a gate without one is an incomplete design",
    )
    default: ValueBody = Field(default_factory=lambda: ValueBody(enabled=False))
    meter_code: str = Field(default="", description="Required when kind is quota")
    max_staleness_seconds: float | None = Field(default=None, gt=0)
    description: str = ""


class UpdateEntitlementDefinitionBody(BaseModel):
    display_name: str | None = None
    classification: GateClassification | None = None
    default: ValueBody | None = None
    meter_code: str | None = None
    max_staleness_seconds: float | None = Field(default=None, gt=0)
    description: str | None = None


class ReasonBody(BaseModel):
    reason: str = ""


class CreateBundleBody(BaseModel):
    code: str
    display_name: str = ""
    entries: list[BundleEntryBody] = Field(default_factory=list)
    member_bundles: list[str] = Field(
        default_factory=list, description="Nested bundles; cycles are rejected with the path"
    )
    description: str = ""


class UpdateBundleBody(BaseModel):
    entries: list[BundleEntryBody] | None = None
    member_bundles: list[str] | None = None
    display_name: str | None = None
    description: str | None = None


class OpenProfileBody(BaseModel):
    account_id: str
    subscription_id: str = ""
    max_staleness_seconds: float | None = Field(default=None, gt=0)


class GrantBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "entitlement_code": "voice.minutes",
                "value": {"enabled": True, "allowance": 10000, "allows_overage": True},
                "source": "contract",
                "source_ref": "ct_9",
            }
        }
    )
    entitlement_code: str
    value: ValueBody = Field(default_factory=ValueBody)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    window: WindowBody = Field(default_factory=WindowBody)
    precedence_override: int | None = Field(
        default=None,
        description="For the negotiated term declared precedence cannot express. Must stay "
        "inside the declared band",
    )
    reason: str = ""


class GrantBundleBody(BaseModel):
    bundle_code: str
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    window: WindowBody = Field(default_factory=WindowBody)
    reason: str = ""


class AdjustGrantBody(BaseModel):
    value: ValueBody | None = None
    window: WindowBody | None = None
    reason: str | None = None


class ScheduleChangeBody(BaseModel):
    kind: EntitlementChangeKind = EntitlementChangeKind.GRANT
    effective_at: float = Field(gt=0)
    entitlement_code: str = ""
    bundle_code: str = ""
    grant_id: str = ""
    value: ValueBody = Field(default_factory=ValueBody)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    reason: str = ""


class PublishSnapshotBody(BaseModel):
    reason: str = ""
    at: float | None = None
    include_defaults: bool = Field(
        default=False,
        description="Emit declared defaults for codes nobody granted. Off by default: a snapshot "
        "asserting a default the customer was never given is unexplainable from the grants",
    )
    allow_identical: bool = Field(
        default=False,
        description="Publish even when the decision is unchanged — an audit boundary",
    )


class RollbackBody(BaseModel):
    to_version: int = Field(ge=1)
    reason: str = ""


# ── responses ───────────────────────────────────────────────────────────────────


class ValueResponse(BaseModel):
    enabled: bool
    allowance: int | None
    allows_overage: bool
    is_unlimited: bool
    is_denial: bool

    @classmethod
    def from_view(cls, v) -> ValueResponse:
        return cls(
            enabled=v.enabled,
            allowance=v.allowance,
            allows_overage=v.allows_overage,
            is_unlimited=v.is_unlimited,
            is_denial=v.is_denial,
        )


class WindowResponse(BaseModel):
    starts_at: float | None
    ends_at: float | None
    is_open_ended: bool

    @classmethod
    def from_view(cls, w) -> WindowResponse:
        return cls(starts_at=w.starts_at, ends_at=w.ends_at, is_open_ended=w.is_open_ended)


class DefinitionResponse(BaseModel):
    id: str
    code: str
    display_name: str
    kind: str
    classification: str
    default: ValueResponse
    meter_code: str
    max_staleness_seconds: float
    description: str
    version: int
    is_retired: bool
    created_at: float
    updated_at: float

    @classmethod
    def from_view(cls, v) -> DefinitionResponse:
        return cls(
            id=v.id,
            code=v.code,
            display_name=v.display_name,
            kind=v.kind,
            classification=v.classification,
            default=ValueResponse.from_view(v.default),
            meter_code=v.meter_code,
            max_staleness_seconds=v.max_staleness_seconds,
            description=v.description,
            version=v.version,
            is_retired=v.is_retired,
            created_at=v.created_at,
            updated_at=v.updated_at,
        )


class BundleEntryResponse(BaseModel):
    code: str
    value: ValueResponse


class BundleResponse(BaseModel):
    id: str
    code: str
    display_name: str
    entries: list[BundleEntryResponse]
    member_bundles: list[str]
    description: str
    version: int
    is_composite: bool
    expanded_codes: list[str]

    @classmethod
    def from_view(cls, v) -> BundleResponse:
        return cls(
            id=v.id,
            code=v.code,
            display_name=v.display_name,
            entries=[
                BundleEntryResponse(code=e.code, value=ValueResponse.from_view(e.value))
                for e in v.entries
            ],
            member_bundles=list(v.member_bundles),
            description=v.description,
            version=v.version,
            is_composite=v.is_composite,
            expanded_codes=list(v.expanded_codes),
        )


class GrantResponse(BaseModel):
    id: str
    account_id: str
    entitlement_code: str
    value: ValueResponse
    source: str
    source_ref: str
    bundle_code: str
    window: WindowResponse
    state: str
    precedence: int
    precedence_override: int | None
    reason: str
    is_live: bool
    is_closed: bool
    is_editable: bool
    allowed_triggers: list[str]
    created_at: float
    updated_at: float
    activated_at: float | None
    revoked_at: float | None
    revoked_reason: str

    @classmethod
    def from_view(cls, v) -> GrantResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            entitlement_code=v.entitlement_code,
            value=ValueResponse.from_view(v.value),
            source=v.source,
            source_ref=v.source_ref,
            bundle_code=v.bundle_code,
            window=WindowResponse.from_view(v.window),
            state=v.state,
            precedence=v.precedence,
            precedence_override=v.precedence_override,
            reason=v.reason,
            is_live=v.is_live,
            is_closed=v.is_closed,
            is_editable=v.is_editable,
            allowed_triggers=list(v.allowed_triggers),
            created_at=v.created_at,
            updated_at=v.updated_at,
            activated_at=v.activated_at,
            revoked_at=v.revoked_at,
            revoked_reason=v.revoked_reason,
        )


class ScheduledChangeResponse(BaseModel):
    id: str
    kind: str
    status: str
    effective_at: float
    entitlement_code: str
    bundle_code: str
    grant_id: str
    value: ValueResponse
    source: str
    reason: str
    requested_by: str
    requested_at: float
    applied_at: float | None

    @classmethod
    def from_view(cls, v) -> ScheduledChangeResponse:
        return cls(
            id=v.id,
            kind=v.kind,
            status=v.status,
            effective_at=v.effective_at,
            entitlement_code=v.entitlement_code,
            bundle_code=v.bundle_code,
            grant_id=v.grant_id,
            value=ValueResponse.from_view(v.value),
            source=v.source,
            reason=v.reason,
            requested_by=v.requested_by,
            requested_at=v.requested_at,
            applied_at=v.applied_at,
        )


class ProfileResponse(BaseModel):
    id: str
    account_id: str
    subscription_id: str
    version: int
    max_staleness_seconds: float
    grants: list[GrantResponse]
    scheduled_changes: list[ScheduledChangeResponse]
    codes: list[str]
    sources: list[str]
    bundle_codes: list[str]
    has_published_snapshot: bool
    created_at: float
    updated_at: float

    @classmethod
    def from_view(cls, v) -> ProfileResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            subscription_id=v.subscription_id,
            version=v.version,
            max_staleness_seconds=v.max_staleness_seconds,
            grants=[GrantResponse.from_view(g) for g in v.grants],
            scheduled_changes=[ScheduledChangeResponse.from_view(c) for c in v.scheduled_changes],
            codes=list(v.codes),
            sources=list(v.sources),
            bundle_codes=list(v.bundle_codes),
            has_published_snapshot=v.has_published_snapshot,
            created_at=v.created_at,
            updated_at=v.updated_at,
        )


class ResolvedEntitlementResponse(BaseModel):
    code: str
    kind: str
    enabled: bool
    allowance: int | None
    allows_overage: bool
    source: str
    classification: str
    grant_id: str
    precedence: int
    from_bundle: str
    is_unlimited: bool
    resolution_path: list[str]

    @classmethod
    def from_view(cls, v) -> ResolvedEntitlementResponse:
        return cls(
            code=v.code,
            kind=v.kind,
            enabled=v.enabled,
            allowance=v.allowance,
            allows_overage=v.allows_overage,
            source=v.source,
            classification=v.classification,
            grant_id=v.grant_id,
            precedence=v.precedence,
            from_bundle=v.from_bundle,
            is_unlimited=v.is_unlimited,
            resolution_path=list(v.resolution_path),
        )


class GateResponse(BaseModel):
    code: str
    kind: str
    classification: str
    meter_code: str
    description: str


class SnapshotResponse(BaseModel):
    """The published decision. This is the *entire* interface between Billing and enforcement:
    what the account is entitled to, plus the metadata a plane needs to reason about staleness.
    It carries no principal and no role — reading it is not an authorization."""

    id: str
    account_id: str
    version: int
    issued_at: float
    entitlements: list[ResolvedEntitlementResponse]
    gates: list[GateResponse]
    subscription_id: str
    max_staleness_seconds: float
    grant_count: int
    resolved_from: list[str]
    codes: list[str]
    unclassified_gates: list[str]
    fail_closed_codes: list[str]

    @classmethod
    def from_view(cls, v) -> SnapshotResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            version=v.version,
            issued_at=v.issued_at,
            entitlements=[ResolvedEntitlementResponse.from_view(e) for e in v.entitlements],
            gates=[
                GateResponse(
                    code=g.code,
                    kind=g.kind,
                    classification=g.classification,
                    meter_code=g.meter_code,
                    description=g.description,
                )
                for g in v.gates
            ],
            subscription_id=v.subscription_id,
            max_staleness_seconds=v.max_staleness_seconds,
            grant_count=v.grant_count,
            resolved_from=list(v.resolved_from),
            codes=list(v.codes),
            unclassified_gates=list(v.unclassified_gates),
            fail_closed_codes=list(v.fail_closed_codes),
        )


class VersionResponse(BaseModel):
    id: str
    account_id: str
    version: int
    published_at: float
    published_by: str
    reason: str
    rolled_back_from: int | None
    entitlement_count: int
    grant_count: int
    codes: list[str]

    @classmethod
    def from_view(cls, v) -> VersionResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            version=v.version,
            published_at=v.published_at,
            published_by=v.published_by,
            reason=v.reason,
            rolled_back_from=v.rolled_back_from,
            entitlement_count=v.entitlement_count,
            grant_count=v.grant_count,
            codes=list(v.codes),
        )


class ActivityResponse(BaseModel):
    id: str
    account_id: str
    action: str
    actor_id: str
    at: float
    entitlement_code: str
    grant_id: str
    from_state: str
    to_state: str
    version: int
    detail: str

    @classmethod
    def from_view(cls, v) -> ActivityResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            action=v.action,
            actor_id=v.actor_id,
            at=v.at,
            entitlement_code=v.entitlement_code,
            grant_id=v.grant_id,
            from_state=v.from_state,
            to_state=v.to_state,
            version=v.version,
            detail=v.detail,
        )


class ConsiderationResponse(BaseModel):
    grant_id: str
    source: str
    precedence: int
    value: ValueResponse
    effective: bool
    won: bool
    excluded_reason: str


class ExplanationResponse(BaseModel):
    """Why this account does — or does not — have this capability. Losing grants are included:
    "why *not*?" is unanswerable from the winner alone."""

    code: str
    at: float
    winner: ResolvedEntitlementResponse | None
    considered: list[ConsiderationResponse]
    definition_known: bool
    is_granted: bool
    contested: bool

    @classmethod
    def from_view(cls, v) -> ExplanationResponse:
        return cls(
            code=v.code,
            at=v.at,
            winner=ResolvedEntitlementResponse.from_view(v.winner) if v.winner else None,
            considered=[
                ConsiderationResponse(
                    grant_id=c.grant_id,
                    source=c.source,
                    precedence=c.precedence,
                    value=ValueResponse.from_view(c.value),
                    effective=c.effective,
                    won=c.won,
                    excluded_reason=c.excluded_reason,
                )
                for c in v.considered
            ],
            definition_known=v.definition_known,
            is_granted=v.is_granted,
            contested=v.contested,
        )


class DirectiveResponse(BaseModel):
    code: str
    classification: str
    directive: str
    stale: bool


class StalenessResponse(BaseModel):
    """What an enforcing plane should do with each gate right now. Information, not permission."""

    account_id: str
    version: int
    issued_at: float
    at: float
    age: float
    max_staleness_seconds: float
    stale: bool
    directives: list[DirectiveResponse]
    denied_codes: list[str]
    degraded_codes: list[str]

    @classmethod
    def from_view(cls, v) -> StalenessResponse:
        return cls(
            account_id=v.account_id,
            version=v.version,
            issued_at=v.issued_at,
            at=v.at,
            age=v.age,
            max_staleness_seconds=v.max_staleness_seconds,
            stale=v.stale,
            directives=[
                DirectiveResponse(
                    code=d.code,
                    classification=d.classification,
                    directive=d.directive,
                    stale=d.stale,
                )
                for d in v.directives
            ],
            denied_codes=list(v.denied_codes),
            degraded_codes=list(v.degraded_codes),
        )


class DiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str


class DiagnosticsResponse(BaseModel):
    account_id: str
    findings: list[DiagnosticResponse]
    error_count: int
    warning_count: int
    is_valid: bool

    @classmethod
    def from_view(cls, v) -> DiagnosticsResponse:
        return cls(
            account_id=v.account_id,
            findings=[
                DiagnosticResponse(
                    path=f.path,
                    code=f.code,
                    severity=f.severity,
                    message=f.message,
                    remediation=f.remediation,
                )
                for f in v.findings
            ],
            error_count=v.error_count,
            warning_count=v.warning_count,
            is_valid=v.is_valid,
        )


class GrantSearchResponse(BaseModel):
    grants: list[GrantResponse]
    total: int

    @classmethod
    def from_view(cls, v) -> GrantSearchResponse:
        return cls(grants=[GrantResponse.from_view(g) for g in v.grants], total=v.total)


def build_grant_query(**kwargs):
    """Wire query parameters → the Foundation's `EntitlementSearchQuery`. Kept here so the
    controller never constructs a domain object field by field."""
    from billing.foundation import EntitlementSearchQuery

    return EntitlementSearchQuery(
        text=kwargs.get("text", ""),
        codes=tuple(kwargs.get("codes") or ()),
        states=tuple(kwargs.get("states") or ()),
        sources=tuple(kwargs.get("sources") or ()),
        account_id=kwargs.get("account_id", ""),
        bundle_code=kwargs.get("bundle_code", ""),
        source_ref=kwargs.get("source_ref", ""),
        effective_at=kwargs.get("effective_at"),
        live_only=kwargs.get("live_only", False),
        bundled_only=kwargs.get("bundled_only", False),
        unlimited_only=kwargs.get("unlimited_only", False),
        denials_only=kwargs.get("denials_only", False),
        limit=kwargs.get("limit", 100),
        offset=kwargs.get("offset", 0),
    )


__all__ = [
    "ActivityResponse",
    "AdjustGrantBody",
    "BundleEntryBody",
    "BundleResponse",
    "CreateBundleBody",
    "DefineEntitlementBody",
    "DefinitionResponse",
    "DiagnosticsResponse",
    "ExplanationResponse",
    "GrantBody",
    "GrantBundleBody",
    "GrantResponse",
    "GrantSearchResponse",
    "GrantSource",
    "GrantState",
    "OpenProfileBody",
    "ProfileResponse",
    "PublishSnapshotBody",
    "ReasonBody",
    "RollbackBody",
    "ScheduleChangeBody",
    "ScheduledChangeResponse",
    "SnapshotResponse",
    "StalenessResponse",
    "UpdateBundleBody",
    "UpdateEntitlementDefinitionBody",
    "ValueBody",
    "VersionResponse",
    "WindowBody",
    "build_grant_query",
]
