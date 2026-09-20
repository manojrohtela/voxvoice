"""runtime.faults — the media-path fault-tolerance layer (M11.4).

Discharges **AR-23** (*"every provider call passes through the fault-tolerance layer… direct raw
calls are defects"*) and **AR-24** (*"degradations are declared, evented, and recorded — never
silent"*) for the conversation path, on the terms ADR-0046 ratified:

* mechanisms are **reused, not rebuilt** — `provider.CircuitBreaker` and
  `provider.FailoverController` are imported (FT-D3), across the acyclic `runtime → provider`
  edge FT-V3 validated;
* the voice path is **not** routed through `platform/provider/`'s synchronous execution runtime
  (FT-D2);
* **no fifth `ProviderRegistry`** is introduced (FT-V6) — the guard decorates calls, it does not
  resolve providers.
"""

from .degradation import (
    DEGRADATION_HISTORY,
    Degradation,
    DegradationKind,
    DegradationRecorder,
    DegradationSink,
)
from .guard import (
    DEFAULT_CALL_TIMEOUT_S,
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    CandidateList,
    GuardPolicy,
    ProviderGuard,
)
from .validation import (
    MAX_SAMPLE_RATE,
    MIN_SAMPLE_RATE,
    ValidationIssue,
    greeting_state_for,
    require_valid_agent_spec,
    validate_agent_spec,
)

__all__ = [
    "DEFAULT_CALL_TIMEOUT_S",
    "DEFAULT_STREAM_IDLE_TIMEOUT_S",
    "DEGRADATION_HISTORY",
    "CandidateList",
    "Degradation",
    "DegradationKind",
    "DegradationRecorder",
    "DegradationSink",
    "GuardPolicy",
    "MAX_SAMPLE_RATE",
    "MIN_SAMPLE_RATE",
    "ProviderGuard",
    "ValidationIssue",
    "greeting_state_for",
    "require_valid_agent_spec",
    "validate_agent_spec",
]
