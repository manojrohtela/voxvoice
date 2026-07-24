"""WorkflowRuntime — the Workflow Runtime core facade (M6.4B-1).

Owns **registration-based discovery only**: register/activate/deprecate/
retire/list/get_descriptor + a temporary registration log + statistics.
**Never executes anything** — there is no `execute`/`invoke`/`run`/`advance`
method anywhere on this class, by design. No checkpoints, no recovery, no
replay, no observability, no node execution, no Tool/Skill/Hook execution
exist here or anywhere in this bundle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ._run import run_coro_sync
from .config import WorkflowConfiguration
from .diagnostics import WorkflowRuntimeDiagnostics, build_diagnostics
from .domain import WorkflowDescriptor, WorkflowManifest, WorkflowStatistics
from .registry import WorkflowRegistry
from .repositories import WorkflowRepository
from .statistics import WorkflowRuntimeStatistics, compute_statistics


class WorkflowRuntime:
    def __init__(
        self,
        *,
        config: WorkflowConfiguration,
        repository: WorkflowRepository,
        registry: WorkflowRegistry,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._clock = clock or time.time

    # ── read-only discovery exposure ──────────────────────────────────────────
    @property
    def registry(self) -> WorkflowRegistry:
        return self._registry

    # ── registration + lifecycle (synchronous; registry has no store I/O) ────
    def register(self, manifest: WorkflowManifest) -> WorkflowDescriptor:
        descriptor = self._registry.register(manifest)
        run_coro_sync(self._repo.add_registration(descriptor))
        return descriptor

    def activate(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._registry.activate(namespace, id_, version)

    def deprecate(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._registry.deprecate(namespace, id_, version)

    def retire(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._registry.retire(namespace, id_, version)

    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> WorkflowDescriptor:
        return self._registry.get_descriptor(namespace, id_, version)

    def list(self, *, include_deprecated: bool = False) -> tuple[WorkflowDescriptor, ...]:
        return self._registry.list(include_deprecated=include_deprecated)

    def list_scoped(
        self, *, namespace: str | None = None, include_deprecated: bool = False,
    ) -> tuple[WorkflowDescriptor, ...]:
        return self._registry.list_scoped(
            namespace=namespace, include_deprecated=include_deprecated
        )

    # ── registration history ─────────────────────────────────────────────────
    def registrations(self) -> Sequence[WorkflowDescriptor]:
        return run_coro_sync(self._repo.all_registrations())

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, healthy: bool = True) -> WorkflowRuntimeDiagnostics:
        count = run_coro_sync(self._repo.registration_count())
        return build_diagnostics(
            config=self._config, registry=self._registry,
            repository_registration_count=count, healthy=healthy,
        )

    def statistics(
        self, descriptors: Sequence[WorkflowDescriptor] | None = None,
    ) -> WorkflowRuntimeStatistics:
        chosen = descriptors if descriptors is not None else self._registry.list()
        return compute_statistics(chosen)

    def runtime_statistics(self) -> WorkflowStatistics:
        return WorkflowStatistics(
            name=self._config.name, state="active", healthy=True,
            workflows=self._registry.names(),
            registrations=run_coro_sync(self._repo.registration_count()),
            metrics={},
        )
