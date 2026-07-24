"""tool.streaming — Tool Runtime Streaming & Cancellation (M6.1B-4).

A new, additive subpackage of `tool` implementing the remaining execution
capabilities the M6.1A design reserved: streaming execution (`ToolStreamingRuntime`),
a streaming result model (`StreamedToolResult`, composing the frozen `ToolResult`
rather than replacing it), cancellation (`ToolCancellationToken`, checked between
every chunk — a local copy of `pipeline.PipelineCancellation`'s exact shape),
deterministic timeout/backpressure policies (`StreamingPolicy`,
`ToolStreamingPolicyEngine` — mirrors `reasoning.policies.ReasoningPolicyEngine`'s
streaming sub-policy), in-memory `StreamingMetrics`/`StreamingDiagnostics`, a
versioned `StreamingSerializer`, and streaming-compatible replay
(`StreamingReplayEngine`, `streamed_replay_record_for` — reusing
`tool.observability`'s `ReplayRecord`/`ExecutionTrace` shapes verbatim).

`tool/runtime.py` (frozen since M6.1B-1), `tool/governance/` (frozen since
M6.1B-2), and `tool/observability/` (frozen since M6.1B-3) are all untouched.
No WebSocket/SSE/MCP/Workflow/Agent/Skills/Hooks/Loop-Engine/external SDK —
streaming here is deterministic, provider-neutral, and transport-neutral: the
adapter yields an in-process `Iterable[ToolChunk]`, nothing more.
"""

from .adapters import EchoStreamingAdapter
from .cancellation import ToolCancellationToken
from .controller import ToolStreamingController
from .diagnostics import StreamingDiagnostics, build_streaming_diagnostics
from .domain import (
    ChunkKind,
    StreamedToolResult,
    StreamingMetadata,
    StreamingSession,
    ToolChunk,
    Verdict,
)
from .events import PHASE_EVENT_TYPE, StreamingPhase
from .factory import StreamingFactory
from .metrics import StreamingMetrics
from .policies import StreamingPolicy, ToolStreamingPolicyEngine
from .replay import StreamingReplayEngine, streamed_replay_record_for
from .runtime import ToolStreamingRuntime
from .serializer import StreamingSerializer

__all__ = [
    # facade + factory
    "ToolStreamingRuntime",
    "StreamingFactory",
    # domain
    "ChunkKind",
    "ToolChunk",
    "StreamingMetadata",
    "Verdict",
    "StreamingSession",
    "StreamedToolResult",
    # cancellation
    "ToolCancellationToken",
    # policy + controller
    "StreamingPolicy",
    "ToolStreamingPolicyEngine",
    "ToolStreamingController",
    # adapters
    "EchoStreamingAdapter",
    # metrics + diagnostics
    "StreamingMetrics",
    "StreamingDiagnostics",
    "build_streaming_diagnostics",
    # replay
    "StreamingReplayEngine",
    "streamed_replay_record_for",
    # serializer
    "StreamingSerializer",
    # events
    "StreamingPhase",
    "PHASE_EVENT_TYPE",
]
