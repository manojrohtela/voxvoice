"""SubscriptionManager — client subscription lifecycle + matching (M4.2).

Adds/removes channels on a `ClientSession` (with an **authorization hook** and
duplicate detection) and matches an event to the set of interested clients (deduped
— one delivery per client). It owns subscription state only; it never creates events
or touches the Runtime.

Authorization is an **extension point** (a `Callable` gating a channel for a client's
auth context) — no implementation here (M4.2 non-goal). Matching is a linear scan of
connected clients — correct and simple; a scope/type index is an additive
optimization (M4.3+).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from core.events import Event

from .errors import DuplicateSubscription
from .metrics import GatewayMetrics
from .scope import EventChannel
from .session import ClientSession

# Authorization hook: (auth context, channel) → allowed? Default accepts all.
Authorizer = Callable[[Any, EventChannel], bool]


def _allow_all(auth: Any, channel: EventChannel) -> bool:  # noqa: ARG001
    return True


class SubscriptionManager:
    def __init__(
        self,
        *,
        metrics: GatewayMetrics | None = None,
        authorize: Authorizer | None = None,
    ) -> None:
        self._metrics = metrics or GatewayMetrics()
        self._authorize = authorize or _allow_all

    def subscribe(self, session: ClientSession, channel: EventChannel) -> bool:
        """Add a channel to a client. Returns False if the authorization hook denies
        it; raises DuplicateSubscription if the client already holds it."""
        if not self._authorize(session.auth, channel):
            return False
        if channel in session.channels:
            raise DuplicateSubscription(
                f"client {session.id!r} already subscribed to {channel}"
            )
        session.channels.add(channel)
        self._metrics.subscriptions_added += 1
        return True

    def unsubscribe(self, session: ClientSession, channel: EventChannel) -> None:
        """Remove a channel. Idempotent."""
        if channel in session.channels:
            session.channels.discard(channel)
            self._metrics.subscriptions_removed += 1

    def matching(
        self, sessions: Iterable[ClientSession], event: Event
    ) -> list[ClientSession]:
        """The connected clients interested in this event — deduped (one delivery
        per client) and in a stable order."""
        return [s for s in sessions if s.channels and s.matches(event)]
