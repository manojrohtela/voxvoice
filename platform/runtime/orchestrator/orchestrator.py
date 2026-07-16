"""ConversationOrchestrator — the sole coordinator of conversation execution (M2B.2).

Component specification (Constitution §A11.3):
  responsibilities : own the ConversationStateMachine and be its SOLE writer;
                     coordinate the conversation lifecycle phases (begin → run →
                     end) and failure routing; invoke provider capabilities ONLY
                     through the injected `core.providers` interfaces; emit
                     lifecycle notifications. It performs no speech processing, AI
                     inference, provider implementation, turn logic, or business
                     logic.
  inputs           : a RuntimeContext (session, injected provider interfaces,
                     config); an optional transition observer.
  outputs          : conversation state + immutable history (read-only);
                     transition notifications (via the state machine observer).
  dependencies     : core.states, runtime.conversation (the state machine),
                     runtime.contracts, runtime.errors, provider *interfaces* via
                     DI. Never imports `api`; never a provider implementation.
  lifecycle        : conversation CREATED → CONNECTED → IN_CONVERSATION (begin) →
                     … → ENDING → ENDED (end); FAILED on startup error. Every
                     move goes through the approved state machine — no hidden
                     transitions, no silent recovery.
  failure modes    : startup failure → conversation FAILED + OrchestrationStartupError;
                     mid-conversation failure → routed ENDING → ENDED (the state
                     machine forbids IN_CONVERSATION → FAILED, §A4.4).
  recovery         : SUSPENDED → IN_CONVERSATION is the recovery edge (driven by
                     the transport reconnect path, wired at M2C+).

Ownership boundary: the SessionWorker owns the worker; this Orchestrator owns the
conversation. The worker delegates conversation control here and never touches
conversation state. Providers own capabilities. Turn execution (M3) is separate.

Scope: the actual conversation loop (turns: STT → LLM → TTS) is M2C+. Here the
"running" phase is coordinated by awaiting an end signal; provider startup/teardown
are placeholders that take no action (no implementations exist yet).
"""

from __future__ import annotations

import asyncio
import logging

from core.states import SessionState

from ..contracts import RuntimeContext, RuntimeDependencies
from ..conversation import ConversationStateMachine, TransitionObserver, TransitionRecord
from ..errors import OrchestrationError, OrchestrationStartupError

log = logging.getLogger("voxvoice.runtime.orchestrator")


class ConversationOrchestrator:
    """Sole owner/writer of one conversation's execution."""

    def __init__(
        self,
        context: RuntimeContext,
        *,
        on_transition: TransitionObserver | None = None,
    ) -> None:
        self._context = context
        # The state machine is PRIVATE — the orchestrator is its only writer.
        self._sm = ConversationStateMachine(on_transition=on_transition)
        self._end_event = asyncio.Event()

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def state(self) -> SessionState:
        return self._sm.state

    @property
    def is_terminal(self) -> bool:
        return self._sm.is_terminal

    @property
    def history(self) -> tuple[TransitionRecord, ...]:
        return self._sm.history

    @property
    def dependencies(self) -> RuntimeDependencies:
        """The injected provider interfaces. Read-only; the orchestrator invokes
        them only through their contracts (M2C+), never a concrete implementation."""
        return self._context.dependencies

    # ── coordination phases ─────────────────────────────────────────────────
    async def begin(self) -> None:
        """Coordinate conversation startup: CREATED → CONNECTED → IN_CONVERSATION.
        On failure: conversation → FAILED and raise OrchestrationStartupError."""
        if self._sm.state is not SessionState.CREATED:
            raise OrchestrationError(
                f"begin() requires CREATED, conversation is {self._sm.state.value}"
            )
        try:
            self._sm.transition(
                SessionState.CONNECTED, reason="transport_ready", source="orchestrator"
            )
            await self._open_providers()
            self._sm.transition(
                SessionState.IN_CONVERSATION,
                reason="conversation_started",
                source="orchestrator",
            )
        except Exception as exc:  # noqa: BLE001 — re-raised as a typed error
            # During startup we are in CREATED/CONNECTED, from which FAILED is legal.
            if self._sm.can_transition(SessionState.FAILED):
                self._sm.transition(
                    SessionState.FAILED, reason="startup_failed", source="orchestrator"
                )
            self._end_event.set()
            log.error("conversation startup failed: %s", exc)
            raise OrchestrationStartupError(f"conversation startup failed: {exc}") from exc

    async def run(self) -> None:
        """Coordinate the running phase. The conversation loop (turns) is M2C+;
        here we await the end signal. Tolerates the conversation having already
        ended/ending (e.g. the session was stopped before this task started) —
        the end signal is already set, so this returns promptly. Only raises if
        run() is called before begin() (a genuine misuse)."""
        if self._sm.is_terminal or self._sm.state is SessionState.ENDING:
            return
        if self._sm.state is not SessionState.IN_CONVERSATION:
            raise OrchestrationError(
                f"run() requires IN_CONVERSATION, conversation is {self._sm.state.value}"
            )
        await self._end_event.wait()

    async def end(self, *, reason: str = "completed", source: str = "orchestrator") -> None:
        """Coordinate graceful shutdown: → ENDING → ENDED. Idempotent once terminal."""
        if self._sm.is_terminal:
            self._end_event.set()
            return
        try:
            if self._sm.state in (SessionState.IN_CONVERSATION, SessionState.SUSPENDED):
                self._sm.transition(SessionState.ENDING, reason=reason, source=source)
            if self._sm.state is SessionState.ENDING:
                await self._close_providers()
                self._sm.transition(SessionState.ENDED, reason=reason, source=source)
            else:
                # CREATED/CONNECTED: the conversation never really started; the
                # state machine has no graceful path to ENDED from here.
                self._sm.transition(
                    SessionState.FAILED,
                    reason=f"ended_before_start:{reason}",
                    source=source,
                )
        finally:
            self._end_event.set()

    async def cancel(self, *, reason: str = "cancelled") -> None:
        """Coordinate cancellation — same terminal path as end(), reason-tagged."""
        await self.end(reason=reason, source="cancel")

    def fail(self, *, reason: str) -> None:
        """Route an unexpected failure through the approved state machine (no hidden
        transitions). Pre-conversation → FAILED; mid-conversation → ENDING → ENDED
        (the machine forbids IN_CONVERSATION → FAILED)."""
        if self._sm.is_terminal:
            self._end_event.set()
            return
        if self._sm.can_transition(SessionState.FAILED):
            self._sm.transition(SessionState.FAILED, reason=reason, source="orchestrator")
        else:
            if self._sm.state in (SessionState.IN_CONVERSATION, SessionState.SUSPENDED):
                self._sm.transition(SessionState.ENDING, reason=reason, source="orchestrator")
            if self._sm.state is SessionState.ENDING:
                self._sm.transition(SessionState.ENDED, reason=reason, source="orchestrator")
        self._end_event.set()
        log.error("conversation failed: %s", reason)

    # ── provider coordination (interfaces only; placeholders until M2C) ──────
    async def _open_providers(self) -> None:
        """M2C: open transport + STT/TTS/LLM streams via `self.dependencies`
        (interfaces). No provider execution at M2B.2 — no implementations exist."""
        return

    async def _close_providers(self) -> None:
        """M2C: close provider streams. No-op at M2B.2."""
        return
