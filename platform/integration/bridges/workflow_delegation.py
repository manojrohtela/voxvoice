"""WorkflowDelegationBridge (M6.7B-1) — the concrete `AgentWorkflowPort`.

Per the M6.7A design §6: Agent Runtime Bundles 1-3 declared and tested the
`AgentWorkflowPort` seam interface but never built a concrete adapter (correct
scoping — Workflow Runtime didn't exist when Agent Bundle 1 shipped). This is
that adapter. It delegates the Agent-owned `resolve`/`submit`/`status`/`signal`
seam to `workflow.execution.WorkflowExecutionRuntime`'s own already-certified
methods — **never** modifying `workflow/`.

**Reality vs. the design's illustrative method names (design §17 risk 3,
DL-0073):** the design §6 named `submit_execution`/`status`/`signal`/
`cancel_execution` illustratively; the certified `WorkflowExecutionRuntime`
surface is `start(namespace, id, version, initial_variables=, cancellation=)`
returning `(execution, result)`, plus `resume_after_event(execution_id,
payload)`. This is exactly the "unverified `WorkflowPort` conformance gap
becomes operationally load-bearing" risk §17 flagged — resolved here by
adapting to the *real* surface, not the illustrative one. Because the offline
execution engine runs synchronously to a terminal state inside `start()`, the
bridge records the returned `WorkflowExecution.state` to answer later
`status()` polls faithfully (it reports the state the runtime actually
produced — never an invented one).

Cancellation (design §6, §16.4): each submitted execution gets its own
`WorkflowCancellationToken`, chained from the top-level
`IntegrationCancellationToken` when one is supplied — token chaining only,
never a shared object (§3.1). The registry is injected explicitly rather than
read off the runtime's private `_registry` (no reaching into privates).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from agent.extensions import AgentWorkflowPort
from workflow import WorkflowRegistry
from workflow.execution import WorkflowExecutionRuntime
from workflow.execution.cancellation import WorkflowCancellationToken

from ..cancellation import IntegrationCancellationToken
from ..errors import IntegrationError
from .refs import parse_workflow_ref


class WorkflowDelegationBridge(AgentWorkflowPort):
    """Concrete `AgentWorkflowPort` over `WorkflowExecutionRuntime`.

    Cancellation (M6.7.1 — F3): the per-request `IntegrationCancellationToken` is
    supplied by `cancellation_provider` (the composition root passes
    `integration.trace.current_cancellation`, a context-local reader — never a
    shared mutable token). On `submit`, the delegated workflow's own
    `WorkflowCancellationToken` is *chained* to that request's token, so
    cancelling the platform token cancels the workflow token through its own
    primitive (design §16.4). `cancellation` remains supported for a fixed token
    (back-compat). Tracking maps are LRU-bounded (F2)."""

    def __init__(
        self,
        execution_runtime: WorkflowExecutionRuntime,
        registry: WorkflowRegistry,
        *,
        cancellation: IntegrationCancellationToken | None = None,
        cancellation_provider: Callable[[], IntegrationCancellationToken | None] | None = None,
        max_tracked: int = 4096,
    ) -> None:
        if max_tracked < 1:
            raise ValueError("max_tracked must be >= 1")
        self._rt = execution_runtime
        self._registry = registry
        self._cancellation = cancellation
        self._cancellation_provider = cancellation_provider
        self._max_tracked = max_tracked
        self._handles: OrderedDict[str, tuple[str, str, str]] = OrderedDict()
        self._states: OrderedDict[str, str] = OrderedDict()
        self._tokens: OrderedDict[str, WorkflowCancellationToken] = OrderedDict()

    def _current_cancellation(self) -> IntegrationCancellationToken | None:
        if self._cancellation_provider is not None:
            return self._cancellation_provider()
        return self._cancellation

    @staticmethod
    def _put(store: OrderedDict, key, value, limit: int) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)

    def resolve(self, workflow_ref: str) -> str:
        """Resolve `namespace.id[@version]` to a concrete registered workflow;
        return an opaque handle. Raises if unregistered (seam contract)."""
        namespace, id_, version = parse_workflow_ref(workflow_ref)
        descriptor = self._registry.get_descriptor(namespace, id_, version)
        resolved = descriptor.manifest.version
        handle = f"{namespace}.{id_}@{resolved}"
        self._put(self._handles, handle, (namespace, id_, resolved), self._max_tracked)
        return handle

    def submit(self, handle: str, inputs: Mapping[str, Any]) -> str:
        """Submit an execution; return an execution_id. Never blocks on a
        remote system (the offline engine completes synchronously)."""
        try:
            namespace, id_, version = self._handles[handle]
        except KeyError:
            raise IntegrationError(
                f"unknown workflow handle {handle!r}; call resolve() first"
            ) from None
        token = WorkflowCancellationToken(handle)
        request_cancellation = self._current_cancellation()
        if request_cancellation is not None:
            # cancelling the platform token cancels this workflow token (chained)
            request_cancellation.chain(token)
        execution, _result = self._rt.start(
            namespace, id_, version,
            initial_variables=dict(inputs), cancellation=token,
        )
        self._put(self._states, execution.execution_id, execution.state.value, self._max_tracked)
        self._put(self._tokens, execution.execution_id, token, self._max_tracked)
        return execution.execution_id

    def status(self, execution_id: str) -> str:
        """One of the `WorkflowExecutionState` values, as a string (the value
        the runtime actually produced)."""
        return self._states.get(execution_id, "unknown")

    def cancellation_token_for(self, execution_id: str) -> WorkflowCancellationToken | None:
        """The delegated workflow's own cancellation token for an execution
        (observability/verification); `None` if not tracked."""
        return self._tokens.get(execution_id)

    def signal(self, execution_id: str, name: str, payload: Mapping[str, Any]) -> None:
        """Deliver an external signal (mirrors `resume_after_event`)."""
        execution, _result = self._rt.resume_after_event(
            execution_id, {"signal": name, **dict(payload)}
        )
        self._put(self._states, execution.execution_id, execution.state.value, self._max_tracked)
