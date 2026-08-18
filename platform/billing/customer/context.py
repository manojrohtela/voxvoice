"""Per-request context (M9.5).

The immutable identity + tracing metadata carried through one HTTP request: request and
correlation ids (set by middleware), the authenticated subject (set by the auth dependency),
and the organization scope. Stored on `request.state.context` so middleware, logging, and
error handling read it uniformly.

**It carries identity; it never evaluates a permission.** `actor()` and `organization()` are
what a controller forwards into a command — the Application's `BillingAuthorizer` seam makes
the decision (ADR-0023 §5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class BillingRequestContext:
    request_id: str
    correlation_id: str
    user_id: str | None = None
    org_id: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def with_identity(self, user_id: str) -> BillingRequestContext:
        return replace(self, user_id=user_id)

    def scoped(self, *, org_id: str | None = None) -> BillingRequestContext:
        return replace(self, org_id=org_id if org_id is not None else self.org_id)

    def actor(self) -> str:
        """The acting subject for a command (empty when unauthenticated)."""
        return self.user_id or ""

    def organization(self) -> str:
        """The organization the request acts within (empty when unscoped). Every org-owned
        Billing command and query is scoped by this — the isolation edge is the query shape
        itself, all the way down (§A11.2)."""
        return self.org_id or ""
