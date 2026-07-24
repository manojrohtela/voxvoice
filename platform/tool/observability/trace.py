"""ExecutionTrace builder (M6.1B-3) — a pure projection of an already-finished
`ToolResult`. Never influences execution; `tool/runtime.py` is untouched.

`transitions` is reconstructed deterministically from `result.success`/`result.error`
(verified against `ToolRuntime.aexecute`'s own frozen control flow in `reasons.py`)
since `ToolResult.state` is always the terminal FSM state (`COMPLETED`/`FAILED`) —
the intermediate phases are not separately recorded on the result itself.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..domain import ToolRequest, ToolResult
from ..ids import content_hash
from .domain import ExecutionTrace
from .ids import trace_id_for
from .reasons import classify_error

_SUCCESS_TRANSITIONS: tuple[str, ...] = ("received", "validating", "executing", "completed")
_FAIL_AT_VALIDATING: tuple[str, ...] = ("received", "validating", "failed")
_FAIL_AT_EXECUTING: tuple[str, ...] = ("received", "validating", "executing", "failed")


def _transitions_for(result: ToolResult) -> tuple[str, ...]:
    if result.success:
        return _SUCCESS_TRANSITIONS
    _, phase = classify_error(result.error)
    return _FAIL_AT_EXECUTING if phase == "executing" else _FAIL_AT_VALIDATING


def execution_trace_for(
    request: ToolRequest,
    result: ToolResult,
    *,
    started_at: float | None = None,
    ended_at: float | None = None,
    metadata: Mapping[str, str] | None = None,
) -> ExecutionTrace:
    start = started_at if started_at is not None else result.created_at
    end = ended_at if ended_at is not None else result.created_at
    # `result.request_hash` is always the runtime-resolved hash (`ToolRuntime._resolve`
    # fills it internally, on its own copy of the request); the caller-supplied
    # `request` object is never mutated, so it is never the authoritative source here.
    arguments_hash = content_hash(_stable_args(request))
    output_hash = result.result_hash or content_hash(result.request_hash, repr(result.output))
    replay_hash = content_hash(result.request_hash, output_hash, result.state.value)

    return ExecutionTrace(
        trace_id=trace_id_for(request.request_id), request_id=request.request_id,
        tool_id=f"{result.namespace}.{result.tool}", tool_version=result.version,
        classification=result.classification.value, transitions=_transitions_for(result),
        duration_ms=max(end - start, 0.0) * 1000.0, started_at=start, ended_at=end,
        arguments_hash=arguments_hash, output_hash=output_hash, replay_hash=replay_hash,
        metadata=dict(metadata or {}),
    )


def _stable_args(request: ToolRequest) -> str:
    import json

    return json.dumps(dict(request.arguments), sort_keys=True, default=str)
