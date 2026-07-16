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


# ── Provider Registry (M2C.1) ────────────────────────────────────────────────
# Rooted directly in PlatformError — these are registration/resolution-time
# errors, distinct from core.errors.ProviderError (execution-time vendor
# failures wrapped by the fault-tolerance layer, Constitution §A10).
class ProviderRegistryError(PlatformError):
    """Base for Provider Registry errors."""


class DuplicateProviderRegistration(ProviderRegistryError):
    """A provider is already registered for this (capability, name), or a
    default is already set for this capability."""


class ProviderCapabilityMismatch(ProviderRegistryError):
    """The provider instance does not implement the declared capability
    contract (fails the isinstance check against the capability ABC)."""


class CapabilityNotRegistered(ProviderRegistryError):
    """No provider is registered for the requested capability/name, or
    resolution is ambiguous (multiple providers, no default, no name given)."""


class InvalidProviderRegistration(ProviderRegistryError):
    """The provider instance is None, or is missing required identity (`name`)
    or declared capability metadata (`capabilities`)."""


class UnsupportedCapabilityKind(ProviderRegistryError):
    """The capability kind is not one the registry currently knows how to
    validate (Constitution §A12: new kinds are an additive extension)."""
