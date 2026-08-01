"""Agent Builder domain model (M8.10) — additive.

The **Agent** aggregate and its immutable configuration, kept in a dedicated module so the
M8.2 `domain.py` stays untouched (mirrors how the lifecycle services live in their own
modules). An Agent is a **configuration object only** — it belongs to exactly one Project,
holds no runtime/session/execution state, and never actuates anything (ADR-0021/0022). It
is authored, validated, versioned and published; it is never *run* here.

Everything is a frozen, slotted value object (the same discipline as the rest of the
foundation). Configuration is provider-independent by construction: the LLM/voice blocks
record a `provider`/`model` string pair and capability flags, never a vendor SDK — a future
provider needs no redesign. Secret variables carry their value in the domain but are
**redacted at every read boundary** (the view DTOs never emit a secret value).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

# ── enumerations ────────────────────────────────────────────────────────────────


class AgentStatus(StrEnum):
    """Agent lifecycle status. `DELETED` is a soft-delete marker — the row is retained
    (audit/isolation), never hard-deleted; `ARCHIVED` is restorable."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AgentVersionStatus(StrEnum):
    """A published version's status. Versions are immutable once created; a version can be
    superseded (a newer publish) or be the current published one."""

    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class VariableType(StrEnum):
    """The typed agent-variable kinds. `SECRET` values are never exposed by any API."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SECRET = "secret"


class ResponseLength(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class CreativityProfile(StrEnum):
    PRECISE = "precise"
    BALANCED = "balanced"
    CREATIVE = "creative"


class SafetyProfile(StrEnum):
    STRICT = "strict"
    STANDARD = "standard"
    RELAXED = "relaxed"


class ReasoningMode(StrEnum):
    NONE = "none"
    LIGHT = "light"
    EXTENDED = "extended"


# ── configuration value objects ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AgentVariable:
    """One typed agent variable. A `SECRET` variable keeps its value in the domain (so it
    round-trips through persistence) but is redacted wherever it is read out."""

    name: str
    type: VariableType = VariableType.STRING
    value: str = ""
    description: str = ""

    @property
    def is_secret(self) -> bool:
        return self.type is VariableType.SECRET

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        secret = self.is_secret
        return {
            "name": self.name,
            "type": self.type.value,
            "value": ("" if (secret and redact_secrets) else self.value),
            "description": self.description,
            "is_secret": secret,
            "has_value": bool(self.value),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentVariable:
        return cls(
            name=d["name"], type=VariableType(d.get("type", "string")),
            value=d.get("value", ""), description=d.get("description", ""),
        )


@dataclass(frozen=True, slots=True)
class PromptConfig:
    """The prompt builder surface — a system prompt, persona, instructions and a
    conversation style. No prompt is ever *executed* here (M8.10 is authoring only)."""

    system_prompt: str = ""
    persona: str = ""
    instructions: str = ""
    conversation_style: str = ""
    sections: tuple[str, ...] = ()  # reusable prompt sections (names/snippets)

    def as_dict(self) -> dict[str, Any]:
        return {
            "system_prompt": self.system_prompt, "persona": self.persona,
            "instructions": self.instructions, "conversation_style": self.conversation_style,
            "sections": list(self.sections),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromptConfig:
        return cls(
            system_prompt=d.get("system_prompt", ""), persona=d.get("persona", ""),
            instructions=d.get("instructions", ""),
            conversation_style=d.get("conversation_style", ""),
            sections=tuple(d.get("sections", ())),
        )


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Provider-independent LLM configuration. `provider`/`model` are opaque strings — the
    foundation never binds to a vendor. Sampling + capability flags are recorded, not acted."""

    provider: str = ""
    model: str = ""
    model_version: str = ""
    context_window: int = 0
    streaming_enabled: bool = True
    structured_output_enabled: bool = False
    reasoning_mode: ReasoningMode = ReasoningMode.NONE
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 1024
    response_length: ResponseLength = ResponseLength.MEDIUM
    creativity_profile: CreativityProfile = CreativityProfile.BALANCED
    safety_profile: SafetyProfile = SafetyProfile.STANDARD

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model,
            "model_version": self.model_version, "context_window": self.context_window,
            "streaming_enabled": self.streaming_enabled,
            "structured_output_enabled": self.structured_output_enabled,
            "reasoning_mode": self.reasoning_mode.value, "temperature": self.temperature,
            "top_p": self.top_p, "max_tokens": self.max_tokens,
            "response_length": self.response_length.value,
            "creativity_profile": self.creativity_profile.value,
            "safety_profile": self.safety_profile.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelConfig:
        return cls(
            provider=d.get("provider", ""), model=d.get("model", ""),
            model_version=d.get("model_version", ""),
            context_window=int(d.get("context_window", 0)),
            streaming_enabled=bool(d.get("streaming_enabled", True)),
            structured_output_enabled=bool(d.get("structured_output_enabled", False)),
            reasoning_mode=ReasoningMode(d.get("reasoning_mode", "none")),
            temperature=float(d.get("temperature", 0.7)), top_p=float(d.get("top_p", 1.0)),
            max_tokens=int(d.get("max_tokens", 1024)),
            response_length=ResponseLength(d.get("response_length", "medium")),
            creativity_profile=CreativityProfile(d.get("creativity_profile", "balanced")),
            safety_profile=SafetyProfile(d.get("safety_profile", "standard")),
        )


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Provider-independent voice configuration — settings only, no runtime (M8.10)."""

    enabled: bool = False
    provider: str = ""
    model: str = ""
    voice_name: str = ""
    language: str = "en"
    speaking_rate: float = 1.0
    pitch: float = 0.0
    volume: float = 1.0
    interruptions_allowed: bool = True
    barge_in_enabled: bool = True
    silence_timeout_ms: int = 4000

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "provider": self.provider, "model": self.model,
            "voice_name": self.voice_name, "language": self.language,
            "speaking_rate": self.speaking_rate, "pitch": self.pitch, "volume": self.volume,
            "interruptions_allowed": self.interruptions_allowed,
            "barge_in_enabled": self.barge_in_enabled,
            "silence_timeout_ms": self.silence_timeout_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VoiceConfig:
        return cls(
            enabled=bool(d.get("enabled", False)), provider=d.get("provider", ""),
            model=d.get("model", ""), voice_name=d.get("voice_name", ""),
            language=d.get("language", "en"),
            speaking_rate=float(d.get("speaking_rate", 1.0)),
            pitch=float(d.get("pitch", 0.0)), volume=float(d.get("volume", 1.0)),
            interruptions_allowed=bool(d.get("interruptions_allowed", True)),
            barge_in_enabled=bool(d.get("barge_in_enabled", True)),
            silence_timeout_ms=int(d.get("silence_timeout_ms", 4000)),
        )


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """The full, immutable agent configuration: prompt + model + voice + typed variables.
    Serialized to a single JSON column in persistence; `as_dict(redact_secrets=...)` is the
    one place secret redaction is applied for reads."""

    prompt: PromptConfig = field(default_factory=PromptConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    variables: tuple[AgentVariable, ...] = ()

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "prompt": self.prompt.as_dict(),
            "model": self.model.as_dict(),
            "voice": self.voice.as_dict(),
            "variables": [v.as_dict(redact_secrets=redact_secrets) for v in self.variables],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        return cls(
            prompt=PromptConfig.from_dict(d.get("prompt", {})),
            model=ModelConfig.from_dict(d.get("model", {})),
            voice=VoiceConfig.from_dict(d.get("voice", {})),
            variables=tuple(
                AgentVariable.from_dict(v) for v in d.get("variables", ())
            ),
        )

    def merged(self, patch: dict[str, Any] | None) -> AgentConfig:
        """Return a new config with `patch` (a partial nested dict) applied over this one.
        Only the sub-blocks present in `patch` are replaced; `variables`, if present, fully
        replaces the set. Deterministic; never mutates self."""
        if not patch:
            return self
        prompt = (
            PromptConfig.from_dict({**self.prompt.as_dict(), **patch["prompt"]})
            if "prompt" in patch else self.prompt
        )
        model = (
            ModelConfig.from_dict({**self.model.as_dict(), **patch["model"]})
            if "model" in patch else self.model
        )
        voice = (
            VoiceConfig.from_dict({**self.voice.as_dict(), **patch["voice"]})
            if "voice" in patch else self.voice
        )
        variables = (
            tuple(AgentVariable.from_dict(v) for v in patch["variables"])
            if "variables" in patch else self.variables
        )
        return AgentConfig(prompt=prompt, model=model, voice=voice, variables=variables)


# ── aggregates ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Agent:
    """An AI-native agent configuration owned by exactly one Project. Configuration object
    only — no runtime, no conversations, no deployment (M8.10)."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    description: str = ""
    icon: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    language: str = "en"
    greeting: str = ""
    status: AgentStatus = AgentStatus.DRAFT
    config: AgentConfig = field(default_factory=AgentConfig)
    # Versioning pointers (M8.10). `version` is the latest published version number (0 = never
    # published); `published_version` is the currently-live one (0 when unpublished/draft).
    version: int = 0
    published_version: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        category: str | None = None,
        tags: tuple[str, ...] | None = None,
        language: str | None = None,
        greeting: str | None = None,
        status: AgentStatus | None = None,
        config: AgentConfig | None = None,
        version: int | None = None,
        published_version: int | None = None,
        updated_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        """Immutable partial update — only provided fields change (services validate)."""
        return replace(
            self,
            name=name if name is not None else self.name,
            description=description if description is not None else self.description,
            icon=icon if icon is not None else self.icon,
            category=category if category is not None else self.category,
            tags=tags if tags is not None else self.tags,
            language=language if language is not None else self.language,
            greeting=greeting if greeting is not None else self.greeting,
            status=status if status is not None else self.status,
            config=config if config is not None else self.config,
            version=version if version is not None else self.version,
            published_version=(
                published_version if published_version is not None else self.published_version
            ),
            updated_at=updated_at if updated_at is not None else self.updated_at,
            metadata=metadata if metadata is not None else dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class AgentVersion:
    """An immutable published snapshot of an Agent (M8.10). Created on every publish; never
    updated or deleted. `snapshot` is the full agent config + descriptive fields captured at
    publish time; `rollback_target` records the version a rollback publish was created from."""

    id: str
    agent_id: str
    org_id: str
    workspace_id: str
    project_id: str
    number: int
    created_by: str = ""
    created_at: float = 0.0
    description: str = ""
    status: AgentVersionStatus = AgentVersionStatus.PUBLISHED
    rollback_target: int | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)

    def superseded(self) -> AgentVersion:
        return replace(self, status=AgentVersionStatus.SUPERSEDED)
