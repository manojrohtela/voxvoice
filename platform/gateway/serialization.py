"""EventSerializer — transport-independent, versioned event encoding (M4.2).

Encodes a `core.events.Event` envelope to a stable, versioned JSON string that every
transport (WebSocket, SSE, future gRPC/…) delivers **identically**. It never exposes
a Runtime object — only the envelope's own fields. Schema evolution is handled by the
`v` version tag + additive fields (older clients ignore unknown keys).

Compression and binary encoding are **extension points** (override `frame` /
`encode`), not implemented here (M4.2 non-goals).
"""

from __future__ import annotations

import json

from core.events import Event

SCHEMA_VERSION = "1"


class EventSerializer:
    """Envelope → wire string. Deterministic (stable key order)."""

    def __init__(self, *, version: str = SCHEMA_VERSION) -> None:
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def to_dict(self, event: Event) -> dict:
        """The versioned wire shape — additive schema evolution lives here."""
        return {
            "v": self._version,
            "id": event.id,
            "type": event.type.value,
            "ts": event.ts,
            "seq": event.seq,
            "session_id": event.session_id,
            "source": event.source.value,
            "org_id": event.org_id,
            "conversation_id": event.conversation_id,
            "correlation_id": event.correlation_id,
            "turn": event.turn,
            "data": event.data,
            "metadata": event.metadata,
        }

    def serialize(self, event: Event) -> str:
        """Encode to JSON. Deterministic key order for replay-stable output."""
        return json.dumps(self.to_dict(event), separators=(",", ":"), sort_keys=True)

    # ── extension points (M4.3+; not implemented) ──────────────────────────
    def encode(self, event: Event) -> bytes:
        """Binary-encoding hook. Default: UTF-8 of the JSON. Override for a binary
        codec (protobuf/msgpack) — transports call `serialize`/`encode`, never touch
        the envelope, so a codec swap changes nothing upstream."""
        return self.serialize(event).encode("utf-8")

    def compress(self, payload: bytes) -> bytes:
        """Compression hook. Default: identity. Override to add gzip/deflate."""
        return payload
