"""Activity querying application layer (M8.9) — additive.

The read side of the durable Audit & Activity Platform. `ActivityFilter` is the shared,
immutable pagination/filter/time-range descriptor threaded through every activity query;
`GetRecentActivity`/`GetUserActivity` are the two new org-wide projections (the Organization/
Workspace/Project activity queries already exist — M8.6/M8.7/M8.8 — and are extended in place
to accept a filter). All of this is **read-model only**: it dispatches to the Foundation's
`ActivityProjectionService`, which runs projections over the durable audit stream. No business
logic, no transactions, no mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api.foundation import Foundation

from .dispatcher import QueryBus

# ── shared filter ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ActivityFilter:
    """Pagination + filtering + time-range for any activity projection. All optional;
    the defaults reproduce the pre-M8.9 behaviour (first page, no filter)."""

    limit: int = 50
    offset: int = 0
    actor: str | None = None
    event_types: tuple[str, ...] = ()
    since: float | None = None
    until: float | None = None

    def kwargs(self) -> dict:
        return {
            "limit": self.limit, "offset": self.offset, "actor": self.actor,
            "event_types": self.event_types, "since": self.since, "until": self.until,
        }

    def kwargs_no_actor(self) -> dict:
        """For projections whose actor is fixed by the route (recent/user)."""
        k = self.kwargs()
        k.pop("actor")
        return k


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetRecentActivity:
    org_id: str
    filter: ActivityFilter = field(default_factory=ActivityFilter)


@dataclass(frozen=True, slots=True)
class GetUserActivity:
    org_id: str
    user_id: str
    filter: ActivityFilter = field(default_factory=ActivityFilter)


# ── read service ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ActivityQueries:
    """Org-wide recent + per-user activity projections over the durable audit stream."""

    foundation: Foundation

    def recent(self, query: GetRecentActivity) -> dict:
        return self.foundation.activity.recent_activity(
            query.org_id, **query.filter.kwargs_no_actor()
        )

    def user(self, query: GetUserActivity) -> dict:
        return self.foundation.activity.user_activity(
            query.org_id, query.user_id, **query.filter.kwargs_no_actor()
        )


def build_and_register(*, foundation: Foundation, query_bus: QueryBus) -> None:
    """Wire the M8.9 recent/user activity queries into the existing query bus (additive)."""
    q = ActivityQueries(foundation=foundation)
    query_bus.register(GetRecentActivity, q.recent)
    query_bus.register(GetUserActivity, q.user)
