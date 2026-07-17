"""Composition root — the single place that wires the Runtime Plane (M2C.2).

This top-level module (NOT part of `api` or `runtime`) is the one sanctioned
place that imports both the runtime and the provider plugins to assemble a
`SessionController` for the Control Plane. Keeping it here preserves the layer
rule that `api` business code never imports `runtime` (AR-14) — the Control Plane
depends only on the `core.session_control.SessionController` interface and
receives a concrete instance from here (DL-0010).

The default profile registers the local, in-process provider set (Constitution
Part B). Real vendor plugins are registered here too once available — a drop-in
change, proving providers are fully replaceable.
"""

from __future__ import annotations

from core.session_control import SessionController, TranscriptSink
from providers.llm.echo import EchoLLM
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.local import LocalTTS
from runtime.registry import CapabilityKind, ProviderRegistry
from runtime.service import RuntimeSessionService


def build_local_registry() -> ProviderRegistry:
    """A ProviderRegistry populated with the local-development provider profile.
    Every provider is registered as the default for its capability."""
    registry = ProviderRegistry()
    registry.register(CapabilityKind.TRANSPORT, LocalTransport(), default=True)
    registry.register(CapabilityKind.STT, LocalSTT(), default=True)
    registry.register(CapabilityKind.LLM, EchoLLM(), default=True)
    registry.register(CapabilityKind.TTS, LocalTTS(), default=True)
    return registry


def build_session_controller(
    *, transcript_sink: TranscriptSink | None = None
) -> SessionController:
    """Assemble the runtime session controller for injection into the Control
    Plane. Swap `build_local_registry` for a registry with real vendor plugins to
    change the profile — nothing else changes."""
    return RuntimeSessionService(build_local_registry(), on_transcript=transcript_sink)
