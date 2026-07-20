"""PromptRuntime — the Prompt Runtime core (M5.4B-1).

A `BusConsumer` (hence a `RuntimeModule`) implementing `core.ports.PromptPort`. It owns
prompt construction and nothing else: it never assembles context, reasons, calls a model,
runs tools, or manages workflows/agents. It consumes the frozen
`core.ports.context.AssembledContext` (never the `context` package) + injected
`ProviderProfile`/`ModelProfile`/`Tokenizer` — so it imports no sibling runtime.

What lands in M5.4B-1 (core):
- **build** — run the deterministic pipeline (sections → format → naive budget → render),
  drive the package lifecycle REQUESTED→BUILT→VALIDATED→RENDERED→DELIVERED, persist to the
  temporary repository, and emit `prompt.*` events.
- **render / register** — the frozen `PromptPort`: render one template into a `PromptFragment`;
  register a template (emits `prompt.template.updated`).
- **get / remove / history** — read/delete a temporary package; list a session's packages.
- **expire** — deterministic TTL sweep (caller-driven). **statistics / health**.

The pipeline is async-first; the synchronous `PromptPort` + `build/get/remove/history` drive
it via the non-blocking sync bridge (local adapters only). Publishing standard envelopes
means Replay records and the Gateway streams them for free. Budget/validation/policy/tenancy
**intelligence is M5.4B-2** — the core validation step is a trivial always-pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.context import AssembledContext
from core.ports.prompt import PromptFragment, PromptPort

from ._run import run_coro_sync
from .builder import PromptBuilder
from .config import PromptConfiguration
from .domain import PackageState, PromptPackage, PromptResponse, PromptScope, PromptStatistics
from .events import PromptPhase
from .lifecycle import PackageTrigger, advance_package
from .observability import Metric
from .repositories import PromptRepository
from .request import PromptRequest
from .serializer import PromptSerializer
from .templates import TemplateLevel, TemplateRegistry, TemplateResolver


class PromptRuntime(BusConsumer, PromptPort):
    def __init__(
        self,
        *,
        config: PromptConfiguration,
        repository: PromptRepository,
        builder: PromptBuilder,
        registry: TemplateRegistry,
        resolver: TemplateResolver,
        serializer: PromptSerializer | None = None,
        provider: str = "neutral",
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Core is request-driven → subscribe to nothing (no content-event ingestion).
        super().__init__(
            name=config.name,
            source=EventSource.PROMPT,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._builder = builder
        self._registry = registry
        self._resolver = resolver
        self._serializer = serializer or PromptSerializer()
        self._provider = provider

    # ── PromptPort (frozen) — single-template render + register ───────────────
    def render(self, template_id: str, variables: Mapping[str, Any]) -> PromptFragment:
        """Render one registered template into a neutral fragment (pure, deterministic).
        No context, no model — the single-template path of the port."""
        template = self._resolver.resolve(template_id)
        text = template.render(content="", variables={k: str(v) for k, v in variables.items()})
        return PromptFragment(
            template_id=template.name, version=template.version, text=text,
            metadata={"role": template.role.value, "level": int(template.level)},
        )

    def register(self, template_id: str, body: str, *, version: str = "1") -> None:
        """Register/replace a template body (shared scope). Emits `prompt.template.updated`."""
        template = self._registry.register(template_id, body, level=TemplateLevel.SHARED)
        run_coro_sync(self._repo.record_template(template))
        self.meter.incr(Metric.TEMPLATE_UPDATED, 1)
        self.publish(
            EventType.PROMPT_TEMPLATE_UPDATED,
            {"template_id": template.name, "version": template.version},
            session_id="",
        )

    # ── rich build surface (drives the async core) ───────────────────────────
    def build(self, request: PromptRequest, context: AssembledContext) -> PromptPackage:
        return run_coro_sync(self.abuild(request, context)).package

    def get(self, package_id: str) -> PromptPackage | None:
        return run_coro_sync(self._repo.get_package(package_id))

    def remove(self, package_id: str) -> None:
        return run_coro_sync(self._repo.remove_package(package_id))

    def history(self, session_id: str) -> Sequence[PromptPackage]:
        return run_coro_sync(self._repo.packages_for_session(session_id))

    # ── async core: build ─────────────────────────────────────────────────────
    async def abuild(self, request: PromptRequest, context: AssembledContext) -> PromptResponse:
        now = self._clock()
        scope = PromptScope(
            org_id=request.org_id,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            user_id=request.user_id,
        )
        seq = len(await self._repo.packages_for_session(request.session_id))
        expires_at = (now + self._config.package_ttl_s) if self._config.package_ttl_s else None

        with self.meter.timer(Metric.BUILD_LATENCY):
            # REQUESTED → BUILT (sections → format → naive budget → render)
            self._emit_lite(PromptPhase.REQUESTED, request)
            package = self._builder.build(
                request, context, session_id=request.session_id, scope=scope, now=now,
                seq=seq, budget_tokens=self._config.default_budget_tokens, expires_at=expires_at,
            )
            package = advance_package(package, PackageTrigger.BUILD, clock=self._clock)
            self.metrics.incr("built")
            self.meter.incr(Metric.BUILT, 1)
            self.meter.incr(Metric.SECTIONS, len(package.sections))
            self._emit(package, PromptPhase.BUILT)

            # VALIDATED (trivial pass in the core; the ValidationEngine is M5.4B-2)
            package = advance_package(package, PackageTrigger.VALIDATE, clock=self._clock)
            self.meter.incr(Metric.VALIDATED, 1)
            self._emit(package, PromptPhase.VALIDATED)

            # RENDERED (messages produced during build; the FSM marks the stage)
            package = advance_package(package, PackageTrigger.RENDER, clock=self._clock)
            self.meter.incr(Metric.RENDERED, 1)
            self.meter.incr(Metric.MESSAGES, len(package.messages))
            self._emit(package, PromptPhase.RENDERED)

            # DELIVERED
            package = advance_package(package, PackageTrigger.DELIVER, clock=self._clock)

        await self._repo.add_package(package)
        self.metrics.incr("delivered")
        self.meter.incr(Metric.DELIVERED, 1)
        self.meter.incr(Metric.USED_TOKENS, package.used_tokens)
        self._emit(package, PromptPhase.DELIVERED)
        if package.truncated:
            self.meter.incr(Metric.TRUNCATED, 1)
        return PromptResponse(package=package, delivered=True)

    # ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        count = 0
        for pkg in await self._repo.all_packages():
            if pkg.state is PackageState.DELIVERED and pkg.is_expired(now):
                expired = advance_package(pkg, PackageTrigger.EXPIRE, clock=self._clock)
                await self._repo.remove_package(pkg.id)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1)
                self._emit(expired, PromptPhase.EXPIRED)
                count += 1
        return count

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> PromptStatistics:
        return PromptStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            packages=run_coro_sync(self._repo.package_count()),
            templates=self._registry.count(),
            provider=self._provider,
            metrics=self.metrics.snapshot(),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit(self, package: PromptPackage, phase: PromptPhase) -> None:
        payload = self._serializer.package_event_payload(package, phase=phase.value)
        self.publish(
            EventType.PROMPT_RENDERED, payload,
            session_id=package.session_id, org_id=package.org_id or "",
            conversation_id=package.conversation_id,
        )

    def _emit_lite(self, phase: PromptPhase, request: PromptRequest) -> None:
        self.publish(
            EventType.PROMPT_RENDERED,
            {"phase": phase.value, "session_id": request.session_id},
            session_id=request.session_id, org_id=request.org_id or "",
            conversation_id=request.conversation_id,
        )
