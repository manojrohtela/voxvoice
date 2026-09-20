"""The ordered-candidate-chain seam (M11.4, ADR-0046 **FT-D3**).

A structural `Protocol`, and that shape is the whole point of the decision it implements.

`provider.FailoverController` already contains the deterministic chain walk the media path needs
— *"provider selection only, never a retry loop (no backoff, no repeated attempts against the same
candidate, no sleeping)"*. M11.1's dependency validation (**FT-V2**) found it was not importable
as written: its only coupling to the Provider Runtime is the annotation
`decision: RoutingDecision`, but that one import transitively binds `provider.router`,
`provider.registry`, `provider.catalog`, `provider.domain` and `provider.policies` — dragging a
request/response domain model onto a duplex media path.

M11.0a's fallback was *lift the mechanism, do not rebuild it*. Copying twenty lines of list
subtraction into `runtime/` would be a rebuild wearing a different name. So the seam is **widened
instead**: this Protocol describes the one attribute `FailoverController.next` actually reads, and
`RoutingDecision` already satisfies it **structurally and without modification**. Widening the
annotation to this type deletes `failover.py`'s `.router` import and collapses the transitive
closure to nothing — one controller, two callers, no duplication, no cycle.

Legal on the `core` floor because it is an interface: `core` holds *"contracts, event types, state
definitions, typed errors, ids, and shared types"* and **no behaviour**.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CandidateChain(Protocol):
    """An ordered list of provider candidates to walk, best first.

    Deliberately one member. A protocol that demanded more would stop `RoutingDecision` from
    satisfying it accidentally-on-purpose, which is exactly the property that makes this a
    widening rather than a new abstraction."""

    @property
    def candidates(self) -> tuple[str, ...]: ...


__all__ = ["CandidateChain"]
