"""Skill Runtime extension points (M6.2B-1) — interfaces only.

`CapabilityResolver` is the seam a future caller (Agent Runtime) injects to
answer "can this declared capability actually be satisfied" — Skill Runtime
never implements one itself and never imports Tool/Memory/Knowledge/Provider
to check (§8 of the M6.2A design). This is the literal meaning of "integrate
through published Tool Runtime extension points only": the integration
happens at whatever future composition root injects a concrete resolver
wrapping e.g. Tool Runtime's already-published, read-only
`ToolCapabilityCatalog.supports()` — never inside this package.

Reserved for: Skill Policy Engine, Skill Observability Sink, Skill Replay
Sink (all Bundle 2, ownership declared only per the M6.2A design §13-15).
None of this changes `registry.py`, `catalog.py`, or the repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import SkillCapabilityRequirement

__all__ = [
    "CapabilityResolver",
    "SkillPolicyEngine",
    "SkillObservabilitySink",
    "SkillReplaySink",
]


class CapabilityResolver(ABC):
    """The seam for capability satisfiability — never implemented by Skill
    Runtime itself. A future caller injects a concrete adapter over the
    capability's owning runtime's own published, read-only surface."""

    @abstractmethod
    def can_satisfy(self, requirement: SkillCapabilityRequirement) -> bool: ...


class SkillPolicyEngine(ABC):
    """Reserved: allow/deny/visibility verdicts over skills (Bundle 2).
    Ownership only — no implementation in this bundle."""

    @abstractmethod
    def authorize(self, descriptor: object) -> object: ...


class SkillObservabilitySink(ABC):
    """Reserved: registration/discovery audit + metrics (Bundle 2). Ownership
    only — no implementation in this bundle."""

    @abstractmethod
    def record(self, event: object) -> None: ...


class SkillReplaySink(ABC):
    """Reserved: plan-determinism verification metadata (Bundle 2). Ownership
    only — no implementation in this bundle (Skill Runtime never executes, so
    there is no execution artifact to replay yet — only future plan data)."""

    @abstractmethod
    def record(self, replay: object) -> None: ...
