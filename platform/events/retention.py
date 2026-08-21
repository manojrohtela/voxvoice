"""`EventRetention` (M10.5, ADR-0041) — the sanctioned deletion path, and its five guards.

ADR-0032 **ED9** always reserved one exception to the append-only durable log, and **EV4**
states its exact width: *"no update or delete path exists on the durable event log **outside
the retention policy**"*. This module is that policy. It is the only place in the repository
authorized to cause an `evt_event` row to disappear, and the only reason it is safe is that it
refuses far more often than it acts.

**The five guards (RT7).** An event is deleted **if and only if** all of these hold:

  1. an explicit `RetentionPolicy` is configured — an unconfigured deployment deletes nothing;
  2. its `recorded_at` falls inside a **closed** `RetentionWindow`, computed from an injected
     clock (**DS11**) — never "everything older than now";
  3. its `evt_outbox` row exists and is `DELIVERED` — the durable drained predicate (**RT1**);
  4. a supplied `MeteredWatermark` attestation covers it (**RT6**);
  5. **default-deny** (**RT8**) — anything absent, unreadable or raising means *keep*.

**Why `DELIVERED` and nothing else (RT3).** `PENDING` and `RETRYING` are undelivered by
definition. `DEAD_LETTERED` and `QUARANTINED` are terminal but they mean delivery **failed** —
deleting them would destroy the operator's evidence at the exact moment it is needed, so
"terminal" is emphatically not "drained". `MetricTurnBuffer` is never consulted (**RT4**): its
`drain` is destructive and its buffer is bounded and in-memory (ADR-0039 MB3/MB5), so it cannot
answer a durable question about the past.

**Caller-driven, never scheduled** (RT9, on the ADR-0037 RC2 precedent that `context` and
`memory` set for their TTL sweeps). Nothing here sleeps, loops, polls, threads or sets a timer;
*when* retention runs is a deployment concern. M10.5 is hermetic and wires nothing.

**No Billing, ever.** The metered guard arrives as an injected attestation. This module imports
no `billing`, no `api`, no `runtime`, reaches no repository or database of another plane, and
computes no usage, quantity or meter state — a second metering authority is the drift ADR-0023
M1 exists to prevent (**RR6**).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from core.policies import InvalidPolicy

from .ports import (
    MeteredWatermark,
    NullMeteredWatermark,
    OutboxState,
    RetentionCandidate,
    RetentionStore,
    RetentionWindow,
)

log = logging.getLogger("voxvoice.events.retention")

#: The one outbox state that means *drained* (ADR-0041 RT1/RT3). A frozen single-member set
#: rather than an `is DELIVERED` comparison at the call site, so the rule is stated once and
#: the tests can assert against the same object the production path reads.
DRAINED_STATES: frozenset[OutboxState] = frozenset({OutboxState.DELIVERED})


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How far back a sweep reaches, and how recent is too recent to touch.

    A frozen, validated value object with no I/O and no clock — the `core.policies` convention
    (*"All frozen, validated at construction (no silent config), and side-effect-side-free"*),
    and it raises the same `InvalidPolicy` those policies raise. It lives here rather than in
    `core.policies` because a retention window is an events concern, not a generic one.

    **Two bounds, both required, both finite and positive.** `max_age_s` sets how old an event
    must be before it is eligible at all — it becomes the window's *end*. `window_span_s` sets
    how far back beyond that the sweep reaches — it becomes the window's *start*. RT7.2 demands a
    **closed** window, and a single "delete anything older than X" parameter cannot express
    one: it is open at the bottom by construction, so its blast radius silently grows with the
    age of the deployment. Requiring the second bound is what makes a sweep's reach a stated
    quantity instead of an emergent one.

    There is deliberately **no default instance and no default period**. Retention is opt-in;
    a deployment that configures nothing deletes nothing (**RT7.1**), and a default here would
    quietly delete history in every deployment that never thought about it.
    """

    max_age_s: float
    window_span_s: float

    def __post_init__(self) -> None:
        if self.max_age_s < 0:
            raise InvalidPolicy(f"max_age_s must be >= 0, got {self.max_age_s}")
        # Finite **and** strictly positive, in one comparison. `0 < x < inf` is False for NaN
        # (every NaN comparison is), for zero, for negatives and for both infinities — so it is
        # `math.isfinite(x) and x > 0` without the import. That matters here: `math` is not in
        # the events plane's permitted stdlib budget (tests/test_events_boundary.py `_STDLIB`),
        # so importing it would trip the boundary guard and require editing an unrelated test.
        #
        # `+inf` is the case worth naming: it passes a bare `> 0` check and yields
        # `RetentionWindow(start=-inf, ...)` — a window open at the bottom, which is exactly the
        # "everything older than X" shape RT7.2 forbids. Every other non-finite value was already
        # rejected downstream by `RetentionWindow`, but this one was not.
        if not 0 < self.window_span_s < float("inf"):
            raise InvalidPolicy(f"window_span_s must be finite and > 0, got {self.window_span_s}")

    def window(self, now: float) -> RetentionWindow:
        """The closed `[start, end)` this policy authorizes at `now`.

        `end = now - max_age_s` is the recency cliff: nothing newer is ever eligible.
        `start = end - window_span_s` closes the window at the bottom. Both move with the
        injected clock, which is what makes a sweep's scope reproducible in a test rather than
        dependent on when the test happened to run.
        """
        end = now - self.max_age_s
        return RetentionWindow(start=end - self.window_span_s, end=end)


class EventRetention:
    """Evaluates the five guards and performs the sanctioned deletion. Nothing else.

    Everything is injected — the store, the attestation and the clock — so this class performs
    no I/O of its own, constructs nothing, and is exercised end-to-end with fakes and a frozen
    clock (the `OutboxRecovery` shape).

    `policy=None` and `watermark=None` are the **safe** configurations, not degraded ones: no
    policy means no window means nothing is eligible, and no attestation means
    `NullMeteredWatermark`, which covers nothing. Both are RT8 default-deny expressed as
    construction rather than as a branch someone could forget.
    """

    def __init__(
        self,
        store: RetentionStore,
        watermark: MeteredWatermark | None = None,
        policy: RetentionPolicy | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        # Not `or NullMeteredWatermark()` on the caller's side: the default belongs here, where
        # it cannot be omitted, and it denies rather than permits.
        self._watermark = watermark if watermark is not None else NullMeteredWatermark()
        self._policy = policy
        # No `time.time()` fallback — the ADR-0037 RC2 rule. A sweep with neither an injected
        # clock nor an explicit `now` is a caller error, not an invitation to read the wall.
        self._clock = clock

    @property
    def policy(self) -> RetentionPolicy | None:
        """The configured policy, or `None` — in which case this instance deletes nothing."""
        return self._policy

    @property
    def configured(self) -> bool:
        """Whether **any** deletion is possible at all (RT7.1)."""
        return self._policy is not None

    # ── the window ──────────────────────────────────────────────────────────

    def window(self, now: float) -> RetentionWindow | None:
        """The closed window authorized at `now`, or `None` when unconfigured."""
        return None if self._policy is None else self._policy.window(now)

    # ── selection ───────────────────────────────────────────────────────────

    def deletable(self, now: float, *, limit: int | None = None) -> tuple[str, ...]:
        """Every event id that satisfies **all five** guards at `now`, in candidate order.

        **A query, and safe to call alone** — it deletes nothing and writes nothing, which is
        what makes the predicate independently testable (the `OutboxRecovery.candidates`
        precedent). `sweep` is exactly this selection followed by the deletion.
        """
        window = self.window(now)
        if window is None:
            return ()  # RT7.1 — unconfigured deletes nothing
        try:
            candidates = self._store.candidates(window)
        except Exception:  # noqa: BLE001 — RT8: a store that cannot answer deletes nothing
            log.warning("retention: candidate read failed; retaining everything", exc_info=True)
            return ()

        kept: list[str] = []
        for candidate in candidates:
            if self._is_deletable(candidate, window):
                kept.append(candidate.event_id)
                if limit is not None and len(kept) >= limit:
                    break
        return tuple(kept)

    def _is_deletable(self, candidate: RetentionCandidate, window: RetentionWindow) -> bool:
        """The complete RT7 predicate for one candidate — the only place it is stated.

        Re-checks the window even though the store already filtered on it: the guard belongs to
        the policy, not to whichever store happens to be bound, and a store that over-selected
        must not be able to cause a deletion (**RT8**).
        """
        try:
            if not window.contains(candidate.recorded_at):
                return False  # RT7.2
            if candidate.outbox_state is None:
                return False  # delivery state unreadable ⇒ un-drained (RT8)
            if candidate.outbox_state not in DRAINED_STATES:
                return False  # RT7.3 — DELIVERED only; terminal-failed is not drained
            return bool(self._watermark.covers(candidate.event_id))  # RT7.4
        except Exception:  # noqa: BLE001 — RT8 is behaviour, not error handling
            log.warning(
                "retention: guard evaluation failed for %s; retaining",
                candidate.event_id,
                exc_info=True,
            )
            return False

    # ── the sweep ───────────────────────────────────────────────────────────

    def sweep(self, *, now: float | None = None, limit: int | None = None) -> int:
        """Delete every event that satisfies all five guards. Returns the number **deleted**.

        Deterministic and caller-driven — no background timer; the caller (or a scheduler)
        invokes it (RC2, on the `context`/`memory` `expire()` precedent).

        `limit=None` sweeps every eligible event; a supplied `limit` caps it, in the same
        deterministic order. There is **no hidden default**: an unbounded sweep is a choice the
        call site makes visibly, exactly as `OutboxRecovery.recover` requires.
        """
        if limit is not None and limit <= 0:
            # Loud, not coerced — the `recover(limit=...)` rule. A silently-zero sweep would be
            # indistinguishable from "nothing was eligible" and would hide a caller's bad batch
            # size for as long as it kept running.
            raise ValueError(f"limit must be a positive integer or None, got {limit!r}")

        selected = self.deletable(self._resolve_now(now), limit=limit)
        if not selected:
            return 0
        return self._store.delete(selected)

    def _resolve_now(self, now: float | None) -> float:
        if now is not None:
            return now
        if self._clock is None:
            raise ValueError("no clock injected: pass now=... or construct with clock=...")
        return self._clock()


def drained(state: OutboxState | None) -> bool:
    """Whether an outbox state means **drained** (ADR-0041 RT1/RT3).

    Exported because "drained" is the term DL-0141 and plan §M10.5 got wrong, and a caller
    that needs the answer should read it from the same constant retention reads rather than
    re-deriving it from a list of states that looks terminal enough.
    """
    return state in DRAINED_STATES


__all__ = [
    "DRAINED_STATES",
    "EventRetention",
    "RetentionPolicy",
    "drained",
]


# Retention deliberately exposes no "delete this event" entry point and no id-taking public
# method. Every deletion goes through `sweep`, which means every deletion has passed the five
# guards — a bypass would make EV4's "outside the retention policy" clause unenforceable, since
# the policy would no longer be the only way in.
