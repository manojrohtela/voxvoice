"""HookEvent builder (M6.3B) — deterministic identity, no live references."""

from __future__ import annotations

from collections.abc import Mapping

from .domain import HookEvent, HookPoint, HookRuntimeScope
from .ids import event_id_for


def build_event(
    point: HookPoint,
    scope: HookRuntimeScope,
    *,
    session_id: str = "",
    trace_id: str = "",
    org_id: str | None = None,
    workspace_id: str | None = None,
    payload_hash: str = "",
    now: float = 0.0,
    metadata: Mapping[str, str] | None = None,
) -> HookEvent:
    return HookEvent(
        event_id=event_id_for(point.value, scope.value, session_id, now),
        point=point, scope=scope, session_id=session_id, trace_id=trace_id,
        org_id=org_id, workspace_id=workspace_id, payload_hash=payload_hash,
        created_at=now, metadata=dict(metadata or {}),
    )
