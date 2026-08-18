"""Payment Engine routes (M9.16) — transport only.

Additive to the M9.5–M9.15 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No payment rule is evaluated
here — routing, idempotency, lifecycle legality, refund bounds, and reconciliation are all
Foundation behaviour reached through the Application.

Mounted at **`/collections`**, not under M9.5's `/payments`. Same reason every domain since M9.8
took its own prefix: siblings under an existing wildcard would be shadowed depending on router
registration order, and M9.2's payment and refund records still answer on the old paths.

## The webhook boundary, stated honestly (M9.16-R1, finding P-4)

There is **no production provider-webhook endpoint at this milestone.** The relay at
`/provider-events/relay/{provider_code}` sits behind `require_org` like every other route here,
which means a real PSP — which holds no Customer-Platform token — cannot call it. It is an
authenticated internal surface for submitting a callback that has already reached the platform some
other way.

The alternative was to add an unauthenticated route that resolves the organization from verified
provider evidence. That was **not** done, because resolving an org before authentication requires a
cross-organization lookup keyed on provider data, and building that to make a demo endpoint
reachable would put a tenant-isolation hole into the one plane that must not have one. The
signature-verification path is implemented and exercised; the deployment boundary around it is not,
and pretending otherwise would be the drift this milestone exists to avoid.

**Six things no endpoint here does.** None calls a payment provider — every provider call happens
inside the Application's three-phase boundary, and a controller that made one would put the most
dangerous call in the system in the layer with no transaction and no idempotency. **None names a
provider**: there is no vendor string anywhere in this file, and a test greps it. None accepts
instrument data — `instrument_token` is a provider token reference and there is no field a PAN
could arrive in. None trusts a webhook body: `/webhooks/{provider}` takes a raw payload and a
signature and the adapter decides. None edits a settled attempt: the surface has no PUT and no
PATCH on one, and the FSM has no edge out of a settled state. And none deletes anything.

## Why the receipt is not just a 200

`PaymentReceiptResponse.outcome` distinguishes `succeeded`, `authorized`, `requires_action`,
`pending`, `failed`, `ambiguous`, `replayed`, and `conflicting`. A client that reads every 2xx as
"paid" will treat a timeout as a collection, and an integration built on that will double-charge on
its first bad day. `202 Accepted` is returned for the money-moving verbs precisely because the
answer may not be final — the outcome field says what actually happened.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    BillingApplication,
    CancelPaymentAttempt,
    CancelPaymentIntent,
    CapturePaymentAttempt,
    CreatePaymentIntent,
    DeclarePaymentRoute,
    DiagnosePayment,
    ExpirePaymentIntent,
    ExplainPayment,
    GetPaymentAttempt,
    GetPaymentIntent,
    GetPaymentPosition,
    GetRefundableAmount,
    IngestProviderEvent,
    InspectPayment,
    ListPaymentAttempts,
    ListPaymentDivergences,
    ListPaymentIntents,
    ListPaymentRefunds,
    ListPaymentRoutes,
    ListProviderEvents,
    ListProviderOperations,
    PaymentTimeline,
    PostPaymentToLedger,
    PreviewPaymentRoute,
    ReconcilePaymentAttempt,
    ReconcileProviderOperations,
    RefundPayment,
    ResolvePaymentDivergence,
    RetirePaymentRoute,
    SearchPayments,
    StartPaymentAttempt,
)
from billing.foundation import Currency, IntentState, PaymentMethodKind, PaymentPurpose

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..payment_schemas import (
    CommercialRefundResponse,
    CreatePaymentIntentBody,
    DeclarePaymentRouteBody,
    IngestProviderEventBody,
    PaymentActivityResponse,
    PaymentAttemptResponse,
    PaymentDiagnosticsResponse,
    PaymentDivergenceResponse,
    PaymentExplanationResponse,
    PaymentInspectionResponse,
    PaymentIntentResponse,
    PaymentPositionResponse,
    PaymentReasonBody,
    PaymentReceiptResponse,
    PaymentRoutePolicyResponse,
    PaymentRouteResponse,
    PaymentSearchResponse,
    ProviderEventResponse,
    ProviderOperationResponse,
    ReconcileOperationsBody,
    RefundPaymentBody,
    ResolveDivergenceBody,
    StartAttemptBody,
)
from ..payment_schemas import _money as _money_response
from ..schemas import ERROR_RESPONSES, MoneyResponse

router = APIRouter(prefix="/collections", tags=["payments"], responses=ERROR_RESPONSES)


# ── routing configuration ───────────────────────────────────────────────────────


@router.post(
    "/routes",
    response_model=PaymentRoutePolicyResponse,
    status_code=201,
    summary="Declare a payment route (the only place a provider is named)",
)
def declare_route(
    body: DeclarePaymentRouteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentRoutePolicyResponse:
    return PaymentRoutePolicyResponse.of(
        app.commands.dispatch(
            DeclarePaymentRoute(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                provider_code=body.provider_code,
                priority=body.priority,
                currency=body.currency,
                country=body.country,
                method=body.method,
                account_id=body.account_id,
                version=body.version,
                notes=body.notes,
            )
        )
    )


@router.get(
    "/routes",
    response_model=list[PaymentRoutePolicyResponse],
    summary="List declared payment routes",
)
def list_routes(
    include_retired: bool = Query(False),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentRoutePolicyResponse]:
    return [
        PaymentRoutePolicyResponse.of(v)
        for v in app.queries.dispatch(
            ListPaymentRoutes(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                include_retired=include_retired,
            )
        )
    ]


@router.get(
    "/routes/preview",
    response_model=PaymentRouteResponse,
    summary="Which provider would serve this payment, and why (sends nothing)",
)
def preview_route(
    currency: Currency = Query(Currency.INR),
    country: str = Query(""),
    method: PaymentMethodKind = Query(PaymentMethodKind.CARD),
    account_id: str = Query(""),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentRouteResponse:
    """Static path, declared **before** `/routes/{route_id}` so a literal never falls into the
    wildcard — the M9.8–M9.15 shadowing lesson, applied again."""
    return PaymentRouteResponse.of(
        app.queries.dispatch(
            PreviewPaymentRoute(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                currency=currency,
                country=country,
                method=method,
                account_id=account_id,
            )
        )
    )


@router.post(
    "/routes/{route_id}/retire",
    response_model=PaymentRoutePolicyResponse,
    summary="Retire a payment route (the row survives)",
)
def retire_route(
    route_id: str,
    body: PaymentReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentRoutePolicyResponse:
    return PaymentRoutePolicyResponse.of(
        app.commands.dispatch(
            RetirePaymentRoute(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                route_id=route_id,
                reason=body.reason,
            )
        )
    )


# ── intents ─────────────────────────────────────────────────────────────────────


@router.post(
    "/intents",
    response_model=PaymentIntentResponse,
    status_code=201,
    summary="Open a collection objective (moves no money)",
)
def create_intent(
    body: CreatePaymentIntentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentIntentResponse:
    """Idempotent on `(subject, purpose, amount, currency, request_key)`: the same request twice is
    one objective, because a retried checkout must not become two collections."""
    return PaymentIntentResponse.of(
        app.commands.dispatch(
            CreatePaymentIntent(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                subject_id=body.subject_id,
                subject_kind=body.subject_kind,
                purpose=body.purpose,
                amount_minor=body.amount_minor,
                currency=body.currency,
                method=body.method,
                instrument_id=body.instrument_id,
                request_key=body.request_key,
                country=body.country,
                description=body.description,
            )
        )
    )


@router.get(
    "/intents", response_model=list[PaymentIntentResponse], summary="List collection objectives"
)
def list_intents(
    account_id: str = Query(""),
    subject_id: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentIntentResponse]:
    return [
        PaymentIntentResponse.of(v)
        for v in app.queries.dispatch(
            ListPaymentIntents(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                subject_id=subject_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/intents/search", response_model=PaymentSearchResponse, summary="Search collection objectives"
)
def search_intents(
    account_id: str = Query(""),
    subject_id: str = Query(""),
    provider_code: str = Query(""),
    state: IntentState | None = Query(None),
    purpose: PaymentPurpose | None = Query(None),
    currency: Currency | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentSearchResponse:
    return PaymentSearchResponse.of(
        app.queries.dispatch(
            SearchPayments(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                subject_id=subject_id,
                provider_code=provider_code,
                state=state,
                purpose=purpose,
                currency=currency,
                limit=limit,
            )
        )
    )


@router.get(
    "/intents/divergences",
    response_model=list[PaymentDivergenceResponse],
    summary="Reconciliation findings that need a person",
)
def list_divergences(
    open_only: bool = Query(True),
    limit: int = Query(200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentDivergenceResponse]:
    return [
        PaymentDivergenceResponse.of(v)
        for v in app.queries.dispatch(
            ListPaymentDivergences(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                open_only=open_only,
                limit=limit,
            )
        )
    ]


@router.get(
    "/intents/operations",
    response_model=list[ProviderOperationResponse],
    summary="Provider operations still sending (the crash-recovery queue)",
)
def list_open_operations(
    limit: int = Query(200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ProviderOperationResponse]:
    return [
        ProviderOperationResponse.of(v)
        for v in app.queries.dispatch(
            ListProviderOperations(
                actor_id=ctx.actor(), org_id=ctx.organization(), open_only=True, limit=limit
            )
        )
    ]


@router.get("/intents/{intent_id}", response_model=PaymentIntentResponse, summary="One objective")
def get_intent(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentIntentResponse:
    return PaymentIntentResponse.of(
        app.queries.dispatch(
            GetPaymentIntent(
                actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id
            )
        )
    )


@router.get(
    "/intents/{intent_id}/position",
    response_model=PaymentPositionResponse,
    summary="What this objective has actually collected (derived, never stored)",
)
def get_position(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentPositionResponse:
    return PaymentPositionResponse.of(
        app.queries.dispatch(
            GetPaymentPosition(
                actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id
            )
        )
    )


@router.get(
    "/intents/{intent_id}/explain",
    response_model=PaymentExplanationResponse,
    summary="Why this payment is in this state (no provider is called)",
)
def explain(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentExplanationResponse:
    return PaymentExplanationResponse.of(
        app.queries.dispatch(
            ExplainPayment(actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id)
        )
    )


@router.get(
    "/intents/{intent_id}/inspect",
    response_model=PaymentInspectionResponse,
    summary="What is open, blocked, or unposted on this objective",
)
def inspect(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentInspectionResponse:
    return PaymentInspectionResponse.of(
        app.queries.dispatch(
            InspectPayment(actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id)
        )
    )


@router.get(
    "/intents/{intent_id}/diagnostics",
    response_model=PaymentDiagnosticsResponse,
    summary="Structural findings for this objective",
)
def diagnose(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentDiagnosticsResponse:
    return PaymentDiagnosticsResponse.of(
        app.queries.dispatch(
            DiagnosePayment(actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id)
        )
    )


@router.get(
    "/intents/{intent_id}/attempts",
    response_model=list[PaymentAttemptResponse],
    summary="Every attempt made for this objective (a retry never rewrites one)",
)
def list_attempts(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentAttemptResponse]:
    return [
        PaymentAttemptResponse.of(v)
        for v in app.queries.dispatch(
            ListPaymentAttempts(
                actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id
            )
        )
    ]


@router.get(
    "/intents/{intent_id}/operations",
    response_model=list[ProviderOperationResponse],
    summary="Every provider operation sent for this objective",
)
def list_operations(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ProviderOperationResponse]:
    return [
        ProviderOperationResponse.of(v)
        for v in app.queries.dispatch(
            ListProviderOperations(
                actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id
            )
        )
    ]


@router.get(
    "/intents/{intent_id}/refunds",
    response_model=list[CommercialRefundResponse],
    summary="Refunds recorded against this objective",
)
def list_refunds(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CommercialRefundResponse]:
    return [
        CommercialRefundResponse.of(v)
        for v in app.queries.dispatch(
            ListPaymentRefunds(
                actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id
            )
        )
    ]


@router.get(
    "/intents/{intent_id}/timeline",
    response_model=list[PaymentActivityResponse],
    summary="The audit trail for this objective",
)
def timeline(
    intent_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentActivityResponse]:
    return [
        PaymentActivityResponse.of(v)
        for v in app.queries.dispatch(
            PaymentTimeline(actor_id=ctx.actor(), org_id=ctx.organization(), intent_id=intent_id)
        )
    ]


@router.post(
    "/intents/{intent_id}/attempts",
    response_model=PaymentReceiptResponse,
    status_code=202,
    summary="Move money (three-phase; the outcome field says what actually happened)",
)
def start_attempt(
    intent_id: str,
    body: StartAttemptBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentReceiptResponse:
    """`202`, not `201`: the answer may legitimately be `requires_action`, `pending`, or
    `ambiguous`, and an endpoint that returned `201 Created` for all three would teach every client
    to read a timeout as a collection."""
    return PaymentReceiptResponse.of(
        app.commands.dispatch(
            StartPaymentAttempt(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                intent_id=intent_id,
                instrument_token=body.instrument_token,
                customer_reference=body.customer_reference,
                return_url=body.return_url,
                correlation_id=body.correlation_id,
            )
        )
    )


@router.post(
    "/intents/{intent_id}/cancel",
    response_model=PaymentIntentResponse,
    summary="Cancel an objective (refused while a movement is in flight)",
)
def cancel_intent(
    intent_id: str,
    body: PaymentReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentIntentResponse:
    return PaymentIntentResponse.of(
        app.commands.dispatch(
            CancelPaymentIntent(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                intent_id=intent_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/intents/{intent_id}/expire",
    response_model=PaymentIntentResponse,
    summary="Expire an objective nobody completed",
)
def expire_intent(
    intent_id: str,
    body: PaymentReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentIntentResponse:
    return PaymentIntentResponse.of(
        app.commands.dispatch(
            ExpirePaymentIntent(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                intent_id=intent_id,
                reason=body.reason,
            )
        )
    )


# ── attempts ────────────────────────────────────────────────────────────────────


@router.get(
    "/attempts/{attempt_id}", response_model=PaymentAttemptResponse, summary="One attempt"
)
def get_attempt(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentAttemptResponse:
    return PaymentAttemptResponse.of(
        app.queries.dispatch(
            GetPaymentAttempt(
                actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id
            )
        )
    )


@router.get(
    "/attempts/{attempt_id}/refundable",
    response_model=MoneyResponse,
    summary="What is still refundable (computed from facts, not stored)",
)
def refundable(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> MoneyResponse:
    view = app.queries.dispatch(
        GetRefundableAmount(actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id)
    )
    return _money_response(view)


@router.get(
    "/attempts/{attempt_id}/events",
    response_model=list[ProviderEventResponse],
    summary="Provider callbacks correlated to this attempt, applied or not",
)
def list_events(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ProviderEventResponse]:
    return [
        ProviderEventResponse.of(v)
        for v in app.queries.dispatch(
            ListProviderEvents(
                actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id
            )
        )
    ]


@router.post(
    "/attempts/{attempt_id}/capture",
    response_model=PaymentReceiptResponse,
    status_code=202,
    summary="Capture an authorized attempt",
)
def capture(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentReceiptResponse:
    return PaymentReceiptResponse.of(
        app.commands.dispatch(
            CapturePaymentAttempt(
                actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id
            )
        )
    )


@router.post(
    "/attempts/{attempt_id}/cancel",
    response_model=PaymentReceiptResponse,
    status_code=202,
    summary="Void an authorization that has not been captured",
)
def cancel_attempt(
    attempt_id: str,
    body: PaymentReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentReceiptResponse:
    return PaymentReceiptResponse.of(
        app.commands.dispatch(
            CancelPaymentAttempt(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                attempt_id=attempt_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/attempts/{attempt_id}/reconcile",
    response_model=PaymentDivergenceResponse,
    summary="Ask the provider about this operation key and record the comparison",
)
def reconcile(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentDivergenceResponse:
    """The only path that resolves an ambiguous attempt — by retrieving the operation already sent,
    never by sending another one."""
    return PaymentDivergenceResponse.of(
        app.commands.dispatch(
            ReconcilePaymentAttempt(
                actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id
            )
        )
    )


@router.post(
    "/attempts/{attempt_id}/refunds",
    response_model=CommercialRefundResponse,
    status_code=202,
    summary="Refund a captured attempt (a new financial fact)",
)
def refund(
    attempt_id: str,
    body: RefundPaymentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialRefundResponse:
    return CommercialRefundResponse.of(
        app.commands.dispatch(
            RefundPayment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                attempt_id=attempt_id,
                amount_minor=body.amount_minor,
                currency=body.currency,
                reason=body.reason,
                request_key=body.request_key,
                note=body.note,
            )
        )
    )


@router.post(
    "/attempts/{attempt_id}/post",
    response_model=PaymentReceiptResponse,
    summary="Post a capture whose ledger fact never landed (idempotent, attributed)",
)
def post_to_ledger(
    attempt_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentReceiptResponse:
    return PaymentReceiptResponse.of(
        app.commands.dispatch(
            PostPaymentToLedger(
                actor_id=ctx.actor(), org_id=ctx.organization(), attempt_id=attempt_id
            )
        )
    )


# ── provider events and reconciliation ──────────────────────────────────────────


@router.post(
    "/provider-events/relay/{provider_code}",
    response_model=ProviderEventResponse,
    summary="INTERNAL RELAY ONLY — not a provider webhook endpoint (see the description)",
    description=(
        "**This is not a provider webhook endpoint and must not be configured as one.** It "
        "requires a Customer-Platform bearer token and an organization claim, which no payment "
        "provider possesses, so a real PSP cannot call it. It exists so an operator or an "
        "already-authenticated internal relay can submit a provider callback for verification and "
        "ingestion.\n\n"
        "The signature is still verified by the adapter before anything is touched, and an "
        "unverified body is recorded and applied to nothing — but the **production trust boundary "
        "for direct provider callbacks does not exist at M9.16** and is recorded as a limitation. "
        "Do not read this endpoint as ADR-0023 PY4 webhook compliance."
    ),
)
def relay_provider_event(
    provider_code: str,
    body: IngestProviderEventBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProviderEventResponse:
    """**Internal relay, not a provider webhook.** See the endpoint description.

    Takes a **raw payload and a signature**, never a parsed status — a body carrying
    `status=succeeded` would let anyone who can reach the endpoint mark invoices paid. The adapter
    verifies before any state is touched.

    The response reports the disposition — `applied`, `duplicate`, `stale`, `uncorrelated`,
    `unverified`, `unsupported`, or `conflicting` — because an ignored webhook is exactly as
    interesting to an investigation as an applied one."""
    return ProviderEventResponse.of(
        app.commands.dispatch(
            IngestProviderEvent(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                provider_code=provider_code or body.provider_code,
                payload=body.payload,
                signature=body.signature,
            )
        )
    )


@router.get(
    "/events", response_model=list[ProviderEventResponse], summary="Recent provider callbacks"
)
def recent_events(
    limit: int = Query(100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ProviderEventResponse]:
    return [
        ProviderEventResponse.of(v)
        for v in app.queries.dispatch(
            ListProviderEvents(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    ]


@router.post(
    "/reconciliation/sweep",
    response_model=list[PaymentDivergenceResponse],
    summary="Retrieve every provider operation still sending (crash recovery)",
)
def sweep(
    body: ReconcileOperationsBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentDivergenceResponse]:
    return [
        PaymentDivergenceResponse.of(v)
        for v in app.commands.dispatch(
            ReconcileProviderOperations(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                older_than_seconds=body.older_than_seconds,
                limit=body.limit,
            )
        )
    ]


@router.post(
    "/reconciliation/{finding_id}/resolve",
    response_model=PaymentDivergenceResponse,
    summary="Close a divergence — always by a named person (ADR-0023 PY5)",
)
def resolve_divergence(
    finding_id: str,
    body: ResolveDivergenceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentDivergenceResponse:
    return PaymentDivergenceResponse.of(
        app.commands.dispatch(
            ResolvePaymentDivergence(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_id=finding_id,
                note=body.note,
                acknowledge_only=body.acknowledge_only,
            )
        )
    )
