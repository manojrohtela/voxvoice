"""persistence — the Customer Platform Persistence Layer (Epic 8, M8.4).

The **only** production implementation of the Foundation's repository interfaces and the
Application's `UnitOfWork` abstraction — SQLModel-backed, transactional, and swapped in at
a composition root. It replaces the in-memory defaults for production without any interface
change: the Foundation and Application layers are byte-for-byte unchanged and never import
this package.

**Ownership & boundaries.** `api.persistence → api.foundation, api.application, core,
tenant, sqlmodel/sqlalchemy` (the persistence composition root — the only Customer-Platform
package that may import both the Foundation and the Application, mirroring
`pipeline`/`integration`). No FastAPI/HTTP, no frozen Epic 5/6/7 runtime (test-enforced).
Repositories never commit — the `SqlUnitOfWork` owns the transaction boundary, giving real
atomic rollback (closing the M8.3 in-memory limitation). Row ↔ domain mapping keeps every
persistence type out of the Foundation/Application (no leakage). Determinism (§A2.4) via
the shared `Clock`/`IdGenerator` seams.
"""

from __future__ import annotations

from .audit_store import SqlAuditStore
from .composition import (
    PersistentApplication,
    build_foundation,
    build_persistent_application,
)
from .database import Database
from .models import (
    CP_TABLES,
    AgentRow,
    AgentToolRow,
    AgentVersionRow,
    AuditEventRow,
    ConversationMessageRow,
    ConversationSessionRow,
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
    UserRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)
from .repositories import (
    SqlAgentRepository,
    SqlAgentToolRepository,
    SqlAgentVersionRepository,
    SqlConversationMessageRepository,
    SqlConversationSessionRepository,
    SqlEnvironmentRepository,
    SqlKnowledgeChunkRepository,
    SqlKnowledgeCollectionRepository,
    SqlKnowledgeDocumentRepository,
    SqlMembershipRepository,
    SqlOrganizationRepository,
    SqlPreferencesRepository,
    SqlProjectMembershipRepository,
    SqlProjectRepository,
    SqlRoleBindingRepository,
    SqlToolRepository,
    SqlUserRepository,
    SqlWorkspaceMembershipRepository,
    SqlWorkspaceRepository,
)
from .unit_of_work import SqlUnitOfWork, SqlUnitOfWorkFactory

__all__ = [
    # composition root
    "PersistentApplication", "build_foundation", "build_persistent_application",
    # database
    "Database",
    # models
    "CP_TABLES", "AgentRow", "AgentToolRow", "AgentVersionRow", "AuditEventRow",
    "ConversationMessageRow", "ConversationSessionRow", "EnvironmentRow",
    "KnowledgeChunkRow", "KnowledgeCollectionRow", "KnowledgeDocumentRow", "MembershipRow",
    "OrganizationRow", "PreferencesRow", "ProjectMembershipRow", "ProjectRow", "RoleBindingRow",
    "ToolRow", "UserRow", "WorkspaceMembershipRow", "WorkspaceRow",
    # audit store (M8.9)
    "SqlAuditStore",
    # repositories
    "SqlAgentRepository", "SqlAgentToolRepository", "SqlAgentVersionRepository",
    "SqlConversationMessageRepository", "SqlConversationSessionRepository",
    "SqlKnowledgeChunkRepository", "SqlKnowledgeCollectionRepository",
    "SqlKnowledgeDocumentRepository",
    "SqlEnvironmentRepository", "SqlMembershipRepository",
    "SqlOrganizationRepository", "SqlPreferencesRepository", "SqlProjectMembershipRepository",
    "SqlProjectRepository", "SqlRoleBindingRepository", "SqlToolRepository", "SqlUserRepository",
    "SqlWorkspaceMembershipRepository", "SqlWorkspaceRepository",
    # unit of work
    "SqlUnitOfWork", "SqlUnitOfWorkFactory",
]
