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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.audio import AudioFrame


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
