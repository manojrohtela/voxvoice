"""RuntimeEventBridge — publishes runtime facts to the Event Bus (M4.1).

The composition-layer bridge that lets runtime engines emit events **without
knowing who consumes them and without any coupling to the bus**. Each engine keeps
its existing sync `on_event` observer hook (the extension point every engine's
`events.py` was built with); the orchestrator wires that hook to a bridge method
here, which maps the engine's native event to a `core.events.Event` envelope and
publishes it through an `EventPublisher` bound to the engine's `EventSource`.

Ownership is unchanged: engines own their state and still only *notify* their
observer; the bridge (not the engine) knows the Event Bus exists. No engine imports
the bus. This module lives in `runtime/` (it imports the runtime engines' decision
enums); the bus lives in `events/` and is injected — `events/` never imports
`runtime` (AR — no upward imports).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.events import EventMetadata, EventSource, EventType

from .endpoint import EndpointDecision
from .interrupt import InterruptDecision
from .turn import TurnPhase
from .voice_activity import VoiceActivityState

if TYPE_CHECKING:
    from core.events import EventBus

    from .endpoint.events import EndpointEvent
    from .interrupt.events import InterruptEvent
    from .smart_turn.events import SmartTurnEvent
    from .turn.events import TurnEvent
    from .voice_activity.events import VoiceActivityEvent


class RuntimeEventBridge:
    """Maps each engine's native event → an `Event` envelope on the injected bus.
    One bridge per session; one `EventPublisher` per source subsystem."""

    def __init__(
        self,
        bus: EventBus,
        *,
        session_id: str,
        org_id: str = "",
        conversation_id: str | None = None,
    ) -> None:
        self._bus = bus

        def _pub(source: EventSource):
            return bus.publisher(
                EventMetadata(
                    session_id=session_id,
                    source=source,
                    org_id=org_id,
                    conversation_id=conversation_id,
                )
            )

        self._runtime = _pub(EventSource.RUNTIME)
        self._orchestrator = _pub(EventSource.ORCHESTRATOR)
        self._turn = _pub(EventSource.TURN_ENGINE)
        self._voice = _pub(EventSource.VOICE_ACTIVITY)
        self._endpoint = _pub(EventSource.ENDPOINT)
        self._interrupt = _pub(EventSource.INTERRUPT)
        self._smart_turn = _pub(EventSource.SMART_TURN)
        self._provider = _pub(EventSource.PROVIDER_REGISTRY)

    # ── lifecycle (published by the orchestrator) ───────────────────────────
    def runtime_started(self) -> None:
        self._runtime.publish(EventType.RUNTIME_STARTED)

    def runtime_stopped(self) -> None:
        self._runtime.publish(EventType.RUNTIME_STOPPED)

    def runtime_error(self, reason: str) -> None:
        self._runtime.publish(EventType.RUNTIME_ERROR, {"reason": reason})

    def session_started(self) -> None:
        self._orchestrator.publish(EventType.SESSION_STARTED)

    def session_ended(self, reason: str | None = None) -> None:
        self._orchestrator.publish(
            EventType.SESSION_ENDED, {"reason": reason} if reason else None
        )

    def provider_connected(self, capability: str, name: str) -> None:
        self._provider.publish(
            EventType.PROVIDER_CONNECTED, {"capability": capability, "provider": name}
        )

    def provider_disconnected(self, capability: str, name: str) -> None:
        self._provider.publish(
            EventType.PROVIDER_DISCONNECTED, {"capability": capability, "provider": name}
        )

    # ── engine observers (wired into each engine's on_event hook) ───────────
    def turn_observer(self, ev: TurnEvent) -> None:
        phase = ev.phase
        if phase is TurnPhase.LISTENING and ev.previous is TurnPhase.IDLE:
            self._turn.publish(EventType.TURN_STARTED, turn=ev.turn_index)
        elif phase is TurnPhase.ASSISTANT_SPEAKING:
            self._turn.publish(EventType.ASSISTANT_STARTED_SPEAKING, turn=ev.turn_index)
        elif phase is TurnPhase.TURN_COMPLETED:
            if ev.previous is TurnPhase.ASSISTANT_SPEAKING:
                self._turn.publish(EventType.ASSISTANT_STOPPED_SPEAKING, turn=ev.turn_index)
            self._turn.publish(EventType.TURN_COMPLETED, turn=ev.turn_index)
        elif phase is TurnPhase.TURN_CANCELLED:
            if ev.previous is TurnPhase.ASSISTANT_SPEAKING:
                self._turn.publish(EventType.ASSISTANT_STOPPED_SPEAKING, turn=ev.turn_index)
            self._turn.publish(EventType.TURN_CANCELLED, turn=ev.turn_index)

    def voice_activity_observer(self, ev: VoiceActivityEvent) -> None:
        if ev.state is VoiceActivityState.SPEAKING:
            self._voice.publish(EventType.SPEECH_STARTED)
        elif ev.state is VoiceActivityState.SILENCE_CONFIRMED:
            self._voice.publish(EventType.SPEECH_STOPPED)

    def endpoint_observer(self, ev: EndpointEvent) -> None:
        d = ev.decision
        if d in (EndpointDecision.ENDPOINT_CONFIRMED, EndpointDecision.TIMEOUT_ENDPOINT):
            self._endpoint.publish(
                EventType.ENDPOINT_DETECTED, {"decision": d.value}, turn=ev.turn_index
            )
        elif d is EndpointDecision.CANCEL_ENDPOINT:
            self._endpoint.publish(EventType.ENDPOINT_CANCELLED, turn=ev.turn_index)

    def interrupt_observer(self, ev: InterruptEvent) -> None:
        d = ev.decision
        if d is InterruptDecision.POTENTIAL_INTERRUPT:
            self._interrupt.publish(EventType.INTERRUPT_DETECTED)
        elif d is InterruptDecision.INTERRUPT_CONFIRMED:
            self._interrupt.publish(EventType.INTERRUPT_CONFIRMED)
        elif d is InterruptDecision.INTERRUPT_CANCELLED:
            self._interrupt.publish(EventType.INTERRUPT_CANCELLED)

    def smart_turn_observer(self, ev: SmartTurnEvent) -> None:
        self._smart_turn.publish(
            EventType.SMART_TURN_DECISION, {"decision": ev.decision.value}
        )
