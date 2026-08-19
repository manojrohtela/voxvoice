"""Storage ports for the durable event spine (M10.2, ADR-0034).

The seam that lets `events/` acquire durable persistence **without importing a storage
library and without importing another plane** (ADR-0034 DS3/DS4, dependency direction
§9). This module declares *what* durable storage must do; `events/persistence/` declares
*how*, and a composition root binds the two.

That inversion is the whole point: `events/bus.py` and every other module here keep the
`core` + stdlib budget, and the only place SQL exists is the one sub-package whose job is
storage — the ADR-0031 IR1a pattern, generalized (ADR-0034 DS2).

**Scope at M10.2.** The durable append-only log and its ordered reads, and nothing else.
There is deliberately no delivery state, no outbox claim, no retry bookkeeping and no
retention here: the outbox is M10.6, the retry ladder and DLQ are M10.7, and retention is
M10.5. A port that grew those methods before the milestones that define them would be
speculative architecture, and every implementation would have to fake them.

**Added at M10.6 (ADR-0035).** `DurableOutbox` — the *mutable delivery state* seam — plus
`OutboxState`, `OutboxEntry` and `DurableWriteFailureHook`. They are a **separate** port
because `DurableEventLog` is append-only by contract (**ED5**) and delivery state is by
definition mutable; putting a `mark_delivered` on the log would give the immutable history
an update path. The two ports are implemented together over one transaction, but they are
not one contract.

**Added at M10.7 (ADR-0036).** The rest of the lifecycle — `RETRYING`, `DEAD_LETTERED`,
`QUARANTINED` (added **additively**, AR-32), the `OUTBOX_LIFECYCLE` state machine declared
with `core.fsm`, `DeliveryOutcome` + `DeliveryClassifier`, the `DeliveryTransport` contract,
and `OutboxFailure` (the append-only evidence shape).

Still deliberately absent: **claim/lease and multi-consumer state**, which no milestone in
Epic 10 introduces (ADR-0035 DW8, DL-0017), and — critically — **any driver**. M10.7 decides
and records; **recovery, restart re-dispatch and the worker/scheduler that performs them are
M10.8** (ADR-0035 DW7, reaffirmed by ADR-0036 RL1). Nothing here polls, sleeps, loops or
dispatches, and `DeliveryTransport` ships **without an implementation** for exactly that reason.

Terminology follows ADR-0032 ED11: the noun **"replay" is reserved** for `platform/replay/`
(the in-memory, visualization-only M4.3 subsystem), and **"event store"** is already a
Billing-internal noun (`SqlBillingEventStore`). This is the **durable event log**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.events import Event
from core.fsm import StateMachineDefinition, define


class DurableEventLogError(Exception):
    """Base for durable-log failures. Never raised into a producer (AR-31)."""


# A durable write that failed, handed to the operator seam. The bus catches the exception,
# declines to dispatch the event, and calls this **exactly once** — it never raises into the
# producer (`core.events.EventBus.publish`: "Must not raise into the producer"; ADR-0032
# ED10; AR-31). ADR-0035 DW4.
DurableWriteFailureHook = Callable[[Event, Exception], None]


class DurableEventLog(ABC):
    """An append-only, restart-safe, ordered record of `core.events.Event` envelopes.

    Three properties are contractual and every implementation is asserted against them:

    * **Append-only.** There is no update method and no delete method. A recorded event is
      a historical fact (ADR-0032 ED5). Retention (ED9) is the sole future exception and
      arrives at M10.5 with the guarantees that make it safe — it is not a delete path.
    * **The envelope is preserved verbatim.** Identity is `Event.id` and the ordering
      authority is the bus-assigned `Event.seq`; the log persists both unchanged and
      invents neither (ADR-0032 ED3). A round-tripped event equals the event appended.
    * **Idempotent on identity.** Appending an event whose `id` is already recorded is a
      no-op, not an error. Delivery is at-least-once (ADR-0032 ED2), so a redelivered
      append is ordinary traffic — an implementation that raised would turn the expected
      case into an incident.
    """

    @abstractmethod
    def append(self, event: Event) -> bool:
        """Durably record one event. Returns `True` if newly recorded, `False` if an event
        with this `id` was already present (idempotent — see the class contract)."""

    @abstractmethod
    def append_many(self, events: Iterable[Event]) -> int:
        """Durably record many events in **one transaction**. Returns the number newly
        recorded. All-or-nothing: a failure records none of them (ADR-0032 ED6)."""

    @abstractmethod
    def read_session(self, session_id: str) -> Sequence[Event]:
        """Every recorded event for one session, ordered by `seq` ascending
        (ADR-0032 ED4). Deterministic across calls and across restarts."""

    @abstractmethod
    def read_all(self) -> Sequence[Event]:
        """Every recorded event, in a deterministic total order (`seq`, then `id` as a
        stable tie-break). For diagnostics and durability verification."""

    @abstractmethod
    def count(self) -> int:
        """How many events are durably recorded."""


class DeliveryOutcome(StrEnum):
    """What one delivery attempt did (M10.7, ADR-0036 RL5).

    Three members, because the ladder has exactly three answers. The *classification* — which
    exception means which outcome — is **not** decided here: it arrives through an injected
    classifier, which is the shape every retry precedent in this repository already uses
    (`RetrySpec.retryable_only`, `RetryController(retryable=...)`, `_RECOVERABLE`).

    Deliberately absent: any transport-specific exception hierarchy, any HTTP status mapping, and
    any built-in rule for authentication or configuration failures. Those are genuinely ambiguous
    (an expired token is transient, a revoked key is permanent) and the caller is the only party
    that knows which it has. A ladder that guessed would be wrong silently.
    """

    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"


#: Turns a delivery exception into a `DeliveryOutcome`. Supplied by the caller (ADR-0036 RL5);
#: `events/` never inspects a transport exception type.
DeliveryClassifier = Callable[[Exception], DeliveryOutcome]


class DeliveryTransport(ABC):
    """Where a durable outbox entry would actually be delivered (M10.7, ADR-0036 RL9).

    **A contract only. M10.7 ships no implementation.** That is deliberate: the moment `events/`
    contains a concrete transport it acquires a delivery mechanism, and a delivery mechanism that
    runs needs something to run it — which is the recovery driver M10.8 owns (RL1). Declaring the
    seam without filling it is what keeps that boundary honest.

    `events/webhooks.py` is **not** an implementation of this port and is not modified by M10.7: it
    is best-effort by design, returns no outcome to branch on, and is async against a synchronous
    persistence layer. Wrapping it would create the second competing delivery mechanism the Epic 10
    blueprint (risk R-11) exists to prevent.
    """

    @abstractmethod
    def deliver(self, event: Event) -> None:
        """Attempt delivery once. Raises on failure; the caller's `DeliveryClassifier` decides
        whether that failure is transient or permanent. Never retries internally — retry is the
        ladder's decision, and a transport that retried privately would make the persisted attempt
        count a lie."""


class OutboxState(StrEnum):
    """The outbox delivery lifecycle (M10.6 + M10.7, ADR-0035 DW6 / ADR-0036 RL2).

    M10.6 defined `PENDING` and `DELIVERED`; **M10.7 added `RETRYING`, `DEAD_LETTERED` and
    `QUARANTINED` additively** (AR-32), exactly as M10.6 anticipated — the backing column is
    a plain string, so not one M10.6 row had to be rewritten or migrated to admit them.

    `DELIVERED`, `DEAD_LETTERED` and `QUARANTINED` are **terminal**. Terminality is not
    declared here: `OUTBOX_LIFECYCLE` derives it from having no outgoing edge, so the states
    and the machine cannot disagree.
    """

    PENDING = "pending"  # written, never yet attempted
    RETRYING = "retrying"  # M10.7 — a transient failure; inside the bounded ladder
    DELIVERED = "delivered"  # terminal
    DEAD_LETTERED = "dead_lettered"  # M10.7 — terminal; the ladder was exhausted (ED8)
    QUARANTINED = "quarantined"  # M10.7 — terminal; deterministically futile (ED8)


class OutboxTrigger(StrEnum):
    """What happened to an entry. One trigger per thing that can actually happen — there is no
    trigger for "an operator changed their mind", because M10.7 has no such transition."""

    DELIVER = "deliver"
    FAIL_TRANSIENT = "fail_transient"
    FAIL_PERMANENT = "fail_permanent"
    EXHAUST = "exhaust"


#: The outbox lifecycle (ADR-0036 RL2), declared with the repository's own FSM rather than a
#: hand-rolled one, so terminality, duplicate-edge rejection and loud refusals come from
#: `core.fsm` instead of from this module's good intentions.
#:
#: **What it refuses is the specification** — the rule the Collections lifecycle states and this
#: one follows:
#:
#:   * **no `RETRYING → PENDING`.** `PENDING` means *never attempted*. An edge back would erase
#:     the attempt history and let a bounded ladder restart forever, which is exactly what ED7
#:     forbids;
#:   * **no edge out of `DELIVERED`.** A delivered entry cannot be reopened by a stale caller or
#:     a redelivered event;
#:   * **no edge out of `DEAD_LETTERED`.** Operator redrive is deliberately not in M10.7
#:     (ADR-0036 RL3). Re-opening a closed entry is a new decision, not a rewrite of the old one;
#:   * **no edge out of `QUARANTINED`**, and no operator-driven quarantine (RL4).
#:
#: `PENDING --fail_permanent--> QUARANTINED` exists so a deterministically-doomed entry reaches
#: quarantine **without entering the ladder** — ED8's "quarantine does not consume the retry
#: ladder", expressed as structure rather than as a comment.
OUTBOX_LIFECYCLE: StateMachineDefinition = define(
    [
        (OutboxState.PENDING, OutboxTrigger.DELIVER, OutboxState.DELIVERED),
        (OutboxState.PENDING, OutboxTrigger.FAIL_TRANSIENT, OutboxState.RETRYING),
        (OutboxState.PENDING, OutboxTrigger.FAIL_PERMANENT, OutboxState.QUARANTINED),
        (OutboxState.RETRYING, OutboxTrigger.DELIVER, OutboxState.DELIVERED),
        (OutboxState.RETRYING, OutboxTrigger.FAIL_TRANSIENT, OutboxState.RETRYING),
        (OutboxState.RETRYING, OutboxTrigger.EXHAUST, OutboxState.DEAD_LETTERED),
        (OutboxState.RETRYING, OutboxTrigger.FAIL_PERMANENT, OutboxState.QUARANTINED),
    ]
)

#: Derived from the machine, never hand-listed, so the two cannot drift apart.
TERMINAL_OUTBOX_STATES: frozenset[OutboxState] = OUTBOX_LIFECYCLE.terminal_states()


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One event's durable delivery state — the read shape of the outbox.

    `event_id` is `Event.id`: the outbox introduces **no second event identity** and **no
    second sequence authority** (ADR-0032 ED3). `seq` is the bus-assigned value copied
    verbatim so pending entries have a deterministic order without a join; it is never
    assigned, re-assigned or derived here.
    """

    event_id: str
    seq: int
    state: OutboxState
    created_at: float
    delivered_at: float | None = None
    # ── M10.7 (ADR-0036 RL8/RL11) ────────────────────────────────────────────
    # `attempt_count` is 1-based and incremented when an attempt *begins*, so a crash
    # mid-attempt cannot under-count and hand out a free retry. `next_attempt_at` is an
    # absolute instant, never a delay recomputed after restart — a restart must not silently
    # reset the ladder. Two terminal timestamps, not one: "we tried and gave up" and "this can
    # never succeed" are different operational facts (ED8) and a shared column would make an
    # operator re-derive which happened. There is deliberately no `last_error` — the append-only
    # failure history is the authoritative evidence.
    attempt_count: int = 0
    next_attempt_at: float | None = None
    dead_lettered_at: float | None = None
    quarantined_at: float | None = None
    quarantine_reason: str = ""

    @property
    def is_pending(self) -> bool:
        return self.state is OutboxState.PENDING

    @property
    def is_terminal(self) -> bool:
        """Whether automation is finished with this entry. Derived from the machine, so it
        cannot drift from `OUTBOX_LIFECYCLE`."""
        return self.state in TERMINAL_OUTBOX_STATES


@dataclass(frozen=True, slots=True)
class OutboxFailure:
    """One recorded failed delivery attempt — the read shape of the append-only history.

    `attempt` is the 1-based attempt this failure belongs to. A **permanent** failure records
    a row at the current `attempt_count` without consuming the ladder (ED8), so the attempt
    number on a quarantine row is the last attempt actually made.
    """

    event_id: str
    attempt: int
    outcome: DeliveryOutcome
    occurred_at: float
    error: str = ""
    id: int | None = None


class DurableOutbox(ABC):
    """Mutable per-event delivery state, one row per event, beside the immutable log.

    **In-process synchronous subscribers are not outbox destinations** (ADR-0035 DW5). They
    remain the M4.1 run-to-completion fan-out and are unchanged. The outbox exists for
    durable/asynchronous consumers — the webhook path (M10.7) and the metric buffer (M10.10)
    — neither of which exists yet. M10.6 therefore delivers the *state transition*, not a
    caller for it, exactly as it delivers the bus seam without wiring the runtime (DW10).

    There is no `create` method: an entry is created **in the same transaction as the
    durable event append** (ADR-0035 DW1/DW5), so it cannot be created independently and
    cannot drift from the log. There is no delete method: retention is M10.5 (ED9).
    """

    @abstractmethod
    def mark_delivered(self, event_id: str) -> bool:
        """Move one entry `PENDING → DELIVERED`. Returns `True` if this call performed the
        transition, `False` if the entry is unknown or already `DELIVERED` — idempotent,
        because at-least-once delivery (ED2) makes a repeated mark ordinary traffic."""

    @abstractmethod
    def get(self, event_id: str) -> OutboxEntry | None:
        """One entry by `Event.id`, or `None`."""

    @abstractmethod
    def read_pending(self) -> Sequence[OutboxEntry]:
        """Every entry still `PENDING`, ordered by `seq` then `event_id` — deterministic
        across calls and across restarts (ED4). This is the durable `un-drained` predicate
        **ED9** requires before retention (M10.5) can run, and the input M10.8 recovery will
        read. **M10.6 neither re-dispatches these nor runs any worker over them** (DW7)."""

    @abstractmethod
    def count_entries(self) -> int:
        """How many outbox entries exist.

        Spelled `count_entries`, not `count`, because `DurableEventLog.count()` already
        means *events* and an implementation may hold both ports (they are written in one
        transaction). One method name with two meanings is how a caller ends up counting the
        wrong thing and never finding out."""

    @abstractmethod
    def count_pending(self) -> int:
        """How many entries are still `PENDING`."""

    # ── M10.7 (ADR-0036) — record a decision the ladder already made ─────────
    #
    # Each method persists **one transition and its evidence in one transaction** (RL13). None
    # of them delivers anything, chooses when to run, or waits: the caller has already attempted
    # delivery and already classified the outcome. That separation is what stops this port from
    # becoming the M10.8 recovery driver.

    @abstractmethod
    def record_failure(
        self,
        event_id: str,
        outcome: DeliveryOutcome,
        *,
        error: str = "",
        policy: object | None = None,
    ) -> OutboxEntry | None:
        """Record one failed attempt and apply the resulting transition, atomically.

        A **transient** failure moves the entry into `RETRYING` with the next backoff instant,
        or to `DEAD_LETTERED` when the ladder is exhausted (ED7). A **permanent** failure moves
        it straight to `QUARANTINED` and **does not consume an attempt** (ED8). Either way one
        `evt_outbox_failure` row is appended in the same transaction, because a `RETRYING` entry
        whose evidence is missing is the disagreement ED6 exists to forbid.

        Returns the updated entry, or `None` if the entry is unknown. Raises on an illegal
        transition — a terminal entry does not silently absorb another failure."""

    @abstractmethod
    def begin_attempt(self, event_id: str) -> OutboxEntry | None:
        """Increment `attempt_count` because an attempt is **starting**, before its outcome is
        known (ADR-0036 RL8). Counting afterwards would let a crash mid-attempt hand out a free
        retry, and a bound that a crash can widen is not a bound."""

    @abstractmethod
    def failures(self, event_id: str) -> Sequence[OutboxFailure]:
        """Every recorded failed attempt for one event, ordered by `attempt`. Append-only:
        there is no update and no delete, because ED8 requires a dead-lettered entry keep its
        failure history and history that can be edited is not evidence."""

    @abstractmethod
    def due(self, now: float) -> Sequence[OutboxEntry]:
        """Entries whose `next_attempt_at` has arrived, ordered deterministically.

        **A query, not an action.** M10.7 never calls it on a timer, never loops over it and
        never delivers anything it returns — that is the recovery driver, and the driver is
        **M10.8** (ADR-0035 DW7 / ADR-0036 RL1)."""
