"""SessionWorker — the Runtime Plane's per-session lifecycle owner (M2A).

Component specification (Constitution §A11.3):
  responsibilities : own the execution lifecycle of exactly ONE voice session —
                     startup, run, graceful shutdown, cancellation, cleanup, and
                     health/metadata exposure. NOTHING else (no conversation
                     orchestration, turn detection, memory, providers, audio, or
                     AI — those are later milestones).
  inputs           : a RuntimeContext (the injected session identity, capability
                     providers, and configuration) and an optional lifecycle
                     observer.
  outputs          : lifecycle signals; health status; a RuntimeMetadata snapshot.
  dependencies     : core (errors); runtime contracts + state. Never imports
                     `api`; never instantiates a provider (AR-20/21).
  lifecycle        : CREATED -> INITIALIZING -> READY -> RUNNING -> STOPPING ->
                     STOPPED, with FAILED reachable on any error (see
                     `runtime.state.WORKER_TRANSITIONS`).
  emitted events   : lifecycle signals only (worker_created/…/stopped/failed) via
                     the injected observer + structured logs. The full Event Bus
                     is M4.
  failure modes    : startup/init failure -> FAILED + WorkerStartupError;
                     shutdown failure -> FAILED + WorkerShutdownError; illegal
                     transition -> InvalidWorkerTransition. No silent failures.
  recovery         : cleanup is guaranteed and idempotent on every stop/cancel/
                     failure path; the owning host may discard a FAILED/STOPPED
                     worker. (Snapshot-based session recovery is Constitution §A7,
                     built with memory at M5.)

Scope note (DL-0006): the Constitution glossary defines a Session Worker as
executing "one or more sessions". M2A implements the *one*-session case; a future
host that multiplexes many workers/sessions is an additive change.

Deterministic: given the same sequence of lifecycle calls, the same transitions
occur. No runtime globals; all state is instance state; one session per worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from .contracts import (
    LifecycleObserver,
    LifecycleSignal,
    RuntimeContext,
    RuntimeDependencies,
    RuntimeMetadata,
    SessionContext,
    WorkerConfiguration,
)
from .errors import InvalidWorkerTransition, WorkerShutdownError, WorkerStartupError
from .state import TERMINAL_WORKER_STATES, WORKER_TRANSITIONS, WorkerState

log = logging.getLogger("voxvoice.runtime.worker")


class SessionWorker:
    """Owns the lifecycle of exactly one voice session. Business logic lives in
    later runtime components, not here."""

    def __init__(
        self,
        context: RuntimeContext,
        *,
        on_lifecycle: LifecycleObserver | None = None,
    ) -> None:
        self._context = context
        self._on_lifecycle = on_lifecycle

        self._state = WorkerState.CREATED
        self._created_at = time.time()
        self._started_at: float | None = None
        self._stopped_at: float | None = None

        self._stop_event = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._cleaned_up = False

        self._emit("constructed")

    # ── read-only exposure (health, metadata, DI points) ────────────────────
    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def session(self) -> SessionContext:
        return self._context.session

    @property
    def dependencies(self) -> RuntimeDependencies:
        """The injected providers/metrics. Exposed so future components read them
        from here — never from a global (AR-4)."""
        return self._context.dependencies

    @property
    def config(self) -> WorkerConfiguration:
        return self._context.config

    @property
    def health(self) -> dict[str, object]:
        return {
            "worker_id": self._context.config.worker_id,
            "session_id": self._context.session.session_id,
            "state": self._state.value,
            "healthy": self._state is not WorkerState.FAILED,
            "uptime_s": (time.time() - self._started_at) if self._started_at else 0.0,
        }

    @property
    def metadata(self) -> RuntimeMetadata:
        return RuntimeMetadata(
            worker_id=self._context.config.worker_id,
            session_id=self._context.session.session_id,
            state=self._state,
            created_at=self._created_at,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        """CREATED -> INITIALIZING -> READY -> RUNNING. Spawns the (cancellable)
        run task. On any failure: -> FAILED and raise WorkerStartupError."""
        if self._state is not WorkerState.CREATED:
            raise InvalidWorkerTransition(
                f"start() requires CREATED, worker is {self._state.value}"
            )
        try:
            self._transition(WorkerState.INITIALIZING, "initializing")
            await self._initialize()
            self._transition(WorkerState.READY, "ready")
            self._started_at = time.time()
            self._transition(WorkerState.RUNNING, "running")
            self._run_task = asyncio.create_task(
                self._run(), name=f"worker-{self._context.config.worker_id}"
            )
        except Exception as exc:  # noqa: BLE001 — re-raised as a typed error
            self._fail("startup failed", exc)
            await self._cleanup()  # cleanup is guaranteed on failure too
            raise WorkerStartupError(f"worker startup failed: {exc}") from exc

    async def stop(self) -> None:
        """Graceful shutdown: signal the run task, drain it (bounded by
        shutdown_timeout_s), then guarantee cleanup and reach STOPPED.
        Idempotent once terminal."""
        if self._state in TERMINAL_WORKER_STATES:
            return
        try:
            self._transition(WorkerState.STOPPING, "stopping")
            self._stop_event.set()
            await self._drain_run_task()
        except InvalidWorkerTransition:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail("shutdown failed", exc)
            raise WorkerShutdownError(f"worker shutdown failed: {exc}") from exc
        finally:
            await self._cleanup()
        self._stopped_at = time.time()
        self._transition(WorkerState.STOPPED, "stopped")

    async def cancel(self) -> None:
        """Forceful shutdown: cancel the run task immediately, then guarantee
        cleanup and reach STOPPED. Idempotent once terminal."""
        if self._state in TERMINAL_WORKER_STATES:
            return
        try:
            if WorkerState.STOPPING in WORKER_TRANSITIONS[self._state]:
                self._transition(WorkerState.STOPPING, "cancelling")
            self._stop_event.set()
            if self._run_task is not None:
                self._run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._run_task
        finally:
            await self._cleanup()
        self._stopped_at = time.time()
        self._transition(WorkerState.STOPPED, "cancelled")

    # ── internals ─────────────────────────────────────────────────────────
    async def _initialize(self) -> None:
        """Startup hook. At M2A this only validates the injected context; future
        milestones open transports/providers here. No behaviour that could hang."""
        if not self._context.config.worker_id:
            raise ValueError("worker_id is required")
        if not self._context.session.session_id:
            raise ValueError("session_id is required")

    async def _run(self) -> None:
        """The running loop. At M2A there is deliberately NO conversation logic —
        the worker idles until asked to stop, and is fully cancellable. The
        conversation loop is wired in from M2B."""
        await self._stop_event.wait()

    async def _drain_run_task(self) -> None:
        if self._run_task is None:
            return
        try:
            await asyncio.wait_for(
                self._run_task, timeout=self._context.config.shutdown_timeout_s
            )
        except TimeoutError:
            log.warning(
                "worker %s run task did not stop in %.1fs; cancelling",
                self._context.config.worker_id,
                self._context.config.shutdown_timeout_s,
            )
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
        except asyncio.CancelledError:
            pass

    async def _cleanup(self) -> None:
        """Idempotent resource release. Runs on every stop/cancel/failure path.
        Nothing to release at M2A; future components register teardown here."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        log.debug("worker %s cleaned up", self._context.config.worker_id)

    def _fail(self, detail: str, cause: BaseException | None = None) -> None:
        """Force FAILED (reachable from any non-terminal state) and record it.
        No silent failures — always logged and signalled."""
        if self._state not in TERMINAL_WORKER_STATES:
            self._state = WorkerState.FAILED
            self._emit(detail)
        log.error(
            "worker %s FAILED: %s (%s)",
            self._context.config.worker_id,
            detail,
            cause,
        )

    def _transition(self, target: WorkerState, detail: str | None = None) -> None:
        if target not in WORKER_TRANSITIONS[self._state]:
            raise InvalidWorkerTransition(
                f"illegal transition {self._state.value} -> {target.value}"
            )
        self._state = target
        self._emit(detail)

    def _emit(self, detail: str | None = None) -> None:
        signal = LifecycleSignal(
            worker_id=self._context.config.worker_id,
            session_id=self._context.session.session_id,
            state=self._state,
            ts=time.time(),
            detail=detail,
        )
        log.info(
            "worker %s -> %s (%s)",
            signal.worker_id,
            signal.state.value,
            detail or "",
        )
        if self._on_lifecycle is not None:
            # The observer must never break the worker (observability is best-effort).
            try:
                self._on_lifecycle(signal)
            except Exception as exc:  # noqa: BLE001
                log.warning("lifecycle observer raised (ignored): %s", exc)
