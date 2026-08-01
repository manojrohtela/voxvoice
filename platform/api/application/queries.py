"""Query messages (M8.3) — the read side of CQRS.

Immutable read requests a client hands to the `QueryBus`. Queries are org-scoped where
applicable (the isolation boundary travels with the request). Read handlers return
Foundation view DTOs (projections the Consumer surface renders; ADR-0021) — they never
mutate and never emit events.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GetUser:
    user_id: str


@dataclass(frozen=True, slots=True)
class GetOrganization:
    org_id: str


@dataclass(frozen=True, slots=True)
class GetWorkspace:
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class ListWorkspaces:
    org_id: str


@dataclass(frozen=True, slots=True)
class ListMembers:
    org_id: str


@dataclass(frozen=True, slots=True)
class ListProjects:
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class ListEnvironments:
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class GetPreferences:
    org_id: str
    user_id: str
