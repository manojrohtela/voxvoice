"""Hook Framework extension points (M6.3B).

`Hook` is the sole seam for third-party/business-logic extension — a
one-method ABC, deliberately the simplest extension shape in the platform
(§ M6.3A design). Every concrete hook implementation lives outside this
package; the Hook Framework itself never ships one.

`HookObservabilitySink`/`HookReplaySink` are reserved interfaces; this bundle
also ships a first concrete, in-memory default of each (`observability.py`),
mirroring Skill Runtime's own Bundle-2 precedent of giving a real, minimal
default body to an ownership-only interface once its milestone's scope
explicitly names it as a deliverable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import HookEvent, HookVerdict

__all__ = ["Hook", "HookObservabilitySink", "HookReplaySink"]


class Hook(ABC):
    """The single extension point. Given an immutable `HookEvent`, decide and
    return exactly one `HookVerdict`. Never executes a Tool, never plans a
    Skill, never mutates runtime state — a hook only decides."""

    @abstractmethod
    def evaluate(self, event: HookEvent) -> HookVerdict: ...


class HookObservabilitySink(ABC):
    """Reserved: hook-run/registration audit + metrics."""

    @abstractmethod
    def record(self, event: object) -> None: ...


class HookReplaySink(ABC):
    """Reserved: hook-run replay-determinism verification metadata."""

    @abstractmethod
    def record(self, replay: object) -> None: ...
