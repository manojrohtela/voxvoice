"""Tool Framework application layer (M8.11) — additive.

Commands, queries, use cases and handler registration for the Tool Framework vertical slice, in
a separate module so the M8.3 modules stay untouched (mirrors `agent_builder.py`). Use cases
coordinate the additive Foundation `tools` (catalog) + `agent_tools` (attachment) services — no
domain logic is duplicated. Authorization, transaction boundaries and audit are handled exactly
as the M8.6–M8.10 use cases do (open a Unit of Work, call the Foundation service which validates
+ persists + emits its domain event, map to a view DTO, stage the application event, commit).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from api.foundation import (
    AgentToolView,
    Foundation,
    ToolCategory,
    ToolConfig,
    ToolSummaryView,
    ToolView,
    ToolVisibility,
)

from .activity import ActivityFilter
from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands (tool catalog) ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateTool:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    category: ToolCategory = ToolCategory.CUSTOM
    description: str = ""
    icon: str = ""
    visibility: ToolVisibility = ToolVisibility.PROJECT
    config: dict | None = None


@dataclass(frozen=True, slots=True)
class UpdateTool:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    tool_id: str
    name: str | None = None
    category: ToolCategory | None = None
    description: str | None = None
    icon: str | None = None
    visibility: ToolVisibility | None = None
    config_patch: dict | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class _ToolOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    tool_id: str


class EnableTool(_ToolOp): ...
class DisableTool(_ToolOp): ...
class ArchiveTool(_ToolOp): ...
class RestoreTool(_ToolOp): ...
class DeleteTool(_ToolOp): ...


# ── commands (agent↔tool attachment) ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _AttachmentOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    tool_id: str


@dataclass(frozen=True, slots=True)
class AttachTool(_AttachmentOp):
    position: int | None = None


class EnableAgentTool(_AttachmentOp): ...
class DisableAgentTool(_AttachmentOp): ...
class DetachTool(_AttachmentOp): ...


@dataclass(frozen=True, slots=True)
class ConfigureAgentTool(_AttachmentOp):
    config_override: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReorderAgentTools:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    ordered_tool_ids: tuple[str, ...] = ()


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetTool:
    org_id: str
    project_id: str
    tool_id: str


@dataclass(frozen=True, slots=True)
class ListTools:
    org_id: str
    project_id: str
    include_archived: bool = True


@dataclass(frozen=True, slots=True)
class SearchTools:
    org_id: str
    project_id: str
    term: str


@dataclass(frozen=True, slots=True)
class ListAgentTools:
    org_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class GetToolActivity:
    org_id: str
    tool_id: str
    filter: ActivityFilter = field(default_factory=ActivityFilter)


# ── use cases (write side) ─────────────────────────────────────────────────────────


class ToolFrameworkUseCases(_UseCaseBase):
    """Write side of the Tool Framework — one Unit of Work per mutation."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    # tool catalog ------------------------------------------------------------------
    def create_tool(self, cmd: CreateTool) -> ToolView:
        with self._uow.new() as uow:
            tool = self._f.tools.create_tool(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                name=cmd.name, category=cmd.category, description=cmd.description,
                icon=cmd.icon, visibility=cmd.visibility,
                config=ToolConfig.from_dict(cmd.config) if cmd.config else None,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_CREATED, "create_tool",
                org_id=cmd.org_id, actor=cmd.actor_user_id, tool_id=tool.id,
            )
        return ToolView.of(tool)

    def update_tool(self, cmd: UpdateTool) -> ToolView:
        with self._uow.new() as uow:
            tool = self._f.tools.update_tool(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.tool_id,
                name=cmd.name, category=cmd.category, description=cmd.description,
                icon=cmd.icon, visibility=cmd.visibility, config_patch=cmd.config_patch,
                metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_UPDATED, "update_tool",
                org_id=cmd.org_id, actor=cmd.actor_user_id, tool_id=tool.id,
            )
        return ToolView.of(tool)

    def enable_tool(self, cmd: EnableTool) -> ToolView:
        return self._toggle_tool(cmd, True, ApplicationEventType.TOOL_ENABLED, "enable_tool")

    def disable_tool(self, cmd: DisableTool) -> ToolView:
        return self._toggle_tool(cmd, False, ApplicationEventType.TOOL_DISABLED, "disable_tool")

    def _toggle_tool(self, cmd, enabled, event, use_case) -> ToolView:
        with self._uow.new() as uow:
            tool = self._f.tools.set_enabled(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.tool_id,
                enabled=enabled,
            )
            self._stage(uow, event, use_case, org_id=cmd.org_id, actor=cmd.actor_user_id,
                        tool_id=tool.id)
        return ToolView.of(tool)

    def archive_tool(self, cmd: ArchiveTool) -> ToolView:
        return self._tool_lifecycle(
            cmd, self._f.tools.archive_tool, ApplicationEventType.TOOL_ARCHIVED, "archive_tool"
        )

    def restore_tool(self, cmd: RestoreTool) -> ToolView:
        return self._tool_lifecycle(
            cmd, self._f.tools.restore_tool, ApplicationEventType.TOOL_RESTORED, "restore_tool"
        )

    def delete_tool(self, cmd: DeleteTool) -> ToolView:
        return self._tool_lifecycle(
            cmd, self._f.tools.delete_tool, ApplicationEventType.TOOL_DELETED, "delete_tool"
        )

    def _tool_lifecycle(self, cmd: _ToolOp, fn, event, use_case) -> ToolView:
        with self._uow.new() as uow:
            tool = fn(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.tool_id
            )
            self._stage(uow, event, use_case, org_id=cmd.org_id, actor=cmd.actor_user_id,
                        tool_id=tool.id)
        return ToolView.of(tool)

    # agent↔tool attachment ---------------------------------------------------------
    def attach_tool(self, cmd: AttachTool) -> AgentToolView:
        with self._uow.new() as uow:
            at = self._f.agent_tools.attach_tool(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, cmd.tool_id, position=cmd.position,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_ATTACHED, "attach_tool",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=cmd.agent_id,
                tool_id=cmd.tool_id,
            )
        return AgentToolView.of(at)

    def detach_tool(self, cmd: DetachTool) -> dict:
        with self._uow.new() as uow:
            self._f.agent_tools.detach_tool(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, cmd.tool_id,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_DETACHED, "detach_tool",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=cmd.agent_id,
                tool_id=cmd.tool_id,
            )
        return {"agent_id": cmd.agent_id, "tool_id": cmd.tool_id, "detached": True}

    def enable_agent_tool(self, cmd: EnableAgentTool) -> AgentToolView:
        return self._toggle_attachment(cmd, True, "enable_agent_tool")

    def disable_agent_tool(self, cmd: DisableAgentTool) -> AgentToolView:
        return self._toggle_attachment(cmd, False, "disable_agent_tool")

    def _toggle_attachment(self, cmd, enabled, use_case) -> AgentToolView:
        with self._uow.new() as uow:
            at = self._f.agent_tools.set_attachment_enabled(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, cmd.tool_id, enabled=enabled,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_ATTACHMENT_TOGGLED, use_case,
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=cmd.agent_id,
                tool_id=cmd.tool_id, enabled=enabled,
            )
        return AgentToolView.of(at)

    def configure_agent_tool(self, cmd: ConfigureAgentTool) -> AgentToolView:
        with self._uow.new() as uow:
            at = self._f.agent_tools.configure_attachment(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, cmd.tool_id, config_override=cmd.config_override,
            )
            self._stage(
                uow, ApplicationEventType.TOOL_ATTACHMENT_CONFIGURED, "configure_agent_tool",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=cmd.agent_id,
                tool_id=cmd.tool_id,
            )
        return AgentToolView.of(at)

    def reorder_agent_tools(self, cmd: ReorderAgentTools) -> list[dict]:
        with self._uow.new() as uow:
            ordered = self._f.agent_tools.reorder_tools(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, tuple(cmd.ordered_tool_ids),
            )
            self._stage(
                uow, ApplicationEventType.TOOL_REORDERED, "reorder_agent_tools",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=cmd.agent_id,
            )
        return [asdict(AgentToolView.of(a)) for a in ordered]


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ToolFrameworkQueries:
    """Read side — tool catalog, agent attachments, and durable per-tool activity."""

    foundation: Foundation

    def get(self, query: GetTool) -> dict:
        return asdict(ToolView.of(
            self.foundation.tools.get_tool(query.org_id, query.project_id, query.tool_id)
        ))

    def list(self, query: ListTools) -> list[dict]:
        return [
            asdict(ToolSummaryView.of(t))
            for t in self.foundation.tools.list_tools(
                query.org_id, query.project_id, include_archived=query.include_archived
            )
        ]

    def search(self, query: SearchTools) -> list[dict]:
        return [
            asdict(ToolSummaryView.of(t))
            for t in self.foundation.tools.search_tools(
                query.org_id, query.project_id, query.term
            )
        ]

    def agent_tools(self, query: ListAgentTools) -> list[dict]:
        return [
            asdict(AgentToolView.of(a))
            for a in self.foundation.agent_tools.list_agent_tools(query.org_id, query.agent_id)
        ]

    def activity(self, query: GetToolActivity) -> dict:
        return self.foundation.activity.resource_activity(
            query.org_id, query.tool_id, **query.filter.kwargs()
        )


# ── registration ───────────────────────────────────────────────────────────────────


def build_and_register(
    *,
    foundation: Foundation,
    authz: AuthorizationOrchestrator,
    uow_factory: UnitOfWorkFactory,
    ids,
    clock,
    command_bus: CommandBus,
    query_bus: QueryBus,
) -> None:
    """Wire the M8.11 use cases + handlers into the existing buses (additive)."""
    uc = ToolFrameworkUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = ToolFrameworkQueries(foundation=foundation)
    command_bus.register(CreateTool, uc.create_tool)
    command_bus.register(UpdateTool, uc.update_tool)
    command_bus.register(EnableTool, uc.enable_tool)
    command_bus.register(DisableTool, uc.disable_tool)
    command_bus.register(ArchiveTool, uc.archive_tool)
    command_bus.register(RestoreTool, uc.restore_tool)
    command_bus.register(DeleteTool, uc.delete_tool)
    command_bus.register(AttachTool, uc.attach_tool)
    command_bus.register(DetachTool, uc.detach_tool)
    command_bus.register(EnableAgentTool, uc.enable_agent_tool)
    command_bus.register(DisableAgentTool, uc.disable_agent_tool)
    command_bus.register(ConfigureAgentTool, uc.configure_agent_tool)
    command_bus.register(ReorderAgentTools, uc.reorder_agent_tools)
    query_bus.register(GetTool, q.get)
    query_bus.register(ListTools, q.list)
    query_bus.register(SearchTools, q.search)
    query_bus.register(ListAgentTools, q.agent_tools)
    query_bus.register(GetToolActivity, q.activity)
