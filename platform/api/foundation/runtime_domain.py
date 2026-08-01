"""Conversation Runtime domain model (M8.12) — additive.

The value objects + aggregates for the execution/orchestration plane that runs a **Published
Agent Version**. Everything is a frozen, slotted value object (the same discipline as the rest
of the foundation). The runtime is **deterministic** and produces an execution *plan* only — no
LLM, no tool execution, no voice, no model response.

Golden rules encoded here: a session references an **immutable published version number** (never
a draft); the `ConversationState` is the *only* thing carried between resume calls (the runtime
is otherwise stateless); nothing here mutates an Agent, a Tool, or any history.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

# ── enumerations ────────────────────────────────────────────────────────────────


class SessionStatus(StrEnum):
    """Conversation session lifecycle. `CLOSED`/`EXPIRED` are terminal; the row is retained
    (audit/isolation)."""

    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"


class VariableSource(StrEnum):
    """Where a resolved variable's value came from — the precedence ladder (later overrides
    earlier): SYSTEM < PROJECT < AGENT < RUNTIME."""

    SYSTEM = "system"
    PROJECT = "project"
    AGENT = "agent"
    RUNTIME = "runtime"


# ── resolution value objects ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ResolvedVariable:
    """One variable after precedence resolution. Secret values are **redacted at the read
    boundary** — the resolved value is kept for the (future) runtime but never emitted."""

    name: str
    value: str
    source: VariableSource
    is_secret: bool = False

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": ("" if (self.is_secret and redact_secrets) else self.value),
            "source": self.source.value,
            "is_secret": self.is_secret,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResolvedVariable:
        return cls(
            name=d["name"], value=d.get("value", ""),
            source=VariableSource(d.get("source", "system")),
            is_secret=bool(d.get("is_secret", False)),
        )


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    """The deterministically-assembled prompt. Sections are kept separate (for the inspector)
    plus a single assembled `text`. Secret variable values are masked in the text."""

    system: str = ""
    persona: str = ""
    instructions: str = ""
    conversation_style: str = ""
    variables_block: str = ""
    history_block: str = ""
    tool_context: str = ""
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "system": self.system, "persona": self.persona, "instructions": self.instructions,
            "conversation_style": self.conversation_style, "variables_block": self.variables_block,
            "history_block": self.history_block, "tool_context": self.tool_context,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResolvedPrompt:
        return cls(**{k: d.get(k, "") for k in (
            "system", "persona", "instructions", "conversation_style",
            "variables_block", "history_block", "tool_context", "text",
        )})


@dataclass(frozen=True, slots=True)
class ToolPlanStep:
    """One resolved tool in the execution plan — declaration only, **never executed**. `config`
    is the tool's typed settings merged with the per-agent override."""

    position: int
    tool_id: str
    name: str
    category: str
    enabled: bool
    config: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position, "tool_id": self.tool_id, "name": self.name,
            "category": self.category, "enabled": self.enabled, "config": self.config,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolPlanStep:
        return cls(
            position=d["position"], tool_id=d["tool_id"], name=d.get("name", ""),
            category=d.get("category", ""), enabled=bool(d.get("enabled", True)),
            config=dict(d.get("config", {})), depends_on=tuple(d.get("depends_on", ())),
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """The resolved, ordered tool plan + the adapter requirements the (future) runtime needs.
    Executable plan only — this milestone never runs it."""

    steps: tuple[ToolPlanStep, ...] = ()
    required_adapters: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.as_dict() for s in self.steps],
            "required_adapters": dict(self.required_adapters),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionPlan:
        return cls(
            steps=tuple(ToolPlanStep.from_dict(s) for s in d.get("steps", ())),
            required_adapters=dict(d.get("required_adapters", {})),
        )


@dataclass(frozen=True, slots=True)
class ConversationState:
    """The explicit, persisted state carried across resume calls (the runtime is otherwise
    stateless). Holds the resolved prompt, resolved variables, tool plan, and metadata —
    **not** message history (M8.12 persists no messages) and no vectors."""

    resolved_prompt: ResolvedPrompt = field(default_factory=ResolvedPrompt)
    resolved_variables: tuple[ResolvedVariable, ...] = ()
    tool_plan: ExecutionPlan = field(default_factory=ExecutionPlan)
    history: tuple[dict[str, Any], ...] = ()  # always empty in M8.12 (no message history)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "resolved_prompt": self.resolved_prompt.as_dict(),
            "resolved_variables": [
                v.as_dict(redact_secrets=redact_secrets) for v in self.resolved_variables
            ],
            "tool_plan": self.tool_plan.as_dict(),
            "history": list(self.history),
            "runtime_metadata": self.runtime_metadata,
            "execution_metadata": self.execution_metadata,
            "timing": self.timing,
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConversationState:
        return cls(
            resolved_prompt=ResolvedPrompt.from_dict(d.get("resolved_prompt", {})),
            resolved_variables=tuple(
                ResolvedVariable.from_dict(v) for v in d.get("resolved_variables", ())
            ),
            tool_plan=ExecutionPlan.from_dict(d.get("tool_plan", {})),
            history=tuple(d.get("history", ())),
            runtime_metadata=dict(d.get("runtime_metadata", {})),
            execution_metadata=dict(d.get("execution_metadata", {})),
            timing=dict(d.get("timing", {})),
            diagnostics=tuple(d.get("diagnostics", ())),
        )


# ── aggregate ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConversationSession:
    """A runtime session bound to one immutable Published Agent Version. Carries correlation
    ids + timestamps + metadata and the explicit `ConversationState`."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    agent_version: int
    correlation_id: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    state: ConversationState = field(default_factory=ConversationState)

    def with_status(
        self, status: SessionStatus, *, updated_at: float | None = None
    ) -> ConversationSession:
        return replace(
            self, status=status,
            updated_at=updated_at if updated_at is not None else self.updated_at,
        )

    def touched(self, updated_at: float) -> ConversationSession:
        return replace(self, updated_at=updated_at)
