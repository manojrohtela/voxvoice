"""Entitlement routes (M9.5) — transport only.

**Billing decides entitlement; it never enforces it.** These endpoints publish an immutable,
versioned snapshot and answer a quota question. The plane that owns the gated resource
enforces locally against the snapshot it holds, using the `classification` this API returns
(fail_open | fail_closed | degrade) when that snapshot is stale (ADR-0023 §11).

The quota verdict is therefore *information*, not permission: a caller that treats a 200 here
as an authorization has misread the contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    GetEntitlementSnapshot,
    GetQuotaVerdict,
    GrantEntitlement,
    PublishEntitlementSnapshot,
    RevokeEntitlement,
)
from billing.foundation import BillingNotFound

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import (
    ERROR_RESPONSES,
    EntitlementSnapshotResponse,
    GrantEntitlementBody,
    PublishSnapshotBody,
    QuotaVerdictResponse,
)

router = APIRouter(prefix="/entitlements", tags=["entitlements"], responses=ERROR_RESPONSES)


@router.post("/grants", status_code=201, summary="Grant an entitlement")
def grant_entitlement(
    body: GrantEntitlementBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    grant = app.commands.dispatch(
        GrantEntitlement(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            entitlement=body.entitlement.to_entitlement(),
            source_ref=body.source_ref,
        )
    )
    return {"id": grant.id, "code": grant.entitlement.code, "source": grant.source.value}


@router.delete("/grants/{grant_id}", summary="Revoke an entitlement grant")
def revoke_entitlement(
    grant_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """Revocation is recorded, never deleted — the grant and its revocation time both
    survive, because *why* an entitlement went away is itself a fact."""
    grant = app.commands.dispatch(
        RevokeEntitlement(actor_id=ctx.actor(), org_id=ctx.organization(), grant_id=grant_id)
    )
    return {"id": grant.id, "revoked_at": grant.revoked_at}


@router.post(
    "/snapshots",
    response_model=EntitlementSnapshotResponse,
    status_code=201,
    summary="Publish an entitlement snapshot",
)
def publish_snapshot(
    body: PublishSnapshotBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EntitlementSnapshotResponse:
    snapshot = app.commands.dispatch(
        PublishEntitlementSnapshot(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            gates=body.to_gates(),
            subscription_id=body.subscription_id,
            max_staleness_seconds=body.max_staleness_seconds,
        )
    )
    view = app.queries.dispatch(
        GetEntitlementSnapshot(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=body.account_id
        )
    )
    if view is None:  # pragma: no cover — a just-published snapshot is always readable
        raise BillingNotFound(f"snapshot for account {body.account_id!r} not found")
    assert snapshot.version == view.version
    return EntitlementSnapshotResponse.from_view(view)


@router.get(
    "/snapshots/{account_id}",
    response_model=EntitlementSnapshotResponse,
    summary="Get the latest entitlement snapshot",
)
def get_snapshot(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EntitlementSnapshotResponse:
    view = app.queries.dispatch(
        GetEntitlementSnapshot(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
        )
    )
    if view is None:
        raise BillingNotFound(f"no entitlement snapshot for account {account_id!r}")
    return EntitlementSnapshotResponse.from_view(view)


@router.get(
    "/quota/{account_id}/{entitlement_code}",
    response_model=QuotaVerdictResponse,
    summary="Ask whether consumption is within allowance (information, not permission)",
)
def quota_verdict(
    account_id: str,
    entitlement_code: str,
    consumed: int,
    at: float,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> QuotaVerdictResponse:
    view = app.queries.dispatch(
        GetQuotaVerdict(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            entitlement_code=entitlement_code,
            consumed=consumed,
            at=at,
        )
    )
    return QuotaVerdictResponse.from_view(view)
