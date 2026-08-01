"""Domain routers for the Control Plane API (M8.5).

`ALL_ROUTERS` is the ordered list the app factory includes. Each router is thin — it maps
HTTP ↔ Application command/query and nothing else.
"""

from __future__ import annotations

from . import (
    agent_builder,
    auth,
    deployment,
    execution,
    knowledge,
    members,
    observability,
    org_lifecycle,
    organizations,
    playground,
    project_lifecycle,
    projects,
    runtime,
    system,
    tool_framework,
    workspace_lifecycle,
    workspaces,
)

ALL_ROUTERS = [
    system.router,
    auth.router,
    organizations.router,
    org_lifecycle.router,  # M8.6 — org settings/delete/activity (additive)
    org_lifecycle.invitations_router,  # M8.6 — invitation lifecycle (additive)
    # M8.7 — workspace lifecycle (additive). Registered BEFORE workspaces.router so the
    # literal `/workspaces/search` path resolves ahead of M8.5's `/workspaces/{id}`.
    workspace_lifecycle.router,
    workspace_lifecycle.navigation_router,
    workspaces.router,
    members.router,
    # M8.8 — project lifecycle (additive). Registered BEFORE projects.router so the literal
    # `…/projects/search` path resolves ahead of any `…/projects/{id}` sub-paths.
    project_lifecycle.router,
    projects.router,
    # M8.10 — agent builder (additive). Agents live under a project; the router's own
    # literal sub-paths (/search, /versions/compare) are ordered ahead of id params within.
    agent_builder.router,
    # M8.11 — tool framework (additive). Tool catalog under a project + agent↔tool
    # attachment under an agent; each router orders its literal sub-paths first.
    tool_framework.router,
    tool_framework.attach_router,
    # M8.12 — conversation runtime (additive). Runtime sessions under a published agent.
    runtime.router,
    # M8.13 — conversation playground (additive). Mock-runtime conversations under an agent.
    playground.router,
    # M8.14 — provider execution (additive). Real execution/stream over a conversation.
    execution.router,
    # M8.15 — knowledge tool (additive). Collections/documents/chunks/search under a project.
    knowledge.router,
    # M8.16 — deployment actuator (additive; ADR-0022). Deployments under a project.
    deployment.router,
    # M8.17 — observability (additive). Metrics/logs/traces/health/usage/cost/alerts under an org.
    observability.router,
]
