"""The Control Plane's WebRTC signalling channel (M11.16.4).

`providers/transport/signalling.py` defines `SdpExchange` — *one* async method that carries an
offer out and brings an answer back — and deliberately leaves the channel to the deployment.
This package **is** that channel for the first-party deployment: a bounded, org-scoped,
in-process rendezvous plus the HTTP endpoints an external peer talks to.

It is not a signalling *server* in the conferencing sense. There are no rooms, no membership, no
presence, no TURN and no SFU (§4). One external peer joins one VoxVoice session, and nothing
here knows what a second peer would mean.
"""

from .binding import PENDING_RENDEZVOUS, RendezvousBindingController
from .config import WebRTCConfigError, WebRTCSettings
from .rendezvous import (
    MAX_OPEN_RENDEZVOUS,
    MAX_SIGNALLING_SDP_BYTES,
    RENDEZVOUS_TTL_S,
    RendezvousError,
    RendezvousRegistry,
    RendezvousState,
)

__all__ = [
    "MAX_OPEN_RENDEZVOUS",
    "PENDING_RENDEZVOUS",
    "MAX_SIGNALLING_SDP_BYTES",
    "RENDEZVOUS_TTL_S",
    "RendezvousError",
    "RendezvousBindingController",
    "RendezvousRegistry",
    "RendezvousState",
    "WebRTCConfigError",
    "WebRTCSettings",
]
