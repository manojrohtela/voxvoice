"""core.module — the shared RuntimeModule + BusConsumer infrastructure (M5.0).

Every AI runtime module composes these: a deterministic FSM-driven lifecycle,
health, metrics, DI, structured logging, error isolation, graceful shutdown
(`RuntimeModule`), and event-bus integration that Replay/Gateway observe for free
(`BusConsumer`). Domain modules write only their own logic.
"""

from .consumer import BusConsumer
from .metrics import HealthStatus, Meter, ModuleMetrics, NullMeter
from .module import ModuleConfig, ModuleError, ModuleState, RuntimeModule

__all__ = [
    "RuntimeModule",
    "BusConsumer",
    "ModuleConfig",
    "ModuleState",
    "ModuleError",
    "ModuleMetrics",
    "HealthStatus",
    "Meter",
    "NullMeter",
]
