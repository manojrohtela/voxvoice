"""Prompt Validation Engine (M5.4B-2) — verdicts only.

The gate that runs after build and before delivery. It **returns verdicts** describing what
is wrong (and which sections are implicated); the **runtime performs the side effects**
(reject the package). The engine does no I/O and mutates nothing.

Checks: template (sections carry a resolved template + version), placeholder (no unresolved
`{{…}}`), prompt (well-formed, non-empty required roles), budget (within limit), citation
(cited kinds carry a context source), provider (rendered messages have valid roles), safety
(a hook — interface only, no classifier in-runtime), role (message roles are known).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .domain import PromptPackage, PromptRole
from .policies import CITED_KINDS, PromptPolicyEngine, Verdict

_UNRESOLVED = re.compile(r"\{\{\s*[a-z0-9_.]+\s*\}\}")
_VALID_ROLES = frozenset(r.value for r in PromptRole)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    verdicts: tuple[Verdict, ...] = ()
    reasons: tuple[str, ...] = ()


class PromptValidationEngine:
    def __init__(self, policy_engine: PromptPolicyEngine | None = None) -> None:
        self._pe = policy_engine or PromptPolicyEngine()

    def validate(self, package: PromptPackage) -> ValidationReport:
        p = self._pe.policies.validation
        verdicts: list[Verdict] = []
        reasons: list[str] = []

        def check(v: Verdict) -> None:
            verdicts.append(v)
            if not v.ok:
                reasons.append(v.reason)

        # ── prompt validation (non-empty) ────────────────────────────────────
        if p.require_non_empty and not package.sections:
            check(Verdict(False, "empty prompt"))

        for s in package.sections:
            # ── template validation ──────────────────────────────────────────
            if not s.lineage.template_id or not s.lineage.template_version:
                check(Verdict(False, f"{s.kind.value} missing template lineage"))
            # ── placeholder validation ───────────────────────────────────────
            if p.enforce_placeholders and _UNRESOLVED.search(s.content):
                check(Verdict(False, f"{s.kind.value} has unresolved placeholders"))
            # ── citation validation ──────────────────────────────────────────
            if (self._pe.citation_required() and s.kind in CITED_KINDS
                    and not any(s.lineage.context_sources)):
                check(Verdict(False, f"{s.kind.value} missing citation source"))

        # ── provider + role validation (rendered messages) ──────────────────
        for m in package.messages:
            if p.enforce_roles and m.role.value not in _VALID_ROLES:
                check(Verdict(False, f"invalid message role {m.role!r}"))

        # ── budget validation ────────────────────────────────────────────────
        if p.enforce_budget:
            check(self._pe.budget_ok(package.used_tokens, package.budget.limit_tokens))

        # ── safety (hook only) ───────────────────────────────────────────────
        if self._pe.safety_enabled():
            check(Verdict(True, "safety hook not configured"))

        ok = all(v.ok for v in verdicts)
        return ValidationReport(ok=ok, verdicts=tuple(verdicts), reasons=tuple(reasons))
