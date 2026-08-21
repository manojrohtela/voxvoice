"""`OutboxRecovery` (M10.8, ADR-0037) — restart recovery and re-dispatch, a caller-driven sweep.

M10.6 made events durable and M10.7 made failed deliveries decidable, but nothing in the
repository actually *delivered* an outbox entry: the spine records transitions a caller already
decided, the ladder decides transitions nobody applies, and `DeliveryTransport` ships without an
implementation. Every verb existed and no sentence called them. **This module is that sentence**
(ADR-0035 DW7, ADR-0036 RL1, ADR-0037 RC1).

**Caller-driven, not scheduled** (RC2). `recover()` is the shape this repository already uses
twice for a periodic sweep over persisted state — `context.ContextRuntime.expire` and
`memory.MemoryRuntime.expire`, both of which say it in their own words: *"Deterministic — no
background timer; the caller (or a scheduler) invokes it. Returns the count expired."* The
scheduler lives in the caller there, and it lives in the caller here. **Nothing in this module
sleeps, loops, polls, schedules, threads, starts a task or sets a timer**, and that is not an
omission to be filled in later — *when* recovery runs is a deployment concern, not an events one.

**It is a driver, not a persistence layer** (RC10). It holds no session, no engine and no storage
import; it calls the M10.7 verbs, each of which owns its own transaction over the existing
`EventDatabase.session`. `begin_attempt`, `mark_delivered` and `record_failure` keep their
atomicity guarantees exactly — a transition and its failure evidence still commit together, and
**no `UnitOfWork` and no second session** appears. Partial progress across a sweep is correct:
entries are independent, so a sweep interrupted after three of ten has durably completed three.

**The transport stays a port** (RC9). This module calls `deliver()` and never learns whether
delivery is HTTP, a webhook, a broker or a queue. No concrete transport ships in production code,
so `events/webhooks.py` stays byte-for-byte unchanged and no second delivery mechanism appears
(blueprint R-11).

**What it refuses.** No new FSM transition — the seven ratified edges are exactly sufficient
(RC6). Terminal entries are never selected, never attempted and never written (RC7); there is no
redrive out of `DEAD_LETTERED` and no operator quarantine (RC8, RL3/RL4). No claim, lease, lock,
`FOR UPDATE` or dedup: two concurrent sweeps may deliver the same event, which is **correct**
under at-least-once (ED2) and idempotent consumers (ED12), and is recorded as a non-claim rather
than fixed (RC11). No migration, no schema change, no retention, no metrics, no runtime wiring
(RC15/RC16).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from core.events import Event

from .ports import (
    DeliveryClassifier,
    DeliveryOutcome,
    DeliveryTransport,
    DurableEventLog,
    DurableOutbox,
    OutboxEntry,
)


def default_classifier(exc: Exception) -> DeliveryOutcome:
    """Treat an unrecognized delivery failure as **transient**.

    The conservative direction, and the only one available without a transport taxonomy: a
    transient misclassification costs bounded retries and ends at `DEAD_LETTERED` (ED7), while a
    permanent misclassification quarantines a deliverable event immediately and permanently,
    since `QUARANTINED` is terminal with no redrive (RL3/RL4).

    This is a **default, not a policy**. ADR-0036 RL5 puts classification in the caller's hands
    precisely because only the caller knows whether its expired token is transient or its revoked
    key is permanent; a module that guessed would be wrong silently.
    """
    return DeliveryOutcome.TRANSIENT_FAILURE


class OutboxRecovery:
    """Re-dispatches durable outbox entries that have not yet reached a terminal state.

    Everything is injected — the two ports, the transport, the classifier and the clock
    (ADR-0034 DS11) — so this class performs no I/O of its own, constructs nothing, and can be
    exercised entirely with fakes and a frozen clock.
    """

    def __init__(
        self,
        outbox: DurableOutbox,
        log: DurableEventLog,
        transport: DeliveryTransport,
        classifier: DeliveryClassifier | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._outbox = outbox
        self._log = log
        self._transport = transport
        self._classifier = classifier or default_classifier
        # No `time.time()` fallback: RL14 keeps wall-clock reads off the transition path, and a
        # caller may always pass `now=` explicitly. Recovery with neither is a caller error.
        self._clock = clock

    # ── selection ───────────────────────────────────────────────────────────

    def candidates(self, now: float, *, limit: int | None = None) -> Sequence[OutboxEntry]:
        """Everything eligible for a delivery attempt at `now`, in `(seq, event_id)` order.

        **A query, and safe to call alone** — it attempts nothing and writes nothing, which is
        what makes the selection rule independently testable (RC3/RC4).

        Two sources, one sweep. `PENDING` entries are eligible **immediately**: they have never
        been attempted, so no backoff has been earned and due-time does not apply to them.
        `RETRYING` entries are eligible **only when due** — `due(now)` is inclusive of `now` and
        excludes future-dated rows, which therefore stay entirely untouched.

        Terminal entries cannot appear: `read_pending()` filters `PENDING`, `due()` filters
        `RETRYING`, and neither can return `DELIVERED`, `DEAD_LETTERED` or `QUARANTINED`.

        The merged set is re-sorted rather than concatenated. Each source is already ordered, but
        `pending + due` would only be ordered *within* each half — and an order that holds
        per-half is not an order, because it would make `limit` select a different set than the
        same sweep without one.
        """
        merged = tuple(self._outbox.read_pending()) + tuple(self._outbox.due(now))
        ordered = sorted(merged, key=lambda e: (e.seq, e.event_id))
        return ordered if limit is None else ordered[:limit]

    # ── the sweep ───────────────────────────────────────────────────────────

    def recover(self, *, now: float | None = None, limit: int | None = None) -> int:
        """Attempt delivery of every eligible entry. Returns the number of attempts **made**.

        Deterministic and caller-driven — no background timer; the caller (or a scheduler)
        invokes it (RC2, on the `context`/`memory` `expire()` precedent).

        `limit=None` processes every eligible candidate; a supplied `limit` processes at most
        that many, in the same deterministic order. There is **no hidden default** (RC12) — an
        unbounded sweep is a choice the call site makes visibly, in the spirit of ED7's
        prohibition on retry constants buried in call sites.

        The count is *attempts*, not successes: a sweep that tried five entries and delivered two
        returns 5. Counting successes would make a fully-failing sweep indistinguishable from an
        empty one, which is precisely the case an operator needs to see.
        """
        if limit is not None and limit <= 0:
            # Loud, not coerced. `limit=0` most likely means a caller computed a batch size
            # wrongly; returning 0 silently would look like "nothing was due" and hide it.
            raise ValueError(f"limit must be a positive integer or None, got {limit!r}")
        now = self._resolve_now(now)

        selected = self.candidates(now, limit=limit)
        if not selected:
            return 0

        # One log read per sweep, not one per entry. `DurableEventLog` has no read-by-id — it was
        # built for ordered history, not point lookups — so a per-entry read would be
        # O(entries x events). Indexing once makes the sweep O(events + entries).
        #
        # **Recorded honestly:** this still materializes the whole log per sweep, which is fine at
        # Epic 10's scale and is not fine forever. Closing it properly means a read-by-id on the
        # port — a change to the M10.7 seam that M10.8 is not authorized to make.
        envelopes = {event.id: event for event in self._log.read_all()}

        attempted = 0
        for entry in selected:
            self._attempt(entry, envelopes)
            attempted += 1
        return attempted

    # ── one attempt ─────────────────────────────────────────────────────────

    def _attempt(self, entry: OutboxEntry, envelopes: dict[str, Event]) -> None:
        """One entry: count the attempt, deliver, record the outcome.

        The order is the contract (RL8, RC5). `begin_attempt` runs **before** `deliver`, while
        the outcome is still unknown, so a crash mid-attempt costs an attempt rather than
        granting a free one — a bound a crash can widen is not a bound.
        """
        event = self._envelope(entry, envelopes)
        self._outbox.begin_attempt(entry.event_id)
        try:
            self._transport.deliver(event)
        except Exception as exc:  # noqa: BLE001 — the classifier is the caller's taxonomy (RL5)
            self._outbox.record_failure(entry.event_id, self._classifier(exc), error=repr(exc))
            return
        self._outbox.mark_delivered(entry.event_id)

    @staticmethod
    def _envelope(entry: OutboxEntry, envelopes: dict[str, Event]) -> Event:
        """The event this entry announces, read back from the immutable log.

        Recovery *must* read `evt_event`: the outbox holds delivery state, never a payload (DW5),
        so the envelope has exactly one source and it is the append-only one. Reads go through
        the `DurableEventLog` port — whose implementation reconstructs the envelope with the
        existing `events.persistence.mapping.to_event` under its round-trip contract — so this
        module acquires no storage import, no session and no second mapping.

        **Retention does not create a missing envelope here** (M10.5, ADR-0041). Retention may
        delete an `evt_event` row while keeping its `evt_outbox` row, so the two are no longer
        strictly co-resident for all time — but it deletes only events whose entry is already
        `DELIVERED`, and `DELIVERED` is terminal, so such an entry is never selected by
        `candidates()` and never reaches this method. The lookup failure therefore still means
        what it always meant: something wrote an outbox entry without its event.
        """
        event = envelopes.get(entry.event_id)
        if event is not None:
            return event
        raise LookupError(
            f"outbox entry {entry.event_id!r} has no event in the durable log; "
            "the two are written in one transaction (ADR-0035 DW1), and retention (M10.5, "
            "ADR-0041) deletes only events whose entry is already DELIVERED — which this "
            "sweep never selects, so neither path explains a missing envelope here"
        )

    def _resolve_now(self, now: float | None) -> float:
        if now is not None:
            return now
        if self._clock is None:
            raise ValueError("no clock injected: pass now=... or construct with clock=...")
        return self._clock()
