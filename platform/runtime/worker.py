"""SessionWorker — Runtime Plane host (Constitution §A4.2, ADR-0003).

Component specification (Constitution §A11.3):
  responsibilities : host N session actors; admit sessions up to capacity;
                     apply backpressure; reap finished/failed sessions.
  inputs           : session assignments (from the Control Plane via the
                     session-control interface); the active Event Bus.
  outputs          : per-worker health/saturation gauges; session actors.
  dependencies     : core (contracts, events), the Conversation Orchestrator,
                     the Provider Registry. Never imports `api`.
  lifecycle        : start -> accept sessions -> drain -> stop.
  emitted events   : none directly (its session actors emit `session.*`).
  failure modes    : worker crash -> in-flight sessions recovered from their
                     last context snapshot on another worker (Constitution §A7).
  recovery         : re-admit recoverable sessions from snapshots.

SKELETON ONLY (M1). No behaviour — implemented at M2.
"""

from __future__ import annotations


class SessionWorker:
    """Hosts live session actors. Not implemented until M2 (Runtime Plane seed)."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "SessionWorker is a Runtime Plane component — implemented at M2, not M1"
        )
