"""Wires engine transcript events -> persistence -> webhook fan-out.

The platform registers `transcript_handler` with the active engine adapter at
startup (adapter.on_transcript). For each item the engine reports, we resolve
the call back to our Session, persist a Transcript row, and emit a
`transcript.item` webhook to the owning Org.
"""

from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session as DBSession
from sqlmodel import select

from webhooks import emit_event

from .db import engine as db_engine
from .models import Org, Session, Transcript

log = logging.getLogger("voxvoice.ingest")


async def transcript_handler(engine_ref: str, role: str, text: str, ts: float) -> None:
    with DBSession(db_engine) as db:
        session = db.exec(
            select(Session).where(Session.engine_ref == engine_ref)
        ).first()
        if session is None:
            log.warning("transcript for unknown engine_ref=%s, dropping", engine_ref)
            return

        db.add(
            Transcript(session_id=session.id, role=role, text=text, ts=ts)
        )
        db.commit()

        org = db.get(Org, session.org_id)
        if org is not None:
            await emit_event(
                org,
                "transcript.item",
                {"session_id": session.id, "role": role, "text": text, "ts": ts},
            )


async def register_transcript_ingest(adapter) -> None:  # noqa: ANN001 — EngineAdapter
    """Call from app startup. Skips engines whose on_transcript is not
    implemented (e.g. the LiveKit stub) so the app still boots."""
    try:
        await adapter.on_transcript(transcript_handler)
    except NotImplementedError:
        log.warning(
            "engine %s does not support on_transcript yet; transcripts disabled",
            getattr(adapter, "name", "?"),
        )


async def _poll_loop(adapter, engine_ref: str, interval: float, max_seconds: float) -> None:  # noqa: ANN001
    waited = 0.0
    while waited <= max_seconds:
        try:
            done = await adapter.poll_transcript(engine_ref)
        except Exception as exc:  # never let a poller crash take anything down
            log.warning("transcript poll failed for %s: %s", engine_ref, exc)
            done = False
        if done:
            await asyncio.sleep(interval)  # let the final artifact settle
            try:
                await adapter.poll_transcript(engine_ref)
            except Exception:  # noqa: BLE001
                pass
            return
        await asyncio.sleep(interval)
        waited += interval


def maybe_start_transcript_poller(
    adapter,  # noqa: ANN001 — EngineAdapter
    engine_ref: str | None,
    interval: float = 5.0,
    max_seconds: float = 1800.0,
) -> None:
    """Spawn a background poller that drains transcripts for engines without a
    push (Dograh). No-op for engines/sessions that don't support polling, so it
    is safe to call unconditionally from the session route."""
    if not engine_ref or not hasattr(adapter, "poll_transcript"):
        return
    asyncio.create_task(_poll_loop(adapter, engine_ref, interval, max_seconds))
