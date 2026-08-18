"""Entitlement Engine routes (M9.10) — transport only.

Additive to the M9.5–M9.9 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No entitlement rule is
evaluated here — gate validity, bundle expansion, precedence, window coverage, snapshot
composition, and staleness directives are all Foundation behaviour reached through the Application.

Mounted at **`/capabilities`**, not under M9.5's `/entitlements`. Same reason as M9.8's
`/billing-accounts` and M9.9's `/agreements`: `/entitlements/grants/{grant_id}` and
`/entitlements/snapshots/{account_id}` are existing wildcards, and siblings under them would be
shadowed depending on router registration order. A separate prefix makes the two surfaces
independent of each other's ordering — a correctness property, not a naming preference. The M9.5
routes are untouched.

The prefix is ADR-0023's own word for this domain: entitlement is *commercial capability*, not
authorization (§11.3).

**Two things no endpoint here does.** None evaluates a permission — a 200 from
`/capabilities/profiles/{id}/snapshot` states what an account is entitled to and is not an
authorization; the plane that owns the gated resource enforces locally against the snapshot,
using the `classification` this API returns when its copy is stale. And none applies a scheduled
change: scheduling records intent, and a caller dispatches the real command when the time comes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ActivateEntitlementGrant,
    AdjustEntitlementGrant,
    ArchiveEntitlementGrant,
    BillingApplication,
    CancelScheduledEntitlementChange,
    CreateEntitlementBundle,
    DefineEntitlement,
    DiagnoseEntitlements,
    EntitlementTimeline,
    EntitlementVersionHistory,
    ExpireEntitlementGrant,
    GetCommercialEntitlementSnapshot,
    GetEntitlementProfile,
    GetEntitlementStaleness,
    GrantCommercialEntitlement,
    GrantEntitlementBundleToAccount,
    InspectEntitlement,
    ListEntitlementBundles,
    ListEntitlementDefinitions,
    ListScheduledEntitlementChanges,
    OpenEntitlementProfile,
    PreviewEntitlementSnapshot,
    PublishCommercialEntitlementSnapshot,
    ResumeEntitlementGrant,
    RetireEntitlementDefinition,
    RevokeCommercialEntitlement,
    RollbackEntitlementVersion,
    ScheduleEntitlementChange,
    SearchEntitlementGrants,
    SuspendEntitlementGrant,
    UpdateEntitlementBundle,
    UpdateEntitlementDefinition,
    bundle_view,
    definition_view,
    entitlement_version_view,
)
from billing.foundation import BillingNotFound, BundleEntry, GrantSource, GrantState

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..entitlement_schemas import (
    ActivityResponse,
    AdjustGrantBody,
    BundleResponse,
    CreateBundleBody,
    DefineEntitlementBody,
    DefinitionResponse,
    DiagnosticsResponse,
    ExplanationResponse,
    GrantBody,
    GrantBundleBody,
    GrantSearchResponse,
    OpenProfileBody,
    ProfileResponse,
    PublishSnapshotBody,
    ReasonBody,
    RollbackBody,
    ScheduleChangeBody,
    ScheduledChangeResponse,
    SnapshotResponse,
    StalenessResponse,
    UpdateBundleBody,
    UpdateEntitlementDefinitionBody,
    VersionResponse,
    build_grant_query,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/capabilities", tags=["entitlements"], responses=ERROR_RESPONSES)


def _profile(app: BillingApplication, ctx: BillingRequestContext, account_id: str):
    """Re-read through the query surface after a command.

    A command returns the aggregate, but the response also carries `allowed_triggers` — which
    grant lifecycle moves are legal next — and that is a Foundation decision. Reading it back
    keeps the computation below this layer instead of importing the state machine into a
    controller."""
    view = app.queries.dispatch(
        GetEntitlementProfile(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
        )
    )
    if view is None:  # pragma: no cover — a just-written profile is always readable
        raise BillingNotFound(f"no entitlement profile for account {account_id!r}")
    return ProfileResponse.from_view(view)


def _entries(bodies) -> tuple[BundleEntry, ...]:
    return tuple(BundleEntry(code=b.code, value=b.value.to_value()) for b in bodies)


# ── the gate registry ───────────────────────────────────────────────────────────


@router.post(
    "/definitions",
    response_model=DefinitionResponse,
    status_code=201,
    summary="Declare a capability and its fail-open / fail-closed classification",
)
def define_entitlement(
    body: DefineEntitlementBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DefinitionResponse:
    """The classification is what an enforcing plane falls back on during a Billing outage, so
    ADR-0023 E3 makes declaring it part of declaring the gate."""
    definition = app.commands.dispatch(
        DefineEntitlement(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            classification=body.classification,
            default=body.default.to_value(),
            meter_code=body.meter_code,
            max_staleness_seconds=body.max_staleness_seconds,
            description=body.description,
        )
    )
    return DefinitionResponse.from_view(definition_view(definition))


@router.get(
    "/definitions",
    response_model=list[DefinitionResponse],
    summary="List declared capabilities",
)
def list_definitions(
    include_retired: bool = Query(default=False),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[DefinitionResponse]:
    views = app.queries.dispatch(
        ListEntitlementDefinitions(
            actor_id=ctx.actor(), org_id=ctx.organization(), include_retired=include_retired
        )
    )
    return [DefinitionResponse.from_view(v) for v in views]


@router.patch(
    "/definitions/{code}",
    response_model=DefinitionResponse,
    summary="Amend a declaration (publishes a new declaration version)",
)
def update_definition(
    code: str,
    body: UpdateEntitlementDefinitionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DefinitionResponse:
    definition = app.commands.dispatch(
        UpdateEntitlementDefinition(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=code,
            display_name=body.display_name,
            classification=body.classification,
            default=body.default.to_value() if body.default else None,
            meter_code=body.meter_code,
            max_staleness_seconds=body.max_staleness_seconds,
            description=body.description,
        )
    )
    return DefinitionResponse.from_view(definition_view(definition))


@router.post(
    "/definitions/{code}/retire",
    response_model=DefinitionResponse,
    summary="Retire a declaration without withdrawing it from anyone who holds it",
)
def retire_definition(
    code: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DefinitionResponse:
    """Retirement is not revocation. Withdrawing a capability from customers who hold it is a
    separate, per-grant act — conflating the two would let a registry cleanup cancel a contract."""
    definition = app.commands.dispatch(
        RetireEntitlementDefinition(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=code,
            reason=body.reason,
        )
    )
    return DefinitionResponse.from_view(definition_view(definition))


# ── bundles ─────────────────────────────────────────────────────────────────────


@router.post("/bundles", response_model=BundleResponse, status_code=201, summary="Create a bundle")
def create_bundle(
    body: CreateBundleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BundleResponse:
    bundle = app.commands.dispatch(
        CreateEntitlementBundle(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            entries=_entries(body.entries),
            member_bundles=tuple(body.member_bundles),
            description=body.description,
        )
    )
    return BundleResponse.from_view(bundle_view(bundle))


@router.get("/bundles", response_model=list[BundleResponse], summary="List bundles")
def list_bundles(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[BundleResponse]:
    views = app.queries.dispatch(
        ListEntitlementBundles(actor_id=ctx.actor(), org_id=ctx.organization())
    )
    return [BundleResponse.from_view(v) for v in views]


@router.patch("/bundles/{code}", response_model=BundleResponse, summary="Amend a bundle")
def update_bundle(
    code: str,
    body: UpdateBundleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BundleResponse:
    bundle = app.commands.dispatch(
        UpdateEntitlementBundle(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=code,
            entries=_entries(body.entries) if body.entries is not None else None,
            member_bundles=(
                tuple(body.member_bundles) if body.member_bundles is not None else None
            ),
            display_name=body.display_name,
            description=body.description,
        )
    )
    return BundleResponse.from_view(bundle_view(bundle))


# ── profiles ────────────────────────────────────────────────────────────────────


@router.post(
    "/profiles",
    response_model=ProfileResponse,
    status_code=201,
    summary="Open an account's entitlement position",
)
def open_profile(
    body: OpenProfileBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        OpenEntitlementProfile(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            subscription_id=body.subscription_id,
            max_staleness_seconds=body.max_staleness_seconds,
        )
    )
    return _profile(app, ctx, body.account_id)


@router.get(
    "/profiles/{account_id}",
    response_model=ProfileResponse,
    summary="Read an account's grants and pending intentions",
)
def get_profile(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    view = app.queries.dispatch(
        GetEntitlementProfile(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
        )
    )
    if view is None:
        raise BillingNotFound(f"no entitlement profile for account {account_id!r}")
    return ProfileResponse.from_view(view)


# ── grants ──────────────────────────────────────────────────────────────────────


@router.post(
    "/profiles/{account_id}/grants",
    response_model=ProfileResponse,
    status_code=201,
    summary="Confer a capability (starts as a draft grant)",
)
def grant(
    account_id: str,
    body: GrantBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        GrantCommercialEntitlement(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            entitlement_code=body.entitlement_code,
            value=body.value.to_value(),
            source=body.source,
            source_ref=body.source_ref,
            window=body.window.to_window(),
            precedence_override=body.precedence_override,
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/bundle-grants",
    response_model=ProfileResponse,
    status_code=201,
    summary="Confer a whole bundle (one grant per expanded entry)",
)
def grant_bundle(
    account_id: str,
    body: GrantBundleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        GrantEntitlementBundleToAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            bundle_code=body.bundle_code,
            source=body.source,
            source_ref=body.source_ref,
            window=body.window.to_window(),
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.patch(
    "/profiles/{account_id}/grants/{grant_id}",
    response_model=ProfileResponse,
    summary="Amend a grant's value or window",
)
def adjust_grant(
    account_id: str,
    grant_id: str,
    body: AdjustGrantBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        AdjustEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
            value=body.value.to_value() if body.value else None,
            window=body.window.to_window() if body.window else None,
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/activate",
    response_model=ProfileResponse,
    summary="Activate a grant",
)
def activate_grant(
    account_id: str,
    grant_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        ActivateEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/suspend",
    response_model=ProfileResponse,
    summary="Suspend a grant (reversible; confers nothing while suspended)",
)
def suspend_grant(
    account_id: str,
    grant_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        SuspendEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/resume",
    response_model=ProfileResponse,
    summary="Resume a suspended grant",
)
def resume_grant(
    account_id: str,
    grant_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        ResumeEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/revoke",
    response_model=ProfileResponse,
    summary="Revoke a grant (recorded, never deleted)",
)
def revoke_grant(
    account_id: str,
    grant_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    """Revocation closes the grant's window as well as its state, so a snapshot composed for a
    later instant cannot re-admit it through effective dating. The grant, its reason, and its
    revocation time all survive — *why* a capability went away is itself a fact."""
    app.commands.dispatch(
        RevokeCommercialEntitlement(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/expire",
    response_model=ProfileResponse,
    summary="Record that a grant has expired",
)
def expire_grant(
    account_id: str,
    grant_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        ExpireEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
        )
    )
    return _profile(app, ctx, account_id)


@router.post(
    "/profiles/{account_id}/grants/{grant_id}/archive",
    response_model=ProfileResponse,
    summary="Archive a closed grant",
)
def archive_grant(
    account_id: str,
    grant_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        ArchiveEntitlementGrant(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            grant_id=grant_id,
        )
    )
    return _profile(app, ctx, account_id)


@router.get(
    "/grants",
    response_model=GrantSearchResponse,
    summary="Search grants (deterministic filtering)",
)
def search_grants(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    codes: list[str] = Query(default=[]),
    states: list[GrantState] = Query(default=[]),
    sources: list[GrantSource] = Query(default=[]),
    account_id: str = Query(default=""),
    bundle_code: str = Query(default=""),
    source_ref: str = Query(default=""),
    effective_at: float | None = Query(default=None),
    live_only: bool = Query(default=False),
    bundled_only: bool = Query(default=False),
    unlimited_only: bool = Query(default=False),
    denials_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> GrantSearchResponse:
    view = app.queries.dispatch(
        SearchEntitlementGrants(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_grant_query(
                text=text,
                codes=codes,
                states=states,
                sources=sources,
                account_id=account_id,
                bundle_code=bundle_code,
                source_ref=source_ref,
                effective_at=effective_at,
                live_only=live_only,
                bundled_only=bundled_only,
                unlimited_only=unlimited_only,
                denials_only=denials_only,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return GrantSearchResponse.from_view(view)


# ── scheduled changes ───────────────────────────────────────────────────────────


@router.post(
    "/profiles/{account_id}/scheduled-changes",
    response_model=ProfileResponse,
    status_code=201,
    summary="Record an intention to change entitlement later (nothing fires it)",
)
def schedule_change(
    account_id: str,
    body: ScheduleChangeBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    app.commands.dispatch(
        ScheduleEntitlementChange(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            kind=body.kind,
            effective_at=body.effective_at,
            entitlement_code=body.entitlement_code,
            bundle_code=body.bundle_code,
            grant_id=body.grant_id,
            value=body.value.to_value(),
            source=body.source,
            source_ref=body.source_ref,
            reason=body.reason,
        )
    )
    return _profile(app, ctx, account_id)


@router.get(
    "/profiles/{account_id}/scheduled-changes",
    response_model=list[ScheduledChangeResponse],
    summary="List pending intentions (or those due by an instant)",
)
def list_scheduled_changes(
    account_id: str,
    due_at: float | None = Query(default=None),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ScheduledChangeResponse]:
    """`due_at` answers "what *would* apply then?". Reading it applies nothing — there is no
    scheduler in this domain."""
    views = app.queries.dispatch(
        ListScheduledEntitlementChanges(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            due_at=due_at,
        )
    )
    return [ScheduledChangeResponse.from_view(v) for v in views]


@router.delete(
    "/profiles/{account_id}/scheduled-changes/{change_id}",
    response_model=ProfileResponse,
    summary="Cancel a pending intention",
)
def cancel_scheduled_change(
    account_id: str,
    change_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProfileResponse:
    """One of only two deletions in this surface, and it removes an *intention* that was never
    acted on — not a grant, a decision, or a version."""
    app.commands.dispatch(
        CancelScheduledEntitlementChange(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            change_id=change_id,
        )
    )
    return _profile(app, ctx, account_id)


# ── snapshots ───────────────────────────────────────────────────────────────────


@router.post(
    "/profiles/{account_id}/snapshots",
    response_model=VersionResponse,
    status_code=201,
    summary="Compose and publish an immutable entitlement snapshot",
)
def publish_snapshot(
    account_id: str,
    body: PublishSnapshotBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> VersionResponse:
    version = app.commands.dispatch(
        PublishCommercialEntitlementSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            reason=body.reason,
            at=body.at,
            include_defaults=body.include_defaults,
            allow_identical=body.allow_identical,
        )
    )
    return VersionResponse.from_view(entitlement_version_view(version))


@router.get(
    "/profiles/{account_id}/snapshot",
    response_model=SnapshotResponse,
    summary="The published decision an enforcing plane reads (information, not permission)",
)
def get_snapshot(
    account_id: str,
    version: int | None = Query(default=None, description="Null = latest"),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SnapshotResponse:
    """Returned as published, never recomputed: a snapshot an enforcing plane acted on must read
    back exactly as it was, and recomputing it here would quietly answer a different question."""
    view = app.queries.dispatch(
        GetCommercialEntitlementSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            version=version,
        )
    )
    if view is None:
        raise BillingNotFound(f"no published entitlement snapshot for account {account_id!r}")
    return SnapshotResponse.from_view(view)


@router.get(
    "/profiles/{account_id}/preview",
    response_model=SnapshotResponse,
    summary="What would be published right now, without publishing it",
)
def preview_snapshot(
    account_id: str,
    at: float | None = Query(default=None),
    include_defaults: bool = Query(default=False),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SnapshotResponse:
    view = app.queries.dispatch(
        PreviewEntitlementSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            at=at,
            include_defaults=include_defaults,
        )
    )
    if view is None:
        raise BillingNotFound(f"no entitlement profile for account {account_id!r}")
    return SnapshotResponse.from_view(view)


@router.get(
    "/profiles/{account_id}/versions",
    response_model=list[VersionResponse],
    summary="The version line — every decision, in order",
)
def list_versions(
    account_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[VersionResponse]:
    views = app.queries.dispatch(
        EntitlementVersionHistory(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id, limit=limit
        )
    )
    return [VersionResponse.from_view(v) for v in views]


@router.post(
    "/profiles/{account_id}/rollback",
    response_model=VersionResponse,
    status_code=201,
    summary="Publish a new version carrying an earlier decision",
)
def rollback(
    account_id: str,
    body: RollbackBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> VersionResponse:
    """Rollback moves *forward*. The versions in between remain readable forever — an enforcing
    plane may have acted on one of them."""
    version = app.commands.dispatch(
        RollbackEntitlementVersion(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            to_version=body.to_version,
            reason=body.reason,
        )
    )
    return VersionResponse.from_view(entitlement_version_view(version))


# ── inspection, diagnostics, staleness ──────────────────────────────────────────


@router.get(
    "/profiles/{account_id}/inspect/{entitlement_code}",
    response_model=ExplanationResponse,
    summary="Why this account does — or does not — have this capability",
)
def inspect(
    account_id: str,
    entitlement_code: str,
    at: float | None = Query(default=None),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ExplanationResponse:
    view = app.queries.dispatch(
        InspectEntitlement(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            entitlement_code=entitlement_code,
            at=at,
        )
    )
    if view is None:
        raise BillingNotFound(f"no entitlement profile for account {account_id!r}")
    return ExplanationResponse.from_view(view)


@router.get(
    "/profiles/{account_id}/diagnostics",
    response_model=DiagnosticsResponse,
    summary="Everything structurally wrong with this account's entitlement position",
)
def diagnostics(
    account_id: str,
    at: float | None = Query(default=None),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DiagnosticsResponse:
    """Reports *all* findings rather than the first — a screen that shows one problem at a time
    turns one fix into five visits."""
    view = app.queries.dispatch(
        DiagnoseEntitlements(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id, at=at
        )
    )
    if view is None:
        raise BillingNotFound(f"no entitlement profile for account {account_id!r}")
    return DiagnosticsResponse.from_view(view)


@router.get(
    "/profiles/{account_id}/staleness",
    response_model=StalenessResponse,
    summary="What an enforcing plane should do with each gate right now",
)
def staleness(
    account_id: str,
    at: float = Query(description="The instant to evaluate staleness at"),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> StalenessResponse:
    """The directive comes from each gate's declared classification, so every enforcing plane
    gets the same answer instead of each doing its own subtraction."""
    view = app.queries.dispatch(
        GetEntitlementStaleness(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id, at=at
        )
    )
    if view is None:
        raise BillingNotFound(f"no published entitlement snapshot for account {account_id!r}")
    return StalenessResponse.from_view(view)


@router.get(
    "/profiles/{account_id}/timeline",
    response_model=list[ActivityResponse],
    summary="The account's entitlement history — what happened, in order",
)
def timeline(
    account_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ActivityResponse]:
    views = app.queries.dispatch(
        EntitlementTimeline(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id, limit=limit
        )
    )
    return [ActivityResponse.from_view(v) for v in views]
