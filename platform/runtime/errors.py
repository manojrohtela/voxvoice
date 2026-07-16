"""Runtime-Plane error hierarchy (M2A).

Rooted in `core.errors.PlatformError` so every runtime failure is typed and
translatable at a boundary (CODING_STANDARDS §3). No silent failures: the
SessionWorker transitions to FAILED and raises one of these.
"""

from __future__ import annotations

from core.errors import PlatformError, RuntimeConflict


class WorkerError(PlatformError):
    """Base for SessionWorker lifecycle failures."""


class WorkerStartupError(WorkerError):
    """Startup/initialization failed; the worker is left FAILED."""


class WorkerShutdownError(WorkerError):
    """Graceful shutdown failed; the worker is left FAILED."""


class InvalidWorkerTransition(WorkerError):
    """An illegal worker state transition was attempted (deterministic guard)."""


# ── Conversation state machine (M2B.1) ──────────────────────────────────────
# Rooted in core.errors.RuntimeConflict (defined for "illegal state-machine
# transition or lifecycle violation") — reused, not duplicated.
class ConversationTransitionError(RuntimeConflict):
    """Base for illegal conversation-state transitions."""


class InvalidConversationTransition(ConversationTransitionError):
    """Target is not a legal transition from the current state."""


class TerminalConversationError(ConversationTransitionError):
    """Attempted to transition out of a terminal state (immutable)."""


class DuplicateConversationTransition(ConversationTransitionError):
    """Attempted to transition into the already-active state (no self-loops)."""


# ── Conversation Orchestrator (M2B.2) ───────────────────────────────────────
class OrchestrationError(PlatformError):
    """Base for Conversation Orchestrator failures."""


class OrchestrationStartupError(OrchestrationError):
    """Conversation startup (begin) failed; the conversation is left FAILED."""
