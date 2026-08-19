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

Still deliberately absent at M10.6: retry, backoff, attempt counters, dead-letter,
quarantine, failure history, claim/lease and multi-consumer state. Those are **M10.7**
(ADR-0035 DW6), and recovery/re-dispatch of pending entries is **M10.8** (DW7).

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


class OutboxState(StrEnum):
    """The delivery states **M10.6 owns**, and only those (ADR-0035 DW6).

    `RETRYING`, `DEAD_LETTERED` and `QUARANTINED` belong to **M10.7** and are deliberately
    absent: a state an implementation cannot yet reach is a promise it cannot keep. The
    column behind this is a plain string precisely so M10.7 can add members **additively**
    (AR-32) without migrating or rewriting a single M10.6 row.
    """

    PENDING = "pending"
    DELIVERED = "delivered"


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

    @property
    def is_pending(self) -> bool:
        return self.state is OutboxState.PENDING


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
