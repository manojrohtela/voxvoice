"""runtime.smart_turn — the Smart Turn Intelligence Engine (Constitution §A4.2, M3.5).

The capstone of Epic 3's Turn Intelligence: a deterministic arbiter that combines
the sub-frameworks' outputs (Turn Engine, Voice Activity, Endpoint, Interrupt) +
runtime timing + a configurable adaptive policy + an optional semantic hint into a
single *final conversational-timing decision* the pipeline consumes. The
sub-frameworks remain independent and keep owning their state; Smart Turn only
arbitrates.

`SmartTurnEngine` owns the decision (sole writer); `SmartTurnCoordinator` is the
pipeline's façade; `SmartTurnPolicy` holds the adaptive knobs; `SmartTurnContext`/
`SmartTurnTiming` carry per-session data; `SmartTurnEvent` is the internal
notification extension point (not the Event Bus, M4). The **semantic extension
interface** is `core.providers.semantic_turn.SemanticTurnEvaluator`
(`CapabilityKind.SEMANTIC_TURN`) — a future semantic model registers and is blended
in with NO runtime change. The runtime never calls an LLM (Constitution §A6).
"""

from .context import SmartTurnContext, SmartTurnTiming
from .coordinator import SmartTurnCoordinator
from .decision import FINISH_DECISIONS, PREEMPT_DECISIONS, SmartTurnDecision
from .engine import SmartTurnEngine
from .events import SmartTurnEvent, SmartTurnObserver
from .null_semantic import NullSemanticEvaluator
from .policy import SmartTurnPolicy
from .signals import SmartTurnInput

__all__ = [
    "SmartTurnEngine",
    "SmartTurnPolicy",
    "SmartTurnCoordinator",
    "SmartTurnContext",
    "SmartTurnTiming",
    "SmartTurnInput",
    "SmartTurnEvent",
    "SmartTurnObserver",
    "SmartTurnDecision",
    "NullSemanticEvaluator",
    "FINISH_DECISIONS",
    "PREEMPT_DECISIONS",
]
