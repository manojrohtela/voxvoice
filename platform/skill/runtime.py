"""SkillRuntime — the Skill Runtime core facade (M6.2B-1).

Owns **registration-based discovery only**: register/activate/deprecate/
retire/list/get_descriptor + a temporary registration log + statistics.
**Never executes anything** — there is no `execute`/`invoke`/`run` method
anywhere on this class, by design (§3 of the M6.2A design: Skill Runtime never
owns Tool execution, Workflow execution, or Agent planning). Planning,
execution context, policies, observability, and replay are explicit
non-goals of this bundle (Bundle 2).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ._run import run_coro_sync
from .catalog import SkillCapabilityCatalog
from .config import SkillConfiguration
from .domain import SkillDescriptor, SkillManifest, SkillStatistics
from .registry import SkillRegistry
from .repositories import SkillRepository


class SkillRuntime:
    def __init__(
        self,
        *,
        config: SkillConfiguration,
        repository: SkillRepository,
        registry: SkillRegistry,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._catalog = SkillCapabilityCatalog(registry)
        self._clock = clock or time.time

    # ── read-only discovery exposure ──────────────────────────────────────────
    @property
    def registry(self) -> SkillRegistry:
        return self._registry

    @property
    def catalog(self) -> SkillCapabilityCatalog:
        return self._catalog

    # ── registration + lifecycle (synchronous; registry has no store I/O) ────
    def register(self, manifest: SkillManifest) -> SkillDescriptor:
        descriptor = self._registry.register(manifest)
        run_coro_sync(self._repo.add_registration(descriptor))
        return descriptor

    def activate(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._registry.activate(namespace, id_, version)

    def deprecate(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._registry.deprecate(namespace, id_, version)

    def retire(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._registry.retire(namespace, id_, version)

    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> SkillDescriptor:
        return self._registry.get_descriptor(namespace, id_, version)

    def list(self, *, include_deprecated: bool = False) -> tuple[SkillDescriptor, ...]:
        return self._registry.list(include_deprecated=include_deprecated)

    def list_scoped(
        self, *, namespace: str | None = None, capability: str | None = None,
        include_deprecated: bool = False,
    ) -> tuple[SkillDescriptor, ...]:
        return self._registry.list_scoped(
            namespace=namespace, capability=capability, include_deprecated=include_deprecated
        )

    # ── registration history ─────────────────────────────────────────────────
    def registrations(self) -> Sequence[SkillDescriptor]:
        return run_coro_sync(self._repo.all_registrations())

    # ── observability (basic counters only) ──────────────────────────────────
    def statistics(self) -> SkillStatistics:
        return SkillStatistics(
            name=self._config.name, state="active", healthy=True,
            skills=self._registry.names(),
            registrations=run_coro_sync(self._repo.registration_count()),
            metrics={},
        )
