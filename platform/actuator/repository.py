"""Deployment execution repository (Epic 8, M8.16) — ADR-0022.

The durable System-of-Record seam for deployment execution state (§D6/§A7 — ordinary durable
state, one writer). The abstract contract lives here with a deterministic in-memory implementation
(insertion-ordered); the persistence layer supplies a SQL-backed implementation over the same
contract, exactly as every other aggregate does. The Actuator writes only through this seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import DeploymentExecution
from .errors import DeploymentNotFoundError


class DeploymentExecutionRepository(ABC):
    """Org/project-scoped by construction. `get` resolves by execution id **or** deployment id
    (both are unique per org) so the API can accept either."""

    @abstractmethod
    def add(self, execution: DeploymentExecution) -> DeploymentExecution: ...
    @abstractmethod
    def update(self, execution: DeploymentExecution) -> DeploymentExecution: ...
    @abstractmethod
    def get(self, org_id: str, execution_ref: str) -> DeploymentExecution | None: ...
    @abstractmethod
    def list_for_project(
        self, org_id: str, project_id: str
    ) -> tuple[DeploymentExecution, ...]: ...


class InMemoryDeploymentExecutionRepository(DeploymentExecutionRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, DeploymentExecution] = {}

    def add(self, execution: DeploymentExecution) -> DeploymentExecution:
        self._by_id[execution.id] = execution
        return execution

    def update(self, execution: DeploymentExecution) -> DeploymentExecution:
        self._by_id[execution.id] = execution
        return execution

    def get(self, org_id: str, execution_ref: str) -> DeploymentExecution | None:
        e = self._by_id.get(execution_ref)
        if e is not None and e.org_id == org_id:
            return e
        for e in self._by_id.values():
            if e.org_id == org_id and e.deployment_id == execution_ref:
                return e
        return None

    def list_for_project(self, org_id: str, project_id: str) -> tuple[DeploymentExecution, ...]:
        return tuple(
            e for e in self._by_id.values()
            if e.org_id == org_id and e.project_id == project_id
        )

    def require(self, org_id: str, execution_ref: str) -> DeploymentExecution:
        e = self.get(org_id, execution_ref)
        if e is None:
            raise DeploymentNotFoundError(f"deployment execution {execution_ref!r} not found")
        return e
