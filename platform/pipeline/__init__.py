"""pipeline — the Runtime Pipeline (Epic 5, M5.7: Runtime Integration).

The official composition root and execution path for VoxVoice: Memory → Knowledge →
Context → Prompt → Reasoning → Provider as one deterministic call. This package owns
**composition, orchestration, propagation, and validation only** — no business logic. Every
stage is invoked through its existing, closed public surface; none of the six runtimes (or
the M5.6B-2 `bridge` package) is modified or bypassed.

`RuntimePipelineFactory.default()` builds the fully offline, deterministic validation
pipeline (in-memory stores, the deterministic `EchoProviderAdapter`) — no network, no
vendor SDK, matching every runtime's own dev/test default.
"""

from .cancellation import PipelineCancellation
from .composition import RuntimePipelineFactory
from .errors import PipelineCancelled, PipelineError, PipelineTimeout, StageError
from .lineage import PipelineLineage, SourceProvenance
from .observability import Metric, PipelineExecutionSnapshot
from .pipeline import RuntimePipeline
from .request_context import RequestContext
from .response import UnifiedResponse
from .sources import UserQuerySource
from .trace import (
    PipelineExecutionTrace,
    PipelineStage,
    PipelineTimings,
    PipelineTraceEvent,
    StageTiming,
)
from .translate import prompt_package_to_inference_request

__all__ = [
    "RuntimePipeline",
    "RuntimePipelineFactory",
    "RequestContext",
    "UnifiedResponse",
    "PipelineCancellation",
    "PipelineError",
    "StageError",
    "PipelineCancelled",
    "PipelineTimeout",
    "PipelineLineage",
    "SourceProvenance",
    "PipelineStage",
    "PipelineTraceEvent",
    "PipelineExecutionTrace",
    "StageTiming",
    "PipelineTimings",
    "Metric",
    "PipelineExecutionSnapshot",
    "UserQuerySource",
    "prompt_package_to_inference_request",
]
