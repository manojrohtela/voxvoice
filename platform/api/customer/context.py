"""Per-request context (M8.5).

The immutable identity + tracing metadata carried through a single HTTP request:
request/correlation ids (set by middleware), the authenticated user (set by the auth
dependency), and the org/workspace scope (from the route path). Stored on
`request.state.context` so middleware, logging, and error handling can read it uniformly.
Transport concern only — it carries identity, it never evaluates permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    correlation_id: str
    user_id: str | None = None
    org_id: str | None = None
    workspace_id: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def with_identity(self, user_id: str) -> RequestContext:
        return replace(self, user_id=user_id)

    def scoped(
        self, *, org_id: str | None = None, workspace_id: str | None = None
    ) -> RequestContext:
        return replace(
            self,
            org_id=org_id if org_id is not None else self.org_id,
            workspace_id=workspace_id if workspace_id is not None else self.workspace_id,
        )

    def actor(self) -> str:
        """The acting subject for an Application command (empty when unauthenticated)."""
        return self.user_id or ""
