"""Degradation recording — making **AR-24** true rather than merely claimed (M11.4).

AR-24: *"Degradations are declared, evented, and recorded in session metadata — never silent."*

Until now that rule was asserted in prose and implemented nowhere. `core/providers/stt.py`'s
docstring has claimed since M2C.2 that *"the runtime adapts and records the degradation (AR-24)"*
while no code did so. This module is the smallest thing that makes the sentence true.

Three obligations, and all three matter:

* **declared** — a degradation names its capability, its kind and its cause, so it can be read
  rather than inferred from a log line;
* **evented** — it goes onto the existing in-process event seam, which the gateway and replay
  already observe for free. **No second telemetry infrastructure**: the Epic 10 event spine stays
  unwired until M11.12 (ADR-0050 SW1), and this module neither anticipates nor duplicates it;
* **recorded** — it is retained on the session so a later reader can ask *"what did this call
  degrade to?"* after the fact.

Bounded by construction. A degrading session is exactly the session most likely to degrade
repeatedly, so an unbounded record would turn a provider outage into a memory leak — the same
reasoning ADR-0039 MB3 applied to the metric buffer.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger("voxvoice.runtime.faults")

#: Retained degradations per session. Generous enough to hold a realistic outage, bounded so a
#: pathological one cannot grow without limit.
DEGRADATION_HISTORY = 64


class DegradationKind(StrEnum):
    """Why a capability is not operating at its declared best.

    Closed vocabulary, and small on purpose: a kind the runtime cannot behave differently about
    is a log message, not a degradation."""

    #: The provider truthfully declares it cannot stream (§A6.4, ADR-0005). Not a failure —
    #: a *declared* capability limit the runtime adapts to.
    NOT_STREAMING = "not_streaming"
    #: The provider could not be reached or dropped mid-operation.
    UNAVAILABLE = "unavailable"
    #: A call exceeded its deadline.
    TIMEOUT = "timeout"
    #: The provider ended a stream before its natural end.
    DISCONNECTED = "disconnected"
    #: Configuration asked for something the provider does not support.
    CAPABILITY_MISMATCH = "capability_mismatch"
    #: An auxiliary capability failed and the conversation continued without it (DL-0013).
    AUXILIARY_DISABLED = "auxiliary_disabled"
    #: The chain moved to another candidate.
    FAILED_OVER = "failed_over"
    #: Every candidate was exhausted.
    EXHAUSTED = "exhausted"
    #: A provider's bounded buffer overflowed and it discarded audio rather than grow (M11.7,
    #: AR-40). Not a failure — the bound working as designed — but never silent.
    AUDIO_DROPPED = "audio_dropped"


@dataclass(frozen=True, slots=True)
class Degradation:
    """One declared degradation. Data only — a value a test can assert on."""

    capability: str
    kind: DegradationKind
    detail: str = ""
    provider: str = ""
    #: What the runtime did instead. Empty means "nothing; the capability is simply reduced".
    fallback: str = ""

    def as_payload(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "kind": self.kind.value,
            "detail": self.detail,
            "provider": self.provider,
            "fallback": self.fallback,
        }


#: The event sink a composition root supplies. Declared structurally so `runtime/faults/` needs no
#: import of `events/` — the same reason `runtime.contracts.LatencyObserver` is a Protocol.
DegradationSink = Callable[[Degradation], None]


@dataclass
class DegradationRecorder:
    """Per-session degradation record: declared, evented, retained, bounded.

    ## A degradation is an **episode**, not an event (M11.15, R-48)

    M11.14 measured what a sustained outage actually costs: a VAD that cannot answer degrades
    **once per inbound frame**, which at the 20 ms media cadence is ~50 publications per second
    per session, for as long as the outage lasts. The retained history was bounded, so memory was
    safe — but the *telling* was not, and a 64-entry window rolled over in about 1.3 seconds,
    destroying the record of everything else that happened during the incident.

    The fix is the semantic this repository already uses for exactly this shape of problem:
    `InterruptPolicy.duplicate_suppression` — *"collapse repeat confirms within one assistant
    utterance"*. Repeats inside an episode produce no transition and therefore no event; a genuine
    new episode does. Nothing here is a rate limit, a time window or a sampling ratio: there is no
    number to tune, which is the point (§5 forbids inventing one).

    An episode is keyed by **`(capability, kind, provider)`** — the same capability failing the
    same way with the same provider. `detail` is deliberately excluded: it carries the vendor's
    message, which can differ on every frame, and keying on it would defeat the coalescing for
    precisely the noisy failures that motivated it. The first occurrence's detail is what is
    retained, because it is the one nobody has seen yet.

    What is preserved, and asserted by test:

    * **first failure is always visible** — published and retained, never delayed or sampled;
    * **counts remain exact and un-suppressed**, so a long outage is still measurable;
    * **the episode stays queryable** while active (`is_active`), so suppression never looks like
      recovery;
    * **no recovery is invented** — `resolved()` only ends suppression; it publishes nothing and
      claims nothing (§5: *no false recovery*);
    * **a new failure after recovery is visible again**, which is the mistake this design is most
      likely to make and the one most worth testing.
    """

    session_id: str
    sink: DegradationSink | None = None
    _history: deque[Degradation] = field(default_factory=lambda: deque(maxlen=DEGRADATION_HISTORY))
    _counts: dict[str, int] = field(default_factory=dict)
    #: Episodes currently open, keyed by `(capability, kind, provider)` → occurrences so far.
    #: Presence means "this degradation is still happening", which is a different question from
    #: "how many times has it happened" and is why it is not derived from `_counts`.
    _active: dict[tuple[str, str, str], int] = field(default_factory=dict)
    #: Episodes coalesced away, for the operator who asks *"how much did I not get told?"*.
    _suppressed: int = 0

    def record(self, degradation: Degradation) -> Degradation:
        """Declare one degradation. Never raises — recording a degradation must not itself
        become a second failure (DL-0013).

        Returns the degradation either way. A suppressed repeat is still a real, counted
        occurrence; the caller is told about it, the *bus* is not."""
        key = (degradation.capability, degradation.kind.value, degradation.provider)
        count_key = f"{degradation.capability}:{degradation.kind.value}"
        # Counted before any suppression decision, so coalescing can never make an outage look
        # smaller than it was.
        self._counts[count_key] = self._counts.get(count_key, 0) + 1
        if key in self._active:
            self._active[key] += 1
            self._suppressed += 1
            return degradation
        self._active[key] = 1
        self._history.append(degradation)
        log.info(
            "session %s degraded: %s/%s (%s)",
            self.session_id, degradation.capability, degradation.kind.value, degradation.detail,
        )
        if self.sink is not None:
            try:
                self.sink(degradation)
            except Exception as exc:  # noqa: BLE001 — a sink must never break the conversation
                log.warning("degradation sink raised (ignored): %s", exc)
        return degradation

    def resolved(self, capability: str, provider: str | None = None) -> None:
        """The capability worked, so any open episode for it ends.

        **This publishes nothing and asserts nothing.** It does not mean the provider is healthy,
        only that one operation succeeded — so inventing a `recovered` event here would be the
        false recovery §5 forbids. All it does is re-arm reporting, so the *next* failure is told
        rather than coalesced into an episode that is no longer current.

        `provider=None` ends every open episode for the capability, which is what a caller that
        does not know which candidate succeeded should ask for."""
        for key in [
            k for k in self._active
            if k[0] == capability and (provider is None or k[2] == provider)
        ]:
            del self._active[key]

    def is_active(self, capability: str, kind: DegradationKind | None = None) -> bool:
        """Whether a degradation is **still happening** — not merely whether one ever did.

        The question suppression would otherwise make unanswerable, and the reason
        `degraded()` (which reads history) is not enough on its own."""
        return any(
            k[0] == capability and (kind is None or k[1] == kind.value) for k in self._active
        )

    @property
    def suppressed(self) -> int:
        """Repeat occurrences coalesced into an open episode, across the session."""
        return self._suppressed

    @property
    def active_episodes(self) -> dict[tuple[str, str, str], int]:
        """Open episodes and their occurrence counts. Read-only snapshot."""
        return dict(self._active)

    @property
    def history(self) -> tuple[Degradation, ...]:
        """Everything retained, oldest first."""
        return tuple(self._history)

    @property
    def counts(self) -> dict[str, int]:
        """Total occurrences per `capability:kind`, **un-truncated** — the history is bounded but
        the count is not, so a long outage is still visible after the oldest entries roll off."""
        return dict(self._counts)

    def degraded(self, capability: str) -> bool:
        return any(d.capability == capability for d in self._history)

    def as_session_metadata(self) -> dict[str, object]:
        """The AR-24 *"recorded in session metadata"* projection."""
        return {
            "degradations": [d.as_payload() for d in self._history],
            "degradation_counts": self.counts,
        }


__all__ = [
    "DEGRADATION_HISTORY",
    "Degradation",
    "DegradationKind",
    "DegradationRecorder",
    "DegradationSink",
]
