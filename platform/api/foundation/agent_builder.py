"""Agent Builder domain service (M8.10) — additive.

The behaviour behind the first AI-native customer feature: designing, configuring,
validating, versioning and publishing **Agents** (configuration objects that belong to one
Project). This service owns the full agent lifecycle — create / update / archive / restore /
duplicate / soft-delete / publish / unpublish / rollback — plus validation and immutable
versioning. It never runs an agent, never calls an LLM, and never actuates anything
(ADR-0021/0022): M8.10 is authoring only.

**Reuse-first (the milestone architecture rule).** No new RBAC scope or permission is
introduced — agents live inside a Project, so agent writes authorize via the existing
**workspace-scoped** `PROJECT_MANAGE` and reads via `PROJECT_READ`. Versioning reuses the
durable **Audit Platform** (M8.9): every lifecycle fact is emitted as a `FoundationEvent`, so
version history + activity feeds are one system, not a second one. Only the genuinely-new
agent aggregates/repositories/events required the Foundation to grow, and every addition is
additive and defaulted — `services.py` and every existing service are byte-for-byte unchanged.
"""

from __future__ import annotations

import re

from .agent_domain import (
    Agent,
    AgentConfig,
    AgentStatus,
    AgentVersion,
    AgentVersionStatus,
)
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
    AgentVersionRepository,
    ProjectRepository,
)
from .seams import PREFIX_AGENT, PREFIX_AGENT_VERSION
from .services import _Base

# The well-known agent variables (Blueprint §Variables) — always resolvable in a prompt
# reference even before a value is set. Custom variables extend this set per agent.
STANDARD_VARIABLES: frozenset[str] = frozenset(
    {"company_name", "support_email", "website", "business_hours", "timezone", "locale"}
)
# Prompt-size guard: total authored prompt text (system + persona + instructions) is capped
# so a runaway prompt is caught at authoring time (not a runtime concern).
MAX_PROMPT_CHARS = 20_000
_VAR_REF = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _diag(path: str, code: FoundationDiagnosticCode, msg: str, fix: str) -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, msg, fix)


def validate_agent(
    name: str, config: AgentConfig
) -> FoundationDiagnostics:
    """Structural validation for an agent + its configuration (deterministic, never raises).

    Covers required fields, prompt size, variable references, and model/voice compatibility.
    Duplicate-name and isolation checks need the repositories and live in the service."""
    out: list[Diagnostic] = []
    if not name.strip():
        out.append(_diag(
            "name", FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
            "agent name must be non-empty", "give the agent a name",
        ))
    prompt = config.prompt
    total = len(prompt.system_prompt) + len(prompt.persona) + len(prompt.instructions)
    if total > MAX_PROMPT_CHARS:
        out.append(_diag(
            "config.prompt", FoundationDiagnosticCode.PROMPT_TOO_LARGE,
            f"prompt text is {total} chars (max {MAX_PROMPT_CHARS})",
            "shorten the system prompt / instructions",
        ))
    # Variable references must resolve to a standard or a declared variable.
    declared = {v.name for v in config.variables}
    known = STANDARD_VARIABLES | declared
    referenced = set()
    for text in (prompt.system_prompt, prompt.persona, prompt.instructions):
        referenced |= set(_VAR_REF.findall(text))
    for ref in sorted(referenced - known):
        out.append(_diag(
            "config.prompt", FoundationDiagnosticCode.INVALID_VARIABLE_REFERENCE,
            f"prompt references undefined variable {{{{{ref}}}}}",
            "declare the variable or remove the reference",
        ))
    # Model compatibility (provider-independent — only shape/ranges, never a vendor list).
    model = config.model
    if not (0.0 <= model.temperature <= 2.0):
        out.append(_diag(
            "config.model.temperature", FoundationDiagnosticCode.INVALID_MODEL_CONFIG,
            f"temperature {model.temperature} out of range [0, 2]", "use 0.0–2.0",
        ))
    if not (0.0 <= model.top_p <= 1.0):
        out.append(_diag(
            "config.model.top_p", FoundationDiagnosticCode.INVALID_MODEL_CONFIG,
            f"top_p {model.top_p} out of range [0, 1]", "use 0.0–1.0",
        ))
    if model.max_tokens <= 0:
        out.append(_diag(
            "config.model.max_tokens", FoundationDiagnosticCode.INVALID_MODEL_CONFIG,
            "max_tokens must be positive", "set a positive token budget",
        ))
    if model.provider and not model.model:
        out.append(_diag(
            "config.model.model", FoundationDiagnosticCode.INVALID_MODEL_CONFIG,
            "a provider is set but no model is selected", "select a model for the provider",
        ))
    if model.structured_output_enabled and not model.model:
        out.append(_diag(
            "config.model", FoundationDiagnosticCode.INVALID_MODEL_CONFIG,
            "structured output requires a selected model", "select a model first",
        ))
    # Voice compatibility (only meaningful when voice is enabled).
    voice = config.voice
    if voice.enabled:
        if not voice.provider or not voice.voice_name:
            out.append(_diag(
                "config.voice", FoundationDiagnosticCode.INVALID_VOICE_CONFIG,
                "voice is enabled but provider/voice_name is missing",
                "choose a voice provider and voice, or disable voice",
            ))
        if not (0.25 <= voice.speaking_rate <= 4.0):
            out.append(_diag(
                "config.voice.speaking_rate", FoundationDiagnosticCode.INVALID_VOICE_CONFIG,
                f"speaking_rate {voice.speaking_rate} out of range [0.25, 4]", "use 0.25–4.0",
            ))
        if not (0.0 <= voice.volume <= 2.0):
            out.append(_diag(
                "config.voice.volume", FoundationDiagnosticCode.INVALID_VOICE_CONFIG,
                f"volume {voice.volume} out of range [0, 2]", "use 0.0–2.0",
            ))
    return FoundationDiagnostics.of(out)


class AgentBuilderService(_Base):
    """The full Agent Builder lifecycle. Every write authorizes via the workspace-scoped
    `PROJECT_MANAGE` permission (reuse-first — no new RBAC scope), persists through the
    repository interfaces, and emits a durable audit event (M8.9)."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        agents: AgentRepository,
        agent_versions: AgentVersionRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._agents = agents
        self._versions = agent_versions

    # ── helpers ───────────────────────────────────────────────────────────────────
    def _load_project(self, org_id: str, workspace_id: str, project_id: str):
        p = self._projects.get(org_id, workspace_id, project_id)
        if p is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        return p

    def _load(self, org_id: str, project_id: str, agent_id: str) -> Agent:
        a = self._agents.get(org_id, project_id, agent_id)
        if a is None or a.status is AgentStatus.DELETED:
            raise NotFoundError(f"agent {agent_id!r} not found in project {project_id!r}")
        return a

    def _require_unique_name(
        self, org_id: str, project_id: str, name: str, *, exclude_id: str | None = None
    ) -> None:
        existing = self._agents.by_name(org_id, project_id, name)
        if existing is not None and existing.id != exclude_id:
            raise FoundationValidationError(
                FoundationDiagnostics.of([_diag(
                    "name", FoundationDiagnosticCode.DUPLICATE_NAME,
                    f"an agent named {name!r} already exists in this project",
                    "choose a different agent name",
                )])
            )

    def _validate_or_raise(self, name: str, config: AgentConfig) -> None:
        diagnostics = validate_agent(name, config)
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)

    # ── create / update / lifecycle ───────────────────────────────────────────────
    def create_agent(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        *,
        name: str,
        description: str = "",
        icon: str = "",
        category: str = "",
        tags: tuple[str, ...] = (),
        language: str = "en",
        greeting: str = "",
        config: AgentConfig | None = None,
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_project(org_id, workspace_id, project_id)
        cfg = config or AgentConfig()
        self._validate_or_raise(name, cfg)
        self._require_unique_name(org_id, project_id, name)
        agent = Agent(
            id=self._ids.new(PREFIX_AGENT), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=name, created_by=actor_user_id,
            created_at=self._clock.now(), updated_at=self._clock.now(),
            description=description, icon=icon, category=category, tags=tags,
            language=language, greeting=greeting, status=AgentStatus.DRAFT, config=cfg,
        )
        self._agents.add(agent)
        self._emit_agent(FoundationEventType.AGENT_CREATED, actor_user_id, agent)
        return agent

    def update_agent(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        category: str | None = None,
        tags: tuple[str, ...] | None = None,
        language: str | None = None,
        greeting: str | None = None,
        config_patch: dict | None = None,
        metadata: dict | None = None,
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        new_config = a.config.merged(config_patch) if config_patch else a.config
        new_name = name if name is not None else a.name
        self._validate_or_raise(new_name, new_config)
        if name is not None and name.strip().casefold() != a.name.strip().casefold():
            self._require_unique_name(org_id, project_id, name, exclude_id=a.id)
        updated = a.with_updates(
            name=name, description=description, icon=icon, category=category, tags=tags,
            language=language, greeting=greeting, config=new_config, metadata=metadata,
            updated_at=self._clock.now(),
        )
        self._agents.update(updated)
        event = (
            FoundationEventType.AGENT_CONFIG_CHANGED
            if config_patch else FoundationEventType.AGENT_UPDATED
        )
        self._emit_agent(event, actor_user_id, updated)
        return updated

    def archive_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        if a.status is AgentStatus.ARCHIVED:
            raise ConflictError(f"agent {agent_id!r} is already archived")
        updated = self._agents.update(
            a.with_updates(status=AgentStatus.ARCHIVED, updated_at=self._clock.now())
        )
        self._emit_agent(FoundationEventType.AGENT_ARCHIVED, actor_user_id, updated)
        return updated

    def restore_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        if a.status is not AgentStatus.ARCHIVED:
            raise ConflictError(f"agent {agent_id!r} is not archived")
        restored_status = (
            AgentStatus.PUBLISHED if a.published_version else AgentStatus.DRAFT
        )
        updated = self._agents.update(
            a.with_updates(status=restored_status, updated_at=self._clock.now())
        )
        self._emit_agent(FoundationEventType.AGENT_RESTORED, actor_user_id, updated)
        return updated

    def delete_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id
    ) -> Agent:
        """Soft-delete — marks the agent DELETED (row retained for audit/isolation)."""
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        updated = self._agents.update(
            a.with_updates(status=AgentStatus.DELETED, updated_at=self._clock.now())
        )
        self._emit_agent(FoundationEventType.AGENT_DELETED, actor_user_id, updated)
        return updated

    def duplicate_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, *, name=None
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        src = self._load(org_id, project_id, agent_id)
        new_name = name or self._unique_copy_name(org_id, project_id, src.name)
        self._require_unique_name(org_id, project_id, new_name)
        clone = Agent(
            id=self._ids.new(PREFIX_AGENT), org_id=org_id, workspace_id=workspace_id,
            project_id=project_id, name=new_name, created_by=actor_user_id,
            created_at=self._clock.now(), updated_at=self._clock.now(),
            description=src.description, icon=src.icon, category=src.category,
            tags=src.tags, language=src.language, greeting=src.greeting,
            status=AgentStatus.DRAFT, config=src.config,
        )
        self._agents.add(clone)
        self._emit(
            FoundationEventType.AGENT_DUPLICATED, org_id=org_id, actor=actor_user_id,
            resource=clone.id, workspace_id=workspace_id, project_id=project_id,
            source_agent_id=src.id, name=clone.name,
        )
        return clone

    def _unique_copy_name(self, org_id: str, project_id: str, base: str) -> str:
        candidate = f"{base} (copy)"
        n = 2
        while self._agents.by_name(org_id, project_id, candidate) is not None:
            candidate = f"{base} (copy {n})"
            n += 1
        return candidate

    # ── publish / unpublish / rollback (versioning) ───────────────────────────────
    def publish_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, *, description=""
    ) -> tuple[Agent, AgentVersion]:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        self._validate_or_raise(a.name, a.config)
        version = self._new_version(
            actor_user_id, a, description=description, rollback_target=None,
            snapshot=self._snapshot(a),
        )
        updated = self._agents.update(
            a.with_updates(
                status=AgentStatus.PUBLISHED, version=version.number,
                published_version=version.number, updated_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.AGENT_PUBLISHED, org_id=org_id, actor=actor_user_id,
            resource=a.id, workspace_id=workspace_id, project_id=project_id,
            version=version.number,
        )
        return updated, version

    def unpublish_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id
    ) -> Agent:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        if a.status is not AgentStatus.PUBLISHED:
            raise ConflictError(f"agent {agent_id!r} is not published")
        updated = self._agents.update(
            a.with_updates(
                status=AgentStatus.DRAFT, published_version=0, updated_at=self._clock.now()
            )
        )
        self._emit(
            FoundationEventType.AGENT_UNPUBLISHED, org_id=org_id, actor=actor_user_id,
            resource=a.id, workspace_id=workspace_id, project_id=project_id,
        )
        return updated

    def rollback_agent(
        self, actor_user_id, org_id, workspace_id, project_id, agent_id, target_number: int
    ) -> tuple[Agent, AgentVersion]:
        """Roll back to a previous version — publishes a NEW immutable version whose config
        is restored from `target_number` (history is never rewritten)."""
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        a = self._load(org_id, project_id, agent_id)
        target = self._versions.get(org_id, agent_id, target_number)
        if target is None:
            raise NotFoundError(
                f"agent {agent_id!r} has no version {target_number}"
            )
        restored_config = AgentConfig.from_dict(target.snapshot.get("config", {}))
        a = a.with_updates(config=restored_config)
        version = self._new_version(
            actor_user_id, a, description=f"rollback to v{target_number}",
            rollback_target=target_number, snapshot=self._snapshot(a),
        )
        updated = self._agents.update(
            a.with_updates(
                status=AgentStatus.PUBLISHED, version=version.number,
                published_version=version.number, updated_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.AGENT_ROLLED_BACK, org_id=org_id, actor=actor_user_id,
            resource=a.id, workspace_id=workspace_id, project_id=project_id,
            version=version.number, rollback_target=target_number,
        )
        return updated, version

    def _new_version(
        self, actor_user_id, agent: Agent, *, description: str, rollback_target, snapshot: dict
    ) -> AgentVersion:
        number = agent.version + 1
        # Supersede the previously-current published version (status flip only — immutable).
        prev = self._versions.get(agent.org_id, agent.id, agent.version)
        if prev is not None and prev.status is AgentVersionStatus.PUBLISHED:
            self._versions.update_status(prev.superseded())
        version = AgentVersion(
            id=self._ids.new(PREFIX_AGENT_VERSION), agent_id=agent.id, org_id=agent.org_id,
            workspace_id=agent.workspace_id, project_id=agent.project_id, number=number,
            created_by=actor_user_id, created_at=self._clock.now(), description=description,
            status=AgentVersionStatus.PUBLISHED, rollback_target=rollback_target,
            snapshot=snapshot,
        )
        self._versions.add(version)
        self._emit(
            FoundationEventType.AGENT_VERSION_CREATED, org_id=agent.org_id,
            actor=actor_user_id, resource=agent.id, workspace_id=agent.workspace_id,
            project_id=agent.project_id, version=number,
        )
        return version

    @staticmethod
    def _snapshot(agent: Agent) -> dict:
        """The immutable publish snapshot — full config (secrets kept for fidelity) + the
        descriptive fields, so a version fully reconstructs the agent at that point."""
        return {
            "name": agent.name, "description": agent.description, "icon": agent.icon,
            "category": agent.category, "tags": list(agent.tags), "language": agent.language,
            "greeting": agent.greeting, "config": agent.config.as_dict(redact_secrets=False),
        }

    # ── reads ─────────────────────────────────────────────────────────────────────
    def get_agent(self, org_id: str, project_id: str, agent_id: str) -> Agent:
        return self._load(org_id, project_id, agent_id)

    def list_agents(
        self, org_id: str, project_id: str, *, include_archived: bool = True
    ) -> tuple[Agent, ...]:
        out = []
        for a in self._agents.list(org_id, project_id):
            if a.status is AgentStatus.DELETED:
                continue
            if not include_archived and a.status is AgentStatus.ARCHIVED:
                continue
            out.append(a)
        return tuple(out)

    def search_agents(self, org_id: str, project_id: str, term: str) -> tuple[Agent, ...]:
        needle = term.strip().casefold()
        return tuple(
            a
            for a in self.list_agents(org_id, project_id)
            if needle in a.name.casefold()
            or needle in a.description.casefold()
            or any(needle in t.casefold() for t in a.tags)
        )

    def list_versions(self, org_id: str, agent_id: str) -> tuple[AgentVersion, ...]:
        return tuple(
            sorted(self._versions.list(org_id, agent_id), key=lambda v: v.number, reverse=True)
        )

    def get_version(self, org_id: str, agent_id: str, number: int) -> AgentVersion:
        v = self._versions.get(org_id, agent_id, number)
        if v is None:
            raise NotFoundError(f"agent {agent_id!r} has no version {number}")
        return v

    def compare_versions(
        self, org_id: str, agent_id: str, from_number: int, to_number: int
    ) -> dict:
        """A deterministic field-level diff between two versions' snapshots."""
        left = self.get_version(org_id, agent_id, from_number)
        right = self.get_version(org_id, agent_id, to_number)
        left_flat = _flatten(left.snapshot)
        right_flat = _flatten(right.snapshot)
        changes = []
        for key in sorted(set(left_flat) | set(right_flat)):
            lv, rv = left_flat.get(key), right_flat.get(key)
            if lv != rv:
                changes.append({"field": key, "from": lv, "to": rv})
        return {
            "agent_id": agent_id, "from_version": from_number, "to_version": to_number,
            "changes": changes,
        }

    def _emit_agent(
        self, event: FoundationEventType, actor_user_id: str, agent: Agent
    ) -> None:
        self._emit(
            event, org_id=agent.org_id, actor=actor_user_id, resource=agent.id,
            workspace_id=agent.workspace_id, project_id=agent.project_id, name=agent.name,
        )


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten a nested snapshot into dotted keys for a stable diff (secrets already stored
    literally in the snapshot are compared by value; views never surface them)."""
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{key}."))
        else:
            out[key] = v
    return out
