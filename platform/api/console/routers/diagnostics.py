"""`GET /v1/console/playground/sessions/{id}/diagnostics` — the session's runtime, as it happens.

One endpoint, one read, two uses. A browser watching a live call asks for everything after the
`seq` it has seen and keeps the connection open; a browser opening a finished call asks from 0 and
gets the whole session before the stream closes. Both run the same query against Epic 10's durable
log, so **live and replay cannot disagree** — there is one source of truth and this is a view of it
(ADR-0055 DP1: the console is a projection, never a second runtime).

Server-sent events rather than a WebSocket or a data channel: the payload is one-directional,
text, and already ordered by `seq`; SSE is a plain `GET`, so it carries the console's own
`HttpOnly` cookie, needs no new handshake, no new credential in the browser, and no dependency.
The signalling channel was rejected because its token is single-purpose and belongs to the media
path (security model S1), and a WebSocket would add a second authenticated transport for a stream
that never needs to be written to.

**Authorization is the Playground's, not Conversations'.** Live runtime detail is for whoever may
place the call: `developer` and above, exactly as `POST /playground/sessions` requires. A `viewer`
can read a conversation's transcript and timeline on the Conversations page and is refused here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session as DBSession

from ...db import engine as db_engine
from ...db import get_session
from ...models import Session as SessionRow
from ..conversations import service as conversations
from ..diagnostics import service as diagnostics
from ..identity.deps import Member, require_console_header, require_role

#: How often the stream looks for new events. One query per tick for one session — not one
#: request per event, and not a client-side poll.
POLL_INTERVAL_S = 0.4

#: A live call that produces nothing for this long still gets a comment frame, so an idle
#: connection is not dropped by an intermediary.
KEEPALIVE_S = 15.0

#: An upper bound on one connection, after which the browser reconnects with its last `seq`.
#: A stream is not a subscription: losing it loses nothing, because the log is durable.
MAX_STREAM_S = 1800.0

router = APIRouter(
    prefix="/v1/console/playground",
    tags=["console"],
    dependencies=[Depends(require_console_header)],
)


def _frame(name: str, body: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"


def _snapshot(db: DBSession, org_id: str, session_id: str, after_seq: int) -> dict[str, Any]:
    """One batch: the events after `after_seq`, the state they imply, and the transcript so far."""
    rows = diagnostics.read(db, org_id, session_id, after_seq=after_seq)
    # The state is derived from the whole session, not from one batch, so a reconnecting reader
    # that asks for a late `seq` is not told the call has only just started.
    whole = rows if after_seq == 0 else diagnostics.read(db, org_id, session_id)
    lines, ordered = conversations.transcript(db, session_id)
    return {
        "events": [row.as_json() for row in rows],
        "state": diagnostics.runtime_state(whole),
        "transcript": [
            {"role": item.role, "text": item.text, "offset_s": item.offset_s} for item in lines
        ],
        "transcript_ordered": ordered,
        "last_seq": max((row.seq for row in rows), default=after_seq),
    }


@router.get("/sessions/{session_id}/diagnostics")
async def stream(
    session_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    member: Member = Depends(require_role("developer")),
    db: DBSession = Depends(get_session),
) -> Any:
    """Stream this session's diagnostics. 404 unless the session is the caller's organization's."""
    from fastapi.responses import StreamingResponse

    session, _agent_name = conversations.get_conversation(db, member.org.id, session_id)
    org_id, ended = member.org.id, session.ended_at is not None

    async def events() -> AsyncIterator[str]:
        # Each tick opens its own short-lived database session: the request's own must not be held
        # open for the length of a call.
        cursor = after_seq
        elapsed = 0.0
        quiet = 0.0
        finished = ended
        try:
            while True:
                with DBSession(db_engine) as tick:
                    batch = _snapshot(tick, org_id, session_id, cursor)
                    row = tick.get(SessionRow, session_id)
                    over = finished or (row is not None and row.ended_at is not None)
                if batch["events"] or cursor == after_seq:
                    cursor = max(cursor, int(batch["last_seq"]))
                    yield _frame("batch", batch)
                    quiet = 0.0
                if over:
                    # Everything up to the end has been sent; a finished session produces no more.
                    yield _frame("end", {"state": batch["state"], "last_seq": cursor})
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(POLL_INTERVAL_S)
                elapsed += POLL_INTERVAL_S
                quiet += POLL_INTERVAL_S
                if quiet >= KEEPALIVE_S:
                    yield ": keepalive\n\n"
                    quiet = 0.0
                if elapsed >= MAX_STREAM_S:
                    yield _frame("end", {"state": batch["state"], "last_seq": cursor})
                    return
        except asyncio.CancelledError:  # the browser went away; nothing to clean up
            return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


__all__ = ["router"]
