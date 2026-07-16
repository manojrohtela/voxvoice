"""Engine adapter interface (SPEC §5).

The platform never imports a concrete engine. It depends only on this ABC, the
`EngineSession` return shape, and the typed `EngineError`. Swapping engines
(Dograh <-> LiveKit) must touch nothing outside the `adapters/` package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with api.models
    from api.models import Agent, Session


class EngineError(Exception):
    """Typed platform error wrapping any engine failure. Raw engine errors are
    never leaked to consumers — the API maps this to a clean 502."""


@dataclass
class WebConnInfo:
    # SPEC §4 shape, kept stable for consumers.
    token: str
    url: str
    room: str
    # Dograh-honest extras (superset, owner decision 2026-06-15). Optional so
    # engines without them (e.g. a future LiveKit) just leave them None.
    embed_script: str | None = None
    turn_url: str | None = None
    run_id: str | None = None


@dataclass
class PhoneConnInfo:
    call_id: str


@dataclass
class EngineSession:
    engine_ref: str
    web: WebConnInfo | None = None
    phone: PhoneConnInfo | None = None


# A transcript handler receives (engine_ref, role, text, ts).
TranscriptHandler = Callable[[str, str, str, float], Awaitable[None]]


class EngineAdapter(ABC):
    name: str
    _transcript_handler: TranscriptHandler | None = None

    @abstractmethod
    async def start_session(self, agent: Agent, session: Session) -> EngineSession:
        """Create a call/room in the engine for this agent's config and return
        connection details (web token+url+room, or a phone call id)."""

    @abstractmethod
    async def end_session(self, session: Session) -> None:
        ...

    @abstractmethod
    async def on_transcript(self, handler: TranscriptHandler) -> None:
        """Register a callback the engine invokes with transcript items; the
        platform persists them and fans them out via webhooks."""

    async def dispatch_transcript(
        self, engine_ref: str, role: str, text: str, ts: float
    ) -> None:
        """Invoke the registered handler for one incoming transcript item. The
        engine event-ingestion endpoint calls this; concrete adapters may also
        call it from a streaming subscription."""
        if self._transcript_handler is None:
            return
        await self._transcript_handler(engine_ref, role, text, ts)

    # ─── Optional now, required for parity later (Phase 3). ─────────────────
    async def start_outbound_call(self, agent, to_number, session):  # noqa: ANN001
        raise NotImplementedError("start_outbound_call is a Phase 3 deliverable")

    async def register_inbound_number(self, number, agent):  # noqa: ANN001
        raise NotImplementedError("register_inbound_number is a Phase 3 deliverable")
