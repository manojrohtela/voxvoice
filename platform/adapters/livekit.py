"""LiveKitAdapter — fallback engine, interface-compatible STUB only (SPEC §5).

Becomes the real implementation only if the owner's Hinglish hands-on test
fails and we switch the primary engine (SPEC §10 open-decision 1). Until then
every method raises NotImplementedError so misconfiguration fails loudly rather
than silently faking a call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import EngineAdapter, EngineSession, TranscriptHandler

if TYPE_CHECKING:
    from api.models import Agent, Session

_TODO = "LiveKitAdapter is a stub (SPEC §10 decision 1); implement if LiveKit becomes primary"


class LiveKitAdapter(EngineAdapter):
    name = "livekit"

    async def start_session(self, agent: Agent, session: Session) -> EngineSession:
        # TODO: create a LiveKit room, mint an access token, start the agent worker.
        raise NotImplementedError(_TODO)

    async def end_session(self, session: Session) -> None:
        # TODO: delete/disconnect the LiveKit room.
        raise NotImplementedError(_TODO)

    async def on_transcript(self, handler: TranscriptHandler) -> None:
        # TODO: subscribe to LiveKit transcription events and pump into handler.
        raise NotImplementedError(_TODO)
