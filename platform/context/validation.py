"""Context Validation Engine (M5.3B-2) — verdicts only.

The gate that runs after assembly and before delivery. It **returns verdicts** describing
what is wrong (and which items are implicated); the **runtime performs the side effects**
(reject the package, or redact flagged items via an injected `Redactor`). The engine does
no I/O and mutates nothing.

Checks: context (non-empty, known kinds), budget (within the global limit), citation
(cited kinds carry a source), privacy (flag items needing redaction), safety (a hook —
interface only, no classifier in-runtime), ordering (indices are contiguous + sorted).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain import ContextPackage
from .policies import ContextPolicyEngine, Verdict


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    verdicts: tuple[Verdict, ...] = ()
    redact_item_ids: tuple[str, ...] = ()  # items the runtime should redact (side effect)
    reasons: tuple[str, ...] = ()

    @property
    def failures(self) -> tuple[str, ...]:
        return self.reasons


class ContextValidationEngine:
    def __init__(self, policy_engine: ContextPolicyEngine | None = None) -> None:
        self._pe = policy_engine or ContextPolicyEngine()

    def validate(self, package: ContextPackage) -> ValidationReport:
        p = self._pe.policies.validation
        verdicts: list[Verdict] = []
        reasons: list[str] = []
        redact: list[str] = []

        # ── context validation ────────────────────────────────────────────────
        if p.require_non_empty and not package.items:
            verdicts.append(Verdict(False, "empty context"))
            reasons.append("empty context")

        # ── budget validation ─────────────────────────────────────────────────
        if p.enforce_budget:
            v = self._pe.budget_ok(package.used_tokens)
            verdicts.append(v)
            if not v.ok:
                reasons.append(v.reason)

        # ── citation validation ───────────────────────────────────────────────
        for item in package.items:
            v = self._pe.citation_ok(item)
            if not v.ok:
                verdicts.append(v)
                reasons.append(v.reason)

        # ── privacy (flag only; redaction is the runtime's side effect) ───────
        for item in package.items:
            if self._pe.needs_redaction(item):
                redact.append(item.id)

        # ── ordering validation (indices contiguous + non-decreasing) ─────────
        if p.enforce_ordering and not _ordering_ok(package.items):
            verdicts.append(Verdict(False, "ordering indices not contiguous"))
            reasons.append("ordering indices not contiguous")

        # ── safety (hook only) ────────────────────────────────────────────────
        if self._pe.safety_enabled():
            verdicts.append(Verdict(True, "safety hook not configured"))

        ok = all(v.ok for v in verdicts)
        return ValidationReport(
            ok=ok, verdicts=tuple(verdicts),
            redact_item_ids=tuple(redact), reasons=tuple(reasons),
        )


def _ordering_ok(items: Sequence) -> bool:
    idx = [i.order_index for i in items]
    return idx == sorted(idx) and idx == list(range(len(idx)))
