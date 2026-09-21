"""Conversation projections (M12.5, LLD §6).

**Nothing here is written, and nothing is a second source of truth** (ADR-0055 DP6, ADR-0021):

* the conversation *is* the M11 `session` row (status, agent, timestamps, metadata);
* its words are the M11 `transcript` rows (final items only — the durable log deliberately holds
  no transcript text, M11.12 §32);
* its turns and lifecycle are the Epic 10 durable log (`evt_event`): `metric.turn` from the sole
  producer `TurnLatencyTap`, and the allow-listed lifecycle events below.

Every read is scoped by the organization resolved from the console session (S3). Events are
filtered on **both** `session_id` and `org_id`, so even a malformed row cannot leak across tenants.
Raw event payloads never leave this module: each is mapped to a product view, and the monotonic
instants inside `metric.turn` (`at`) are not exposed at all.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import select

from core.events import Event, EventType
from events.persistence.mapping import to_event
from events.persistence.models import DurableEventRow

from ...models import Agent, Session, Transcript
from ..errors import InvalidRequest, NotFound

#: Where a session came from, read from the metadata its creator recorded.
PLAYGROUND_SOURCE = "console_playground"

#: Lifecycle events shown on the timeline, with their product labels. Everything else in the log
#: (token streams, memory, tools, ...) is engine detail, not conversation history.
#: `provider.connected/disconnected` are excluded too: every session emits one per provider at
#: start and at a normal end (seen at M12.5 in a live run), so on a timeline they read as failures.
#: Real failures surface as `runtime.error` and `transport.failed`.
TIMELINE_LABELS: dict[EventType, str] = {
    EventType.SESSION_STARTED: "Session started",
    EventType.TRANSPORT_CONNECTED: "Caller connected",
    EventType.TRANSPORT_MEDIA_READY: "Audio flowing",
    EventType.TURN_STARTED: "Turn started",
    EventType.TURN_COMPLETED: "Turn completed",
    EventType.TURN_CANCELLED: "Turn cancelled",
    EventType.INTERRUPT_CONFIRMED: "Caller interrupted",
    EventType.RUNTIME_ERROR: "Runtime error",
    EventType.TRANSPORT_FAILED: "Media failed",
    EventType.TRANSPORT_DISCONNECTED: "Caller disconnected",
    EventType.SESSION_ENDED: "Session ended",
}

#: The latency stages `metric.turn` derives (ADR-0038 TP5, ADR-0047 ME2), in chain order.
STAGES = ("endpoint", "stt", "llm", "tts", "egress")

MAX_PAGE = 100


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def source_of(session: Session) -> str:
    meta = session.meta or {}
    return PLAYGROUND_SOURCE if meta.get("source") == PLAYGROUND_SOURCE else "api"


def duration_s(session: Session) -> float | None:
    started, ended = _aware(session.started_at), _aware(session.ended_at)
    if started is None or ended is None:
        return None
    return max(0.0, round((ended - started).total_seconds(), 1))


# ── cursor ────────────────────────────────────────────────────────────────────


def encode_cursor(session: Session) -> str:
    started = _aware(session.started_at) or datetime.fromtimestamp(0, UTC)
    raw = f"{started.isoformat()}|{session.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        started_raw, session_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        started = datetime.fromisoformat(started_raw)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidRequest("That page cursor is not valid") from exc
    return started, session_id


# ── list ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConversationRow:
    session: Session
    agent_name: str | None
    message_count: int


def list_conversations(
    db: DBSession,
    org_id: str,
    *,
    agent_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> tuple[list[ConversationRow], str | None]:
    """Newest first, keyset-paginated on `(started_at, id)`."""
    limit = max(1, min(limit, MAX_PAGE))
    counts = (
        select(Transcript.session_id, func.count().label("messages"))
        .group_by(Transcript.session_id)
        .subquery()
    )
    query = (
        select(Session, Agent.name, counts.c.messages)
        .join(Agent, Agent.id == Session.agent_id, isouter=True)
        .join(counts, counts.c.session_id == Session.id, isouter=True)
        .where(Session.org_id == org_id)
    )
    if agent_id:
        query = query.where(Session.agent_id == agent_id)
    if status:
        query = query.where(Session.status == status)
    if cursor:
        started, session_id = decode_cursor(cursor)
        naive = started.astimezone(UTC).replace(tzinfo=None)  # stored naive-UTC, like all M11 times
        query = query.where(
            (Session.started_at < naive)
            | ((Session.started_at == naive) & (Session.id < session_id))
        )
    rows = db.exec(
        query.order_by(Session.started_at.desc(), Session.id.desc()).limit(limit + 1)  # type: ignore[union-attr,attr-defined]
    ).all()
    page = [ConversationRow(s, name, int(n or 0)) for s, name, n in rows[:limit]]
    next_cursor = encode_cursor(page[-1].session) if len(rows) > limit and page else None
    return page, next_cursor


# ── detail ────────────────────────────────────────────────────────────────────


def get_conversation(db: DBSession, org_id: str, session_id: str) -> tuple[Session, str | None]:
    session = db.get(Session, session_id)
    if session is None or session.org_id != org_id:
        raise NotFound("Conversation not found")
    agent = db.get(Agent, session.agent_id)
    return session, agent.name if agent else None


@dataclass(frozen=True)
class TranscriptItem:
    role: str
    text: str
    offset_s: float


def transcript(db: DBSession, session_id: str) -> tuple[list[TranscriptItem], bool]:
    """Final transcript items in spoken order, and whether that order is known.

    Order is `ts` — seconds since the session started, written by the transcript sink since M12.5
    (finding F-3). Sessions recorded before that (or by the frozen default app) carry `ts = 0.0` on
    every row and ids are random, so their order is **unknown**: reported, never guessed."""
    rows = db.exec(
        select(Transcript)
        .where(Transcript.session_id == session_id)
        .order_by(Transcript.ts, Transcript.id)  # type: ignore[arg-type]
    ).all()
    ordered = len(rows) < 2 or any(row.ts > 0 for row in rows)
    return [TranscriptItem(r.role, r.text, round(r.ts, 2)) for r in rows], ordered


def events(db: DBSession, org_id: str, session_id: str) -> list[Event]:
    """The session's durable events, in the bus's own order (`seq`), through Epic 10's mapping.

    The same query as `SqlDurableEventLog.read_session`, plus the tenant predicate that method has
    no reason to know about, run on the request's own session rather than the log's."""
    rows = db.exec(
        select(DurableEventRow)
        .where(DurableEventRow.session_id == session_id, DurableEventRow.org_id == org_id)
        .order_by(DurableEventRow.seq, DurableEventRow.event_id)  # type: ignore[arg-type]
    ).all()
    return [to_event(row) for row in rows]


@dataclass(frozen=True)
class Turn:
    turn: int | None
    status: str | None
    voice_to_voice_ms: int | None
    stages_ms: dict[str, int]
    user_speech_ms: int | None
    #: Stages the runtime could not measure for this turn — absent, never reported as 0 (E11-10).
    unmeasured: list[str]


def turns(event_log: list[Event]) -> list[Turn]:
    result: list[Turn] = []
    for event in event_log:
        if event.type is not EventType.METRIC_TURN:
            continue
        data: dict[str, Any] = event.data or {}
        ms = {k: v for k, v in (data.get("ms") or {}).items() if isinstance(v, int)}
        stages = {stage: ms[stage] for stage in STAGES if stage in ms}
        v2v = data.get("voice_to_voice_ms")
        result.append(
            Turn(
                turn=event.turn,
                status=data.get("status") if isinstance(data.get("status"), str) else None,
                voice_to_voice_ms=v2v if isinstance(v2v, int) else None,
                stages_ms=stages,
                user_speech_ms=ms.get("user_speech"),
                unmeasured=[s for s in STAGES if s not in stages],
            )
        )
    return result


@dataclass(frozen=True)
class TimelineEntry:
    seq: int
    type: str
    label: str
    turn: int | None
    offset_s: float


def timeline(event_log: list[Event]) -> list[TimelineEntry]:
    shown = [e for e in event_log if e.type in TIMELINE_LABELS]
    if not shown:
        return []
    origin = min(e.ts for e in event_log)
    return [
        TimelineEntry(
            seq=e.seq,
            type=e.type.value,
            label=TIMELINE_LABELS[e.type],
            turn=e.turn,
            offset_s=round(max(0.0, e.ts - origin), 2),
        )
        for e in shown
    ]
