"""Row ↔ domain mapping (M8.4): Persistence Model ↔ Foundation domain aggregate.

The single translation boundary. Repositories accept/return **frozen Foundation domain
aggregates**; internally they map to/from `*Row` persistence models here. No persistence
type ever leaks into the Foundation or Application (they never import `sqlmodel` or these
rows). Pure, total, deterministic; enum values round-trip through their string form.
"""

from __future__ import annotations

from actuator import (
    ApprovalDecision,
    ApprovalRecord,
    DeploymentExecution,
    DeploymentExecutionResult,
    DeploymentExecutionState,
    DeploymentJob,
    HealthReport,
    HealthState,
    RollbackReason,
    RollbackRecord,
    RollbackTarget,
    StateTransition,
)
from actuator.domain import LogEntry
from api.foundation import (
    Agent,
    AgentConfig,
    AgentStatus,
    AgentTool,
    AgentVersion,
    AgentVersionStatus,
    AlertComparator,
    AlertRule,
    AlertSeverity,
    ConversationMessage,
    ConversationSession,
    ConversationState,
    DocumentStatus,
    EmbeddingStatus,
    Environment,
    EnvironmentKind,
    IndexStatus,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    Project,
    ProjectMembership,
    ProjectMembershipStatus,
    ProjectStatus,
    Role,
    RoleBinding,
    RoleScope,
    SessionStatus,
    Tool,
    ToolCategory,
    ToolConfig,
    ToolStatus,
    ToolVisibility,
    User,
    UserPreferences,
    Workspace,
    WorkspaceMembership,
    WorkspaceMembershipStatus,
    WorkspaceStatus,
)

from .models import (
    AgentRow,
    AgentToolRow,
    AgentVersionRow,
    AlertRuleRow,
    ConversationMessageRow,
    ConversationSessionRow,
    DeploymentExecutionRow,
    EnvironmentRow,
    KnowledgeChunkRow,
    KnowledgeCollectionRow,
    KnowledgeDocumentRow,
    MembershipRow,
    OrganizationRow,
    PreferencesRow,
    ProjectMembershipRow,
    ProjectRow,
    RoleBindingRow,
    ToolRow,
    UserRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)

# ── User ─────────────────────────────────────────────────────────────────────────


def user_to_row(u: User) -> UserRow:
    return UserRow(
        id=u.id, email=u.email, display_name=u.display_name,
        created_at=u.created_at, meta=dict(u.metadata),
    )


def user_to_domain(r: UserRow) -> User:
    return User(
        id=r.id, email=r.email, display_name=r.display_name,
        created_at=r.created_at, metadata=dict(r.meta),
    )


# ── Organization ─────────────────────────────────────────────────────────────────


def org_to_row(o: Organization) -> OrganizationRow:
    return OrganizationRow(
        id=o.id, name=o.name, plan=o.plan, region=o.region,
        created_at=o.created_at, meta=dict(o.metadata),
        status=o.status.value, timezone=o.timezone, language=o.language,
        logo_url=o.logo_url, updated_at=o.updated_at,
    )


def org_to_domain(r: OrganizationRow) -> Organization:
    return Organization(
        id=r.id, name=r.name, plan=r.plan, region=r.region,
        created_at=r.created_at, metadata=dict(r.meta),
        status=OrganizationStatus(r.status), timezone=r.timezone, language=r.language,
        logo_url=r.logo_url, updated_at=r.updated_at,
    )


# ── Workspace ────────────────────────────────────────────────────────────────────


def workspace_to_row(w: Workspace) -> WorkspaceRow:
    return WorkspaceRow(
        id=w.id, org_id=w.org_id, name=w.name, slug=w.slug,
        created_at=w.created_at, meta=dict(w.metadata),
        description=w.description, icon=w.icon, color=w.color,
        status=w.status.value, updated_at=w.updated_at,
    )


def workspace_to_domain(r: WorkspaceRow) -> Workspace:
    return Workspace(
        id=r.id, org_id=r.org_id, name=r.name, slug=r.slug,
        created_at=r.created_at, metadata=dict(r.meta),
        description=r.description, icon=r.icon, color=r.color,
        status=WorkspaceStatus(r.status), updated_at=r.updated_at,
    )


# ── WorkspaceMembership (M8.7) ───────────────────────────────────────────────────


def workspace_membership_to_row(m: WorkspaceMembership) -> WorkspaceMembershipRow:
    return WorkspaceMembershipRow(
        id=m.id, org_id=m.org_id, workspace_id=m.workspace_id, user_id=m.user_id,
        role=m.role, status=m.status.value, created_at=m.created_at,
    )


def workspace_membership_to_domain(r: WorkspaceMembershipRow) -> WorkspaceMembership:
    return WorkspaceMembership(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, user_id=r.user_id,
        role=r.role, status=WorkspaceMembershipStatus(r.status), created_at=r.created_at,
    )


# ── Membership ───────────────────────────────────────────────────────────────────


def membership_to_row(m: Membership) -> MembershipRow:
    return MembershipRow(
        id=m.id, org_id=m.org_id, user_id=m.user_id,
        status=m.status.value, created_at=m.created_at,
        invited_role=m.invited_role, expires_at=m.expires_at,
    )


def membership_to_domain(r: MembershipRow) -> Membership:
    return Membership(
        id=r.id, org_id=r.org_id, user_id=r.user_id,
        status=MembershipStatus(r.status), created_at=r.created_at,
        invited_role=r.invited_role, expires_at=r.expires_at,
    )


# ── RoleBinding ──────────────────────────────────────────────────────────────────


def role_binding_to_row(b: RoleBinding) -> RoleBindingRow:
    return RoleBindingRow(
        id=b.id, org_id=b.org_id, user_id=b.user_id, role=b.role.value,
        scope=b.scope.value, scope_id=b.scope_id, created_at=b.created_at,
    )


def role_binding_to_domain(r: RoleBindingRow) -> RoleBinding:
    return RoleBinding(
        id=r.id, org_id=r.org_id, user_id=r.user_id, role=Role(r.role),
        scope=RoleScope(r.scope), scope_id=r.scope_id, created_at=r.created_at,
    )


# ── Environment ──────────────────────────────────────────────────────────────────


def environment_to_row(e: Environment) -> EnvironmentRow:
    return EnvironmentRow(
        id=e.id, org_id=e.org_id, workspace_id=e.workspace_id, name=e.name,
        kind=e.kind.value, created_at=e.created_at,
    )


def environment_to_domain(r: EnvironmentRow) -> Environment:
    return Environment(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, name=r.name,
        kind=EnvironmentKind(r.kind), created_at=r.created_at,
    )


# ── Project ──────────────────────────────────────────────────────────────────────


def project_to_row(p: Project) -> ProjectRow:
    return ProjectRow(
        id=p.id, org_id=p.org_id, workspace_id=p.workspace_id, name=p.name,
        slug=p.slug, created_at=p.created_at,
        description=p.description, icon=p.icon, status=p.status.value,
        updated_at=p.updated_at, meta=dict(p.metadata),
    )


def project_to_domain(r: ProjectRow) -> Project:
    return Project(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, name=r.name,
        slug=r.slug, created_at=r.created_at,
        description=r.description, icon=r.icon, status=ProjectStatus(r.status),
        updated_at=r.updated_at, metadata=dict(r.meta),
    )


# ── ProjectMembership (M8.8) ─────────────────────────────────────────────────────


def project_membership_to_row(m: ProjectMembership) -> ProjectMembershipRow:
    return ProjectMembershipRow(
        id=m.id, org_id=m.org_id, workspace_id=m.workspace_id, project_id=m.project_id,
        user_id=m.user_id, role=m.role, status=m.status.value, created_at=m.created_at,
    )


def project_membership_to_domain(r: ProjectMembershipRow) -> ProjectMembership:
    return ProjectMembership(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        user_id=r.user_id, role=r.role, status=ProjectMembershipStatus(r.status),
        created_at=r.created_at,
    )


# ── Agent (M8.10) ────────────────────────────────────────────────────────────────


def agent_to_row(a: Agent) -> AgentRow:
    return AgentRow(
        id=a.id, org_id=a.org_id, workspace_id=a.workspace_id, project_id=a.project_id,
        name=a.name, created_by=a.created_by, created_at=a.created_at,
        updated_at=a.updated_at, description=a.description, icon=a.icon, category=a.category,
        tags=list(a.tags), language=a.language, greeting=a.greeting, status=a.status.value,
        version=a.version, published_version=a.published_version,
        config=a.config.as_dict(redact_secrets=False), meta=dict(a.metadata),
    )


def agent_to_domain(r: AgentRow) -> Agent:
    return Agent(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        name=r.name, created_by=r.created_by, created_at=r.created_at,
        updated_at=r.updated_at, description=r.description, icon=r.icon, category=r.category,
        tags=tuple(r.tags), language=r.language, greeting=r.greeting,
        status=AgentStatus(r.status), config=AgentConfig.from_dict(r.config),
        version=r.version, published_version=r.published_version, metadata=dict(r.meta),
    )


def agent_version_to_row(v: AgentVersion) -> AgentVersionRow:
    return AgentVersionRow(
        id=v.id, agent_id=v.agent_id, org_id=v.org_id, workspace_id=v.workspace_id,
        project_id=v.project_id, number=v.number, created_by=v.created_by,
        created_at=v.created_at, description=v.description, status=v.status.value,
        rollback_target=v.rollback_target, snapshot=dict(v.snapshot),
    )


def agent_version_to_domain(r: AgentVersionRow) -> AgentVersion:
    return AgentVersion(
        id=r.id, agent_id=r.agent_id, org_id=r.org_id, workspace_id=r.workspace_id,
        project_id=r.project_id, number=r.number, created_by=r.created_by,
        created_at=r.created_at, description=r.description,
        status=AgentVersionStatus(r.status), rollback_target=r.rollback_target,
        snapshot=dict(r.snapshot),
    )


# ── Tool (M8.11) ─────────────────────────────────────────────────────────────────


def tool_to_row(t: Tool) -> ToolRow:
    return ToolRow(
        id=t.id, org_id=t.org_id, workspace_id=t.workspace_id, project_id=t.project_id,
        name=t.name, category=t.category.value, created_by=t.created_by,
        created_at=t.created_at, updated_at=t.updated_at, description=t.description,
        icon=t.icon, status=t.status.value, visibility=t.visibility.value, enabled=t.enabled,
        version=t.version, config=t.config.as_dict(), meta=dict(t.metadata),
    )


def tool_to_domain(r: ToolRow) -> Tool:
    return Tool(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        name=r.name, category=ToolCategory(r.category), created_by=r.created_by,
        created_at=r.created_at, updated_at=r.updated_at, description=r.description,
        icon=r.icon, status=ToolStatus(r.status), visibility=ToolVisibility(r.visibility),
        enabled=r.enabled, version=r.version, config=ToolConfig.from_dict(r.config),
        metadata=dict(r.meta),
    )


def agent_tool_to_row(a: AgentTool) -> AgentToolRow:
    return AgentToolRow(
        id=a.id, org_id=a.org_id, workspace_id=a.workspace_id, project_id=a.project_id,
        agent_id=a.agent_id, tool_id=a.tool_id, position=a.position, enabled=a.enabled,
        config_override=dict(a.config_override), created_at=a.created_at,
    )


def agent_tool_to_domain(r: AgentToolRow) -> AgentTool:
    return AgentTool(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        agent_id=r.agent_id, tool_id=r.tool_id, position=r.position, enabled=r.enabled,
        config_override=dict(r.config_override), created_at=r.created_at,
    )


# ── ConversationSession (M8.12) ──────────────────────────────────────────────────


def session_to_row(s: ConversationSession) -> ConversationSessionRow:
    # State is stored WITHOUT redaction (fidelity for the future runtime); the view layer
    # redacts secrets on every read. No message history is stored.
    return ConversationSessionRow(
        id=s.id, org_id=s.org_id, workspace_id=s.workspace_id, project_id=s.project_id,
        agent_id=s.agent_id, agent_version=s.agent_version, correlation_id=s.correlation_id,
        status=s.status.value, created_at=s.created_at, updated_at=s.updated_at,
        meta=dict(s.metadata), state=s.state.as_dict(redact_secrets=False),
    )


def session_to_domain(r: ConversationSessionRow) -> ConversationSession:
    return ConversationSession(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        agent_id=r.agent_id, agent_version=r.agent_version, correlation_id=r.correlation_id,
        status=SessionStatus(r.status), created_at=r.created_at, updated_at=r.updated_at,
        metadata=dict(r.meta), state=ConversationState.from_dict(r.state),
    )


# ── ConversationMessage (M8.14) ──────────────────────────────────────────────────


def message_to_row(m: ConversationMessage) -> ConversationMessageRow:
    return ConversationMessageRow(
        id=m.id, org_id=m.org_id, session_id=m.session_id, seq=m.seq, role=m.role,
        content=m.content, meta=dict(m.metadata), created_at=m.created_at,
    )


def message_to_domain(r: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        id=r.id, org_id=r.org_id, session_id=r.session_id, seq=r.seq, role=r.role,
        content=r.content, metadata=dict(r.meta), created_at=r.created_at,
    )


# ── Knowledge (M8.15) ────────────────────────────────────────────────────────────


def knowledge_collection_to_row(c: KnowledgeCollection) -> KnowledgeCollectionRow:
    return KnowledgeCollectionRow(
        id=c.id, org_id=c.org_id, workspace_id=c.workspace_id, project_id=c.project_id,
        name=c.name, base=c.base, description=c.description, status=c.status.value,
        version=c.version, embedding_provider=c.embedding_provider,
        embedding_model=c.embedding_model, chunk_size=c.chunk_size, chunk_overlap=c.chunk_overlap,
        created_by=c.created_by, created_at=c.created_at, updated_at=c.updated_at,
        meta=dict(c.metadata),
    )


def knowledge_collection_to_domain(r: KnowledgeCollectionRow) -> KnowledgeCollection:
    return KnowledgeCollection(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        name=r.name, base=r.base, description=r.description, status=KnowledgeStatus(r.status),
        version=r.version, embedding_provider=r.embedding_provider,
        embedding_model=r.embedding_model, chunk_size=r.chunk_size, chunk_overlap=r.chunk_overlap,
        created_by=r.created_by, created_at=r.created_at, updated_at=r.updated_at,
        metadata=dict(r.meta),
    )


def knowledge_document_to_row(d: KnowledgeDocument) -> KnowledgeDocumentRow:
    return KnowledgeDocumentRow(
        id=d.id, org_id=d.org_id, workspace_id=d.workspace_id, project_id=d.project_id,
        collection_id=d.collection_id, name=d.name, mime_type=d.mime_type,
        size_bytes=d.size_bytes, checksum=d.checksum, content=d.content, version=d.version,
        status=d.status.value, created_by=d.created_by, created_at=d.created_at,
        updated_at=d.updated_at, meta=dict(d.metadata),
    )


def knowledge_document_to_domain(r: KnowledgeDocumentRow) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=r.id, org_id=r.org_id, workspace_id=r.workspace_id, project_id=r.project_id,
        collection_id=r.collection_id, name=r.name, mime_type=r.mime_type,
        size_bytes=r.size_bytes, checksum=r.checksum, content=r.content, version=r.version,
        status=DocumentStatus(r.status), created_by=r.created_by, created_at=r.created_at,
        updated_at=r.updated_at, metadata=dict(r.meta),
    )


def knowledge_chunk_to_row(c: KnowledgeChunk) -> KnowledgeChunkRow:
    return KnowledgeChunkRow(
        id=c.id, org_id=c.org_id, collection_id=c.collection_id, document_id=c.document_id,
        idx=c.index, text=c.text, checksum=c.checksum, embedding=list(c.embedding),
        embedding_status=c.embedding_status.value, index_status=c.index_status.value,
        created_at=c.created_at, meta=dict(c.metadata),
    )


def knowledge_chunk_to_domain(r: KnowledgeChunkRow) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=r.id, org_id=r.org_id, collection_id=r.collection_id, document_id=r.document_id,
        index=r.idx, text=r.text, checksum=r.checksum, embedding=tuple(r.embedding),
        embedding_status=EmbeddingStatus(r.embedding_status),
        index_status=IndexStatus(r.index_status),
        created_at=r.created_at, metadata=dict(r.meta),
    )


# ── DeploymentExecution (M8.16, ADR-0022) ─────────────────────────────────────────


def _job_to_dict(j: DeploymentJob) -> dict:
    d = j.as_dict()
    d.update({"id": j.id, "org_id": j.org_id, "workspace_id": j.workspace_id,
              "project_id": j.project_id, "metadata": dict(j.metadata)})
    return d


def _job_from_dict(d: dict) -> DeploymentJob:
    return DeploymentJob(
        id=d["id"], deployment_id=d["deployment_id"], org_id=d["org_id"],
        workspace_id=d["workspace_id"], project_id=d["project_id"], source_ref=d["source_ref"],
        environment=d["environment"], profile=d["profile"], strategy=d["strategy"],
        target=d["target"], requires_approval=d["requires_approval"],
        rollback_mode=d["rollback_mode"], rollback_version=d.get("rollback_version", ""),
        requested_by=d["requested_by"], requested_at=d["requested_at"],
        plan_content_hash=d.get("plan_content_hash", ""), metadata=dict(d.get("metadata", {})),
    )


def _exec_to_dict(e: DeploymentExecution) -> dict:
    return {
        "job": _job_to_dict(e.job),
        "cancel_requested": e.cancel_requested,
        "transitions": [t.as_dict() for t in e.transitions],
        "approvals": [a.as_dict() for a in e.approvals],
        "rollbacks": [r.as_dict() for r in e.rollbacks],
        "health": [h.as_dict() for h in e.health],
        "logs": [ln.as_dict() for ln in e.logs],
        "result": e.result.as_dict() if e.result else None,
    }


def deployment_execution_to_row(e: DeploymentExecution) -> DeploymentExecutionRow:
    return DeploymentExecutionRow(
        id=e.id, org_id=e.org_id, workspace_id=e.job.workspace_id, project_id=e.project_id,
        deployment_id=e.deployment_id, environment=e.job.environment, state=e.state.value,
        attempts=e.attempts, requested_by=e.job.requested_by,
        created_at=e.created_at, updated_at=e.updated_at, data=_exec_to_dict(e),
    )


def deployment_execution_to_domain(r: DeploymentExecutionRow) -> DeploymentExecution:
    d = r.data
    transitions = tuple(
        StateTransition(
            DeploymentExecutionState(t["from"]), DeploymentExecutionState(t["to"]),
            t["at"], actor=t.get("actor", ""), reason=t.get("reason", ""),
        )
        for t in d.get("transitions", [])
    )
    approvals = tuple(
        ApprovalRecord(a["id"], ApprovalDecision(a["decision"]), a["actor"], a["at"],
                       reason=a.get("reason", ""))
        for a in d.get("approvals", [])
    )
    rollbacks = tuple(
        RollbackRecord(
            rb["id"], RollbackTarget(rb["target_version"], rb.get("mode", "previous_version")),
            RollbackReason(rb["reason"]), rb["at"], actor=rb.get("actor", ""),
            ok=rb.get("ok", True), detail=rb.get("detail", ""),
        )
        for rb in d.get("rollbacks", [])
    )
    health = tuple(
        HealthReport(
            HealthState(h["state"]), h["startup_ok"], h["ready"], h["live"], h["at"],
            checks=tuple((c["name"], c["ok"]) for c in h.get("checks", [])),
            detail=h.get("detail", ""),
        )
        for h in d.get("health", [])
    )
    logs = tuple(LogEntry(ln["at"], ln["stage"], ln["message"]) for ln in d.get("logs", []))
    result = None
    if d.get("result"):
        rs = d["result"]
        result = DeploymentExecutionResult(rs["status"], rs.get("live_version", ""),
                                           rs.get("message", ""))
    return DeploymentExecution(
        id=r.id, job=_job_from_dict(d["job"]), state=DeploymentExecutionState(r.state),
        attempts=r.attempts, created_at=r.created_at, updated_at=r.updated_at,
        transitions=transitions, approvals=approvals, rollbacks=rollbacks, health=health,
        logs=logs, result=result, cancel_requested=d.get("cancel_requested", False),
    )


# ── AlertRule (M8.17) ──────────────────────────────────────────────────────────────


def alert_rule_to_row(r: AlertRule) -> AlertRuleRow:
    return AlertRuleRow(
        id=r.id, org_id=r.org_id, project_id=r.project_id, name=r.name, metric_key=r.metric_key,
        comparator=r.comparator.value, threshold=r.threshold, window_seconds=r.window_seconds,
        severity=r.severity.value, enabled=r.enabled, created_by=r.created_by,
        created_at=r.created_at, updated_at=r.updated_at,
    )


def alert_rule_to_domain(r: AlertRuleRow) -> AlertRule:
    return AlertRule(
        id=r.id, org_id=r.org_id, project_id=r.project_id, name=r.name, metric_key=r.metric_key,
        comparator=AlertComparator(r.comparator), threshold=r.threshold,
        window_seconds=r.window_seconds, severity=AlertSeverity(r.severity), enabled=r.enabled,
        created_by=r.created_by, created_at=r.created_at, updated_at=r.updated_at,
    )


# ── UserPreferences ──────────────────────────────────────────────────────────────


def preferences_to_row(p: UserPreferences) -> PreferencesRow:
    return PreferencesRow(
        user_id=p.user_id, org_id=p.org_id, theme=p.theme, locale=p.locale,
        keyboard_shortcuts_enabled=p.keyboard_shortcuts_enabled,
        favorites=list(p.favorites), meta=dict(p.metadata),
    )


def preferences_to_domain(r: PreferencesRow) -> UserPreferences:
    return UserPreferences(
        user_id=r.user_id, org_id=r.org_id, theme=r.theme, locale=r.locale,
        keyboard_shortcuts_enabled=r.keyboard_shortcuts_enabled,
        favorites=tuple(r.favorites), metadata=dict(r.meta),
    )
