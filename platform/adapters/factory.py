"""Adapter factory — the single place engine selection happens.

`get_adapter()` returns a process-wide singleton chosen by `ENGINE`. This is
the only code that knows which concrete engine exists; the API and consumers
see only `EngineAdapter`. Swapping engines is therefore pure config."""

from __future__ import annotations

from api.config import get_settings

from .base import EngineAdapter
from .dograh import DograhAdapter
from .livekit import LiveKitAdapter

_adapter: EngineAdapter | None = None


def build_adapter() -> EngineAdapter:
    settings = get_settings()
    engine = settings.engine.lower()
    if engine == "dograh":
        return DograhAdapter(
            base_url=settings.dograh_base_url,
            api_key=settings.dograh_api_key,
            ui_base_url=settings.dograh_ui_base_url,
        )
    if engine == "livekit":
        return LiveKitAdapter()
    raise ValueError(f"Unknown ENGINE={settings.engine!r} (expected 'dograh' or 'livekit')")


def get_adapter() -> EngineAdapter:
    global _adapter
    if _adapter is None:
        _adapter = build_adapter()
    return _adapter


def set_adapter(adapter: EngineAdapter | None) -> None:
    """Test hook: inject a fake adapter (or reset with None)."""
    global _adapter
    _adapter = adapter
