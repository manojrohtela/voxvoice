"""UnifiedResponse (M5.7) — the pipeline's single output shape.

Wraps Reasoning's already-self-sufficient `ResponsePackage` (which itself already carries
Provider's contribution via the `ProviderModelBridge`) with the pipeline-level lineage +
trace + timings. No text/usage/finish-reason is recomputed — everything is read straight
off the `ResponsePackage` the Reasoning Runtime produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reasoning.domain import FinishReason, ResponseChunk, TokenUsage

from .lineage import PipelineLineage
from .observability import PipelineExecutionSnapshot
from .trace import PipelineExecutionTrace


@dataclass(frozen=True, slots=True)
class UnifiedResponse:
    """Self-sufficient: a caller reads `text`/`usage`/`finish_reason` and proceeds, exactly
    like every closed runtime's own package — the pipeline adds nothing that changes that
    contract, only the merged lineage/trace/snapshot around it."""

    request_id: str
    trace_id: str
    text: str
    chunks: tuple[ResponseChunk, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: FinishReason = FinishReason.STOP
    provider: str = ""
    model: str = ""
    response_hash: str = ""
    lineage: PipelineLineage = field(default_factory=PipelineLineage)
    trace: PipelineExecutionTrace = field(default_factory=PipelineExecutionTrace)
    snapshot: PipelineExecutionSnapshot | None = None
