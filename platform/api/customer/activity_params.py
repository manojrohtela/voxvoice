"""Activity query-parameter dependency (M8.9) — transport mapping only.

A single FastAPI dependency that maps the optional activity query-string parameters
(pagination + filtering + time-range) into the Application's `ActivityFilter`. Pure transport
translation — no business logic, no persistence, no authorization. Shared by every `/activity`
route so filtering/pagination/time-range are consistent across Organization/Workspace/Project/
Recent/User activity.
"""

from __future__ import annotations

from fastapi import Query

from api.application import ActivityFilter


def activity_filter(
    limit: int = Query(50, ge=1, le=200, description="page size"),
    offset: int = Query(0, ge=0, description="rows to skip (pagination)"),
    actor: str | None = Query(None, description="filter by acting user id"),
    event_type: list[str] | None = Query(
        None, description="filter by event type(s); repeatable"
    ),
    since: float | None = Query(None, description="inclusive lower bound on ts (epoch secs)"),
    until: float | None = Query(None, description="inclusive upper bound on ts (epoch secs)"),
) -> ActivityFilter:
    return ActivityFilter(
        limit=limit, offset=offset, actor=actor,
        event_types=tuple(event_type or ()), since=since, until=until,
    )
