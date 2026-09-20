"""`TurnLatencyTap` (M10.9, ADR-0038) — the per-turn latency producer.

The component **AR-42** has required since it was written: *"every stage boundary in the
conversation loop carries a latency tap feeding `metric.turn`."* Until now
`platform/observability/` was a two-file skeleton and nothing emitted a runtime metric at all.

**What it is.** A notify-only observer. `RuntimePipeline` calls `record()` at each of the six
ratified boundaries and `complete()` when the turn terminates; this class turns those instants
into exactly one `metric.turn` and publishes it through the ordinary `EventPublisher` path. It is
the **sole producer** of runtime `metric.*` (ADR-0033 MS8, AR-2).

**Why capture is separated from publication** (ADR-0038 TP10). `InProcessEventBus.publish()` is
**synchronous run-to-completion**: it stamps `seq`, may perform a durable write inline, and then
drains its queue calling every subscriber **on the caller's stack**. Publishing at each boundary
would run unbounded third-party work six times per turn inside a ≤1.0 s voice-to-voice budget
(invariant **I1**) — Blueprint risk **R-9** exactly. So `record()` reads a clock and writes a dict,
and the single `publish` happens in `complete()`, after the turn is already terminal, where added
latency cannot reach the caller.

**Why it owns its clock** (TP7). `runtime.turn.TurnTiming` already captures phase instants, but
through the Turn Engine's clock, which is documented as *"monotonic-agnostic wall clock"*.
Constitution v2 requires monotonic. Subtracting a monotonic instant from a wall-clock one is a
correctness bug, so this tap carries **one injected monotonic clock** and every instant it records
comes from it — which is also why `runtime/turn/**` needs no change.

**Ordering, recorded honestly** (TP3a). This pipeline is **segment-driven**: an endpoint decision
is derived *from* an STT `Final`, so `stt_final` precedes `endpoint_decided` — the inverse of
Constitution v2's budget ordering, which describes the audio-driven architecture Epic 11 will
build. The derived stage keys follow the **real** order so no negative duration is ever emitted;
the raw instants in `at` are order-agnostic evidence, so M10.11 can re-derive any interval it
wants even if a derived key is remapped later.

**Not** a tracing framework, a logging framework, an aggregation engine, a cost meter or a
persistence layer. It holds no thread, scheduler, timer or buffer — the metric buffer is M10.10.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from core.events import EventType


class MetricPublisher(Protocol):
    """The one method this producer needs from a source-bound publish handle.

    Declared structurally rather than imported: `events.EventPublisher` is the concrete type a
    composition root supplies, but `observability/` keeps a **`core` + stdlib budget**
    (ADR-0033 MS8) and importing `events` would break it. `events.EventPublisher` satisfies this
    as-is — no adapter, no second publishing mechanism.
    """

    def publish(
        self,
        type: EventType,
        data: dict | None = None,
        *,
        turn: int | None = None,
        correlation_id: str | None = None,
        metadata: dict | None = None,
    ) -> object: ...

log = logging.getLogger("voxvoice.observability.turn_latency")

#: The six AR-42 latency boundaries, in the order this pipeline actually reaches them
#: (ADR-0038 TP3/TP3a). The enumeration is what makes AR-42's merge gate enforceable: a new
#: stage without a tap fails against this list rather than against someone's memory.
STAGE_BOUNDARIES: tuple[str, ...] = (
    "speech_end",
    "endpoint_decided",
    "stt_final",
    "llm_first_token",
    "tts_first_audio",
    "first_audio_out",
)

#: Recorded, but not a latency stage — it marks the turn, and no duration is derived across it.
LIFECYCLE_MARKERS: tuple[str, ...] = ("playback_start",)

#: Boundaries recorded **outside** the voice-to-voice chain (M11.8). `speech_start` is the instant
#: the caller began the utterance, which is *before* `speech_end` and therefore before the chain
#: starts: it is evidence about the turn, not a stage of responding to it. Keeping it out of
#: `STAGE_BOUNDARIES` is what preserves the chain's summation property (below).
AUDIO_BOUNDARIES: tuple[str, ...] = ("speech_start",)

#: Every boundary name this tap accepts.
KNOWN_BOUNDARIES: frozenset[str] = frozenset(
    STAGE_BOUNDARIES + LIFECYCLE_MARKERS + AUDIO_BOUNDARIES
)

#: Derived stage → (from, to). A contiguous chain over the six boundaries, so when all six are
#: present the stages sum to `voice_to_voice_ms` up to integer rounding.
#:
#: **M11.8 remap (ADR-0047 ME1, completing ADR-0038 TP3a).** The chain now follows §A8's ordering —
#: *"endpoint decision ≤ 250 ms after true end-of-speech · STT final ≤ 150 ms after endpoint"* —
#: because endpointing is audio-driven: true end-of-speech comes from the voice-activity signal,
#: not from an STT `Final`. **Every key name is preserved** (`stt`, `endpoint`, `llm`, `tts`,
#: `egress`); what changed is which instants they span, which is an accuracy gain, not a contract
#: change (ME2: additive; ME4: `voice_to_voice_ms` frozen).
#:
#: A segment-driven profile (no audio endpointing) reaches `stt_final` *before* `endpoint_decided`,
#: so its `stt` interval would be negative and is **dropped, never emitted** (ME6/TP5) — the
#: honest outcome ADR-0038 TP3a anticipated for exactly this transition.
STAGE_INTERVALS: tuple[tuple[str, str, str], ...] = (
    ("endpoint", "speech_end", "endpoint_decided"),
    ("stt", "endpoint_decided", "stt_final"),
    ("llm", "stt_final", "llm_first_token"),
    ("tts", "llm_first_token", "tts_first_audio"),
    ("egress", "tts_first_audio", "first_audio_out"),
)

#: Derived stages **outside** the chain (M11.8, ME2's additive limb). `user_speech` is how long the
#: caller actually spoke; it precedes the chain, so it is deliberately not part of the sum that
#: makes up `voice_to_voice_ms`.
ADDITIVE_INTERVALS: tuple[tuple[str, str, str], ...] = (
    ("user_speech", "speech_start", "speech_end"),
)

#: Terminal outcomes (ADR-0038 TP6).
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "interrupted"})


def _ms(start: float, end: float) -> int | None:
    """Integer milliseconds, or `None` when the interval is not a measurement.

    A negative interval is evidence of a defect, not a latency, and ADR-0038 TP5 forbids
    emitting one. Returning `None` drops the key rather than fabricating a value.
    """
    delta = end - start
    if delta < 0:
        return None
    return int(round(delta * 1000))


class TurnLatencyTap:
    """Records stage boundaries for in-flight turns and emits one `metric.turn` each.

    Satisfies `runtime.contracts.LatencyObserver` **structurally** — this module imports no
    `runtime` and `runtime` imports no `observability`; a composition root binds them.

    One tap per session, matching `RuntimeEventBridge`'s *"one bridge per session"*: the
    publisher is bound to that session's `EventMetadata`. State is nonetheless keyed
    `(session_id, turn_index)` so two sessions can never overwrite one another (TP8).
    """

    def __init__(
        self,
        publisher: MetricPublisher,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._publisher = publisher
        # Monotonic, not wall clock (TP7). Injected so tests are deterministic and assert by
        # value; `time.monotonic` is the only sane production default for a duration.
        self._clock = clock or time.monotonic
        self._turns: dict[tuple[str, int], dict[str, float]] = {}

    # ── LatencyObserver ─────────────────────────────────────────────────────

    def record(
        self, session_id: str, turn_index: int, boundary: str, *, backdate_ms: float = 0.0
    ) -> None:
        """Note that `boundary` was reached, now — or `at` an explicitly supplied instant.

        The **first** occurrence wins: a boundary is the instant a stage was *first* reached, and
        a re-record would move a measurement backwards in the middle of a turn.

        `backdate_ms` exists for audio-derived boundaries (M11.8). Endpointing can only
        *recognise* the end of speech once its silence hold has elapsed, so stamping the clock at
        recognition time would place `speech_end` a few hundred milliseconds after the caller
        actually stopped — silently **understating** every `voice_to_voice_ms` derived from it. The
        caller reports how much audio ago the boundary really was and this tap subtracts it from
        its own clock, so TP7 holds: the instant is still monotonic and still from one clock.
        """
        if backdate_ms < 0:
            raise ValueError(f"backdate_ms must be >= 0, got {backdate_ms}")
        if boundary not in KNOWN_BOUNDARIES:
            # Loud, because an unknown boundary means AR-42's enumeration and the pipeline have
            # drifted — exactly what the written list exists to catch. The pipeline isolates
            # this, so a drift is reported without ending a conversation.
            raise ValueError(
                f"unknown stage boundary {boundary!r}; "
                f"expected one of {sorted(KNOWN_BOUNDARIES)}"
            )
        instants = self._turns.setdefault((session_id, turn_index), {})
        # Membership first, then the clock: `setdefault(k, self._clock())` would read the clock
        # even when the key exists, which is a wasted read in production and a *moved* instant
        # under any clock with side effects.
        if boundary not in instants:
            instants[boundary] = self._clock() - backdate_ms / 1000

    def complete(self, session_id: str, turn_index: int, status: str) -> None:
        """The turn is terminal: emit at most one `metric.turn` and release its state.

        A turn with **no** recorded boundary emits nothing — there is no metric to report and a
        zero-filled one would be a fabrication (TP6). An unknown key is a no-op rather than an
        error: a stray terminal notification must not resurrect a released turn.
        """
        if status not in TERMINAL_STATUSES:
            raise ValueError(
                f"unknown terminal status {status!r}; "
                f"expected one of {sorted(TERMINAL_STATUSES)}"
            )
        at = self._turns.pop((session_id, turn_index), None)
        if not at:
            return
        self._publisher.publish(EventType.METRIC_TURN, self._payload(at, status), turn=turn_index)

    # ── payload ─────────────────────────────────────────────────────────────

    @staticmethod
    def _payload(at: dict[str, float], status: str) -> dict:
        """The `metric.turn` contract (ADR-0038 TP5).

        Absolute instants are the evidence; derived durations are the consumption. **An
        unreached boundary is an absent key, never `0`** — a fabricated zero is indistinguishable
        from a real zero-millisecond stage.

        Identity is deliberately **not** here: `session_id`, `org_id`, `conversation_id` and
        `turn` are stamped on the envelope by `EventPublisher`, and a second copy in the payload
        is a second thing that can disagree (ED3).
        """
        ms: dict[str, int] = {}
        for stage, start, end in STAGE_INTERVALS + ADDITIVE_INTERVALS:
            if start in at and end in at:
                value = _ms(at[start], at[end])
                if value is None:
                    log.warning("negative %s interval dropped (%s → %s)", stage, start, end)
                else:
                    ms[stage] = value

        voice_to_voice: int | None = None
        if "speech_end" in at and "first_audio_out" in at:
            voice_to_voice = _ms(at["speech_end"], at["first_audio_out"])

        return {
            "status": status,
            "at": dict(at),
            "ms": ms,
            "voice_to_voice_ms": voice_to_voice,
        }

    # ── introspection (tests and operators; no behaviour) ───────────────────

    @property
    def in_flight(self) -> int:
        """How many turns currently hold state. Bounded by in-flight turns, never by session
        lifetime (TP8) — this is the number that must not grow across a conversation."""
        return len(self._turns)
