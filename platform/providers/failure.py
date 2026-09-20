"""The neutral failure vocabulary every streaming provider seam classifies into (M11.9).

M11.6 introduced this enum inside `stt/transcription_session.py` because STT was the first seam
that needed it. M11.9 adds a second seam — streaming LLM completions — with exactly the same
classification problem, and duplicating the vocabulary would mean two enums for one concept
(AI_AGENT_RULES rule 14, Constitution §A0: one concept, one name).

So it is promoted here, unchanged, and both seams import it. `providers/failure.py` sits at the
`providers` root deliberately: it is shared *between plugins* and belongs to none of them, and it
imports nothing at all — so it creates no coupling in either direction (AR-11).

Why the vocabulary exists at all rather than each adapter raising `core.errors` directly:
classification happens once, in the adapter that can see the vendor's status codes, and
translation into the platform taxonomy happens once, in the provider that owns it. That keeps the
vendor SDK behind one door (AR-12) while keeping `core.errors` the only taxonomy the runtime sees.
"""

from __future__ import annotations

from enum import StrEnum


class FailureKind(StrEnum):
    """Why a provider session failed.

    One member per `core.errors` class a provider maps to, and nothing finer — a distinction the
    runtime cannot act on differently is decoration (ADR-0046 FT-D1)."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    PROTOCOL = "protocol"
    TRUNCATED = "truncated"


class SessionFailure(Exception):
    """A provider session could not do what was asked.

    Carries a `FailureKind` and a **sanitized** detail. An adapter must never place a credential,
    a request header, a URL with query parameters or a raw vendor message into `detail`
    (ADR-0049 CR5/CR6) — the detail ends up in a typed error message, and an error message is a
    sink like any other.

    Seams subclass this so a caller can catch one seam's failures specifically; the shared base
    exists so the `kind`/`detail` carrying logic is written once."""

    def __init__(self, kind: FailureKind, detail: str = "") -> None:
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)
        self.kind = kind
        self.detail = detail


__all__ = ["FailureKind", "SessionFailure"]
