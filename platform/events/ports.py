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

Terminology follows ADR-0032 ED11: the noun **"replay" is reserved** for `platform/replay/`
(the in-memory, visualization-only M4.3 subsystem), and **"event store"** is already a
Billing-internal noun (`SqlBillingEventStore`). This is the **durable event log**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from core.events import Event


class DurableEventLogError(Exception):
    """Base for durable-log failures. Never raised into a producer (AR-31)."""


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
