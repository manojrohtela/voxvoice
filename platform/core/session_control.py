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

from .audio import DEFAULT_SAMPLE_RATE


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
    # The session's audio sample rate in Hz (M11.2, ADR-0044 AD3). Sample rate is a
    # *session* property, never an ambient assumption (Constitution §A8, AR-41): the
    # Runtime converts to it at exactly one ingress and one egress point. Defaulted
    # here so existing callers are unaffected; the Runtime requires it explicitly.
    sample_rate: int = DEFAULT_SAMPLE_RATE
    # Barge-in (M11.4, ADR-0046 FT-D9). The Control Plane has exposed `allow_interruptions` since
    # M8 and it could never reach the Runtime: `AgentSpec.allow_interrupt` defaults False and no
    # field carried the caller's choice across this seam, so the M3.4/M3.5 Interrupt and Smart
    # Turn frameworks were complete, tested and **unreachable in production**. AR-25 requires
    # accepted configuration to act or the write to be rejected; GOVERNANCE §4 makes silent
    # acceptance a release blocker. This field is the missing carrier.
    allow_interruptions: bool = False
    # Audio-driven endpointing (M11.8, ADR-0047 ME1). When True the caller's turn is ended by
    # **true end-of-speech detected from audio** instead of by an STT Final. Requires a
    # voice-activity provider; a session that asks for it without one is refused at start (AR-25),
    # never silently downgraded. Default False keeps every existing profile's behaviour.
    audio_endpointing: bool = False
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
