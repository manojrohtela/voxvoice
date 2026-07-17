"""SmartTurnEngine — the session-scoped owner of final conversational-timing
decisions (M3.5).

Combines the sub-frameworks' *own* outputs (Voice Activity, Endpoint, Interrupt,
Turn Engine) + runtime timing + a configurable `SmartTurnPolicy` + an optional
semantic hint into a single deterministic `SmartTurnDecision`. It is the SOLE
writer of Smart Turn decision state. It owns NO turn/endpoint/interrupt/voice
state — those frameworks remain independent and keep owning theirs; Smart Turn only
*arbitrates* their signals. It never calls an LLM: any semantic reasoning is behind
the injected `SemanticTurnEvaluator` contract (a registry-resolved provider), which
returns a neutral hint.

Determinism: `decide` is a pure function of the input snapshot + policy + the
evaluator's hint (the evaluator is required to be deterministic). Conflicts are
resolved by a fixed, documented precedence (interrupt over endpoint). Optional
timing smoothing requires N consecutive identical raw decisions before a *changed*
decision is committed. No hidden state beyond the smoothing counters + the
observable context. No silent recovery — an invalid policy raises at construction.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from core.providers.semantic_turn import SemanticHint, SemanticTurnEvaluator

from ..endpoint import EndpointDecision
from ..interrupt import InterruptDecision
from .context import SmartTurnContext
from .decision import SmartTurnDecision
from .events import SmartTurnEvent, SmartTurnObserver
from .null_semantic import NullSemanticEvaluator
from .policy import SmartTurnPolicy
from .signals import SmartTurnInput

log = logging.getLogger("voxvoice.runtime.smart_turn")

_ST = SmartTurnDecision


class SmartTurnEngine:
    def __init__(
        self,
        session_id: str,
        *,
        policy: SmartTurnPolicy | None = None,
        semantic: SemanticTurnEvaluator | None = None,
        on_event: SmartTurnObserver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._policy = policy or SmartTurnPolicy()  # validates itself (may raise)
        self._semantic = semantic or NullSemanticEvaluator()
        self._on_event = on_event
        self._clock = clock or time.time
        self._context = SmartTurnContext(session_id=session_id)
        # Smoothing working state.
        self._last_raw: SmartTurnDecision | None = None
        self._repeat = 0
        # The adaptive delay computed for the most recent FINISH/DELAY decision.
        self._pending_delay_frames = 0

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def context(self) -> SmartTurnContext:
        return self._context

    @property
    def policy(self) -> SmartTurnPolicy:
        return self._policy

    @property
    def semantic(self) -> SemanticTurnEvaluator:
        return self._semantic

    @property
    def pending_delay_frames(self) -> int:
        """The adaptive hold (frames) attached to the last FINISH/DELAY decision."""
        return self._pending_delay_frames

    # ── the deterministic arbiter (sole writer of decision state) ───────────
    def decide(self, inp: SmartTurnInput) -> SmartTurnDecision:
        """Arbitrate one signal snapshot into a final decision (deterministic)."""
        hint = self._safe_evaluate(inp)
        raw = self._raw_decision(inp, hint)
        committed = self._smooth(raw)
        return self._commit(inp, committed)

    # ── deterministic decision core ─────────────────────────────────────────
    def _raw_decision(self, inp: SmartTurnInput, hint: SemanticHint) -> SmartTurnDecision:
        w = self._policy.semantic_weight
        # 1) Interrupt domain takes precedence over endpoint (a barge-in is urgent).
        #    Fixed, documented conflict resolution.
        if inp.interrupt is InterruptDecision.INTERRUPT_CONFIRMED:
            weighted = (
                inp.confidence * self._policy.speech_confidence_weight
                + hint.interrupt_intent * w
            )
            if weighted >= self._policy.dynamic_interruption_threshold:
                return _ST.CONFIRM_INTERRUPT
            return _ST.IGNORE_INTERRUPT
        if inp.interrupt is InterruptDecision.INTERRUPT_CANCELLED:
            return _ST.RESUME_ASSISTANT

        # 2) Endpoint domain.
        if inp.endpoint in (
            EndpointDecision.ENDPOINT_CONFIRMED,
            EndpointDecision.TIMEOUT_ENDPOINT,
        ):
            # Adaptive grace: an early end may resolve to WAIT to let a trailing
            # clause land before finishing.
            if inp.elapsed_frames < self._policy.adaptive_grace_frames:
                return _ST.WAIT
            self._pending_delay_frames = self._policy.response_delay_frames(
                delay_scale=hint.delay_scale
            )
            return _ST.DELAY_RESPONSE if self._pending_delay_frames > 0 else _ST.FINISH_TURN
        if inp.endpoint is EndpointDecision.CANCEL_ENDPOINT:
            return _ST.CANCEL_ENDPOINT
        if inp.endpoint is EndpointDecision.POTENTIAL_ENDPOINT:
            return _ST.WAIT
        if inp.endpoint is EndpointDecision.CONTINUE_LISTENING:
            return _ST.CONTINUE_LISTENING

        # 3) No actionable endpoint/interrupt signal → keep listening.
        return _ST.CONTINUE_LISTENING

    def _smooth(self, raw: SmartTurnDecision) -> SmartTurnDecision:
        """Timing smoothing: require N consecutive identical raw decisions before a
        *changed* decision is committed. `WAIT` bridges the gap while stabilizing."""
        if self._policy.timing_smoothing_frames <= 0:
            return raw
        if raw == self._last_raw:
            self._repeat += 1
        else:
            self._last_raw = raw
            self._repeat = 1
        if raw == self._context.last_decision:
            return raw  # unchanged decision — no smoothing needed
        if self._repeat >= self._policy.timing_smoothing_frames:
            return raw
        return _ST.WAIT

    def _commit(self, inp: SmartTurnInput, decision: SmartTurnDecision) -> SmartTurnDecision:
        previous = self._context.last_decision
        self._context.last_decision = decision
        self._context.decision_count += 1
        ts = self._clock()
        self._context.timing.record(decision, ts)
        if self._on_event is not None:
            event = SmartTurnEvent(
                session_id=self._context.session_id,
                previous=previous,
                decision=decision,
                confidence=inp.confidence,
                ts=ts,
            )
            try:
                self._on_event(event)
            except Exception as exc:  # noqa: BLE001 — observability is best-effort
                log.warning("smart-turn observer raised (ignored): %s", exc)
        return decision

    def _safe_evaluate(self, inp: SmartTurnInput) -> SemanticHint:
        """Call the semantic evaluator behind the neutral contract. A misbehaving
        evaluator must never break the deterministic core — it degrades to the
        identity hint (logged), never a silent wrong decision."""
        try:
            hint = self._semantic.evaluate(inp.features())
        except Exception as exc:  # noqa: BLE001 — extension must not break the runtime
            log.warning("semantic evaluator raised; using identity hint: %s", exc)
            return SemanticHint()
        return hint

    def reset(self) -> None:
        """Clear smoothing/adaptive working state (runtime cancellation/shutdown)."""
        self._last_raw = None
        self._repeat = 0
        self._pending_delay_frames = 0
