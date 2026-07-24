"""Skill planning policy (M6.2B-2) — a concrete, permissive default
implementation of Bundle 1's `SkillPolicyEngine` interface (`extensions.py`,
untouched). Authorizes a *plan's root descriptor* only — never a capability,
never a Tool, never an execution (§13 of the M6.2A design: ownership only,
here given its first concrete, deliberately minimal shape).
"""

from __future__ import annotations

from ..domain import SkillDescriptor
from ..extensions import SkillPolicyEngine
from .domain import SkillPolicyVerdict


class AllowAllSkillPolicy(SkillPolicyEngine):
    """The default planning policy: always allows. A future, real policy
    engine (Agent Runtime composition root) would inject a stricter
    `SkillPolicyEngine` implementation without any change to this bundle."""

    def authorize(self, descriptor: SkillDescriptor) -> SkillPolicyVerdict:
        return SkillPolicyVerdict.ALLOW
