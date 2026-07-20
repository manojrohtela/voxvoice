"""Cancellation Manager (M5.5B-2) — always reaches a terminal state.

Tracks in-flight inference by cancellation token and turns a cancel signal (manual, timeout,
provider, tool, streaming, or whole-session) into a deterministic drive to the terminal
`CANCELLED` state via the shared FSM. It records a `CancellationRecord` (reason + time) onto
the package and preserves the cancellation history on the lineage. Cancellation is guaranteed
terminal: once requested for a token, `is_cancelled` stays true and `apply` yields a
`CANCELLED` package.

No networking: it signals; the provider adapter (later) observes the token and stops streaming.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .domain import CancellationRecord, InferenceState, ResponsePackage
from .lifecycle import InferenceTrigger, advance


class CancelReason(StrEnum):
    MANUAL = "manual"
    TIMEOUT = "timeout"
    PROVIDER = "provider"
    TOOL = "tool"
    STREAMING = "streaming"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class CancellationSignal:
    token: str
    reason: CancelReason
    at: float = 0.0


class CancellationManager:
    """Deterministic cancellation registry + terminal-state driver. No I/O."""

    def __init__(self) -> None:
        self._cancelled: dict[str, CancellationSignal] = {}
        self._session_cancelled: set[str] = set()
        self._count = 0

    # ── signalling ────────────────────────────────────────────────────────────
    def cancel(self, token: str, reason: CancelReason, *, now: float = 0.0) -> CancellationSignal:
        sig = CancellationSignal(token=token, reason=reason, at=now)
        self._cancelled.setdefault(token, sig)
        self._count += 1
        return self._cancelled[token]

    def cancel_session(self, session_id: str, *, now: float = 0.0) -> None:
        """Session cancel — every subsequent token in the session is cancelled."""
        self._session_cancelled.add(session_id)
        self.cancel(f"session:{session_id}", CancelReason.SESSION, now=now)

    def is_cancelled(self, token: str | None, *, session_id: str | None = None) -> bool:
        if session_id is not None and session_id in self._session_cancelled:
            return True
        return token is not None and token in self._cancelled

    def signal_for(self, token: str | None) -> CancellationSignal | None:
        return self._cancelled.get(token) if token is not None else None

    @property
    def statistics(self) -> dict[str, int]:
        return {"cancelled": self._count, "sessions": len(self._session_cancelled)}

    # ── terminal-state application (always reaches CANCELLED) ──────────────────
    def apply(
        self, package: ResponsePackage, signal: CancellationSignal, *, clock=None
    ) -> ResponsePackage:
        """Drive `package` to the terminal `CANCELLED` state and stamp the record + lineage.
        Idempotent: applying to an already-cancelled package is a no-op."""
        if package.state is InferenceState.CANCELLED:
            return package
        record = CancellationRecord(reason=signal.reason.value, at=signal.at)
        cancelled = advance(package, InferenceTrigger.CANCEL, clock=clock)
        lineage = replace(
            cancelled.lineage,
            cancellation_history=(*cancelled.lineage.cancellation_history, record),
        )
        return replace(cancelled, cancellation=record, lineage=lineage)
