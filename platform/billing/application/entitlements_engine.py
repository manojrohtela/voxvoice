"""Entitlement Engine application layer (M9.10) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6/M9.7/M9.8/M9.9: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This module
adds 19 commands, 11 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every entitlement rule — gate validity, bundle expansion, precedence, window
coverage, snapshot composition, staleness directives, schedule conflicts — is Foundation
behaviour these methods *call*. There is no rule here, and a test greps for the ones that would
be tempting to inline.

Names differ from the frozen M9.3 surface where they must: M9.3 owns `GrantEntitlement`,
`RevokeEntitlement`, `PublishEntitlementSnapshot`, `GetEntitlementSnapshot`, and `GetQuotaVerdict`,
which drive the minimal M9.2 entitlement records. Everything here is named distinctly — the same
call M9.6 made for `CreateCatalogPlan`, M9.7 for `PublishPricingBook`, M9.8 for
`CloseCommercialAccount`, and M9.9 for `GetCommercialSubscription`.

Two authorization notes, both deliberate:

* declaring a gate is a **platform** act, so definitions and bundles require
  `BILLING_CATALOG_MANAGE` — the same permission the commercial catalog uses. Granting to an
  account is a commercial act and requires `BILLING_MANAGE`;
* **none of this is authorization.** Requiring a permission to *change* entitlement data is not
  the same as evaluating entitlement as permission, and Billing still does none of the latter.

As in the milestones before it, **every command also appends an `EntitlementActivity`** inside
the same transaction as the change it describes; and a **rejected command emits no business
event**, because the Foundation fails fast before any emission.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import BillingNotFound as _NotFound
from billing.foundation import (
    BundleEntry,
    CommercialGrant,
    EntitlementAction,
    EntitlementBundle,
    EntitlementChangeKind,
    EntitlementDefinition,
    EntitlementProfile,
    EntitlementSearchQuery,
    EntitlementSet,
    EntitlementValue,
    EntitlementVersion,
    EntitlementWindow,
    FeatureKind,
    GateClassification,
    GrantSource,
    GrantTrigger,
)

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DefineEntitlement(_Command):
    """Declare a capability *and* how enforcing planes behave when they cannot reach Billing."""

    code: str = ""
    display_name: str = ""
    kind: FeatureKind = FeatureKind.FLAG
    classification: GateClassification = GateClassification.FAIL_OPEN
    default: EntitlementValue = field(default_factory=lambda: EntitlementValue(enabled=False))
    meter_code: str = ""
    max_staleness_seconds: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class UpdateEntitlementDefinition(_Command):
    code: str = ""
    display_name: str | None = None
    classification: GateClassification | None = None
    default: EntitlementValue | None = None
    meter_code: str | None = None
    max_staleness_seconds: float | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class RetireEntitlementDefinition(_Command):
    """Retire a declaration without withdrawing it from anyone who holds it."""

    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreateEntitlementBundle(_Command):
    code: str = ""
    display_name: str = ""
    entries: tuple[BundleEntry, ...] = ()
    member_bundles: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class UpdateEntitlementBundle(_Command):
    code: str = ""
    entries: tuple[BundleEntry, ...] | None = None
    member_bundles: tuple[str, ...] | None = None
    display_name: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class OpenEntitlementProfile(_Command):
    account_id: str = ""
    subscription_id: str = ""
    max_staleness_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class GrantCommercialEntitlement(_Command):
    """Named distinctly from M9.3's `GrantEntitlement`, which writes the minimal M9.2 record."""

    account_id: str = ""
    entitlement_code: str = ""
    value: EntitlementValue = field(default_factory=EntitlementValue)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    window: EntitlementWindow = field(default_factory=EntitlementWindow)
    precedence_override: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GrantEntitlementBundleToAccount(_Command):
    account_id: str = ""
    bundle_code: str = ""
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    window: EntitlementWindow = field(default_factory=EntitlementWindow)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ActivateEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""


@dataclass(frozen=True, slots=True)
class AdjustEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""
    value: EntitlementValue | None = None
    window: EntitlementWindow | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SuspendEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResumeEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""


@dataclass(frozen=True, slots=True)
class RevokeCommercialEntitlement(_Command):
    """Named distinctly from M9.3's `RevokeEntitlement`. Revocation closes the grant's window as
    well as its state, so a snapshot for a later instant cannot re-admit it."""

    account_id: str = ""
    grant_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExpireEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveEntitlementGrant(_Command):
    account_id: str = ""
    grant_id: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleEntitlementChange(_Command):
    """Intent only. Nothing in this plane fires it — a caller dispatches the real command when
    the time comes, and it is validated then."""

    account_id: str = ""
    kind: EntitlementChangeKind = EntitlementChangeKind.GRANT
    effective_at: float = 0.0
    entitlement_code: str = ""
    bundle_code: str = ""
    grant_id: str = ""
    value: EntitlementValue = field(default_factory=EntitlementValue)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelScheduledEntitlementChange(_Command):
    account_id: str = ""
    change_id: str = ""


@dataclass(frozen=True, slots=True)
class PublishCommercialEntitlementSnapshot(_Command):
    """Compose the account's grants into an immutable, versioned snapshot.

    Named distinctly from M9.3's `PublishEntitlementSnapshot`. `allow_identical` exists because
    a caller may legitimately want a version boundary for an audit even when nothing changed;
    off by default so a hundred identical versions cannot bury the ones that meant something."""

    account_id: str = ""
    reason: str = ""
    at: float | None = None
    include_defaults: bool = False
    allow_identical: bool = False


@dataclass(frozen=True, slots=True)
class RollbackEntitlementVersion(_Command):
    """Publish a **new** version carrying an earlier decision. History is never rewritten."""

    account_id: str = ""
    to_version: int = 1
    reason: str = ""


ALL_ENTITLEMENT_COMMANDS = (
    DefineEntitlement,
    UpdateEntitlementDefinition,
    RetireEntitlementDefinition,
    CreateEntitlementBundle,
    UpdateEntitlementBundle,
    OpenEntitlementProfile,
    GrantCommercialEntitlement,
    GrantEntitlementBundleToAccount,
    ActivateEntitlementGrant,
    AdjustEntitlementGrant,
    SuspendEntitlementGrant,
    ResumeEntitlementGrant,
    RevokeCommercialEntitlement,
    ExpireEntitlementGrant,
    ArchiveEntitlementGrant,
    ScheduleEntitlementChange,
    CancelScheduledEntitlementChange,
    PublishCommercialEntitlementSnapshot,
    RollbackEntitlementVersion,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetEntitlementProfile(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class GetCommercialEntitlementSnapshot(_Query):
    """The published decision. `version=None` ⇒ the latest.

    Named distinctly from M9.3's `GetEntitlementSnapshot`, which reads the minimal M9.2 store."""

    account_id: str = ""
    version: int | None = None


@dataclass(frozen=True, slots=True)
class PreviewEntitlementSnapshot(_Query):
    """What *would* be published right now, without publishing it.

    A read: composition is pure, so previewing costs nothing and changes nothing. This is what
    makes "show me the effect before I approve it" possible without a draft version polluting
    the published line."""

    account_id: str = ""
    at: float | None = None
    include_defaults: bool = False


@dataclass(frozen=True, slots=True)
class EntitlementVersionHistory(_Query):
    account_id: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class EntitlementTimeline(_Query):
    account_id: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class SearchEntitlementGrants(_Query):
    query: EntitlementSearchQuery = field(default_factory=EntitlementSearchQuery)


@dataclass(frozen=True, slots=True)
class InspectEntitlement(_Query):
    """Why this account does — or does not — have this capability."""

    account_id: str = ""
    entitlement_code: str = ""
    at: float | None = None


@dataclass(frozen=True, slots=True)
class DiagnoseEntitlements(_Query):
    account_id: str = ""
    at: float | None = None


@dataclass(frozen=True, slots=True)
class GetEntitlementStaleness(_Query):
    """What an enforcing plane should do with each gate right now."""

    account_id: str = ""
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class ListEntitlementDefinitions(_Query):
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class ListEntitlementBundles(_Query):
    pass


@dataclass(frozen=True, slots=True)
class ListScheduledEntitlementChanges(_Query):
    account_id: str = ""
    due_at: float | None = None  # set ⇒ only changes due by then


ALL_ENTITLEMENT_QUERIES = (
    GetEntitlementProfile,
    GetCommercialEntitlementSnapshot,
    PreviewEntitlementSnapshot,
    EntitlementVersionHistory,
    EntitlementTimeline,
    SearchEntitlementGrants,
    InspectEntitlement,
    DiagnoseEntitlements,
    GetEntitlementStaleness,
    ListEntitlementDefinitions,
    ListEntitlementBundles,
    ListScheduledEntitlementChanges,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementValueView:
    enabled: bool
    allowance: int | None
    allows_overage: bool
    is_unlimited: bool
    is_denial: bool


@dataclass(frozen=True, slots=True)
class WindowView:
    starts_at: float | None
    ends_at: float | None
    is_open_ended: bool


@dataclass(frozen=True, slots=True)
class EntitlementDefinitionView:
    id: str
    code: str
    display_name: str
    kind: str
    classification: str
    default: EntitlementValueView
    meter_code: str
    max_staleness_seconds: float
    description: str
    version: int
    is_retired: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class BundleEntryView:
    code: str
    value: EntitlementValueView


@dataclass(frozen=True, slots=True)
class EntitlementBundleView:
    id: str
    code: str
    display_name: str
    entries: tuple[BundleEntryView, ...]
    member_bundles: tuple[str, ...]
    description: str
    version: int
    is_composite: bool
    expanded_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GrantView:
    id: str
    account_id: str
    entitlement_code: str
    value: EntitlementValueView
    source: str
    source_ref: str
    bundle_code: str
    window: WindowView
    state: str
    precedence: int
    precedence_override: int | None
    reason: str
    is_live: bool
    is_closed: bool
    is_editable: bool
    allowed_triggers: tuple[str, ...]
    created_at: float
    updated_at: float
    activated_at: float | None
    revoked_at: float | None
    revoked_reason: str


@dataclass(frozen=True, slots=True)
class ScheduledEntitlementChangeView:
    id: str
    kind: str
    status: str
    effective_at: float
    entitlement_code: str
    bundle_code: str
    grant_id: str
    value: EntitlementValueView
    source: str
    reason: str
    requested_by: str
    requested_at: float
    applied_at: float | None


@dataclass(frozen=True, slots=True)
class EntitlementProfileView:
    id: str
    account_id: str
    subscription_id: str
    version: int
    max_staleness_seconds: float
    grants: tuple[GrantView, ...]
    scheduled_changes: tuple[ScheduledEntitlementChangeView, ...]
    codes: tuple[str, ...]
    sources: tuple[str, ...]
    bundle_codes: tuple[str, ...]
    has_published_snapshot: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class ResolvedEntitlementView:
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
    resolution_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateView:
    code: str
    kind: str
    classification: str
    meter_code: str
    description: str


@dataclass(frozen=True, slots=True)
class EntitlementSetView:
    id: str
    account_id: str
    version: int
    issued_at: float
    entitlements: tuple[ResolvedEntitlementView, ...]
    gates: tuple[GateView, ...]
    subscription_id: str
    max_staleness_seconds: float
    grant_count: int
    resolved_from: tuple[str, ...]
    codes: tuple[str, ...]
    unclassified_gates: tuple[str, ...]
    fail_closed_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntitlementVersionView:
    id: str
    account_id: str
    version: int
    published_at: float
    published_by: str
    reason: str
    rolled_back_from: int | None
    entitlement_count: int
    grant_count: int
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntitlementActivityView:
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


@dataclass(frozen=True, slots=True)
class GrantConsiderationView:
    grant_id: str
    source: str
    precedence: int
    value: EntitlementValueView
    effective: bool
    won: bool
    excluded_reason: str


@dataclass(frozen=True, slots=True)
class EntitlementExplanationView:
    code: str
    at: float
    winner: ResolvedEntitlementView | None
    considered: tuple[GrantConsiderationView, ...]
    definition_known: bool
    is_granted: bool
    contested: bool


@dataclass(frozen=True, slots=True)
class GateDirectiveView:
    code: str
    classification: str
    directive: str
    stale: bool


@dataclass(frozen=True, slots=True)
class StalenessReportView:
    account_id: str
    version: int
    issued_at: float
    at: float
    age: float
    max_staleness_seconds: float
    stale: bool
    directives: tuple[GateDirectiveView, ...]
    denied_codes: tuple[str, ...]
    degraded_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntitlementDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class EntitlementDiagnosticsView:
    account_id: str
    findings: tuple[EntitlementDiagnosticView, ...]
    error_count: int
    warning_count: int
    is_valid: bool


@dataclass(frozen=True, slots=True)
class EntitlementSearchView:
    grants: tuple[GrantView, ...]
    total: int


def _value_view(value: EntitlementValue) -> EntitlementValueView:
    return EntitlementValueView(
        enabled=value.enabled,
        allowance=value.allowance,
        allows_overage=value.allows_overage,
        is_unlimited=value.is_unlimited,
        is_denial=value.is_denial,
    )


def _window_view(window: EntitlementWindow) -> WindowView:
    return WindowView(
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        is_open_ended=window.is_open_ended,
    )


def definition_view(definition: EntitlementDefinition) -> EntitlementDefinitionView:
    return EntitlementDefinitionView(
        id=definition.id,
        code=definition.code,
        display_name=definition.display_name,
        kind=definition.kind.value,
        classification=definition.classification.value,
        default=_value_view(definition.default),
        meter_code=definition.meter_code,
        max_staleness_seconds=definition.max_staleness_seconds,
        description=definition.description,
        version=definition.version,
        is_retired=definition.is_retired,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def bundle_view(bundle: EntitlementBundle, expanded: tuple[str, ...] = ()) -> EntitlementBundleView:
    return EntitlementBundleView(
        id=bundle.id,
        code=bundle.code,
        display_name=bundle.display_name,
        entries=tuple(
            BundleEntryView(code=e.code, value=_value_view(e.value)) for e in bundle.entries
        ),
        member_bundles=bundle.member_bundles,
        description=bundle.description,
        version=bundle.version,
        is_composite=bundle.is_composite,
        expanded_codes=expanded or bundle.codes,
    )


def grant_view(grant: CommercialGrant, allowed: tuple[GrantTrigger, ...] = ()) -> GrantView:
    return GrantView(
        id=grant.id,
        account_id=grant.account_id,
        entitlement_code=grant.entitlement_code,
        value=_value_view(grant.value),
        source=grant.source.value,
        source_ref=grant.source_ref,
        bundle_code=grant.bundle_code,
        window=_window_view(grant.window),
        state=grant.state.value,
        precedence=grant.precedence,
        precedence_override=grant.precedence_override,
        reason=grant.reason,
        is_live=grant.is_live,
        is_closed=grant.is_closed,
        is_editable=grant.is_editable,
        allowed_triggers=tuple(t.value for t in allowed),
        created_at=grant.created_at,
        updated_at=grant.updated_at,
        activated_at=grant.activated_at,
        revoked_at=grant.revoked_at,
        revoked_reason=grant.revoked_reason,
    )


def _change_view(change) -> ScheduledEntitlementChangeView:
    return ScheduledEntitlementChangeView(
        id=change.id,
        kind=change.kind.value,
        status=change.status.value,
        effective_at=change.effective_at,
        entitlement_code=change.entitlement_code,
        bundle_code=change.bundle_code,
        grant_id=change.grant_id,
        value=_value_view(change.value),
        source=change.source.value,
        reason=change.reason,
        requested_by=change.requested_by,
        requested_at=change.requested_at,
        applied_at=change.applied_at,
    )


def profile_view(profile: EntitlementProfile, lifecycle=None) -> EntitlementProfileView:
    return EntitlementProfileView(
        id=profile.id,
        account_id=profile.account_id,
        subscription_id=profile.subscription_id,
        version=profile.version,
        max_staleness_seconds=profile.max_staleness_seconds,
        grants=tuple(
            grant_view(g, lifecycle.allowed(g.state) if lifecycle else ()) for g in profile.grants
        ),
        scheduled_changes=tuple(_change_view(c) for c in profile.scheduled_changes),
        codes=profile.codes,
        sources=profile.sources,
        bundle_codes=profile.bundle_codes,
        has_published_snapshot=profile.has_published_snapshot,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _resolved_view(resolved) -> ResolvedEntitlementView:
    return ResolvedEntitlementView(
        code=resolved.code,
        kind=resolved.kind.value,
        enabled=resolved.enabled,
        allowance=resolved.allowance,
        allows_overage=resolved.allows_overage,
        source=resolved.source.value,
        classification=resolved.classification.value,
        grant_id=resolved.grant_id,
        precedence=resolved.precedence,
        from_bundle=resolved.from_bundle,
        is_unlimited=resolved.is_unlimited,
        resolution_path=resolved.resolution_path,
    )


def entitlement_set_view(entitlements: EntitlementSet) -> EntitlementSetView:
    return EntitlementSetView(
        id=entitlements.id,
        account_id=entitlements.account_id,
        version=entitlements.version,
        issued_at=entitlements.issued_at,
        entitlements=tuple(_resolved_view(e) for e in entitlements.entitlements),
        gates=tuple(
            GateView(
                code=g.code,
                kind=g.kind.value,
                classification=g.classification.value,
                meter_code=g.meter_code,
                description=g.description,
            )
            for g in entitlements.gates
        ),
        subscription_id=entitlements.subscription_id,
        max_staleness_seconds=entitlements.max_staleness_seconds,
        grant_count=entitlements.grant_count,
        resolved_from=entitlements.resolved_from,
        codes=entitlements.codes,
        unclassified_gates=entitlements.unclassified_gates,
        fail_closed_codes=entitlements.fail_closed_codes,
    )


def entitlement_version_view(version: EntitlementVersion) -> EntitlementVersionView:
    return EntitlementVersionView(
        id=version.id,
        account_id=version.account_id,
        version=version.version,
        published_at=version.published_at,
        published_by=version.published_by,
        reason=version.reason,
        rolled_back_from=version.rolled_back_from,
        entitlement_count=version.entitlement_count,
        grant_count=version.grant_count,
        codes=version.codes,
    )


def entitlement_activity_view(activity) -> EntitlementActivityView:
    return EntitlementActivityView(
        id=activity.id,
        account_id=activity.account_id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        entitlement_code=activity.entitlement_code,
        grant_id=activity.grant_id,
        from_state=activity.from_state,
        to_state=activity.to_state,
        version=activity.version,
        detail=activity.detail,
    )


def explanation_view(explanation) -> EntitlementExplanationView:
    return EntitlementExplanationView(
        code=explanation.code,
        at=explanation.at,
        winner=_resolved_view(explanation.winner) if explanation.winner else None,
        considered=tuple(
            GrantConsiderationView(
                grant_id=c.grant_id,
                source=c.source.value,
                precedence=c.precedence,
                value=_value_view(c.value),
                effective=c.effective,
                won=c.won,
                excluded_reason=c.excluded_reason,
            )
            for c in explanation.considered
        ),
        definition_known=explanation.definition_known,
        is_granted=explanation.is_granted,
        contested=explanation.contested,
    )


def staleness_view(report) -> StalenessReportView:
    return StalenessReportView(
        account_id=report.account_id,
        version=report.version,
        issued_at=report.issued_at,
        at=report.at,
        age=report.age,
        max_staleness_seconds=report.max_staleness_seconds,
        stale=report.stale,
        directives=tuple(
            GateDirectiveView(
                code=d.code,
                classification=d.classification.value,
                directive=d.directive.value,
                stale=d.stale,
            )
            for d in report.directives
        ),
        denied_codes=report.denied_codes,
        degraded_codes=report.degraded_codes,
    )


def diagnostics_view(account_id: str, diagnostics) -> EntitlementDiagnosticsView:
    return EntitlementDiagnosticsView(
        account_id=account_id,
        findings=tuple(
            EntitlementDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
        error_count=len(diagnostics.errors),
        warning_count=len(diagnostics.warnings),
        is_valid=diagnostics.is_valid,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementRepositories:
    """The entitlement seams, injected. Interfaces only — implementations in persistence."""

    definitions: object
    bundles: object
    profiles: object
    versions: object
    activity: object


#: Which `billing.*` fact each grant lifecycle move publishes, and which activity action records
#: it. A table so "exactly one event per business fact" is checkable rather than asserted in prose.
def _grant_transitions() -> dict:
    from billing.foundation import BillingEventType as E

    return {
        GrantTrigger.ACTIVATE: (E.ENTITLEMENT_GRANT_ACTIVATED, EntitlementAction.ACTIVATED),
        GrantTrigger.SUSPEND: (E.ENTITLEMENT_GRANT_SUSPENDED, EntitlementAction.SUSPENDED),
        GrantTrigger.RESUME: (E.ENTITLEMENT_GRANT_RESUMED, EntitlementAction.RESUMED),
        GrantTrigger.REVOKE: (E.ENTITLEMENT_REVOKED, EntitlementAction.REVOKED),
        GrantTrigger.EXPIRE: (E.ENTITLEMENT_GRANT_EXPIRED, EntitlementAction.EXPIRED),
        GrantTrigger.ARCHIVE: (E.ENTITLEMENT_GRANT_ARCHIVED, EntitlementAction.ARCHIVED),
    }


_GRANT_TRANSITIONS = _grant_transitions()


class EntitlementCommandUseCases:
    """Orchestration for the Entitlement Engine. Coordinates; decides nothing.

    Not one method here resolves precedence, expands a bundle, decides a lifecycle edge, or
    composes a snapshot. Those are `EntitlementPrecedenceResolver`, `EntitlementBundleExpander`,
    the FSM, and `EntitlementAssembler` respectively — and a test greps this module to keep it
    that way."""

    def __init__(
        self,
        *,
        context,
        entitlement_repos: EntitlementRepositories,
        factory,
        lifecycle,
        expander,
        assembler,
        schedules,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = entitlement_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._expander = expander
        self._assembler = assembler
        self._schedules = schedules
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── definitions ─────────────────────────────────────────────────────────────

    def define(self, command: DefineEntitlement) -> EntitlementDefinition:
        """Declaring a gate is a platform act, so it takes the catalog permission rather than
        the commercial one."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            definition = self._factory.define_entitlement(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                kind=command.kind,
                classification=command.classification,
                default=command.default,
                meter_code=command.meter_code,
                max_staleness_seconds=command.max_staleness_seconds,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored = self._repos.definitions.add(definition)
            self._record(
                command,
                "",
                EntitlementAction.DEFINITION_CREATED,
                entitlement_code=stored.code,
                detail=stored.classification.value,
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    code=stored.code,
                    classification=stored.classification.value,
                )
            )
            return stored

    def update_definition(self, command: UpdateEntitlementDefinition) -> EntitlementDefinition:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            definition = self._definition(command.org_id, command.code)
            stored = self._repos.definitions.replace(
                self._factory.update_definition(
                    definition,
                    actor_id=command.actor_id,
                    display_name=command.display_name,
                    classification=command.classification,
                    default=command.default,
                    meter_code=command.meter_code,
                    max_staleness_seconds=command.max_staleness_seconds,
                    description=command.description,
                )
            )
            self._record(
                command,
                "",
                EntitlementAction.DEFINITION_UPDATED,
                entitlement_code=stored.code,
                version=stored.version,
            )
            uow.stage(self._event(command, stored.id, code=stored.code, version=stored.version))
            return stored

    def retire_definition(self, command: RetireEntitlementDefinition) -> EntitlementDefinition:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            definition = self._definition(command.org_id, command.code)
            stored = self._repos.definitions.replace(
                self._factory.retire_definition(
                    definition, reason=command.reason, actor_id=command.actor_id
                )
            )
            self._record(
                command,
                "",
                EntitlementAction.DEFINITION_RETIRED,
                entitlement_code=stored.code,
                detail=command.reason,
            )
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    # ── bundles ─────────────────────────────────────────────────────────────────

    def create_bundle(self, command: CreateEntitlementBundle) -> EntitlementBundle:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            bundle = self._factory.create_bundle(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                entries=command.entries,
                member_bundles=command.member_bundles,
                description=command.description,
                known_codes=self._known_codes(command.org_id),
                known_bundles=self._known_bundles(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.bundles.add(bundle)
            self._record(
                command,
                "",
                EntitlementAction.BUNDLE_CREATED,
                detail=stored.code,
            )
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def update_bundle(self, command: UpdateEntitlementBundle) -> EntitlementBundle:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            bundle = self._bundle(command.org_id, command.code)
            stored = self._repos.bundles.replace(
                self._factory.update_bundle(
                    bundle,
                    entries=command.entries,
                    member_bundles=command.member_bundles,
                    display_name=command.display_name,
                    description=command.description,
                    known_codes=self._known_codes(command.org_id),
                    known_bundles=self._known_bundles(command.org_id),
                    actor_id=command.actor_id,
                )
            )
            self._record(command, "", EntitlementAction.BUNDLE_UPDATED, detail=stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code, version=stored.version))
            return stored

    # ── profiles + grants ───────────────────────────────────────────────────────

    def open_profile(self, command: OpenEntitlementProfile) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._factory.open_profile(
                org_id=command.org_id,
                account_id=command.account_id,
                subscription_id=command.subscription_id,
                max_staleness_seconds=command.max_staleness_seconds,
                actor_id=command.actor_id,
            )
            stored = self._repos.profiles.add(profile)
            self._record(command, stored.account_id, EntitlementAction.PROFILE_OPENED)
            uow.stage(self._event(command, stored.id, account_id=stored.account_id))
            return stored

    def grant(self, command: GrantCommercialEntitlement) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            updated, minted = self._factory.grant(
                profile,
                entitlement_code=command.entitlement_code,
                value=command.value,
                source=command.source,
                source_ref=command.source_ref,
                window=command.window,
                precedence_override=command.precedence_override,
                reason=command.reason,
                definition=self._repos.definitions.by_code(
                    command.org_id, command.entitlement_code
                ),
                known_codes=self._known_codes(command.org_id),
                subscription_state=self._subscription_state(
                    command.org_id, command.source, command.source_ref
                ),
                actor_id=command.actor_id,
            )
            stored = self._repos.profiles.replace(updated)
            self._record(
                command,
                stored.account_id,
                EntitlementAction.GRANTED,
                entitlement_code=minted.entitlement_code,
                grant_id=minted.id,
                to_state=minted.state.value,
            )
            uow.stage(
                self._event(
                    command,
                    minted.id,
                    account_id=stored.account_id,
                    code=minted.entitlement_code,
                    source=minted.source.value,
                )
            )
            return stored

    def grant_bundle(self, command: GrantEntitlementBundleToAccount) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            updated, minted = self._factory.grant_bundle(
                profile,
                self._repos.bundles.all(command.org_id),
                bundle_code=command.bundle_code,
                source=command.source,
                source_ref=command.source_ref,
                window=command.window,
                reason=command.reason,
                known_codes=self._known_codes(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.profiles.replace(updated)
            self._record(
                command,
                stored.account_id,
                EntitlementAction.BUNDLE_GRANTED,
                detail=f"{command.bundle_code}: {len(minted)} grant(s)",
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    account_id=stored.account_id,
                    bundle=command.bundle_code,
                    grants=len(minted),
                )
            )
            return stored

    def activate_grant(self, command: ActivateEntitlementGrant) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.ACTIVATE)

    def suspend_grant(self, command: SuspendEntitlementGrant) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.SUSPEND, reason=command.reason)

    def resume_grant(self, command: ResumeEntitlementGrant) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.RESUME)

    def revoke_grant(self, command: RevokeCommercialEntitlement) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.REVOKE, reason=command.reason)

    def expire_grant(self, command: ExpireEntitlementGrant) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.EXPIRE)

    def archive_grant(self, command: ArchiveEntitlementGrant) -> EntitlementProfile:
        return self._transition(command, GrantTrigger.ARCHIVE)

    def adjust_grant(self, command: AdjustEntitlementGrant) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            existing = profile.grant(command.grant_id)
            if existing is None:
                raise _NotFound(f"entitlement grant {command.grant_id!r} not found")
            self._lifecycle.guard_editable(existing)
            stored = self._repos.profiles.replace(
                self._factory.adjust_grant(
                    profile,
                    command.grant_id,
                    value=command.value,
                    window=command.window,
                    reason=command.reason,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                stored.account_id,
                EntitlementAction.ADJUSTED,
                entitlement_code=existing.entitlement_code,
                grant_id=command.grant_id,
            )
            uow.stage(self._event(command, command.grant_id, account_id=stored.account_id))
            return stored

    # ── scheduled changes ───────────────────────────────────────────────────────

    def schedule_change(self, command: ScheduleEntitlementChange) -> EntitlementProfile:
        """Records intent. Nothing here applies it, now or later."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            stored = self._repos.profiles.replace(
                self._factory.schedule_change(
                    profile,
                    kind=command.kind,
                    effective_at=command.effective_at,
                    entitlement_code=command.entitlement_code,
                    bundle_code=command.bundle_code,
                    grant_id=command.grant_id,
                    value=command.value,
                    source=command.source,
                    source_ref=command.source_ref,
                    reason=command.reason,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                stored.account_id,
                EntitlementAction.CHANGE_SCHEDULED,
                entitlement_code=command.entitlement_code,
                detail=f"{command.kind.value} at {command.effective_at}",
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    kind=command.kind.value,
                    effective_at=command.effective_at,
                )
            )
            return stored

    def cancel_scheduled_change(
        self, command: CancelScheduledEntitlementChange
    ) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            stored = self._repos.profiles.replace(
                self._schedules.cancel_change(profile, command.change_id, at=self._clock.now())
            )
            self._factory.emit_profile_fact(
                stored,
                event_type=_change_cancelled_event(),
                actor_id=command.actor_id,
                change=command.change_id,
            )
            self._record(
                command,
                stored.account_id,
                EntitlementAction.CHANGE_CANCELLED,
                detail=command.change_id,
            )
            uow.stage(self._event(command, stored.id, change=command.change_id))
            return stored

    # ── publishing ──────────────────────────────────────────────────────────────

    def publish(self, command: PublishCommercialEntitlementSnapshot) -> EntitlementVersion:
        """Compose and freeze. The composition is the Foundation's; this method decides only
        which version number it gets and whether it is worth publishing at all."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            version = self._repos.versions.next_version(command.org_id, profile.account_id)
            at = command.at if command.at is not None else self._clock.now()
            composed = self._factory.compose_set(
                profile,
                self._repos.definitions.all(command.org_id),
                version=version,
                at=at,
                include_defaults=command.include_defaults,
            )
            if not command.allow_identical:
                self._refuse_identical(command.org_id, profile.account_id, composed)
            frozen = self._factory.publish_version(
                profile,
                composed,
                version=version,
                reason=command.reason,
                actor_id=command.actor_id,
            )
            stored = self._repos.versions.publish(frozen)
            self._repos.profiles.replace(
                replace(profile, version=version, updated_at=self._clock.now())
            )
            self._record(
                command,
                profile.account_id,
                EntitlementAction.VERSION_PUBLISHED,
                version=version,
                detail=command.reason,
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    account_id=profile.account_id,
                    version=version,
                    entitlements=stored.entitlement_count,
                )
            )
            return stored

    def rollback(self, command: RollbackEntitlementVersion) -> EntitlementVersion:
        """Publish a **new** version carrying an earlier decision. The versions in between remain
        readable forever."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            source = self._repos.versions.get(
                command.org_id, profile.account_id, command.to_version
            )
            if source is None:
                raise _NotFound(
                    f"entitlement version {command.to_version} not found for account "
                    f"{profile.account_id!r}"
                )
            next_version = self._repos.versions.next_version(command.org_id, profile.account_id)
            rolled = self._factory.rollback_version(
                source, version=next_version, reason=command.reason, actor_id=command.actor_id
            )
            stored = self._repos.versions.publish(rolled)
            if rolled.profile is not None:
                self._repos.profiles.replace(rolled.profile)
            self._record(
                command,
                profile.account_id,
                EntitlementAction.ROLLED_BACK,
                version=next_version,
                detail=f"decision restored from v{source.version}",
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    version=next_version,
                    rolled_back_from=source.version,
                )
            )
            return stored

    # ── internals ───────────────────────────────────────────────────────────────

    def _transition(
        self, command, trigger: GrantTrigger, *, reason: str = ""
    ) -> EntitlementProfile:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            profile = self._profile(command.org_id, command.account_id)
            grant = profile.grant(command.grant_id)
            if grant is None:
                raise _NotFound(f"entitlement grant {command.grant_id!r} not found")
            # Legality is the Foundation's FSM decision, not this method's.
            moved = self._lifecycle.apply(grant, trigger, at=self._clock.now(), reason=reason)
            stored = self._repos.profiles.replace(self._factory.replace_grant(profile, moved))
            event_type, action = _GRANT_TRANSITIONS[trigger]
            self._factory.emit_transition(
                moved,
                event_type=event_type,
                actor_id=command.actor_id,
                account_id=stored.account_id,
                code=moved.entitlement_code,
                state=moved.state.value,
            )
            self._record(
                command,
                stored.account_id,
                action,
                entitlement_code=moved.entitlement_code,
                grant_id=moved.id,
                from_state=grant.state.value,
                to_state=moved.state.value,
                detail=reason,
            )
            uow.stage(
                self._event(command, moved.id, code=moved.entitlement_code, state=moved.state.value)
            )
            return stored

    def _refuse_identical(self, org_id: str, account_id: str, candidate: EntitlementSet) -> None:
        """Decline to mint a version that would restate the previous one verbatim.

        The comparison is the assembler's (`says_the_same_as`); the decision to refuse is a
        policy this use case applies, and a caller that genuinely wants an audit boundary passes
        `allow_identical`."""
        previous = self._repos.versions.latest(org_id, account_id)
        if previous is None or previous.entitlements is None:
            return
        if self._assembler.says_the_same_as(candidate, previous.entitlements):
            from billing.foundation import BillingConflict

            raise BillingConflict(
                f"entitlements for account {account_id!r} are unchanged since v{previous.version}; "
                "pass allow_identical to publish an audit boundary anyway"
            )

    def _profile(self, org_id: str, account_id: str) -> EntitlementProfile:
        found = self._repos.profiles.for_account(org_id, account_id) or self._repos.profiles.get(
            org_id, account_id
        )
        if found is None:
            raise _NotFound(f"no entitlement profile for account {account_id!r}")
        return found

    def _definition(self, org_id: str, code: str) -> EntitlementDefinition:
        found = self._repos.definitions.by_code(org_id, code) or self._repos.definitions.get(
            org_id, code
        )
        if found is None:
            raise _NotFound(f"entitlement {code!r} is not defined")
        return found

    def _bundle(self, org_id: str, code: str) -> EntitlementBundle:
        found = self._repos.bundles.by_code(org_id, code) or self._repos.bundles.get(org_id, code)
        if found is None:
            raise _NotFound(f"entitlement bundle {code!r} not found")
        return found

    def _known_codes(self, org_id: str) -> frozenset[str]:
        return frozenset(d.code for d in self._repos.definitions.all(org_id))

    def _known_bundles(self, org_id: str) -> frozenset[str]:
        return frozenset(b.code for b in self._repos.bundles.all(org_id))

    def _subscription_state(self, org_id: str, source: GrantSource, source_ref: str) -> str | None:
        """The referenced agreement's state as a **string**, or `None` when the check does not
        apply.

        Only `PLAN`-sourced grants are checked: a `source_ref` on a contract, partner, or
        marketplace grant names a document this domain knows nothing about, and treating every
        reference as a subscription id would reject perfectly valid grants. Passed as a string
        deliberately — this domain refers to a subscription and refuses to import its enum,
        which keeps the dependency one-way and shallow (the call M9.9 made about accounts)."""
        subscriptions = getattr(self._ctx, "subscriptions_engine", None)
        if subscriptions is None or source is not GrantSource.PLAN or not source_ref:
            return None
        found = subscriptions.subscriptions.get(org_id, source_ref)
        return "" if found is None else found.state.value

    def _record(
        self,
        command,
        account_id: str,
        action: EntitlementAction,
        *,
        entitlement_code: str = "",
        grant_id: str = "",
        from_state: str = "",
        to_state: str = "",
        version: int = 0,
        detail: str = "",
    ) -> None:
        """Append entitlement history **inside the caller's transaction**, so the history and the
        change it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                account_id=account_id,
                action=action,
                actor_id=command.actor_id,
                entitlement_code=entitlement_code,
                grant_id=grant_id,
                from_state=from_state,
                to_state=to_state,
                version=version,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.ENTITLEMENTS_REPUBLISHED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _change_cancelled_event():
    from billing.foundation import BillingEventType as E

    return E.ENTITLEMENT_CHANGE_CANCELLED


class EntitlementQueryUseCases:
    """Read-only. No transaction, no audit, no mutation — and no permission evaluation: these
    methods say *what* an account is entitled to, never *who* may act."""

    def __init__(
        self,
        *,
        context,
        entitlement_repos: EntitlementRepositories,
        factory,
        lifecycle,
        expander,
        assembler,
        resolver,
        inspector,
        staleness,
        schedules,
        search,
        authz,
    ) -> None:
        self._ctx = context
        self._repos = entitlement_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._expander = expander
        self._assembler = assembler
        self._resolver = resolver
        self._inspector = inspector
        self._staleness = staleness
        self._schedules = schedules
        self._search = search
        self._authz = authz

    def profile(self, query: GetEntitlementProfile) -> EntitlementProfileView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.profiles.for_account(query.org_id, query.account_id)
        return None if found is None else profile_view(found, self._lifecycle)

    def snapshot(self, query: GetCommercialEntitlementSnapshot) -> EntitlementSetView | None:
        """The published decision — never a recomputation. A snapshot an enforcing plane acted
        on must read back exactly as it was published, and recomputing it here would quietly
        answer a different question."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        version = (
            self._repos.versions.get(query.org_id, query.account_id, query.version)
            if query.version is not None
            else self._repos.versions.latest(query.org_id, query.account_id)
        )
        if version is None or version.entitlements is None:
            return None
        return entitlement_set_view(version.entitlements)

    def preview(self, query: PreviewEntitlementSnapshot) -> EntitlementSetView | None:
        """What *would* be published. Composition is pure, so this changes nothing."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.profiles.for_account(query.org_id, query.account_id)
        if found is None:
            return None
        composed = self._factory.compose_set(
            found,
            self._repos.definitions.all(query.org_id),
            version=max(found.version, 1),
            at=query.at,
            include_defaults=query.include_defaults,
        )
        return entitlement_set_view(composed)

    def versions(self, query: EntitlementVersionHistory) -> tuple[EntitlementVersionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            entitlement_version_view(v)
            for v in self._repos.versions.history(query.org_id, query.account_id, query.limit)
        )

    def timeline(self, query: EntitlementTimeline) -> tuple[EntitlementActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            entitlement_activity_view(a)
            for a in self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )

    def search(self, query: SearchEntitlementGrants) -> EntitlementSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        grants: list[CommercialGrant] = []
        for profile in self._repos.profiles.all(query.org_id):
            grants.extend(profile.grants)
        result = self._search.search(tuple(grants), query.query)
        return EntitlementSearchView(
            grants=tuple(grant_view(g, self._lifecycle.allowed(g.state)) for g in result.grants),
            total=result.total,
        )

    def inspect(self, query: InspectEntitlement) -> EntitlementExplanationView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.profiles.for_account(query.org_id, query.account_id)
        if found is None:
            return None
        return explanation_view(
            self._inspector.explain(
                found,
                self._repos.definitions.all(query.org_id),
                query.entitlement_code,
                at=query.at if query.at is not None else found.updated_at,
            )
        )

    def diagnose(self, query: DiagnoseEntitlements) -> EntitlementDiagnosticsView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.profiles.for_account(query.org_id, query.account_id)
        if found is None:
            return None
        return diagnostics_view(
            query.account_id,
            self._inspector.diagnose(
                found,
                self._repos.definitions.all(query.org_id),
                at=query.at if query.at is not None else found.updated_at,
            ),
        )

    def staleness(self, query: GetEntitlementStaleness) -> StalenessReportView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        version = self._repos.versions.latest(query.org_id, query.account_id)
        if version is None or version.entitlements is None:
            return None
        return staleness_view(self._staleness.report(version.entitlements, at=query.at))

    def definitions(
        self, query: ListEntitlementDefinitions
    ) -> tuple[EntitlementDefinitionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.definitions.all(query.org_id)
        return tuple(definition_view(d) for d in found if query.include_retired or not d.is_retired)

    def bundles(self, query: ListEntitlementBundles) -> tuple[EntitlementBundleView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.bundles.all(query.org_id)
        return tuple(bundle_view(b, self._expanded(found, b.code)) for b in found)

    def scheduled_changes(
        self, query: ListScheduledEntitlementChanges
    ) -> tuple[ScheduledEntitlementChangeView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.profiles.for_account(query.org_id, query.account_id)
        if found is None:
            return ()
        changes = (
            self._schedules.due(found, at=query.due_at)
            if query.due_at is not None
            else found.pending_changes
        )
        return tuple(_change_view(c) for c in changes)

    def _expanded(self, bundles, code: str) -> tuple[str, ...]:
        """Expansion can legitimately fail on a cycle. A listing screen should show the bundles
        it has rather than 500 because one of them is malformed — the diagnostics surface is
        where a broken bundle is reported."""
        try:
            return self._expander.codes(bundles, code)
        except Exception:  # noqa: BLE001 — see docstring
            return ()


# ── wiring table ────────────────────────────────────────────────────────────────


def register_entitlement_command_handlers(bus, uc: EntitlementCommandUseCases) -> None:
    bus.register(DefineEntitlement, uc.define)
    bus.register(UpdateEntitlementDefinition, uc.update_definition)
    bus.register(RetireEntitlementDefinition, uc.retire_definition)
    bus.register(CreateEntitlementBundle, uc.create_bundle)
    bus.register(UpdateEntitlementBundle, uc.update_bundle)
    bus.register(OpenEntitlementProfile, uc.open_profile)
    bus.register(GrantCommercialEntitlement, uc.grant)
    bus.register(GrantEntitlementBundleToAccount, uc.grant_bundle)
    bus.register(ActivateEntitlementGrant, uc.activate_grant)
    bus.register(AdjustEntitlementGrant, uc.adjust_grant)
    bus.register(SuspendEntitlementGrant, uc.suspend_grant)
    bus.register(ResumeEntitlementGrant, uc.resume_grant)
    bus.register(RevokeCommercialEntitlement, uc.revoke_grant)
    bus.register(ExpireEntitlementGrant, uc.expire_grant)
    bus.register(ArchiveEntitlementGrant, uc.archive_grant)
    bus.register(ScheduleEntitlementChange, uc.schedule_change)
    bus.register(CancelScheduledEntitlementChange, uc.cancel_scheduled_change)
    bus.register(PublishCommercialEntitlementSnapshot, uc.publish)
    bus.register(RollbackEntitlementVersion, uc.rollback)


def register_entitlement_query_handlers(bus, uc: EntitlementQueryUseCases) -> None:
    bus.register(GetEntitlementProfile, uc.profile)
    bus.register(GetCommercialEntitlementSnapshot, uc.snapshot)
    bus.register(PreviewEntitlementSnapshot, uc.preview)
    bus.register(EntitlementVersionHistory, uc.versions)
    bus.register(EntitlementTimeline, uc.timeline)
    bus.register(SearchEntitlementGrants, uc.search)
    bus.register(InspectEntitlement, uc.inspect)
    bus.register(DiagnoseEntitlements, uc.diagnose)
    bus.register(GetEntitlementStaleness, uc.staleness)
    bus.register(ListEntitlementDefinitions, uc.definitions)
    bus.register(ListEntitlementBundles, uc.bundles)
    bus.register(ListScheduledEntitlementChanges, uc.scheduled_changes)
