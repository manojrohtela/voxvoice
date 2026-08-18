"""Contract routes (M9.5) — read-only, transport only.

**Deliberately read-only at this milestone.** `RecordContract` takes a fully-formed `Contract`
aggregate (term, commitment, settlement, overrides); building one from a request body would
put a nested enterprise-agreement schema in the transport layer with no Foundation factory to
validate it against. Enterprise contracts are also negotiated and signed offline — a
self-serve endpoint that mints one would be the wrong product shape, not just the wrong
layering.

Contract *creation* lands with M9.21, which brings the request DTO and factory method the
other aggregates already have. Until then the API exposes what a customer legitimately needs:
reading the contracts that govern their account.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import BillingApplication, ListContracts

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, ContractResponse

router = APIRouter(prefix="/contracts", tags=["contracts"], responses=ERROR_RESPONSES)


@router.get("", response_model=list[ContractResponse], summary="List an account's contracts")
def list_contracts(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ContractResponse]:
    views = app.queries.dispatch(
        ListContracts(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return [ContractResponse.from_view(v) for v in views]
