"""Tool Framework domain model (M8.11) — additive.

The **Tool** aggregate, its provider-independent configuration, and the **AgentTool**
attachment (the join that lets a published Agent declare which tools it uses, in what order,
with what per-agent overrides). Kept in a dedicated module so the M8.2 `domain.py` stays
untouched (mirrors `agent_domain.py`).

A Tool is a **declaration**, not an implementation — it belongs to one Project and is reusable
across that project's agents. This framework **never executes a tool** and **knows nothing
about any LLM**: categories (HTTP/Webhook/Database/Function/MCP/…) are metadata only, and all
configuration is **typed data** (plain dicts/values) — never an SDK object or a provider client.
Conversation Runtime (a later, separate milestone) consumes this framework; nothing here calls
out to anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

# ── enumerations ────────────────────────────────────────────────────────────────


class ToolCategory(StrEnum):
    """What kind of tool this is — **metadata only**, no implementation is implied or wired.
    Additive: new categories append here without touching behaviour."""

    KNOWLEDGE = "knowledge"
    HTTP_API = "http_api"
    WEBHOOK = "webhook"
    DATABASE = "database"
    FUNCTION = "function"
    MCP = "mcp"
    CUSTOM = "custom"


class ToolStatus(StrEnum):
    """Tool lifecycle status. `DELETED` is a soft-delete marker (row retained for
    audit/isolation); `ARCHIVED` is restorable. `enabled` is a separate flag (a tool can be
    published-but-disabled)."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ToolVisibility(StrEnum):
    """How widely a tool is offered in the catalog — metadata for the Studio library filter.
    The isolation boundary is always the project; visibility never widens access."""

    PROJECT = "project"
    WORKSPACE = "workspace"
    ORGANIZATION = "organization"


# ── configuration value objects (typed data — no SDK objects) ─────────────────────


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """A provider-independent retry declaration. Data only — nothing here retries anything."""

    max_retries: int = 0
    backoff_seconds: float = 0.0
    retry_on: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries, "backoff_seconds": self.backoff_seconds,
            "retry_on": list(self.retry_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RetryPolicy:
        return cls(
            max_retries=int(d.get("max_retries", 0)),
            backoff_seconds=float(d.get("backoff_seconds", 0.0)),
            retry_on=tuple(d.get("retry_on", ())),
        )


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """The full, immutable tool configuration — **typed data only**. `config_schema`/
    `input_schema`/`output_schema` are plain JSON-schema-shaped dicts; `settings` holds the
    concrete configured values; `depends_on` lists other tool ids this tool builds on (used
    only for circular-reference validation — this framework never resolves or executes them)."""

    config_schema: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 30_000
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    depends_on: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "config_schema": self.config_schema, "input_schema": self.input_schema,
            "output_schema": self.output_schema, "settings": self.settings,
            "timeout_ms": self.timeout_ms, "retry": self.retry.as_dict(),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolConfig:
        return cls(
            config_schema=dict(d.get("config_schema", {})),
            input_schema=dict(d.get("input_schema", {})),
            output_schema=dict(d.get("output_schema", {})),
            settings=dict(d.get("settings", {})),
            timeout_ms=int(d.get("timeout_ms", 30_000)),
            retry=RetryPolicy.from_dict(d.get("retry", {})),
            depends_on=tuple(d.get("depends_on", ())),
        )

    def merged(self, patch: dict[str, Any] | None) -> ToolConfig:
        """Return a new config with `patch` (a partial dict) applied over this one. Only the
        keys present in `patch` are replaced (`retry`, if present, replaces wholesale).
        Deterministic; never mutates self."""
        if not patch:
            return self
        base = self.as_dict()
        base.update({k: v for k, v in patch.items() if k != "retry"})
        if "retry" in patch:
            base["retry"] = {**base.get("retry", {}), **patch["retry"]}
        return ToolConfig.from_dict(base)


# ── aggregates ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Tool:
    """A reusable tool declaration owned by one Project (M8.11). Declaration only — never
    executed here. `version` is a monotonic counter bumped on each config change (the tool's
    history lives in the durable audit stream)."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    category: ToolCategory = ToolCategory.CUSTOM
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    description: str = ""
    icon: str = ""
    status: ToolStatus = ToolStatus.DRAFT
    visibility: ToolVisibility = ToolVisibility.PROJECT
    enabled: bool = True
    version: int = 1
    config: ToolConfig = field(default_factory=ToolConfig)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(
        self,
        *,
        name: str | None = None,
        category: ToolCategory | None = None,
        description: str | None = None,
        icon: str | None = None,
        status: ToolStatus | None = None,
        visibility: ToolVisibility | None = None,
        enabled: bool | None = None,
        version: int | None = None,
        config: ToolConfig | None = None,
        updated_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Tool:
        """Immutable partial update — only provided fields change (services validate)."""
        return replace(
            self,
            name=name if name is not None else self.name,
            category=category if category is not None else self.category,
            description=description if description is not None else self.description,
            icon=icon if icon is not None else self.icon,
            status=status if status is not None else self.status,
            visibility=visibility if visibility is not None else self.visibility,
            enabled=enabled if enabled is not None else self.enabled,
            version=version if version is not None else self.version,
            config=config if config is not None else self.config,
            updated_at=updated_at if updated_at is not None else self.updated_at,
            metadata=metadata if metadata is not None else dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class AgentTool:
    """The attachment of a Tool to an Agent (M8.11) — the join carrying per-agent ordering,
    an enable flag, and an optional config override. Isolation is org+workspace+project scoped
    by construction; an agent may only attach tools from its own project."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    tool_id: str
    position: int = 0
    enabled: bool = True
    config_override: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def with_updates(
        self,
        *,
        position: int | None = None,
        enabled: bool | None = None,
        config_override: dict[str, Any] | None = None,
    ) -> AgentTool:
        return replace(
            self,
            position=position if position is not None else self.position,
            enabled=enabled if enabled is not None else self.enabled,
            config_override=(
                config_override if config_override is not None else dict(self.config_override)
            ),
        )
