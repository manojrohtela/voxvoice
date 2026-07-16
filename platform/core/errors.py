"""Typed platform error hierarchy (Constitution §A2.5 CODING_STANDARDS §3).

One root, `PlatformError`. Every layer raises typed errors; boundaries translate
them (provider errors -> capability errors; runtime errors -> typed events; API
errors -> HTTP codes). Raw third-party errors never cross a boundary.

Scaffolding established at M1; subclasses are added as the components that raise
them are built (runtime, providers). No behaviour here — definitions only.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Root of all VoxVoice-raised errors."""


class ConfigError(PlatformError):
    """Invalid or inconsistent configuration (fail fast, do not run degraded)."""


class ProviderError(PlatformError):
    """A capability provider failed. Wrapped by the fault-tolerance layer; a raw
    vendor error must never reach a consumer (Constitution §A10, §A6)."""


class RuntimeConflict(PlatformError):
    """An illegal state-machine transition or lifecycle violation
    (Constitution §A4.4). Raised by the runtime once it exists (M2+)."""
