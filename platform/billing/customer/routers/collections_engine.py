"""Dunning & Collections routes (M9.17) — transport only.

Additive to the M9.5–M9.16 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No collection rule is
evaluated here — whether a retry is eligible, how long grace lasts, which stage comes next, what a
payment state means and when a strategy is exhausted are all Foundation behaviour reached through
the Application.

Mounted at **`/recovery`**, and the prefix was chosen by inspection rather than by preference:
`/collections` is already M9.16's, where it serves payment *collection* — the act of taking money.
Two routers under one prefix would shadow each other depending on registration order, and the one
that lost would do so silently. `/recovery` is also the word M9.2 used for this ladder
(`DunningState` is documented as "the recovery ladder"), so the URL and the domain agree.

**Six things no endpoint here does.** None decides retry eligibility. None calculates grace — the
waits arrive from an immutable policy version. None chooses a stage. None resolves payment state:
`/signal` takes the payment's own state strings and the server interprets them, because an endpoint
that accepted a verdict would let its caller close a recovery. **None calculates money**: there is
no amount in any request or response in this file, and a test greps it. And none writes to a
payment, invoice, ledger, subscription, or account — the only outward write in the whole engine is
a command dispatched into the Payment Engine's own use cases.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ActionsDue,
    BillingApplication,
    BlockCollectionCase,
    CancelCollectionCase,
    CasesDue,
    ClaimCollectionAction,
    CollectionTimeline,
    DeactivateCollectionPolicy,
    EscalateCollectionCase,
    EvaluateCollectionCase,
    ExecuteCollectionAction,
    ExplainCollectionCase,
    GetCaseForObligation,
    GetCollectionAction,
    GetCollectionCase,
    GetCollectionPolicyVersions,
    InspectCollectionCase,
    ListCollectionActions,
    ListCollectionAttempts,
    ListCollectionCases,
    ListCollectionDecisions,
    ListCollectionPolicies,
    ObsoleteCollectionAction,
    OpenCollectionCase,
    OpenCollectionCycle,
    PublishCollectionPolicy,
    RecordPaymentSignal,
    SearchCollectionCases,
)
from billing.foundation import CollectionStage, ObligationKind

from ..auth import require_org
from ..collection_schemas import (
    CollectionActionResponse,
    CollectionActivityResponse,
    CollectionAttemptResponse,
    CollectionCaseResponse,
    CollectionDecisionResponse,
    CollectionExplanationResponse,
    CollectionInspectionResponse,
    CollectionOutcomeResponse,
    CollectionPolicyResponse,
    CollectionReasonBody,
    CollectionSearchResponse,
    EvaluateCaseBody,
    ExecuteActionBody,
    OpenCaseBody,
    OpenCycleBody,
    PublishPolicyBody,
    RecordPaymentSignalBody,
)
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/recovery", tags=["collections"], responses=ERROR_RESPONSES)


# ── policy: immutable, versioned ────────────────────────────────────────────────


@router.post(
    "/policies",
    response_model=CollectionPolicyResponse,
    status_code=201,
    summary="Publish a collection strategy version (immutable — there is no update)",
)
def publish_policy(
    body: PublishPolicyBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionPolicyResponse:
    """Publishing the same version twice is idempotent, so a deploy that runs its seed twice does
    not fail. Publishing a *different* body under the same version is refused by the database."""
    return CollectionPolicyResponse.of(
        app.commands.dispatch(
            PublishCollectionPolicy(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                code=body.code,
                version=body.version,
                stages=tuple(
                    CollectionStage(
                        sequence=s.sequence,
                        kind=s.kind,
                        wait_seconds=s.wait_seconds,
                        grace_seconds=s.grace_seconds,
                        label=s.label,
                    )
                    for s in body.stages
                ),
                obligation_kind=body.obligation_kind,
                max_cycles=body.max_cycles,
                description=body.description,
            )
        )
    )


@router.get("/policies", response_model=list[CollectionPolicyResponse],
            summary="List published strategies")
def list_policies(
    include_inactive: bool = False,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionPolicyResponse]:
    return [
        CollectionPolicyResponse.of(p)
        for p in app.queries.dispatch(
            ListCollectionPolicies(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                include_inactive=include_inactive,
            )
        )
    ]


@router.get(
    "/policies/{code}/versions",
    response_model=list[CollectionPolicyResponse],
    summary="Every version of one strategy, oldest first",
)
def policy_versions(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionPolicyResponse]:
    """The audit answer to "under what rules was this customer dunned in March?"."""
    return [
        CollectionPolicyResponse.of(p)
        for p in app.queries.dispatch(
            GetCollectionPolicyVersions(
                actor_id=ctx.actor(), org_id=ctx.organization(), code=code
            )
        )
    ]


@router.post(
    "/policies/{policy_id}/deactivate",
    response_model=CollectionPolicyResponse,
    summary="Withdraw a version from future binding (the row stays readable forever)",
)
def deactivate_policy(
    policy_id: str,
    body: CollectionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionPolicyResponse:
    return CollectionPolicyResponse.of(
        app.commands.dispatch(
            DeactivateCollectionPolicy(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                policy_id=policy_id,
                reason=body.reason,
            )
        )
    )


# ── cases ───────────────────────────────────────────────────────────────────────


@router.post(
    "/cases",
    response_model=CollectionOutcomeResponse,
    status_code=201,
    summary="Open the recovery of one obligation (idempotent on the obligation)",
)
def open_case(
    body: OpenCaseBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """One debt, one case. Asking twice returns the existing recovery with `disposition=duplicate`,
    which is a distinct answer from `new` precisely so a caller cannot miscount."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            OpenCollectionCase(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                obligation_kind=body.obligation_kind,
                obligation_id=body.obligation_id,
                policy_code=body.policy_code,
                payment_intent_id=body.payment_intent_id,
                currency=body.currency,
            )
        )
    )


@router.get("/cases", response_model=list[CollectionCaseResponse], summary="List collection cases")
def list_cases(
    account_id: str = "",
    state: list[str] = Query(default=[]),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionCaseResponse]:
    return [
        CollectionCaseResponse.of(c)
        for c in app.queries.dispatch(
            ListCollectionCases(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                states=tuple(state),
                limit=limit,
            )
        )
    ]


@router.get(
    "/cases/due",
    response_model=list[CollectionCaseResponse],
    summary="Cases whose next action has matured (a read — there is no scheduler in Billing)",
)
def cases_due(
    at: float | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionCaseResponse]:
    """Whoever calls this on a timer lives outside Billing. The engine answers what is due; it does
    not decide when to be asked, and it runs no loop of its own."""
    return [
        CollectionCaseResponse.of(c)
        for c in app.queries.dispatch(
            CasesDue(actor_id=ctx.actor(), org_id=ctx.organization(), at=at, limit=limit)
        )
    ]


@router.get(
    "/cases/search", response_model=CollectionSearchResponse, summary="Search collection cases"
)
def search_cases(
    account_id: str = "",
    obligation_id: str = "",
    state: list[str] = Query(default=[]),
    signal: list[str] = Query(default=[]),
    due_before: float | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionSearchResponse:
    return CollectionSearchResponse.of(
        app.queries.dispatch(
            SearchCollectionCases(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                obligation_id=obligation_id,
                states=tuple(state),
                signals=tuple(signal),
                due_before=due_before,
                limit=limit,
            )
        )
    )


@router.get(
    "/cases/for-obligation",
    response_model=CollectionCaseResponse | None,
    summary="The single case recovering one obligation, if any",
)
def case_for_obligation(
    obligation_id: str,
    obligation_kind: ObligationKind = ObligationKind.INVOICE,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionCaseResponse | None:
    found = app.queries.dispatch(
        GetCaseForObligation(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            obligation_kind=obligation_kind,
            obligation_id=obligation_id,
        )
    )
    return CollectionCaseResponse.of(found) if found is not None else None


@router.get("/cases/{case_id}", response_model=CollectionCaseResponse, summary="One case")
def get_case(
    case_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionCaseResponse:
    return CollectionCaseResponse.of(
        app.queries.dispatch(
            GetCollectionCase(actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id)
        )
    )


@router.post(
    "/cases/{case_id}/evaluate",
    response_model=CollectionOutcomeResponse,
    summary="Decide what should happen next and plan at most one action",
)
def evaluate_case(
    case_id: str,
    body: EvaluateCaseBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """Safe to call repeatedly and concurrently. Repetition collapses onto derived ids; concurrency
    is arbitrated by a compare-and-set on the case."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            EvaluateCollectionCase(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                at=body.at,
            )
        )
    )


@router.post(
    "/cases/{case_id}/signal",
    response_model=CollectionOutcomeResponse,
    summary="Record what the payment did (states, never a verdict)",
)
def record_signal(
    case_id: str,
    body: RecordPaymentSignalBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """A success recovers the case **and** retires every open action in the same transaction — a
    recovery that left a planned retry behind would be chased against an invoice that is paid.

    An ambiguous state blocks the case. It does not fail it, and it never becomes a retry."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            RecordPaymentSignal(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                intent_state=body.intent_state,
                attempt_state=body.attempt_state,
                failure_code=body.failure_code,
                payment_intent_id=body.payment_intent_id,
            )
        )
    )


@router.post(
    "/cases/{case_id}/block",
    response_model=CollectionOutcomeResponse,
    summary="Hold a case pending something Collections does not own",
)
def block_case(
    case_id: str,
    body: CollectionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            BlockCollectionCase(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/cases/{case_id}/escalate",
    response_model=CollectionOutcomeResponse,
    summary="Hand a case to a human — automation stops (always attributed)",
)
def escalate_case(
    case_id: str,
    body: CollectionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            EscalateCollectionCase(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/cases/{case_id}/cancel",
    response_model=CollectionOutcomeResponse,
    summary="The obligation went away — not a write-off",
)
def cancel_case(
    case_id: str,
    body: CollectionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """Collections cannot forgive a debt. This records that the thing being recovered no longer
    exists — a fact the Invoice or Credit domain established and this one is told about."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            CancelCollectionCase(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/cases/{case_id}/cycles",
    response_model=CollectionOutcomeResponse,
    status_code=201,
    summary="Begin a second recovery cycle on an exhausted case",
)
def open_cycle(
    case_id: str,
    body: OpenCycleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """The honest alternative to reopening a closed recovery. The previous cycle's actions,
    attempts and decisions stay exactly as they are."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            OpenCollectionCycle(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                policy_code=body.policy_code,
                reason=body.reason,
            )
        )
    )


# ── actions and evidence ────────────────────────────────────────────────────────


@router.get(
    "/actions/due",
    response_model=list[CollectionActionResponse],
    summary="Actions that have matured (a read — nothing is claimed here)",
)
def actions_due(
    at: float | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionActionResponse]:
    return [
        CollectionActionResponse.of(a)
        for a in app.queries.dispatch(
            ActionsDue(actor_id=ctx.actor(), org_id=ctx.organization(), at=at, limit=limit)
        )
    ]


@router.get("/actions/{action_id}", response_model=CollectionActionResponse, summary="One action")
def get_action(
    action_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionActionResponse:
    return CollectionActionResponse.of(
        app.queries.dispatch(
            GetCollectionAction(
                actor_id=ctx.actor(), org_id=ctx.organization(), action_id=action_id
            )
        )
    )


@router.post(
    "/actions/{action_id}/claim",
    response_model=CollectionActionResponse,
    summary="Take exclusive hold of a due action (conditional — exactly one worker wins)",
)
def claim_action(
    action_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionActionResponse:
    return CollectionActionResponse.of(
        app.commands.dispatch(
            ClaimCollectionAction(
                actor_id=ctx.actor(), org_id=ctx.organization(), action_id=action_id
            )
        )
    )


@router.post(
    "/actions/{action_id}/execute",
    response_model=CollectionOutcomeResponse,
    summary="Claim, do the external thing, record what happened",
)
def execute_action(
    action_id: str,
    body: ExecuteActionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionOutcomeResponse:
    """Three phases: the action is durable and claimed before anything leaves, and the evidence is
    recorded after. A payment retry is *requested through the Payment Engine's own command* — this
    endpoint reaches no provider, and it refuses outright if the payment outcome is unknown."""
    return CollectionOutcomeResponse.of(
        app.commands.dispatch(
            ExecuteCollectionAction(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                action_id=action_id,
                notifier_code=body.notifier_code,
                correlation_id=body.correlation_id,
                at=body.at,
            )
        )
    )


@router.post(
    "/actions/{action_id}/obsolete",
    response_model=CollectionActionResponse,
    summary="Retire an action overtaken by events",
)
def obsolete_action(
    action_id: str,
    body: CollectionReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionActionResponse:
    return CollectionActionResponse.of(
        app.commands.dispatch(
            ObsoleteCollectionAction(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                action_id=action_id,
                reason=body.reason,
            )
        )
    )


@router.get(
    "/cases/{case_id}/actions",
    response_model=list[CollectionActionResponse],
    summary="Every action on a case, oldest first",
)
def list_actions(
    case_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionActionResponse]:
    return [
        CollectionActionResponse.of(a)
        for a in app.queries.dispatch(
            ListCollectionActions(
                actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id
            )
        )
    ]


@router.get(
    "/cases/{case_id}/attempts",
    response_model=list[CollectionAttemptResponse],
    summary="Execution evidence, append-only",
)
def list_attempts(
    case_id: str,
    action_id: str = "",
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionAttemptResponse]:
    return [
        CollectionAttemptResponse.of(a)
        for a in app.queries.dispatch(
            ListCollectionAttempts(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                case_id=case_id,
                action_id=action_id,
            )
        )
    ]


@router.get(
    "/cases/{case_id}/decisions",
    response_model=list[CollectionDecisionResponse],
    summary="The decision trail — every input that produced every choice",
)
def list_decisions(
    case_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionDecisionResponse]:
    """Reproducible after the policy changes: each row names the immutable version it applied."""
    return [
        CollectionDecisionResponse.of(d)
        for d in app.queries.dispatch(
            ListCollectionDecisions(
                actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id
            )
        )
    ]


# ── observability ───────────────────────────────────────────────────────────────


@router.get(
    "/cases/{case_id}/explain",
    response_model=CollectionExplanationResponse,
    summary="Why this case is where it is — assembled from stored evidence",
)
def explain_case(
    case_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionExplanationResponse:
    """Observability, never authority: nothing reads this back, and no decision depends on it."""
    return CollectionExplanationResponse.of(
        app.queries.dispatch(
            ExplainCollectionCase(
                actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id
            )
        )
    )


@router.get(
    "/cases/{case_id}/inspect",
    response_model=CollectionInspectionResponse,
    summary="A case with its counts — the operator's single screen",
)
def inspect_case(
    case_id: str,
    at: float | None = None,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CollectionInspectionResponse:
    return CollectionInspectionResponse.of(
        app.queries.dispatch(
            InspectCollectionCase(
                actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id, at=at
            )
        )
    )


@router.get(
    "/cases/{case_id}/timeline",
    response_model=list[CollectionActivityResponse],
    summary="The audit trail a support agent reads",
)
def timeline(
    case_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CollectionActivityResponse]:
    return [
        CollectionActivityResponse.of(a)
        for a in app.queries.dispatch(
            CollectionTimeline(
                actor_id=ctx.actor(), org_id=ctx.organization(), case_id=case_id, limit=limit
            )
        )
    ]
