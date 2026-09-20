"""Real-time Transport capability contract (Constitution §A6).

Duplex audio between the caller endpoint and a Session Worker (a local/loopback
transport for dev now; WebRTC/telephony later — all behind this one contract).
The media path never touches the Control Plane.

M2C.2: refined from the M1 interface *essence* into an execution-ready shape
(audio in via `inbound()`, audio out via `send()`, lifecycle via `start`/`stop`).
Unreleased contract — no external consumers, so this is a refinement, not a
versioned break (Constitution §A6 describes essence, not signatures).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.audio import AudioFrame


class TransportEventKind(StrEnum):
    """What the transport actually did (M11.5, **AR-6**).

    AR-6: *"Any component learning of a real-world lifecycle fact (hangup, connect, failure) must
    emit the event that drives the state machine — status may never be inferred-only from API
    calls."* These four kinds are the transport's half of that rule, and each names a **fact**,
    never a request:

    * `CONNECTED` — the transport session is genuinely established. Not *"start() was called"*.
    * `MEDIA_READY` — media is genuinely usable. Distinct from `CONNECTED` because a connection
      can complete with no media flowing, and a runtime that treated the two as one would report
      a working call before a single frame existed.
    * `DISCONNECTED` — the transport went away. The real-world hangup.
    * `FAILED` — the transport could not be established, or died in a way that ends usability.
      Separate from `DISCONNECTED` because a clean hangup and a broken transport are different
      facts and lead to different session outcomes.

    Deliberately small: only the states the VoxVoice contract needs. A transport implementation's
    own state machine stays inside that implementation."""

    CONNECTED = "connected"
    MEDIA_READY = "media_ready"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TransportEvent:
    """One transport-reality fact. Data only — no vendor object, no credential, no SDP.

    `detail` is a short human-readable reason. It must never carry a token, a secret or raw
    connection internals (ADR-0049 CR5); the payload that reaches the event bus is built from
    these fields alone."""

    kind: TransportEventKind
    session_id: str
    detail: str = ""


#: How a transport reports reality. Declared on the `core` floor so a plugin can emit without
#: importing `runtime` (AR-11); the runtime maps these onto `EventType.TRANSPORT_*`.
TransportObserver = Callable[[TransportEvent], None]


@runtime_checkable
class ObservableTransport(Protocol):
    """A transport that can report AR-6 reality for a session.

    **Optional by design.** `TransportProvider` is unchanged, so `LocalTransport` — which has no
    real connection to report — neither implements this nor emits anything, and the local profile
    behaves exactly as before. A transport that *does* know real facts advertises it structurally,
    and the Orchestrator registers per session."""

    def observe(self, session_id: str, observer: TransportObserver) -> None:
        """Register the observer for one session's transport-reality facts."""


@dataclass(frozen=True, slots=True)
class TransportCapabilities:
    media_kinds: tuple[str, ...] = field(default_factory=lambda: ("audio",))
    telephony: bool = False
    reconnection: bool = False
    codecs: tuple[str, ...] = field(default_factory=tuple)


class TransportProvider(ABC):
    name: str
    capabilities: TransportCapabilities

    @abstractmethod
    async def start(self, *, session_id: str) -> None:
        """Establish the duplex media channel for a session."""

    @abstractmethod
    async def stop(self, *, session_id: str) -> None:
        """Tear the channel down. Must cause `inbound()` to terminate."""

    @abstractmethod
    def inbound(self, *, session_id: str) -> AsyncIterator[AudioFrame]:
        """Yield audio frames from the caller until the channel closes."""

    @abstractmethod
    async def send(self, *, session_id: str, frame: AudioFrame) -> None:
        """Play one audio frame back to the caller."""
