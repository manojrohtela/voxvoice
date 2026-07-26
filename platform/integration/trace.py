"""Execution-scoped trace + cancellation propagation (M6.7.1 — F1/F3 hardening).

Replaces the M6.7B-2 single-slot `_CurrentTrace` holder (M6.7C finding F1) with
`contextvars`, the standard concurrency-safe mechanism:

- **async-safe** — each `asyncio` task runs in its own copied context, so a
  concurrent task never sees another's trace/cancellation.
- **thread-safe** — each OS thread has its own context; there is no shared
  mutable slot to race on.
- **nested-safe** — `set()` returns a token and `execution_scope` `reset()`s to
  the exact prior value on exit, so nested scopes restore correctly (LIFO).
- **leak-free** — the `finally` reset guarantees the value never outlives its
  scope, even on exception/cancellation.

A `ContextVar` is **context-local**, not global mutable state: the composition
root sets it per request and the Integration-owned `PipelineRoutedReasoningPort`
/ `WorkflowDelegationBridge` read it — the one platform `trace_id` still reaches
exactly the two downstream calls Integration owns, and capabilities keep minting
their own internal ids (DL-0077, unchanged). No new propagation mechanism: one
value, read by the same two adapters as before.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

from .cancellation import IntegrationCancellationToken

_current_trace: ContextVar[str] = ContextVar("voxvoice_integration_trace", default="")
_current_cancellation: ContextVar[IntegrationCancellationToken | None] = ContextVar(
    "voxvoice_integration_cancellation", default=None
)


def current_trace() -> str:
    """The current execution's platform `trace_id` (empty outside any scope)."""
    return _current_trace.get()


def current_cancellation() -> IntegrationCancellationToken | None:
    """The current execution's `IntegrationCancellationToken` (None outside a
    scope). Read by `WorkflowDelegationBridge` to chain the delegated workflow's
    own token (F3)."""
    return _current_cancellation.get()


@contextlib.contextmanager
def execution_scope(
    trace_id: str, cancellation: IntegrationCancellationToken | None = None
) -> Iterator[None]:
    """Bind `trace_id` (and optionally the request's cancellation token) for the
    dynamic extent of one execution. Restores the prior values on exit — nested
    scopes are safe and leak nothing."""
    trace_token = _current_trace.set(trace_id)
    cancel_token = _current_cancellation.set(cancellation)
    try:
        yield
    finally:
        _current_cancellation.reset(cancel_token)
        _current_trace.reset(trace_token)
