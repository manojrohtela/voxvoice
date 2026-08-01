"""Conversation Runtime domain service (M8.12) — additive.

The execution/orchestration plane. `ConversationRuntimeService` runs a **Published Agent
Version** through a deterministic pipeline and produces an execution **plan** — it never calls
an LLM, executes a tool, or synthesizes voice (only orchestration). It reuses the immutable
published version (M8.10), the Tool Framework (M8.11), and the durable Audit Platform (M8.9),
and defines its execution seam through the adapter interfaces in `runtime_adapters` (which the
frozen Epic 5/6 runtime satisfies later — the control plane never imports the runtime).

Golden rules enforced here: the runtime consumes **only** a published version (a draft — an
agent with `published_version == 0` — never executes); it **never mutates** an Agent, a Tool, a
Version, or any history; the pipeline is **deterministic** (pure function of the published
config + project/runtime variables + the injected clock); and the only thing carried across
resume calls is the explicit `ConversationState`.
"""

from __future__ import annotations

import re

from .agent_domain import AgentConfig
from .errors import ConflictError, NotFoundError
from .events import FoundationEventType
from .rbac import Permission
from .repositories import (
    AgentRepository,
    AgentToolRepository,
    AgentVersionRepository,
    ConversationSessionRepository,
    ProjectRepository,
    ToolRepository,
)
from .runtime_domain import (
    ConversationSession,
    ConversationState,
    ExecutionPlan,
    ResolvedPrompt,
    ResolvedVariable,
    SessionStatus,
    ToolPlanStep,
    VariableSource,
)
from .seams import PREFIX_SESSION
from .services import _Base

# System-default variables — the lowest precedence tier (overridden by project/agent/runtime).
SYSTEM_DEFAULT_VARIABLES: dict[str, str] = {"timezone": "UTC", "locale": "en"}
_VAR_REF = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class ConversationRuntimeService(_Base):
    """Runs a published agent version to an execution plan (deterministic; no execution)."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        agents: AgentRepository,
        agent_versions: AgentVersionRepository,
        tools: ToolRepository,
        agent_tools: AgentToolRepository,
        sessions: ConversationSessionRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._agents = agents
        self._versions = agent_versions
        self._tools = tools
        self._agent_tools = agent_tools
        self._sessions = sessions

    # ── session lifecycle ─────────────────────────────────────────────────────────
    def create_session(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        agent_id: str,
        *,
        runtime_variables: dict[str, str] | None = None,
        correlation_id: str = "",
        metadata: dict | None = None,
    ) -> ConversationSession:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        project = self._projects.get(org_id, workspace_id, project_id)
        if project is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        agent = self._agents.get(org_id, project_id, agent_id)
        if agent is None:
            raise NotFoundError(f"agent {agent_id!r} not found in project {project_id!r}")
        # Golden rule: only a PUBLISHED version executes — a draft never does.
        if agent.published_version <= 0:
            raise ConflictError(
                f"agent {agent_id!r} has no published version; drafts never execute"
            )
        version = self._versions.get(org_id, agent_id, agent.published_version)
        if version is None:
            raise NotFoundError(
                f"published version {agent.published_version} of agent {agent_id!r} not found"
            )
        try:
            state = self._plan(project, agent, version, runtime_variables or {})
        except Exception as exc:  # planning failure is audited, then re-raised
            self._emit(
                FoundationEventType.EXECUTION_FAILED, org_id=org_id, actor=actor_user_id,
                resource=agent_id, workspace_id=workspace_id, project_id=project_id,
                error=str(exc),
            )
            raise
        now = self._clock.now()
        session = ConversationSession(
            id=self._ids.new(PREFIX_SESSION), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, agent_id=agent_id, agent_version=version.number,
            correlation_id=correlation_id or self._ids.new(PREFIX_SESSION),
            status=SessionStatus.ACTIVE, created_at=now, updated_at=now,
            metadata=dict(metadata or {}), state=state,
        )
        self._sessions.add(session)
        self._emit(
            FoundationEventType.SESSION_CREATED, org_id=org_id, actor=actor_user_id,
            resource=session.id, workspace_id=workspace_id, project_id=project_id,
            agent_id=agent_id, version=version.number,
        )
        self._emit(
            FoundationEventType.EXECUTION_PLANNED, org_id=org_id, actor=actor_user_id,
            resource=session.id, workspace_id=workspace_id, project_id=project_id,
            agent_id=agent_id, steps=len(state.tool_plan.steps),
        )
        return session

    def _load_session(self, org_id: str, session_id: str) -> ConversationSession:
        s = self._sessions.get(org_id, session_id)
        if s is None:
            raise NotFoundError(f"session {session_id!r} not found")
        return s

    def resume_session(
        self, actor_user_id, org_id, workspace_id, project_id, session_id
    ) -> ConversationSession:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        s = self._load_session(org_id, session_id)
        if s.status is not SessionStatus.ACTIVE:
            raise ConflictError(f"session {session_id!r} is {s.status.value}, cannot resume")
        updated = self._sessions.update(s.touched(self._clock.now()))
        self._emit(
            FoundationEventType.SESSION_RESUMED, org_id=org_id, actor=actor_user_id,
            resource=session_id, workspace_id=workspace_id, project_id=project_id,
        )
        return updated

    def close_session(
        self, actor_user_id, org_id, workspace_id, project_id, session_id
    ) -> ConversationSession:
        return self._terminate(
            actor_user_id, org_id, workspace_id, project_id, session_id,
            SessionStatus.CLOSED, FoundationEventType.SESSION_CLOSED,
        )

    def expire_session(
        self, actor_user_id, org_id, workspace_id, project_id, session_id
    ) -> ConversationSession:
        return self._terminate(
            actor_user_id, org_id, workspace_id, project_id, session_id,
            SessionStatus.EXPIRED, FoundationEventType.SESSION_EXPIRED,
        )

    def _terminate(
        self, actor_user_id, org_id, workspace_id, project_id, session_id, status, event
    ) -> ConversationSession:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        s = self._load_session(org_id, session_id)
        if s.status is not SessionStatus.ACTIVE:
            raise ConflictError(f"session {session_id!r} is already {s.status.value}")
        updated = self._sessions.update(s.with_status(status, updated_at=self._clock.now()))
        self._emit(
            event, org_id=org_id, actor=actor_user_id, resource=session_id,
            workspace_id=workspace_id, project_id=project_id,
        )
        return updated

    # ── reads ─────────────────────────────────────────────────────────────────────
    def get_session(self, org_id: str, session_id: str) -> ConversationSession:
        return self._load_session(org_id, session_id)

    def list_sessions(self, org_id: str, agent_id: str) -> tuple[ConversationSession, ...]:
        return tuple(
            sorted(self._sessions.list(org_id, agent_id), key=lambda s: s.created_at)
        )

    # ── the deterministic pipeline ─────────────────────────────────────────────────
    def _plan(self, project, agent, version, runtime_variables: dict) -> ConversationState:
        """Load version → resolve variables → build prompt → resolve tools → plan.
        Pure + deterministic (given the fixed inputs + the injected clock)."""
        snapshot = version.snapshot
        config = AgentConfig.from_dict(snapshot.get("config", {}))
        resolved = self._resolve_variables(project, config, runtime_variables)
        tool_plan, tool_diags = self._resolve_tools(agent, config)
        prompt, prompt_diags = self._build_prompt(snapshot, config, resolved, tool_plan)
        diagnostics = tuple(tool_diags + prompt_diags)
        execution_metadata = {
            "agent_id": agent.id,
            "agent_version": version.number,
            "version_status": version.status.value,
            "immutable": True,
        }
        return ConversationState(
            resolved_prompt=prompt, resolved_variables=resolved, tool_plan=tool_plan,
            history=(), runtime_metadata={"correlation": True},
            execution_metadata=execution_metadata,
            timing={"planned_at": self._clock.now()}, diagnostics=diagnostics,
        )

    def _resolve_variables(
        self, project, config: AgentConfig, runtime_variables: dict
    ) -> tuple[ResolvedVariable, ...]:
        """Precedence (later overrides earlier): system < project < agent < runtime.
        Immutable output; deterministic (sorted by name)."""
        resolved: dict[str, ResolvedVariable] = {}
        for name, value in SYSTEM_DEFAULT_VARIABLES.items():
            resolved[name] = ResolvedVariable(name, str(value), VariableSource.SYSTEM)
        for name, value in dict(project.metadata.get("variables", {})).items():
            resolved[name] = ResolvedVariable(name, str(value), VariableSource.PROJECT)
        for var in config.variables:
            resolved[var.name] = ResolvedVariable(
                var.name, var.value, VariableSource.AGENT, is_secret=var.is_secret
            )
        for name, value in dict(runtime_variables).items():
            # a runtime override preserves the secret flag if it shadows a secret agent var
            was_secret = name in resolved and resolved[name].is_secret
            resolved[name] = ResolvedVariable(
                name, str(value), VariableSource.RUNTIME, is_secret=was_secret
            )
        return tuple(resolved[k] for k in sorted(resolved))

    def _resolve_tools(
        self, agent, config: AgentConfig
    ) -> tuple[ExecutionPlan, list[dict]]:
        """Resolve attached tools respecting ordering, enable flags, config overrides and
        dependencies. Returns an executable **plan** — nothing is executed."""
        diagnostics: list[dict] = []
        steps: list[ToolPlanStep] = []
        attachments = sorted(
            self._agent_tools.list(agent.org_id, agent.id), key=lambda a: a.position
        )
        for pos, at in enumerate(attachments):
            tool = self._tools.get(agent.org_id, agent.project_id, at.tool_id)
            if tool is None or tool.status.value == "deleted":
                diagnostics.append({
                    "code": "tool_missing", "severity": "error",
                    "message": f"attached tool {at.tool_id!r} not found",
                })
                continue
            archived = tool.status.value == "archived"
            enabled = at.enabled and tool.enabled and not archived
            if archived:
                diagnostics.append({
                    "code": "tool_archived", "severity": "warning",
                    "message": f"tool {tool.name!r} is archived; step disabled",
                })
            elif not enabled:
                diagnostics.append({
                    "code": "tool_disabled", "severity": "info",
                    "message": f"tool {tool.name!r} is disabled for this agent",
                })
            merged = {**tool.config.settings, **dict(at.config_override)}
            steps.append(ToolPlanStep(
                position=pos, tool_id=tool.id, name=tool.name, category=tool.category.value,
                enabled=enabled, config=merged, depends_on=tool.config.depends_on,
            ))
        required = {
            "llm": True,
            "voice": bool(config.voice.enabled),
            "tool_executor": any(s.enabled for s in steps),
            "memory": False,
        }
        return ExecutionPlan(steps=tuple(steps), required_adapters=required), diagnostics

    def _build_prompt(
        self, snapshot: dict, config: AgentConfig, resolved: tuple[ResolvedVariable, ...],
        tool_plan: ExecutionPlan,
    ) -> tuple[ResolvedPrompt, list[dict]]:
        """Assemble the prompt deterministically. Secret variable values are **masked** in the
        assembled text (the inspector never leaks a secret). No prompt is executed."""
        prompt = config.prompt
        values = {v.name: ("***" if v.is_secret else v.value) for v in resolved}
        diagnostics: list[dict] = []

        def substitute(text: str) -> str:
            def repl(match: re.Match) -> str:
                name = match.group(1)
                if name not in values:
                    diagnostics.append({
                        "code": "unresolved_variable", "severity": "warning",
                        "message": f"prompt references unresolved variable {{{{{name}}}}}",
                    })
                    return match.group(0)
                return values[name]
            return _VAR_REF.sub(repl, text)

        system = substitute(prompt.system_prompt)
        persona = substitute(prompt.persona)
        instructions = substitute(prompt.instructions)
        style = prompt.conversation_style
        variables_block = "\n".join(f"{v.name}={values[v.name]}" for v in resolved)
        enabled_tools = [s for s in tool_plan.steps if s.enabled]
        tool_context = "\n".join(
            f"{s.position}. {s.name} ({s.category})" for s in enabled_tools
        )
        greeting = snapshot.get("greeting", "")
        text = "\n\n".join(part for part in (
            f"[SYSTEM]\n{system}" if system else "",
            f"[PERSONA]\n{persona}" if persona else "",
            f"[INSTRUCTIONS]\n{instructions}" if instructions else "",
            f"[STYLE]\n{style}" if style else "",
            f"[VARIABLES]\n{variables_block}" if variables_block else "",
            f"[TOOLS]\n{tool_context}" if tool_context else "",
            f"[GREETING]\n{greeting}" if greeting else "",
        ) if part)
        return ResolvedPrompt(
            system=system, persona=persona, instructions=instructions,
            conversation_style=style, variables_block=variables_block, history_block="",
            tool_context=tool_context, text=text,
        ), diagnostics
