"""Session-control interface — the Control Plane ↔ Runtime Plane seam (M2C.2).

Per Constitution §A4.1 (AR-14) the Control Plane (`api`) reaches the Runtime
Plane ONLY through a session-control interface (this) and the Event Bus. The
interface is a `core` contract (definition only); `runtime` implements it and it
is injected into `api` at the composition root. `api` never imports `runtime`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StartSessionRequest:
    """Everything the Runtime needs to start one session's conversation. Built by
    the Control Plane from the org-scoped Agent + session it just persisted."""

    session_id: str
    org_id: str
    agent_id: str
    channel: str
    system_prompt: str
    first_message: str | None = None
    language: str = "en-IN"
    stt_provider: str = "default"
    tts_provider: str = "default"
    tts_voice: str | None = None
    llm_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StartSessionResult:
    """Connection details returned to the consumer. `web`/`phone` shapes are
    finalized with real transports + the SDK (later)."""

    session_id: str
    engine: str
    channel: str
    web: dict[str, Any] | None = None
    phone: dict[str, Any] | None = None


# The Control Plane provides this sink to the Runtime to receive transcript
# items as they are produced: (session_id, role, text, is_final).
TranscriptSink = Callable[[str, str, str, bool], None]


class SessionController(ABC):
    """Implemented by the Runtime Plane; consumed by the Control Plane."""

    @abstractmethod
    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        """Create + start a runtime session; return its connection details."""

    @abstractmethod
    async def end(self, session_id: str) -> None:
        """End a running session (idempotent)."""

    @abstractmethod
    def conversation_state(self, session_id: str) -> str | None:
        """Current conversation state of a session, or None if unknown."""
