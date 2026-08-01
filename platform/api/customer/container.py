"""Dependency-injection container + FastAPI providers (M8.5).

The composition seam for the API. `Container` holds the wired `PersistentApplication`
(from the M8.4 persistence composition root) and the settings; the FastAPI dependency
providers pull the Application/database/settings off `request.app.state.container`. Every
dependency is replaceable for tests — a test builds a `Container` over an in-memory database
(or a stub) and hands it to `create_app(container=...)`, overriding nothing else.

The API never constructs repositories, a UnitOfWork, or the Foundation itself — it asks the
persistence composition root to wire them. No business logic here.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from api.adapters import build_execution_engine, build_knowledge_retriever
from api.application import Application
from api.foundation import Clock, IdGenerator
from api.persistence import Database, PersistentApplication, build_persistent_application

from .config import CustomerApiSettings


@dataclass(frozen=True, slots=True)
class Container:
    settings: CustomerApiSettings
    persistent: PersistentApplication

    @property
    def application(self) -> Application:
        return self.persistent.application

    @property
    def database(self) -> Database:
        return self.persistent.database

    @classmethod
    def build(
        cls,
        settings: CustomerApiSettings,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
    ) -> Container:
        # M8.14/M8.15 — inject the real Provider Execution Engine + Knowledge retriever (adapters
        # layer). Both default to the deterministic offline `EchoTransport` + local embedder; a
        # production deployment swaps in an HTTP transport + real API keys + a real vector store.
        # Persistence stays provider-agnostic (it receives callables). The **same** retriever backs
        # the Studio knowledge search and the Knowledge Tool wired into the execution engine.
        retriever = build_knowledge_retriever()
        persistent = build_persistent_application(
            url=settings.database_url, ids=ids, clock=clock,
            engine_factory=lambda msgs, k_collections, k_chunks: build_execution_engine(
                msgs, knowledge_collections=k_collections, knowledge_chunks=k_chunks,
                knowledge_retriever=retriever,
            ),
            knowledge_retriever=retriever,
        )
        return cls(settings=settings, persistent=persistent)


# ── FastAPI providers (all replaceable via app.state / dependency overrides) ──────


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_settings(container: Container = Depends(get_container)) -> CustomerApiSettings:
    return container.settings


def get_application(container: Container = Depends(get_container)) -> Application:
    return container.application


def get_database(container: Container = Depends(get_container)) -> Database:
    return container.database
