"""integration.replay — cross-capability replay *correlation* (M6.7B-2).

Per the M6.7A design §13 and ADR rec 6: Agent Platform Integration adds **zero
replay logic of its own**. Each Epic 6 capability replays only its own state
through its own hash-chain engine (`AgentReplayEngine`, `WorkflowExecutionRuntime.
validate_replay`, `LoopReplayEngine`); Integration's only replay responsibility
is to correlate those already-independent records by the shared `trace_id`, and
— when asked to validate — to delegate to each artifact's **owning** engine's
own `validate`, never replaying another capability's state itself.

This package imports no capability replay *internals* and creates no new replay
engine (Bundle 2 constraint). It also does not import the standalone top-level
`replay/` platform (not an allowed `integration` dependency) — that edge tool's
raw-event correlation stays separate (design §13).
"""

from .correlation import (
    PlatformReplayCorrelation,
    PlatformReplayIndex,
    PlatformReplayQuery,
    ReplayArtifact,
    ReplayValidationRef,
)

__all__ = [
    "PlatformReplayCorrelation",
    "PlatformReplayIndex",
    "PlatformReplayQuery",
    "ReplayArtifact",
    "ReplayValidationRef",
]
