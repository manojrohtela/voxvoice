"""Runtime-Plane error hierarchy (M2A).

Rooted in `core.errors.PlatformError` so every runtime failure is typed and
translatable at a boundary (CODING_STANDARDS §3). No silent failures: the
SessionWorker transitions to FAILED and raises one of these.
"""

from __future__ import annotations

from core.errors import PlatformError


class WorkerError(PlatformError):
    """Base for SessionWorker lifecycle failures."""


class WorkerStartupError(WorkerError):
    """Startup/initialization failed; the worker is left FAILED."""


class WorkerShutdownError(WorkerError):
    """Graceful shutdown failed; the worker is left FAILED."""


class InvalidWorkerTransition(WorkerError):
    """An illegal worker state transition was attempted (deterministic guard)."""
