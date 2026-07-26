"""IntegrationExecutionContext (M6.7B-1) — the composition-root context shape.

Per the M6.7A design §4.1: this sits *above* the Skill→Workflow→Agent→Loop
structural-duplication lineage (ADR-0017), at the composition root — the same
relationship `pipeline.RequestContext` already has to the capability context
shapes below it. It is **never** passed into a capability's internals
directly; each capability downstream still constructs its *own* context shape
from the fields extracted here (§4.1, §17 "Context boundaries").

Trace propagation (design §14, ADR rec 5): one `trace_id` is minted once, at
request entry, and threaded **by value** into every capability's own existing
`trace_id` field — never a new propagation mechanism, never a shared mutable
object. `default_trace_id` is a content-hash (deterministic), never
`random()` — matching `IntegrationConfiguration.trace_id_factory`'s documented
default (§3.2) and the platform-wide determinism rule (§17).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace


def default_trace_id(*parts: object) -> str:
    """A deterministic, content-addressed trace id — never random (§3.2).

    Joining is unambiguous (length-prefixed) so distinct part tuples can never
    collide by concatenation."""
    payload = "\x1f".join(f"{len(s := str(p))}:{s}" for p in parts)
    return "tr_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class IntegrationExecutionContext:
    """Immutable per-request correlation context. `goal_id` is populated once
    Agent Runtime accepts the goal (design §4.1)."""

    trace_id: str
    session_id: str
    org_id: str | None = None
    goal_id: str | None = None
    conversation_id: str | None = None

    def with_goal_id(self, goal_id: str) -> IntegrationExecutionContext:
        """Return a new context carrying the accepted goal's id — never a
        mutation (frozen; §4.1 "never a shared mutable object")."""
        return replace(self, goal_id=goal_id)
