"""ReplaySerializer (M4.3).

Turns the read-only replay views into stable JSON-able dicts for the API / UI. It
never exposes a Runtime object — only recorded envelopes + derived, display-only
structures. Deterministic (stable field order via the dataclasses).
"""

from __future__ import annotations

import json
from dataclasses import asdict

from core.events import Event

from .inspector import RuntimeStateSnapshot
from .metrics import ConversationMetrics
from .recorder import ReplaySession
from .timeline import Timeline, TimelineEntry


class ReplaySerializer:
    def event(self, e: Event) -> dict:
        return {
            "id": e.id,
            "seq": e.seq,
            "ts": e.ts,
            "type": e.type.value,
            "source": e.source.value,
            "session_id": e.session_id,
            "conversation_id": e.conversation_id,
            "correlation_id": e.correlation_id,
            "org_id": e.org_id,
            "turn": e.turn,
            "data": e.data,
            "metadata": e.metadata,
        }

    def timeline_entry(self, entry: TimelineEntry) -> dict:
        return asdict(entry) | {"type": entry.type.value}

    def timeline(self, tl: Timeline) -> dict:
        return {
            "start_ts": tl.start_ts,
            "end_ts": tl.end_ts,
            "duration_ms": tl.duration_ms,
            "count": len(tl),
            "entries": [self.timeline_entry(e) for e in tl.entries],
        }

    def metrics(self, m: ConversationMetrics) -> dict:
        return m.to_dict()

    def snapshot(self, s: RuntimeStateSnapshot) -> dict:
        return s.to_dict()

    def session_summary(self, session: ReplaySession) -> dict:
        return {
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "org_id": session.org_id,
            "event_count": session.event_count,
            "duration_s": session.duration_s,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "closed": session.closed,
        }

    def to_json(self, payload: dict) -> str:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
