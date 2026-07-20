"""Prompt Runtime inputs (M5.4B-1) — immutable request value objects.

`GenerationParameters` are **carried, never interpreted** — the Prompt Runtime records
them on the `PromptPackage` for the future Reasoning Runtime to apply at inference time; it
never runs a model. `PromptRequest` is the immutable construction request. Neither imports
a domain aggregate, so the package stays acyclic. The assembled context is passed to
`build()` separately as the frozen `core.ports.context.AssembledContext` (never the
`context` package).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    """Model generation knobs — carried through to the package, interpreted by Reasoning.
    The Prompt Runtime never reads these to make a decision (no inference)."""

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromptRequest:
    """A prompt-construction request. Immutable. `template_overrides` maps a template name
    (a section kind, e.g. ``"system"`` / ``"knowledge_context"``) to an inline body that
    takes highest precedence; `variables` are substituted into template placeholders."""

    session_id: str
    org_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    provider: str = "neutral"  # the ProviderProfile name (default = provider-neutral)
    model: str = "default"  # the ModelProfile name
    template_overrides: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    generation: GenerationParameters = field(default_factory=GenerationParameters)
    metadata: dict[str, str] = field(default_factory=dict)
