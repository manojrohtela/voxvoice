"""Real-time Transport capability contract (Constitution §A6).

Duplex media frames + client events between the caller endpoint and a Session
Worker (WebRTC now; telephony/SIP later — all behind this one contract).
The media path never touches the Control Plane. Interface only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
    async def connect(self, *, session_id: str) -> None:
        """Establish the duplex media channel for a session."""

    @abstractmethod
    async def close(self, *, session_id: str) -> None:
        """Tear the channel down."""
