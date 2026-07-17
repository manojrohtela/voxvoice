"""Runtime-Plane error hierarchy (M2A).

Rooted in `core.errors.PlatformError` so every runtime failure is typed and
translatable at a boundary (CODING_STANDARDS §3). No silent failures: the
SessionWorker transitions to FAILED and raises one of these.
"""

from __future__ import annotations

from core.errors import PlatformError, ProviderError, RuntimeConflict


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


class PipelineError(OrchestrationError):
    """A runtime pipeline stage (transport/STT/LLM/TTS) failed during execution."""


# ── Turn Engine (M3.1) ───────────────────────────────────────────────────────
# Rooted in core.errors.RuntimeConflict (illegal state-machine transition), like
# the conversation transition errors — reused, not duplicated.
class TurnError(RuntimeConflict):
    """Base for turn-lifecycle errors."""


class InvalidTurnTransition(TurnError):
    """Target is not a legal transition from the current turn phase."""


class TerminalTurnError(TurnError):
    """Attempted to transition out of a terminal turn phase (completed/cancelled)."""


class DuplicateTurnTransition(TurnError):
    """Attempted to transition into the already-active turn phase (no self-loops)."""


class NoActiveTurnError(TurnError):
    """An operation requires an active turn but none exists."""


# ── Voice Activity Framework (M3.2) ──────────────────────────────────────────
# State-machine violations root in core.errors.RuntimeConflict (like the turn and
# conversation machines). A provider *execution* failure is different — it roots
# in core.errors.ProviderError (a capability failed), not a state conflict.
class VoiceActivityError(RuntimeConflict):
    """Base for voice-activity state-machine errors."""


class InvalidVoiceActivityTransition(VoiceActivityError):
    """Target is not a legal transition from the current voice-activity state."""


class VoiceActivityProviderError(ProviderError):
    """A voice-activity provider failed to start, stop, or detect. Auxiliary to
    the conversation: the coordinator/pipeline degrade gracefully rather than
    letting it break the call (DL-0013)."""


# ── Endpoint Intelligence Framework (M3.3) ───────────────────────────────────
# State-machine violations root in core.errors.RuntimeConflict (like the turn,
# conversation, and voice-activity machines). The endpoint framework has no
# provider — it is pure runtime logic driven by a configurable policy — so there
# is no provider-error variant here.
class EndpointError(RuntimeConflict):
    """Base for endpoint-decision state-machine errors."""


class InvalidEndpointTransition(EndpointError):
    """Target is not a legal transition from the current endpoint decision."""


class DuplicateEndpointDecision(EndpointError):
    """Attempted to transition into the already-active decision (no self-loops)."""


# ── Interrupt Intelligence Framework (M3.4) ──────────────────────────────────
# State-machine violations root in core.errors.RuntimeConflict (like the turn,
# endpoint, voice-activity, and conversation machines). The interrupt framework
# has no provider — it is pure runtime logic driven by a configurable policy — so
# there is no provider-error variant; `AssistantStopError` is a lifecycle conflict
# (the pipeline failed to stop assistant playback during a confirmed interrupt).
class InterruptError(RuntimeConflict):
    """Base for interrupt-decision state-machine errors."""


class InvalidInterruptTransition(InterruptError):
    """Target is not a legal transition from the current interrupt decision."""


class DuplicateInterruptDecision(InterruptError):
    """Attempted to transition into the already-active decision (no self-loops)."""


class AssistantStopError(InterruptError):
    """Assistant playback could not be stopped during a confirmed interrupt."""


# ── Smart Turn Intelligence Engine (M3.5) ────────────────────────────────────
# The Smart Turn engine arbitrates the sub-frameworks' decisions; its own errors
# are lifecycle/config conflicts rooted in core.errors.RuntimeConflict (like the
# turn/endpoint/interrupt/voice-activity machines). Conflicting sub-decisions are
# resolved by a fixed, documented precedence — not an error. The semantic evaluator
# is a provider behind a neutral contract; a misbehaving one degrades to the
# identity hint (logged), it does not raise into the deterministic core.
class SmartTurnError(RuntimeConflict):
    """Base for Smart Turn engine errors."""


class InvalidSmartTurnPolicy(SmartTurnError):
    """A SmartTurnPolicy has an invalid value/combination (rejected at construction)."""


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
