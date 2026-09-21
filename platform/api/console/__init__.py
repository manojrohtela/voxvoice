"""The Developer Platform — the product surface over the M11 runtime (Epic 12, ADR-0055).

Additive by construction: this package wraps the M11 WebRTC application and calls frozen
Control-Plane functions; it modifies none of them (`E10-7`). See
`docs/architecture/epic12/m12-hld.md`.
"""

from .app import build_console_app
from .config import ConsoleConfigError, ConsoleSettings

__all__ = ["ConsoleConfigError", "ConsoleSettings", "build_console_app"]
