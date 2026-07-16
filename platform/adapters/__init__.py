"""Engine adapters. Consumers and the API talk only to the abstract
`EngineAdapter`; concrete engines live behind it and are swapped via config."""

from .base import EngineAdapter, EngineError, EngineSession
from .factory import get_adapter, set_adapter

__all__ = ["EngineAdapter", "EngineError", "EngineSession", "get_adapter", "set_adapter"]
