"""Loop Engine (M6.6B-1) — a deterministic propose→critique→repair→validate→
stop-check *library*, never a Runtime. See
`docs/architecture/epic6/loop-engine-design.md` (M6.6A) for the full design.

No Registry, Manifest, Descriptor, Lifecycle FSM, or Repository — Loop Engine
has no registrable identity at all (the design's own §1, ADR item 1). This
package re-exports the model shapes, the mechanical stopping-policy checks,
the `LoopValidator` interface seam, the cancellation token, the serializer,
diagnostics builder, and the factory. `loop/core/pipeline.py` (actual
propose→critique→repair→validate→stop-check sequencing) and `loop/replay/`
are out of scope for this bundle (M6.6B-2).
"""

from __future__ import annotations

from .core.cancellation import LoopCancellationToken
from .core.config import RUNTIME_VERSION, LoopConfiguration
from .core.domain import (
    LoopCritique,
    LoopDefinition,
    LoopExecutionContext,
    LoopIteration,
    LoopProposal,
    LoopRepair,
    LoopResult,
    LoopStatistics,
    LoopStoppingPolicy,
    LoopValidation,
)
from .core.factory import LoopFactory
from .core.ids import content_hash
from .core.serializer import LoopSerializer
from .core.stopping import evaluate_stop
from .core.validator import LoopValidator
from .diagnostics.diagnostics import LoopDiagnostics, build_diagnostics

__all__ = [
    "RUNTIME_VERSION",
    "LoopCancellationToken",
    "LoopConfiguration",
    "LoopCritique",
    "LoopDefinition",
    "LoopDiagnostics",
    "LoopExecutionContext",
    "LoopFactory",
    "LoopIteration",
    "LoopProposal",
    "LoopRepair",
    "LoopResult",
    "LoopSerializer",
    "LoopStatistics",
    "LoopStoppingPolicy",
    "LoopValidation",
    "LoopValidator",
    "build_diagnostics",
    "content_hash",
    "evaluate_stop",
]
