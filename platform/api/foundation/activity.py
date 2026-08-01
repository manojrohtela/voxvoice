"""Activity projections (M8.9) — additive, read-model only.

Activity feeds are **projections** over the append-only audit stream (`audit.AuditReader`),
not authoritative state. This service builds the five projections the Blueprint calls for —
Organization / Workspace / Project / Recent / User activity — each as a paginated, filtered,
time-rangeable page. It contains **no business logic**: it only shapes `AuditQuery`s, runs
them through the injected reader, and maps records to feed items. The domain summaries
(member counts, status) stay with the domain services; this service owns the *feed* only.
"""

from __future__ import annotations

from .audit import AuditQuery, AuditReader


class ActivityProjectionService:
    """Read-model projections over the durable audit stream. One injected `AuditReader`
    (in-memory for the default Foundation, SQL for the persistent one) — this service is
    unaware which, so the same projections run over either backing store."""

    def __init__(self, reader: AuditReader) -> None:
        self._reader = reader

    def _page(self, q: AuditQuery) -> dict:
        page = self._reader.query(q)
        return {
            "feed": [r.as_dict() for r in page.records],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
            "has_more": page.has_more,
            "next_offset": page.next_offset,
        }

    def organization_activity(
        self,
        org_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        actor: str | None = None,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        return self._page(AuditQuery(
            org_id=org_id, actor=actor, event_types=event_types, since=since, until=until,
            limit=limit, offset=offset,
        ))

    def workspace_activity(
        self,
        org_id: str,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        actor: str | None = None,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        return self._page(AuditQuery(
            org_id=org_id, workspace_id=workspace_id, actor=actor, event_types=event_types,
            since=since, until=until, limit=limit, offset=offset,
        ))

    def project_activity(
        self,
        org_id: str,
        workspace_id: str,
        project_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        actor: str | None = None,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        return self._page(AuditQuery(
            org_id=org_id, project_id=project_id, actor=actor, event_types=event_types,
            since=since, until=until, limit=limit, offset=offset,
        ))

    def recent_activity(
        self,
        org_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        """Org-wide most-recent activity across every resource (the activity landing feed)."""
        return self._page(AuditQuery(
            org_id=org_id, event_types=event_types, since=since, until=until,
            limit=limit, offset=offset,
        ))

    def user_activity(
        self,
        org_id: str,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        """Everything a given actor did within an org."""
        return self._page(AuditQuery(
            org_id=org_id, actor=user_id, event_types=event_types, since=since, until=until,
            limit=limit, offset=offset,
        ))

    def resource_activity(
        self,
        org_id: str,
        resource_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        actor: str | None = None,
        event_types: tuple[str, ...] = (),
        since: float | None = None,
        until: float | None = None,
    ) -> dict:
        """Every audit fact about one resource id (e.g. a single Agent) — used by the Agent
        Builder to show an agent's own history over the durable stream (M8.10)."""
        return self._page(AuditQuery(
            org_id=org_id, resource=resource_id, actor=actor, event_types=event_types,
            since=since, until=until, limit=limit, offset=offset,
        ))
