"""Tool tenancy + isolation (M6.1B-2).

Enforces organization/workspace isolation, tool allow/deny-lists, regional
restrictions, and manifest `Visibility` gating — **before** a request reaches
`ToolPolicyEngine`, exactly like `ProviderTenancy` runs before provider selection.

Deliberately does **not** import `tenant`: reuse here means reusing
`ProviderTenancy`'s *pattern* (permissive-default hooks + an isolation guard
raising/returning a denial), not the `tenant.TenantContext`/`Authorizer` classes
themselves. Two independent reasons converge on the same answer:

1. `tool`'s own frozen M6.1B-1 dependency-boundary test asserts `tool` imports no
   `tenant` (`test_tool_dependency_rules`) — importing it here would be exactly the
   kind of reverse-dependency leak DL-0044 already flagged for `pipeline`.
2. `GovernanceIdentity` (this milestone's local identity shape) is richer than
   `tenant.TenantContext` needs to be for tool governance (kind-tagged: user /
   organization / workspace / service / future agent) — a straight re-use would
   have meant widening `tenant.TenantContext` itself, a change to a frozen M5.0
   floor package, which is out of scope.

So `tool/governance` duplicates the *shape* of `tenant.access`'s permissive hooks
locally (`GovernanceIdentity`/`Effect` in `domain.py`), the same "duplicate, don't
cross-import" precedent already established for `_run.py`/`ids.py` across every
closed runtime (recorded M5.8 technical debt, not a new decision).
"""

from __future__ import annotations

from ..domain import ToolRequest, Visibility
from .domain import GovernanceIdentity, TenantDecision


class ToolTenancy:
    def __init__(
        self,
        *,
        allowed_tools: frozenset[str] = frozenset(),
        denied_tools: frozenset[str] = frozenset(),
        allowed_orgs: frozenset[str] = frozenset(),
        allowed_workspaces: frozenset[str] = frozenset(),
        allowed_regions: frozenset[str] = frozenset(),
    ) -> None:
        self._allowed_tools = allowed_tools
        self._denied_tools = denied_tools
        self._allowed_orgs = allowed_orgs
        self._allowed_workspaces = allowed_workspaces
        self._allowed_regions = allowed_regions

    def guard(
        self,
        request: ToolRequest,
        *,
        identity: GovernanceIdentity | None = None,
        region: str | None = None,
        visibility: Visibility = Visibility.PUBLIC,
        owner: str = "",
    ) -> TenantDecision:
        qualified = f"{request.namespace}.{request.tool}"
        workspace_id = identity.workspace_id if identity else None

        if qualified in self._denied_tools:
            return self._deny(request, workspace_id, f"tool {qualified!r} denied")
        if self._allowed_tools and qualified not in self._allowed_tools:
            return self._deny(request, workspace_id, f"tool {qualified!r} not allowed")
        if (self._allowed_orgs and request.org_id is not None
                and request.org_id not in self._allowed_orgs):
            return self._deny(request, workspace_id, f"organization {request.org_id!r} not allowed")
        if (self._allowed_workspaces and workspace_id is not None
                and workspace_id not in self._allowed_workspaces):
            return self._deny(request, workspace_id, f"workspace {workspace_id!r} not allowed")
        if region and self._allowed_regions and region not in self._allowed_regions:
            return self._deny(request, workspace_id, f"region {region!r} not allowed")

        if visibility is Visibility.PRIVATE:
            actor = identity.id if identity else None
            if actor is None or actor != owner:
                return self._deny(request, workspace_id, "private tool: owner mismatch")
        elif visibility is Visibility.ORG:
            actor_org = identity.org_id if identity else None
            if actor_org is None or actor_org != request.org_id:
                return self._deny(request, workspace_id, "org-visibility tool: org mismatch")

        return TenantDecision(allowed=True, org_id=request.org_id, workspace_id=workspace_id)

    @staticmethod
    def _deny(request: ToolRequest, workspace_id: str | None, reason: str) -> TenantDecision:
        return TenantDecision(
            allowed=False, org_id=request.org_id, workspace_id=workspace_id, reason=reason
        )
