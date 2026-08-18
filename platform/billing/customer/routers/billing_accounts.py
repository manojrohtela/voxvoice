"""Billing Account routes (M9.8) — transport only.

Additive to the M9.5–M9.7 transport layer and identical in discipline: parse, take identity
from the verified token, construct a command or query, dispatch, serialize. No account rule is
evaluated here — number and identifier uniqueness, hierarchy legality, cycle and payer-loop
detection, address and contact consistency, credit limits, and lifecycle legality are all
Foundation behaviour reached through the Application.

Mounted at **`/billing-accounts`**, not under M9.5's `/accounts`. That is deliberate rather
than cosmetic: `/accounts` already owns `GET /accounts/{account_id}`, and any sibling path
under it would be shadowed by that wildcard depending on router registration order. A separate
prefix makes the two surfaces independent of each other's ordering — a correctness property,
not a naming preference. The M9.5 routes are untouched.

**There is no DELETE on an active account, at any path.** Revenue outlives the customer record
(ADR-0023 RS4); the only deletion this surface offers is of a draft, which has billed nobody.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AccountHistory,
    ActivateBillingAccount,
    AddAccountNote,
    ArchiveBillingAccount,
    AssignParent,
    AssignPayer,
    BillingApplication,
    CloseCommercialAccount,
    CommercialProfile,
    CreateBillingAccount,
    DeleteDraftAccount,
    GetBillingAccount,
    ListHierarchy,
    RestoreBillingAccount,
    SearchBillingAccounts,
    SuspendBillingAccount,
    UpdateAddresses,
    UpdateBillingPreferences,
    UpdateContacts,
    UpdateCreditProfile,
)
from billing.foundation import (
    AccountKind,
    AccountState,
    BillingNotFound,
    CommercialStatus,
    Currency,
    RiskRating,
)

from ..account_schemas import (
    AccountActivityResponse,
    AccountSearchResponse,
    AddNoteBody,
    AssignParentBody,
    AssignPayerBody,
    BillingAccountResponse,
    CloseAccountReasonBody,
    CommercialProfileResponse,
    CreateAccountBody,
    HierarchyResponse,
    PreferencesBody,
    SuspendAccountBody,
    UpdateAddressesBody,
    UpdateContactsBody,
    UpdateCreditBody,
    build_account_query,
)
from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/billing-accounts", tags=["accounts"], responses=ERROR_RESPONSES)


def _account_response(app: BillingApplication, ctx: BillingRequestContext, identifier: str):
    """Re-read the account through the query surface after a command.

    A command returns the aggregate, but the response also carries `allowed_triggers` — which
    lifecycle moves are legal next — and that is a Foundation decision. Reading it back keeps
    the computation below this layer instead of importing the state machine into a
    controller."""
    view = app.queries.dispatch(
        GetBillingAccount(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier)
    )
    if view is None:  # pragma: no cover — a just-written account is always readable
        raise BillingNotFound(f"billing account {identifier!r} not found")
    return BillingAccountResponse.from_view(view)


# ── the account ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=BillingAccountResponse,
    status_code=201,
    summary="Create a draft billing account",
)
def create_account(
    body: CreateAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """Every account starts as a draft — a customer nobody has traded with cannot go live by
    accident, and draft is the only state from which the record can still be deleted."""
    account = app.commands.dispatch(
        CreateBillingAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            display_name=body.display_name,
            currency=body.currency,
            account_number=body.account_number,
            kind=body.kind,
            legal_entity=body.legal_entity.to_entity(),
            parent_account_id=body.parent_account_id,
            payer_account_id=body.payer_account_id,
            contacts=tuple(c.to_contact() for c in body.contacts),
            addresses=tuple(a.to_address() for a in body.addresses),
            preferences=body.preferences.to_preferences(),
            credit=body.credit.to_credit(),
            tax_profile_id=body.tax_profile_id,
        )
    )
    return _account_response(app, ctx, account.id)


@router.get(
    "",
    response_model=AccountSearchResponse,
    summary="Search billing accounts (deterministic filtering)",
)
def search_accounts(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    states: list[AccountState] = Query(default=[]),
    kinds: list[AccountKind] = Query(default=[]),
    statuses: list[CommercialStatus] = Query(default=[]),
    currencies: list[Currency] = Query(default=[]),
    countries: list[str] = Query(default=[]),
    risk_ratings: list[RiskRating] = Query(default=[]),
    parent_account_id: str = Query(default=""),
    payer_account_id: str = Query(default=""),
    roots_only: bool = Query(default=False),
    on_hold_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountSearchResponse:
    """The same accounts and the same query always return the same rows in the same order."""
    view = app.queries.dispatch(
        SearchBillingAccounts(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_account_query(
                text=text,
                states=states,
                kinds=kinds,
                statuses=statuses,
                currencies=currencies,
                countries=countries,
                risk_ratings=risk_ratings,
                parent_account_id=parent_account_id,
                payer_account_id=payer_account_id,
                roots_only=roots_only,
                on_hold_only=on_hold_only,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return AccountSearchResponse.from_view(view)


@router.get(
    "/{identifier}",
    response_model=BillingAccountResponse,
    summary="Get a billing account by id or account number",
)
def get_account(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    view = app.queries.dispatch(
        GetBillingAccount(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier)
    )
    if view is None:
        raise BillingNotFound(f"billing account {identifier!r} not found")
    return BillingAccountResponse.from_view(view)


# ── lifecycle ───────────────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/activate",
    response_model=BillingAccountResponse,
    summary="Activate a draft or resume a suspended account",
)
def activate_account(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """A draft is *activated*, a suspended account is *resumed*. The Foundation picks the
    trigger so the history records which one actually happened."""
    app.commands.dispatch(
        ActivateBillingAccount(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/suspend",
    response_model=BillingAccountResponse,
    summary="Suspend an account (usage still accrues)",
)
def suspend_account(
    identifier: str,
    body: SuspendAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        SuspendBillingAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            reason=body.reason,
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/close",
    response_model=BillingAccountResponse,
    summary="Close an account (records are retained forever)",
)
def close_account(
    identifier: str,
    body: CloseAccountReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """Closing ends commercial activity; it never deletes revenue records (ADR-0023 RS4).
    There is deliberately no DELETE on this resource once it has left draft."""
    app.commands.dispatch(
        CloseCommercialAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            reason=body.reason,
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/archive",
    response_model=BillingAccountResponse,
    summary="Archive a closed account (still fully readable)",
)
def archive_account(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        ArchiveBillingAccount(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/restore",
    response_model=BillingAccountResponse,
    summary="Un-file an archived account (returns to closed, not active)",
)
def restore_account(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """Un-filing a record and reopening a commercial relationship are separate decisions."""
    app.commands.dispatch(
        RestoreBillingAccount(
            actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier
        )
    )
    return _account_response(app, ctx, identifier)


@router.delete(
    "/{identifier}",
    response_model=BillingAccountResponse,
    summary="Delete a DRAFT account (rejected in any other state)",
)
def delete_draft(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """The only deletion this domain offers. From any state past draft the FSM rejects it with
    409, because everything past draft has revenue attached."""
    app.commands.dispatch(
        DeleteDraftAccount(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier)
    )
    return _account_response(app, ctx, identifier)


# ── commercial detail ───────────────────────────────────────────────────────────


@router.patch(
    "/{identifier}/preferences",
    response_model=BillingAccountResponse,
    summary="Update billing and invoice preferences",
)
def update_preferences(
    identifier: str,
    body: PreferencesBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        UpdateBillingPreferences(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            preferences=body.to_preferences(),
        )
    )
    return _account_response(app, ctx, identifier)


@router.patch(
    "/{identifier}/contacts",
    response_model=BillingAccountResponse,
    summary="Replace the contact list",
)
def update_contacts(
    identifier: str,
    body: UpdateContactsBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        UpdateContacts(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            contacts=tuple(c.to_contact() for c in body.contacts),
        )
    )
    return _account_response(app, ctx, identifier)


@router.patch(
    "/{identifier}/addresses",
    response_model=BillingAccountResponse,
    summary="Replace the address book",
)
def update_addresses(
    identifier: str,
    body: UpdateAddressesBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        UpdateAddresses(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            addresses=tuple(a.to_address() for a in body.addresses),
        )
    )
    return _account_response(app, ctx, identifier)


@router.patch(
    "/{identifier}/credit",
    response_model=BillingAccountResponse,
    summary="Set the credit profile and commercial status",
)
def update_credit(
    identifier: str,
    body: UpdateCreditBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """A credit limit is a policy figure, not a balance — this endpoint records what we are
    willing to be owed and computes nothing about what currently is."""
    app.commands.dispatch(
        UpdateCreditProfile(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            credit=body.credit.to_credit(),
            commercial_status=body.commercial_status,
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/notes",
    response_model=BillingAccountResponse,
    status_code=201,
    summary="Append a note (there is no edit and no delete)",
)
def add_note(
    identifier: str,
    body: AddNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    app.commands.dispatch(
        AddAccountNote(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            body=body.body,
            pinned=body.pinned,
        )
    )
    return _account_response(app, ctx, identifier)


# ── relationships ───────────────────────────────────────────────────────────────


@router.post(
    "/{identifier}/parent",
    response_model=BillingAccountResponse,
    summary="Assign or detach the billing parent",
)
def assign_parent(
    identifier: str,
    body: AssignParentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """A **billing** relationship only. Organization, workspace, and project hierarchy are
    untouched — which is what lets a reseller structure be configured rather than architected.
    A cycle is rejected with 422 and the offending path."""
    app.commands.dispatch(
        AssignParent(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            parent_account_id=body.parent_account_id,
        )
    )
    return _account_response(app, ctx, identifier)


@router.post(
    "/{identifier}/payer",
    response_model=BillingAccountResponse,
    summary="Assign or clear the payer",
)
def assign_payer(
    identifier: str,
    body: AssignPayerBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BillingAccountResponse:
    """Deliberately independent of the parent: who consolidates the invoices and who actually
    pays are different questions. A payer loop is rejected with 422 and the offending path."""
    app.commands.dispatch(
        AssignPayer(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            payer_account_id=body.payer_account_id,
        )
    )
    return _account_response(app, ctx, identifier)


# ── reads ───────────────────────────────────────────────────────────────────────


@router.get(
    "/{identifier}/hierarchy",
    response_model=HierarchyResponse,
    summary="The billing tree this account belongs to",
)
def get_hierarchy(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> HierarchyResponse:
    view = app.queries.dispatch(
        ListHierarchy(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier)
    )
    if view is None:
        raise BillingNotFound(f"billing account {identifier!r} not found")
    return HierarchyResponse.from_view(view)


@router.get(
    "/{identifier}/profile",
    response_model=CommercialProfileResponse,
    summary="The composed commercial profile",
)
def get_profile(
    identifier: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialProfileResponse:
    """A derived read model: everything in it is computed from the account and its neighbours,
    so there is no second copy of a commercial fact that can fall out of date."""
    view = app.queries.dispatch(
        CommercialProfile(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=identifier)
    )
    if view is None:
        raise BillingNotFound(f"billing account {identifier!r} not found")
    return CommercialProfileResponse.from_view(view)


@router.get(
    "/{identifier}/history",
    response_model=list[AccountActivityResponse],
    summary="Account history (append-only)",
)
def get_history(
    identifier: str,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[AccountActivityResponse]:
    views = app.queries.dispatch(
        AccountHistory(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=identifier,
            limit=limit,
        )
    )
    return [AccountActivityResponse.from_view(v) for v in views]
