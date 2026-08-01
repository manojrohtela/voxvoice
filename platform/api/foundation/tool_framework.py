"""Tool Framework domain services (M8.11) — additive.

Two services behind interfaces, in a separate module so the M8.2 `services.py` stays untouched
(mirrors `agent_builder.py`):

  * `ToolCatalogService` — the reusable tool library within a project: register/update/
    enable/disable/archive/restore/soft-delete, plus list/get/search and validation.
  * `AgentToolService` — the agent↔tool attachment surface: attach/detach/enable/disable/
    reorder/configure, plus list.

**Reuse-first (the milestone architecture rule).** No new RBAC scope — tools live in a Project,
so writes authorize via the existing **workspace-scoped** `PROJECT_MANAGE` and reads via
`PROJECT_READ`. History reuses the durable **Audit Platform** (M8.9): every tool/attachment
fact is emitted as a `FoundationEvent`. **Tool execution is abstracted away entirely** — this
framework declares and validates tools; it never runs one, never calls an external API, and
**knows nothing about any LLM**. Configuration is typed data (plain dicts) — no SDK objects, no
provider clients.
"""

from __future__ import annotations

from .diagnostics import (
    Diagnostic,
    FoundationDiagnosticCode,
    FoundationDiagnostics,
    Severity,
)
from .errors import (
    ConflictError,
    FoundationValidationError,
    NotFoundError,
)
from .events import FoundationEventType
from .rbac import Permission
from .repositories import (
    AgentRepository,
    AgentToolRepository,
    ProjectRepository,
    ToolRepository,
)
from .seams import PREFIX_AGENT_TOOL, PREFIX_TOOL
from .services import _Base
from .tool_domain import (
    AgentTool,
    Tool,
    ToolCategory,
    ToolConfig,
    ToolStatus,
    ToolVisibility,
)


def _diag(path: str, code: FoundationDiagnosticCode, msg: str, fix: str) -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, msg, fix)


def validate_tool(name: str, category: str, config: ToolConfig) -> FoundationDiagnostics:
    """Structural validation for a tool + its configuration (deterministic, never raises).

    Covers required name, a known category, typed-data schemas (dicts only — no SDK objects),
    a positive timeout, and a sane retry policy. Duplicate-name, isolation, and circular
    references need the repositories and live in the service."""
    out: list[Diagnostic] = []
    if not name.strip():
        out.append(_diag(
            "name", FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
            "tool name must be non-empty", "give the tool a name",
        ))
    if category not in tuple(c.value for c in ToolCategory):
        out.append(_diag(
            "category", FoundationDiagnosticCode.INVALID_TOOL_CATEGORY,
            f"unknown tool category {category!r}",
            "use one of: " + ", ".join(c.value for c in ToolCategory),
        ))
    for key in ("config_schema", "input_schema", "output_schema", "settings"):
        value = getattr(config, key)
        if not isinstance(value, dict):
            out.append(_diag(
                f"config.{key}", FoundationDiagnosticCode.INVALID_TOOL_CONFIG,
                f"{key} must be typed data (an object), not {type(value).__name__}",
                "represent configuration as plain typed data (no SDK objects)",
            ))
    if config.timeout_ms <= 0:
        out.append(_diag(
            "config.timeout_ms", FoundationDiagnosticCode.INVALID_TOOL_CONFIG,
            "timeout_ms must be positive", "set a positive timeout",
        ))
    if config.retry.max_retries < 0:
        out.append(_diag(
            "config.retry.max_retries", FoundationDiagnosticCode.INVALID_TOOL_CONFIG,
            "max_retries cannot be negative", "use 0 or more",
        ))
    return FoundationDiagnostics.of(out)


class ToolCatalogService(_Base):
    """The reusable tool library within a project. Every write authorizes via the
    workspace-scoped `PROJECT_MANAGE` permission (reuse-first — no new RBAC scope)."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        tools: ToolRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._tools = tools

    def _load_project(self, org_id: str, workspace_id: str, project_id: str):
        p = self._projects.get(org_id, workspace_id, project_id)
        if p is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        return p

    def _load(self, org_id: str, project_id: str, tool_id: str) -> Tool:
        t = self._tools.get(org_id, project_id, tool_id)
        if t is None or t.status is ToolStatus.DELETED:
            raise NotFoundError(f"tool {tool_id!r} not found in project {project_id!r}")
        return t

    def _require_unique_name(
        self, org_id: str, project_id: str, name: str, *, exclude_id: str | None = None
    ) -> None:
        existing = self._tools.by_name(org_id, project_id, name)
        if existing is not None and existing.id != exclude_id:
            raise FoundationValidationError(
                FoundationDiagnostics.of([_diag(
                    "name", FoundationDiagnosticCode.DUPLICATE_NAME,
                    f"a tool named {name!r} already exists in this project",
                    "choose a different tool name",
                )])
            )

    def _check_no_cycle(
        self, org_id: str, project_id: str, tool_id: str, depends_on: tuple[str, ...]
    ) -> None:
        """Reject a dependency set that would introduce a circular reference among the
        project's tools (the candidate's edges are overlaid on the live graph)."""
        graph: dict[str, tuple[str, ...]] = {}
        for t in self._tools.list(org_id, project_id):
            if t.status is ToolStatus.DELETED:
                continue
            graph[t.id] = t.config.depends_on
        graph[tool_id] = depends_on  # overlay the candidate's proposed edges
        # DFS cycle detection from the candidate.
        visiting: set[str] = set()
        done: set[str] = set()

        def walk(node: str) -> bool:
            if node in done:
                return False
            if node in visiting:
                return True
            visiting.add(node)
            for dep in graph.get(node, ()):  # unknown deps are inert (no edge)
                if dep in graph and walk(dep):
                    return True
            visiting.discard(node)
            done.add(node)
            return False

        if walk(tool_id):
            raise FoundationValidationError(
                FoundationDiagnostics.of([_diag(
                    "config.depends_on", FoundationDiagnosticCode.CIRCULAR_TOOL_REFERENCE,
                    "these dependencies would create a circular tool reference",
                    "remove the dependency that closes the cycle",
                )])
            )

    def _validate_or_raise(self, name: str, category: str, config: ToolConfig) -> None:
        diagnostics = validate_tool(name, category, config)
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)

    # ── catalog / registration ────────────────────────────────────────────────────
    def create_tool(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        *,
        name: str,
        category: ToolCategory = ToolCategory.CUSTOM,
        description: str = "",
        icon: str = "",
        visibility: ToolVisibility = ToolVisibility.PROJECT,
        config: ToolConfig | None = None,
    ) -> Tool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_project(org_id, workspace_id, project_id)
        cfg = config or ToolConfig()
        self._validate_or_raise(name, category.value, cfg)
        self._require_unique_name(org_id, project_id, name)
        tool = Tool(
            id=self._ids.new(PREFIX_TOOL), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=name, category=category, created_by=actor_user_id,
            created_at=self._clock.now(), updated_at=self._clock.now(), description=description,
            icon=icon, status=ToolStatus.PUBLISHED, visibility=visibility, enabled=True,
            version=1, config=cfg,
        )
        if cfg.depends_on:
            self._check_no_cycle(org_id, project_id, tool.id, cfg.depends_on)
        self._tools.add(tool)
        self._emit_tool(FoundationEventType.TOOL_CREATED, actor_user_id, tool)
        return tool

    def update_tool(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        tool_id: str,
        *,
        name: str | None = None,
        category: ToolCategory | None = None,
        description: str | None = None,
        icon: str | None = None,
        visibility: ToolVisibility | None = None,
        config_patch: dict | None = None,
        metadata: dict | None = None,
    ) -> Tool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        t = self._load(org_id, project_id, tool_id)
        new_config = t.config.merged(config_patch) if config_patch else t.config
        new_name = name if name is not None else t.name
        new_category = category.value if category is not None else t.category.value
        self._validate_or_raise(new_name, new_category, new_config)
        if name is not None and name.strip().casefold() != t.name.strip().casefold():
            self._require_unique_name(org_id, project_id, name, exclude_id=t.id)
        if config_patch and "depends_on" in config_patch:
            self._check_no_cycle(org_id, project_id, t.id, new_config.depends_on)
        # A config change bumps the tool version (its history is the durable audit stream).
        bump = 1 if config_patch else 0
        updated = t.with_updates(
            name=name, category=category, description=description, icon=icon,
            visibility=visibility, config=new_config, metadata=metadata,
            version=t.version + bump, updated_at=self._clock.now(),
        )
        self._tools.update(updated)
        self._emit_tool(FoundationEventType.TOOL_UPDATED, actor_user_id, updated)
        return updated

    def set_enabled(
        self, actor_user_id, org_id, workspace_id, project_id, tool_id, *, enabled: bool
    ) -> Tool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        t = self._load(org_id, project_id, tool_id)
        if t.enabled == enabled:
            raise ConflictError(
                f"tool {tool_id!r} is already {'enabled' if enabled else 'disabled'}"
            )
        updated = self._tools.update(
            t.with_updates(enabled=enabled, updated_at=self._clock.now())
        )
        self._emit_tool(
            FoundationEventType.TOOL_ENABLED if enabled else FoundationEventType.TOOL_DISABLED,
            actor_user_id, updated,
        )
        return updated

    def archive_tool(self, actor_user_id, org_id, workspace_id, project_id, tool_id) -> Tool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        t = self._load(org_id, project_id, tool_id)
        if t.status is ToolStatus.ARCHIVED:
            raise ConflictError(f"tool {tool_id!r} is already archived")
        updated = self._tools.update(
            t.with_updates(status=ToolStatus.ARCHIVED, updated_at=self._clock.now())
        )
        self._emit_tool(FoundationEventType.TOOL_ARCHIVED, actor_user_id, updated)
        return updated

    def restore_tool(self, actor_user_id, org_id, workspace_id, project_id, tool_id) -> Tool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        t = self._load(org_id, project_id, tool_id)
        if t.status is not ToolStatus.ARCHIVED:
            raise ConflictError(f"tool {tool_id!r} is not archived")
        updated = self._tools.update(
            t.with_updates(status=ToolStatus.PUBLISHED, updated_at=self._clock.now())
        )
        self._emit_tool(FoundationEventType.TOOL_RESTORED, actor_user_id, updated)
        return updated

    def delete_tool(self, actor_user_id, org_id, workspace_id, project_id, tool_id) -> Tool:
        """Soft-delete — marks the tool DELETED (row retained for audit/isolation)."""
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        t = self._load(org_id, project_id, tool_id)
        updated = self._tools.update(
            t.with_updates(status=ToolStatus.DELETED, updated_at=self._clock.now())
        )
        self._emit_tool(FoundationEventType.TOOL_DELETED, actor_user_id, updated)
        return updated

    # ── reads ─────────────────────────────────────────────────────────────────────
    def get_tool(self, org_id: str, project_id: str, tool_id: str) -> Tool:
        return self._load(org_id, project_id, tool_id)

    def list_tools(
        self, org_id: str, project_id: str, *, include_archived: bool = True
    ) -> tuple[Tool, ...]:
        out = []
        for t in self._tools.list(org_id, project_id):
            if t.status is ToolStatus.DELETED:
                continue
            if not include_archived and t.status is ToolStatus.ARCHIVED:
                continue
            out.append(t)
        return tuple(out)

    def search_tools(self, org_id: str, project_id: str, term: str) -> tuple[Tool, ...]:
        needle = term.strip().casefold()
        return tuple(
            t
            for t in self.list_tools(org_id, project_id)
            if needle in t.name.casefold()
            or needle in t.description.casefold()
            or needle in t.category.value
        )

    def _emit_tool(self, event, actor_user_id, tool: Tool) -> None:
        self._emit(
            event, org_id=tool.org_id, actor=actor_user_id, resource=tool.id,
            workspace_id=tool.workspace_id, project_id=tool.project_id, name=tool.name,
        )


class AgentToolService(_Base):
    """The agent↔tool attachment surface (M8.11). An agent may only attach tools from its own
    project (isolation); the attachment carries ordering, an enable flag and a config override.
    No tool is ever executed."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        agents: AgentRepository,
        tools: ToolRepository,
        agent_tools: AgentToolRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._agents = agents
        self._tools = tools
        self._agent_tools = agent_tools

    def _load_agent(self, org_id: str, project_id: str, agent_id: str):
        a = self._agents.get(org_id, project_id, agent_id)
        if a is None:
            raise NotFoundError(f"agent {agent_id!r} not found in project {project_id!r}")
        return a

    def _load_tool(self, org_id: str, project_id: str, tool_id: str) -> Tool:
        t = self._tools.get(org_id, project_id, tool_id)
        if t is None or t.status is ToolStatus.DELETED:
            raise NotFoundError(f"tool {tool_id!r} not found in project {project_id!r}")
        return t

    def _load_attachment(self, org_id: str, agent_id: str, tool_id: str) -> AgentTool:
        at = self._agent_tools.get(org_id, agent_id, tool_id)
        if at is None:
            raise NotFoundError(
                f"tool {tool_id!r} is not attached to agent {agent_id!r}"
            )
        return at

    def attach_tool(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, tool_id, *, position=None
    ) -> AgentTool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_agent(org_id, project_id, agent_id)
        self._load_tool(org_id, project_id, tool_id)  # isolation: same project only
        if self._agent_tools.get(org_id, agent_id, tool_id) is not None:
            raise ConflictError(
                f"tool {tool_id!r} is already attached to agent {agent_id!r}"
            )
        existing = self._agent_tools.list(org_id, agent_id)
        pos = position if position is not None else len(existing)
        attachment = AgentTool(
            id=self._ids.new(PREFIX_AGENT_TOOL), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id, tool_id=tool_id, position=pos,
            enabled=True, created_at=self._clock.now(),
        )
        self._agent_tools.add(attachment)
        self._emit_attachment(
            FoundationEventType.TOOL_ATTACHED, actor_user_id, attachment
        )
        return attachment

    def detach_tool(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, tool_id
    ) -> None:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_attachment(org_id, agent_id, tool_id)
        self._agent_tools.remove(org_id, agent_id, tool_id)
        self._emit(
            FoundationEventType.TOOL_DETACHED, org_id=org_id, actor=actor_user_id,
            resource=agent_id, workspace_id=workspace_id, project_id=project_id,
            agent_id=agent_id, tool_id=tool_id,
        )

    def set_attachment_enabled(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, tool_id, *, enabled
    ) -> AgentTool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        at = self._load_attachment(org_id, agent_id, tool_id)
        updated = self._agent_tools.update(at.with_updates(enabled=enabled))
        self._emit_attachment(
            FoundationEventType.TOOL_ATTACHMENT_ENABLED
            if enabled else FoundationEventType.TOOL_ATTACHMENT_DISABLED,
            actor_user_id, updated,
        )
        return updated

    def configure_attachment(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, tool_id, *, config_override
    ) -> AgentTool:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        at = self._load_attachment(org_id, agent_id, tool_id)
        updated = self._agent_tools.update(
            at.with_updates(config_override=dict(config_override or {}))
        )
        self._emit_attachment(
            FoundationEventType.TOOL_ATTACHMENT_CONFIGURED, actor_user_id, updated
        )
        return updated

    def reorder_tools(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, ordered_tool_ids
    ) -> tuple[AgentTool, ...]:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        current = {at.tool_id: at for at in self._agent_tools.list(org_id, agent_id)}
        if set(ordered_tool_ids) != set(current):
            raise ConflictError(
                "reorder must list exactly the agent's currently-attached tools"
            )
        out = []
        for pos, tool_id in enumerate(ordered_tool_ids):
            out.append(self._agent_tools.update(current[tool_id].with_updates(position=pos)))
        self._emit(
            FoundationEventType.TOOL_REORDERED, org_id=org_id, actor=actor_user_id,
            resource=agent_id, workspace_id=workspace_id, project_id=project_id,
            agent_id=agent_id, order=list(ordered_tool_ids),
        )
        return tuple(sorted(out, key=lambda a: a.position))

    def list_agent_tools(self, org_id: str, agent_id: str) -> tuple[AgentTool, ...]:
        return tuple(
            sorted(self._agent_tools.list(org_id, agent_id), key=lambda a: a.position)
        )

    def _emit_attachment(self, event, actor_user_id, at: AgentTool) -> None:
        self._emit(
            event, org_id=at.org_id, actor=actor_user_id, resource=at.agent_id,
            workspace_id=at.workspace_id, project_id=at.project_id, agent_id=at.agent_id,
            tool_id=at.tool_id,
        )
