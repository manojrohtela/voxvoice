"""Capability contracts (Constitution §A6 / ADR-0007).

One contract per capability the runtime consumes. A Provider Plugin (in the
top-level `providers/` package) implements exactly one of these and declares its
capability flags, which the Control Plane validates at configuration write time
(AR-22). Contracts live in `core` so `providers/` and `runtime/` depend only on
the floor, never on each other.

Interfaces only — **no implementations** (those are `providers/*`, from M2+).
"""

from .analytics import AnalyticsProvider
from .llm import LLMProvider
from .semantic_turn import SemanticCapabilities, SemanticHint, SemanticTurnEvaluator
from .storage import StorageProvider
from .stt import STTProvider
from .transport import TransportProvider
from .tts import TTSProvider
from .voice_activity import VoiceActivityProvider

__all__ = [
    "STTProvider",
    "TTSProvider",
    "LLMProvider",
    "TransportProvider",
    "StorageProvider",
    "AnalyticsProvider",
    "VoiceActivityProvider",
    "SemanticTurnEvaluator",
    "SemanticHint",
    "SemanticCapabilities",
]
