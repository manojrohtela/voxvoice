"""replay — the Conversation Replay & Observability Platform (M4.3).

Records every Internal Event Bus event and turns it into deterministic,
visualization-only replay: a timeline, a filterable/searchable event index, event
correlation, derived metrics, and a point-in-time runtime-state inspector, driven by
a playback controller (play/pause/step/jump/speed).

`ConversationRecorder` subscribes to the bus **read-only** through the public
`core.events.EventBus` contract, so — like the gateway — the **Runtime is never
modified and never knows it is being recorded**. Everything here is a pure function
of recorded events: **no Runtime logic runs during replay** (§ replay is
visualization only). Edge layer: imports `core` + stdlib — never `runtime`/`api`.
"""

from .correlator import CorrelationGroup, EventCorrelator
from .engine import ReplayController, ReplayEngine, ReplayState
from .errors import CursorOutOfRange, InvalidFilter, ReplayError, UnknownSession
from .index import EventFilter, EventIndex
from .inspector import RuntimeStateSnapshot, SessionInspector
from .metrics import ConversationMetrics, MetricsCollector
from .recorder import ConversationRecorder, ReplaySession
from .serializer import ReplaySerializer
from .timeline import (
    CATEGORY_COLORS,
    Timeline,
    TimelineBuilder,
    TimelineCursor,
    TimelineEntry,
)

__all__ = [
    "ConversationRecorder",
    "ReplaySession",
    "ReplayEngine",
    "ReplayController",
    "ReplayState",
    "TimelineBuilder",
    "Timeline",
    "TimelineEntry",
    "TimelineCursor",
    "CATEGORY_COLORS",
    "EventIndex",
    "EventFilter",
    "EventCorrelator",
    "CorrelationGroup",
    "MetricsCollector",
    "ConversationMetrics",
    "SessionInspector",
    "RuntimeStateSnapshot",
    "ReplaySerializer",
    "ReplayError",
    "UnknownSession",
    "CursorOutOfRange",
    "InvalidFilter",
]
