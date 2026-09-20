"""Session configuration validation — the AR-25 boundary (M11.4).

**AR-25:** *"Accepted config must act — every accepted field is honoured by the runtime or the
write is rejected. Both directions: no dead config, no hidden tunables."* GOVERNANCE §4 makes
the point sharper: *"Every configuration field is enforced or rejected… Silent acceptance is a
release blocker."*

So this module rejects impossible configuration at the seam where the session is created, rather
than accepting it and quietly doing something else. That is the entire idea — a rejected write is
a *better* outcome than a session that runs with settings nobody honoured.

**Capability truth lives with the provider, not here** (AR-22, §A6.1). This module never asks
whether a model belongs to some particular vendor; it asks the **registry** what the
registered provider declared, and compares. There is no vendor name in this file and there must
never be one (AR-21).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import ConfigError
from runtime.contracts import AgentSpec, GreetingState
from runtime.registry import CapabilityKind, ProviderRegistry

from .guard import GuardPolicy

#: Sample rates the runtime will admit. Bounded on both ends because §A8 makes sample rate a
#: session property the media path converts to — a nonsensical value is a configuration error,
#: not something to discover at the first frame.
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 48_000


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover — formatting only
        return f"{self.field}: {self.detail}"


def validate_agent_spec(
    agent: AgentSpec,
    registry: ProviderRegistry,
    *,
    policy: GuardPolicy | None = None,
) -> tuple[ValidationIssue, ...]:
    """Every way this configuration cannot be honoured, as data.

    Returns issues rather than raising so a caller can report all of them at once — a validator
    that stops at the first problem makes fixing a configuration an iterative guessing game."""
    issues: list[ValidationIssue] = []
    issues.extend(_check_sample_rate(agent))
    issues.extend(_check_capability_selection(agent, registry))
    issues.extend(_check_model_selection(agent, registry))
    issues.extend(_check_interruption(agent, registry))
    issues.extend(_check_audio_endpointing(agent, registry))
    issues.extend(_check_greeting(agent))
    if policy is not None:
        issues.extend(_check_policy(policy))
    return tuple(issues)


def require_valid_agent_spec(
    agent: AgentSpec, registry: ProviderRegistry, *, policy: GuardPolicy | None = None
) -> None:
    """Raise `ConfigError` if the configuration cannot be honoured.

    `ConfigError`'s own docstring already states the stance this enforces: *"fail fast, do not run
    degraded"*. A configuration problem is not a degradation — a degradation is a capability the
    runtime adapts to at call time, whereas this is a session that should never start."""
    issues = validate_agent_spec(agent, registry, policy=policy)
    if issues:
        raise ConfigError(
            "session configuration cannot be honoured: " + "; ".join(str(i) for i in issues)
        )


# ── individual checks ────────────────────────────────────────────────────────


def _check_sample_rate(agent: AgentSpec) -> list[ValidationIssue]:
    rate = agent.sample_rate
    if not isinstance(rate, int) or isinstance(rate, bool):
        return [ValidationIssue("sample_rate", f"must be an integer, got {rate!r}")]
    if not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
        return [
            ValidationIssue(
                "sample_rate",
                f"{rate} Hz is outside the supported range "
                f"{MIN_SAMPLE_RATE}–{MAX_SAMPLE_RATE} Hz",
            )
        ]
    return []


def _selector(name: str | None) -> str | None:
    """`"default"`/empty mean *"whatever the registry defaults to"*, matching the Orchestrator."""
    return None if (not name or name == "default") else name


def _check_capability_selection(
    agent: AgentSpec, registry: ProviderRegistry
) -> list[ValidationIssue]:
    """A named provider that is not registered is dead configuration, caught here."""
    issues: list[ValidationIssue] = []
    for field_name, kind, selector in (
        ("stt_provider", CapabilityKind.STT, _selector(agent.stt_provider)),
        ("tts_provider", CapabilityKind.TTS, _selector(agent.tts_provider)),
    ):
        if selector is not None and not registry.has(kind, selector):
            issues.append(
                ValidationIssue(field_name, f"no provider named {selector!r} for {kind.value}")
            )
    return issues


def _check_model_selection(agent: AgentSpec, registry: ProviderRegistry) -> list[ValidationIssue]:
    """`llm_model` is validated against what the registered provider **declared**.

    A provider that enumerates no models (`models = ()`) is declaring *"I do not enumerate"*, and
    the honest response is to accept any selection rather than invent a whitelist on its behalf."""
    model = agent.llm_model
    if not model:
        return []
    if not registry.has(CapabilityKind.LLM):
        return [
            ValidationIssue("llm_model", "a model is selected but no LLM provider is registered")
        ]
    provider = registry.resolve(CapabilityKind.LLM)
    declared = tuple(getattr(provider.capabilities, "models", ()) or ())
    if declared and model not in declared:
        return [
            ValidationIssue(
                "llm_model",
                f"provider {provider.name!r} does not serve {model!r} "
                f"(declared: {list(declared)})",
            )
        ]
    return []


def _check_interruption(agent: AgentSpec, registry: ProviderRegistry) -> list[ValidationIssue]:
    """Barge-in without a speech signal is accepted-but-inert, which AR-25 forbids.

    The Interrupt framework needs voice activity to detect a barge-in at all, so asking for
    interruptions with no VAD registered is a configuration that cannot be honoured — and saying
    so is the difference between a rejected write and a caller who wonders why interrupting does
    nothing."""
    if agent.allow_interrupt and not registry.has(CapabilityKind.VOICE_ACTIVITY):
        return [
            ValidationIssue(
                "allow_interrupt",
                "interruptions require a voice-activity provider; none is registered",
            )
        ]
    return []


def _check_audio_endpointing(
    agent: AgentSpec, registry: ProviderRegistry
) -> list[ValidationIssue]:
    """Audio-driven endpointing without a speech signal is accepted-but-inert (M11.8, AR-25).

    The same stance `_check_interruption` takes, for the same reason: end-of-speech is an *audio*
    fact supplied by a voice-activity provider (ADR-0047 ME1), so asking for audio endpointing with
    no VAD registered is a configuration that cannot be honoured. Refusing at start is what stops a
    session from silently falling back to segment-driven turns while an operator believes otherwise.
    """
    if agent.audio_endpointing and not registry.has(CapabilityKind.VOICE_ACTIVITY):
        return [
            ValidationIssue(
                "audio_endpointing",
                "audio-driven endpointing requires a voice-activity provider; none is registered",
            )
        ]
    return []


def _check_greeting(agent: AgentSpec) -> list[ValidationIssue]:
    """`first_message` and `greeting_state` must agree — the AR-25 discharge for the greeting.

    M11.4 does not play the greeting (D-11 assigns that to M11.10), but it refuses to let the
    field sit unread: a configured greeting must be *declared pending*, and a declared-pending
    state with nothing to say is equally incoherent."""
    configured = bool(agent.first_message and agent.first_message.strip())
    state = agent.greeting_state
    if configured and state is not GreetingState.PENDING_PLAYBACK:
        return [
            ValidationIssue(
                "first_message",
                "a greeting is configured but its state is "
                f"{state.value!r}; it must be declared pending playback (M11.10)",
            )
        ]
    if not configured and state is GreetingState.PENDING_PLAYBACK:
        return [
            ValidationIssue("greeting_state", "declared pending playback with no first_message")
        ]
    return []


def _check_policy(policy: GuardPolicy) -> list[ValidationIssue]:
    """`GuardPolicy` validates itself on construction; this catches a breaker that cannot fire."""
    issues: list[ValidationIssue] = []
    breaker = policy.breaker
    if breaker is not None:
        if breaker.failure_threshold < 1:
            issues.append(
                ValidationIssue(
                    "breaker.failure_threshold",
                    f"must be at least 1, got {breaker.failure_threshold}",
                )
            )
        if breaker.reset_timeout_s < 0:
            issues.append(
                ValidationIssue(
                    "breaker.reset_timeout_s",
                    f"must not be negative, got {breaker.reset_timeout_s}",
                )
            )
    return issues


def greeting_state_for(first_message: str | None) -> GreetingState:
    """The state a `first_message` implies. One place, so the Control Plane seam and the
    validator cannot disagree about what "configured" means."""
    if first_message and first_message.strip():
        return GreetingState.PENDING_PLAYBACK
    return GreetingState.ABSENT


__all__ = [
    "MAX_SAMPLE_RATE",
    "MIN_SAMPLE_RATE",
    "ValidationIssue",
    "greeting_state_for",
    "require_valid_agent_spec",
    "validate_agent_spec",
]
