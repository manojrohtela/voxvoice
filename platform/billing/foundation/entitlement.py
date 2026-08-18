"""Entitlements (M9.9 domain) — Billing decides; the owning platform enforces.

The most load-bearing module in the Foundation, because entitlement is where Billing
touches every other plane. ADR-0023 §11 ratified the split:

* **Decision** — *what is this account entitled to?* — is Billing's, exclusively.
* **Enforcement** — *is this specific write allowed right now?* — belongs to the plane
  that owns the write, evaluated **locally** against a snapshot.

Therefore, and enforced by the shape of the code below:

* Billing **performs no permission check**. Nothing here takes a principal, a role, a
  scope, or a request. Entitlement is *commercial capability*, not authorization — §A11.2
  RBAC keeps that job and gains no rival (ADR-0023 §11.3).
* A snapshot is **immutable and versioned**. A new entitlement produces a new version;
  snapshots are never mutated in place (the ADR-0019 injected-immutable-snapshot pattern).
* **Every gate carries a fail-open / fail-closed / degrade classification.** A gate
  without one is an incomplete design and fails validation (ADR-0023 E3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .catalog import FeatureKind


class GateClassification(StrEnum):
    """What an enforcing plane must do when entitlement data is stale or absent.

    ADR-0023 E4: anything that could interrupt a live conversation or an in-flight
    rollout is FAIL_OPEN; anything that could create unbounded new cost or unearned
    capability is FAIL_CLOSED. Bounded, measured revenue leakage is preferred, explicitly,
    to a customer-visible interruption."""

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"
    DEGRADE = "degrade"


class GrantSource(StrEnum):
    """Where an entitlement came from. Precedence is declared, never inferred (§14)."""

    PLAN = "plan"
    CONTRACT = "contract"
    CREDIT = "credit"
    PURCHASE = "purchase"  # marketplace order (M9.20)
    PARTNER = "partner"  # reseller/white-label grant (Blueprint §17)
    PROMOTION = "promotion"


#: Declared precedence when the same entitlement code is granted by several sources.
#: Higher wins. Contract overrides plan; a partner grant bounds everything beneath it.
GRANT_PRECEDENCE: dict[GrantSource, int] = {
    GrantSource.PLAN: 10,
    GrantSource.PROMOTION: 20,
    GrantSource.CREDIT: 30,
    GrantSource.PURCHASE: 40,
    GrantSource.CONTRACT: 50,
    GrantSource.PARTNER: 60,
}


@dataclass(frozen=True, slots=True)
class EntitlementGate:
    """The declaration of one enforceable capability: what it is, and how an enforcing
    plane must behave when it cannot see fresh data. Gates are declared once, in the
    Foundation, so classification is a design-time property rather than a call-site
    decision."""

    code: str
    kind: FeatureKind
    classification: GateClassification
    description: str = ""
    meter_code: str = ""  # set for QUOTA gates


@dataclass(frozen=True, slots=True, order=True)
class Entitlement:
    """One resolved capability. `allowance=None` means unlimited; `0` means explicitly
    none. `allows_overage` distinguishes a plan that bills overage from one that caps."""

    code: str
    kind: FeatureKind
    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False
    source: GrantSource = GrantSource.PLAN

    @property
    def is_unlimited(self) -> bool:
        return self.enabled and self.allowance is None


@dataclass(frozen=True, slots=True)
class EntitlementGrant:
    """A durable grant Billing owns — the audit-bearing record of *why* an account has a
    capability. Snapshots are computed from grants; grants are never computed from
    snapshots."""

    id: str
    org_id: str
    account_id: str
    entitlement: Entitlement
    source: GrantSource
    source_ref: str = ""  # subscription id, contract id, order id, partner id
    granted_at: float = 0.0
    revoked_at: float | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    """The immutable, versioned, org-scoped, cacheable statement enforcing planes read.

    This object is the *entire* interface between Billing and enforcement. It contains no
    principals, no roles, and no request context — only what the account is entitled to,
    plus the metadata an enforcing plane needs to reason about staleness. Reading it
    creates no dependency on the Billing Platform (ADR-0031 D3)."""

    id: str
    org_id: str
    account_id: str
    version: int
    issued_at: float
    entitlements: tuple[Entitlement, ...] = ()
    gates: tuple[EntitlementGate, ...] = ()
    subscription_id: str = ""
    max_staleness_seconds: float = 3600.0

    # ── read-only queries (no evaluation of *who* — only *what*) ────────────────
    def entitlement(self, code: str) -> Entitlement | None:
        return next((e for e in self.entitlements if e.code == code), None)

    def gate(self, code: str) -> EntitlementGate | None:
        return next((g for g in self.gates if g.code == code), None)

    def is_enabled(self, code: str) -> bool:
        """Whether a capability is present. **Not** a permission check — the caller is the
        owner of the write and decides what to do with the answer (ADR-0023 §11.1)."""
        e = self.entitlement(code)
        return bool(e and e.enabled)

    def allowance(self, code: str) -> int | None:
        e = self.entitlement(code)
        return None if e is None else e.allowance

    def classification(self, code: str) -> GateClassification | None:
        g = self.gate(code)
        return None if g is None else g.classification

    def is_stale_at(self, ts: float) -> bool:
        """Whether an enforcing plane should treat this snapshot as stale — the input to
        its declared fail-open/fail-closed behaviour."""
        return (ts - self.issued_at) > self.max_staleness_seconds

    @property
    def unclassified_gates(self) -> tuple[str, ...]:
        """Entitlement codes with no declared gate. Non-empty ⇒ an incomplete design
        (ADR-0023 E3); validation reports it as an error."""
        declared = {g.code for g in self.gates}
        return tuple(sorted(e.code for e in self.entitlements if e.code not in declared))
