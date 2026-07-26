"""IntegrationCancellationToken (M6.7B-1) — one top-level token, chained.

Per the M6.7A design §3.1 and ADR rec 4: Agent Platform Integration holds
**one** cancellation token at the top (request scope) and, at each delegation
boundary, *chains* it into the next capability's own local cancellation
primitive — never propagating a raw shared object across a capability
boundary. Every capability keeps its own token
(`pipeline.PipelineCancellation`, `loop.LoopCancellationToken`,
`agent.execution.AgentCancellationToken`,
`workflow.execution.WorkflowCancellationToken`) — all structural duplicates of
the same shape, per the established "duplicate, don't cross-import" precedent.

This token therefore imports **none** of them. A chained child is any object
exposing the shared `cancel(*, reason=...)` / `is_cancelled()` surface; the
child is cancelled *through its own method*, never by sharing this token's
mutable state (§3.1, §17 "Cancellation boundaries").
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable


@runtime_checkable
class _Cancellable(Protocol):
    """The shared cancellation surface every capability's own token exposes."""

    def cancel(self, *, reason: str = ...) -> None: ...
    def is_cancelled(self) -> bool: ...


_C = TypeVar("_C", bound=_Cancellable)


class IntegrationCancellationToken:
    """The single request-scoped token. Chains into — never shares state
    with — each capability's own local token."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._cancelled = False
        self._reason = ""
        self._children: list[_Cancellable] = []

    @property
    def token(self) -> str:
        return self._token

    def cancel(self, *, reason: str = "manual") -> None:
        """Cancel this token and, by chaining, every capability token linked
        to it — each through its own `cancel`, never by shared state."""
        if self._cancelled:
            return
        self._cancelled = True
        self._reason = reason
        for child in self._children:
            if not child.is_cancelled():
                child.cancel(reason=reason)

    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason

    def chain(self, child: _C) -> _C:
        """Link a capability's own local token to this one and return it
        (so the caller can hand the *child* to that capability). If this
        token is already cancelled, the child is cancelled immediately — a
        late link still honours a prior cancellation. The child is a distinct
        object; no mutable state is shared (§3.1)."""
        self._children.append(child)
        if self._cancelled and not child.is_cancelled():
            child.cancel(reason=self._reason)
        return child
