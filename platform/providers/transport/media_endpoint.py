"""The media-endpoint seam the WebRTC transport is built on (M11.5).

`WebRTCTransport` needs four things from a peer connection and nothing else: establish it, tear it
down, hand over an inbound audio source, and accept an outbound one. This module states exactly
that, as a Protocol.

**Why the seam exists.** Two reasons, and both are load-bearing:

1. **aiortc stays behind one door.** `AiortcMediaEndpoint` (in `aiortc_endpoint.py`) is the only
   module in the repository that imports the library. The transport itself — lifecycle, AR-6
   facts, bounded buffering, frame conversion — is first-party and library-free, which is what
   P-ADR-0002 means by *renting a narrow commodity library* rather than adopting a substrate.
2. **The contract is testable without a network.** A deterministic fake satisfies this Protocol,
   so every lifecycle, ordering, backpressure and failure assertion runs hermetically
   (TESTING_POLICY rule 1). A transport whose only proof required a live peer would be a
   transport nobody could regression-test.

Nothing here is vendor-shaped: the frames crossing this seam are `core.audio.AudioFrame`, so the
conversion from whatever the library produces happens **inside** the endpoint and never leaks
outward.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from core.audio import AudioFrame


class MediaEndpointError(Exception):
    """The endpoint could not do what was asked.

    Raised by an endpoint implementation and translated at the transport boundary into the
    `core.errors` taxonomy — a library exception never travels further than this module's
    implementations (AR-12, §A10)."""


@runtime_checkable
class MediaEndpoint(Protocol):
    """A duplex audio endpoint for one or more sessions, keyed by `session_id`."""

    async def connect(self, session_id: str) -> None:
        """Establish the media session. Returns when the peer connection is usable.

        Raises `MediaEndpointError` if it cannot be established — never returns a half-open
        endpoint, because a transport that reported `CONNECTED` for one of those would be
        fabricating the very fact AR-6 exists to make truthful."""

    async def close(self, session_id: str) -> None:
        """Tear the session down and release peer/media resources. Idempotent."""

    def inbound(self, session_id: str) -> AsyncIterator[AudioFrame]:
        """Yield decoded caller audio until the media path ends.

        Terminating normally means *the media path ended*; raising `MediaEndpointError` means it
        **broke**. The transport depends on that distinction to tell a hangup from a failure."""

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        """Place one already-normalized frame onto the outbound media path."""


__all__ = ["MediaEndpoint", "MediaEndpointError"]
