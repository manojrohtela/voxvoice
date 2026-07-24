"""AgentRuntime — the Agent Runtime core facade (M6.5B-1).

Owns **registration-based discovery only**: register/activate/deprecate/
retire/list/get_descriptor + a temporary registration log + statistics.
**Never plans, never reasons, never delegates, never executes anything** —
there is no `decompose`/`resolve`/`delegate`/`run`/`advance_goal` method
anywhere on this class, by design. No `ReasoningPort`/`MemoryPort`/
`KnowledgePort`/`PromptPort`/`ContextPort`/`AgentWorkflowPort` call site
exists here or anywhere in this bundle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ._run import run_coro_sync
from .config import AgentConfiguration
from .diagnostics import AgentRuntimeDiagnostics, build_diagnostics
from .domain import AgentDescriptor, AgentManifest, AgentStatistics
from .registry import AgentRegistry
from .repositories import AgentRepository
from .statistics import AgentRuntimeStatistics, compute_statistics


class AgentRuntime:
    def __init__(
        self,
        *,
        config: AgentConfiguration,
        repository: AgentRepository,
        registry: AgentRegistry,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._clock = clock or time.time

    # ── read-only discovery exposure ──────────────────────────────────────────
    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    # ── registration + lifecycle (synchronous; registry has no store I/O) ────
    def register(self, manifest: AgentManifest) -> AgentDescriptor:
        descriptor = self._registry.register(manifest)
        run_coro_sync(self._repo.add_registration(descriptor))
        return descriptor

    def activate(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._registry.activate(namespace, id_, version)

    def deprecate(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._registry.deprecate(namespace, id_, version)

    def retire(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._registry.retire(namespace, id_, version)

    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> AgentDescriptor:
        return self._registry.get_descriptor(namespace, id_, version)

    def list(self, *, include_deprecated: bool = False) -> tuple[AgentDescriptor, ...]:
        return self._registry.list(include_deprecated=include_deprecated)

    def list_scoped(
        self, *, namespace: str | None = None, include_deprecated: bool = False,
    ) -> tuple[AgentDescriptor, ...]:
        return self._registry.list_scoped(
            namespace=namespace, include_deprecated=include_deprecated
        )

    # ── registration history ─────────────────────────────────────────────────
    def registrations(self) -> Sequence[AgentDescriptor]:
        return run_coro_sync(self._repo.all_registrations())

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, healthy: bool = True) -> AgentRuntimeDiagnostics:
        count = run_coro_sync(self._repo.registration_count())
        return build_diagnostics(
            config=self._config, registry=self._registry,
            repository_registration_count=count, healthy=healthy,
        )

    def statistics(
        self, descriptors: Sequence[AgentDescriptor] | None = None,
    ) -> AgentRuntimeStatistics:
        chosen = descriptors if descriptors is not None else self._registry.list()
        return compute_statistics(chosen)

    def runtime_statistics(self) -> AgentStatistics:
        return AgentStatistics(
            name=self._config.name, state="active", healthy=True,
            agents=self._registry.names(),
            registrations=run_coro_sync(self._repo.registration_count()),
            metrics={},
        )
