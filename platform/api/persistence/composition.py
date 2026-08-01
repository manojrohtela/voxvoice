"""Persistence composition root (M8.4).

The one place persistence is wired into the Foundation and Application — mirroring how
`pipeline`/`integration` are the composition roots for their layers. This module imports
`api.foundation` + `api.application` + the SQL implementations; **neither the Foundation
nor the Application imports persistence** (dependency direction preserved; both layers stay
byte-for-byte unchanged and unaware of the database).

`build_persistent_application(...)` returns a fully wired `Application` whose repositories
are SQL-backed and whose Unit of Work is the transactional `SqlUnitOfWork` — the production
equivalent of `ApplicationFactory.default()`, swapping only the seams the abstractions were
designed to swap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from api.application import (
    Application,
    ApplicationFactory,
    RecordingApplicationEventEmitter,
)
from api.foundation import (
    Clock,
    DefaultIdGenerator,
    ExecutionEngine,
    Foundation,
    FoundationFactory,
    IdGenerator,
    KnowledgeRetriever,
    SystemClock,
)

from .audit_store import SqlAuditStore
from .database import Database
from .repositories import (
    SqlAgentRepository,
    SqlAgentToolRepository,
    SqlAgentVersionRepository,
    SqlAlertRuleRepository,
    SqlConversationMessageRepository,
    SqlConversationSessionRepository,
    SqlDeploymentExecutionRepository,
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
from .unit_of_work import SqlUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class PersistentApplication:
    """The wired result: the Application plus the owning `Database` (so a caller can
    `create_all()` / `close()` / inspect it)."""

    application: Application
    database: Database


def build_foundation(
    database: Database,
    *,
    ids: IdGenerator,
    clock: Clock,
    engine_factory: Callable[..., ExecutionEngine] | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
) -> Foundation:
    """Wire SQL repositories into the Foundation via its existing `create` seam.

    M8.9: the audit stream is durable via `SqlAuditStore` (emitter + reader).
    M8.14: the durable message store is wired, and — if an `engine_factory` is supplied (the
    provider-adapter binding, injected by the customer Container) — the real
    `ProviderExecutionEngine` is built over that store and injected; otherwise the Foundation
    keeps its default offline `MockExecutionEngine`. Persistence never imports `api.adapters`;
    it only receives a callable, so it stays provider-agnostic."""
    audit_store = SqlAuditStore(database)
    messages = SqlConversationMessageRepository(database)
    k_collections = SqlKnowledgeCollectionRepository(database)
    k_documents = SqlKnowledgeDocumentRepository(database)
    k_chunks = SqlKnowledgeChunkRepository(database)
    # M8.15: the engine factory (customer Container) wires the Knowledge Tool over the same
    # collection/chunk repos so retrieval during a turn reads the durably-indexed vectors.
    execution_engine = (
        engine_factory(messages, k_collections, k_chunks)
        if engine_factory is not None else None
    )
    return FoundationFactory.create(
        users=SqlUserRepository(database),
        orgs=SqlOrganizationRepository(database),
        workspaces=SqlWorkspaceRepository(database),
        memberships=SqlMembershipRepository(database),
        role_bindings=SqlRoleBindingRepository(database),
        environments=SqlEnvironmentRepository(database),
        projects=SqlProjectRepository(database),
        preferences=SqlPreferencesRepository(database),
        ids=ids,
        clock=clock,
        emitter=audit_store,
        workspace_memberships=SqlWorkspaceMembershipRepository(database),  # M8.7 (additive)
        project_memberships=SqlProjectMembershipRepository(database),  # M8.8 (additive)
        audit_reader=audit_store,  # M8.9 (additive) — durable projections
        agents=SqlAgentRepository(database),  # M8.10 (additive)
        agent_versions=SqlAgentVersionRepository(database),  # M8.10 (additive)
        tools=SqlToolRepository(database),  # M8.11 (additive)
        agent_tools=SqlAgentToolRepository(database),  # M8.11 (additive)
        sessions=SqlConversationSessionRepository(database),  # M8.12 (additive)
        messages=messages,  # M8.14 (additive) — durable message store
        execution_engine=execution_engine,  # M8.14 (additive) — real provider engine (or None)
        knowledge_collections=k_collections,  # M8.15 (additive)
        knowledge_documents=k_documents,  # M8.15 (additive)
        knowledge_chunks=k_chunks,  # M8.15 (additive)
        knowledge_retriever=knowledge_retriever,  # M8.15 (additive) — provider retriever (or None)
        deployment_executions=SqlDeploymentExecutionRepository(database),  # M8.16 (additive)
        alert_rules=SqlAlertRuleRepository(database),  # M8.17 (additive)
    )


def build_persistent_application(
    *,
    url: str = "sqlite://",
    ids: IdGenerator | None = None,
    clock: Clock | None = None,
    create_all: bool = True,
    engine_factory: Callable[..., ExecutionEngine] | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
) -> PersistentApplication:
    """A production-shaped Application over a real database. Pass a
    `SequentialIdGenerator` + `FixedClock` for deterministic persistence tests. `engine_factory`
    (M8.14) injects the real provider execution engine (the customer Container passes
    `api.adapters.build_execution_engine`); omit it to keep the offline mock engine."""
    ids = ids or DefaultIdGenerator()
    clock = clock or SystemClock()
    database = Database(url)
    if create_all:
        database.create_all()
    foundation = build_foundation(
        database, ids=ids, clock=clock, engine_factory=engine_factory,
        knowledge_retriever=knowledge_retriever,
    )
    emitter = RecordingApplicationEventEmitter()
    application = ApplicationFactory.create(
        foundation=foundation,
        ids=ids,
        clock=clock,
        emitter=emitter,
        uow_factory=SqlUnitOfWorkFactory(database, emitter),
    )
    return PersistentApplication(application=application, database=database)
