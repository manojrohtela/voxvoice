"""Subscription scopes + channels (M4.2).

A client subscribes to one or more **channels**; each channel is a `Scope`
(session / conversation / agent / org / all) plus an optional `EventType` filter.
Matching reads only fields of the `core.events.Event` envelope (never a Runtime
object), so the gateway stays provider-neutral and Runtime-isolated.

The scope model is deliberately extensible: new dimensions are additive (a new
`ScopeKind` + its field accessor) and future filters compose as additional
predicates — no change to producers, the bus, or the Runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.events import Event, EventType

from .errors import InvalidSubscription


class ScopeKind(StrEnum):
    ALL = "all"  # wildcard — every event
    SESSION = "session"
    CONVERSATION = "conversation"
    AGENT = "agent"
    ORG = "org"


def _scope_value(kind: ScopeKind, event: Event) -> str | None:
    if kind is ScopeKind.SESSION:
        return event.session_id
    if kind is ScopeKind.CONVERSATION:
        return event.conversation_id
    if kind is ScopeKind.ORG:
        return event.org_id
    if kind is ScopeKind.AGENT:
        # Agent id travels in metadata when the producer supplies it (extensible;
        # unset today → matches nothing, which is correct).
        v = event.metadata.get("agent_id")
        return str(v) if v is not None else None
    return None  # ALL has no field


@dataclass(frozen=True, slots=True)
class Scope:
    kind: ScopeKind
    value: str | None = None  # ignored for ALL; required otherwise

    def __post_init__(self) -> None:
        if self.kind is not ScopeKind.ALL and not self.value:
            raise InvalidSubscription(f"scope {self.kind.value} requires a value")

    def matches(self, event: Event) -> bool:
        if self.kind is ScopeKind.ALL:
            return True
        return _scope_value(self.kind, event) == self.value


@dataclass(frozen=True, slots=True)
class EventChannel:
    """One subscription unit: a scope + an optional event-type filter. A client may
    hold many channels; an event is delivered once if *any* channel matches."""

    scope: Scope
    types: frozenset[EventType] | None = None  # None → all types in scope

    def matches(self, event: Event) -> bool:
        if not self.scope.matches(event):
            return False
        return self.types is None or event.type in self.types

    # Convenience constructors -------------------------------------------------
    @classmethod
    def all(cls, *, types: frozenset[EventType] | None = None) -> EventChannel:
        return cls(Scope(ScopeKind.ALL), types)

    @classmethod
    def session(cls, sid: str, *, types: frozenset[EventType] | None = None) -> EventChannel:
        return cls(Scope(ScopeKind.SESSION, sid), types)

    @classmethod
    def conversation(
        cls, cid: str, *, types: frozenset[EventType] | None = None
    ) -> EventChannel:
        return cls(Scope(ScopeKind.CONVERSATION, cid), types)

    @classmethod
    def org(cls, org_id: str, *, types: frozenset[EventType] | None = None) -> EventChannel:
        return cls(Scope(ScopeKind.ORG, org_id), types)

    @classmethod
    def agent(cls, agent_id: str, *, types: frozenset[EventType] | None = None) -> EventChannel:
        return cls(Scope(ScopeKind.AGENT, agent_id), types)
