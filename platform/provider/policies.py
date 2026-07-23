"""Provider Policy Engine (M5.6B-2) — deterministic, AI-free.

Provider-specific policy value objects + a `ProviderPolicyEngine` that turns them into
**verdicts** the router/runtime execute. The engine performs no I/O and no side effects: it
decides, the caller acts.

Policies (all seven from the milestone): Allow List, Deny List, Organization Restrictions,
Region Restrictions, Compliance, Quotas, Feature Restrictions. The `ProviderPolicies` bundle
is the single config the factory threads into the router/tenancy; each consumer reads its own
sub-policy so the surface stays in one place and is validated once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.policies import InvalidPolicy


# ── policy value objects ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ProviderAccessPolicy:
    """Allow list / deny list over provider names. Deny wins on conflict."""

    allowed_providers: frozenset[str] = frozenset()  # empty -> all
    denied_providers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class OrganizationPolicy:
    """Which organizations may route through this runtime at all (empty -> all)."""

    allowed_orgs: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RegionPolicy:
    """Which regions are admissible (empty -> all)."""

    allowed_regions: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CompliancePolicy:
    """Data-residency style constraint: when both are set, a candidate's declared region
    must match the request's scope region."""

    require_region_match: bool = False


@dataclass(frozen=True, slots=True)
class ProviderQuotaPolicy:
    """Quota is a hook (real enforcement is a `tenant.QuotaEnforcer`, wired in `tenancy.py`);
    this flag only controls whether the hook is consulted."""

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class FeaturePolicy:
    """Which capabilities are permitted org-wide (empty -> all)."""

    allowed_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ProviderPolicies:
    """The full deterministic policy bundle for a runtime."""

    access: ProviderAccessPolicy = field(default_factory=ProviderAccessPolicy)
    organization: OrganizationPolicy = field(default_factory=OrganizationPolicy)
    region: RegionPolicy = field(default_factory=RegionPolicy)
    compliance: CompliancePolicy = field(default_factory=CompliancePolicy)
    quota: ProviderQuotaPolicy = field(default_factory=ProviderQuotaPolicy)
    feature: FeaturePolicy = field(default_factory=FeaturePolicy)

    def __post_init__(self) -> None:
        overlap = self.access.allowed_providers & self.access.denied_providers
        if overlap:
            raise InvalidPolicy(f"providers both allowed and denied: {sorted(overlap)}")


# ── verdicts ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reason: str = ""


# ── the engine ────────────────────────────────────────────────────────────────
class ProviderPolicyEngine:
    """Holds the policy bundle and produces **verdicts** (no I/O, no side effects). The
    router calls `admit_provider`/`admit_region`/`admit_compliance`/`admit_feature`;
    `ProviderTenancy` calls `admit_organization` (+ its own isolation checks)."""

    def __init__(self, policies: ProviderPolicies | None = None) -> None:
        self._p = policies or ProviderPolicies()

    @property
    def policies(self) -> ProviderPolicies:
        return self._p

    def admit_provider(self, name: str) -> Verdict:
        p = self._p.access
        if name in p.denied_providers:
            return Verdict(False, f"provider {name!r} denied")
        if p.allowed_providers and name not in p.allowed_providers:
            return Verdict(False, f"provider {name!r} not allowed")
        return Verdict(True)

    def admit_organization(self, org_id: str | None) -> Verdict:
        allowed = self._p.organization.allowed_orgs
        if allowed and (org_id is None or org_id not in allowed):
            return Verdict(False, f"organization {org_id!r} not allowed")
        return Verdict(True)

    def admit_region(self, region: str | None) -> Verdict:
        allowed = self._p.region.allowed_regions
        if allowed and (region is None or region not in allowed):
            return Verdict(False, f"region {region!r} not allowed")
        return Verdict(True)

    def admit_compliance(self, candidate_region: str | None, request_region: str | None) -> Verdict:
        if not self._p.compliance.require_region_match:
            return Verdict(True)
        if request_region is None or candidate_region is None:
            return Verdict(True)  # nothing declared to enforce against
        if candidate_region != request_region:
            return Verdict(False, f"region mismatch {candidate_region!r} != {request_region!r}")
        return Verdict(True)

    def admit_feature(self, capability: str) -> Verdict:
        allowed = self._p.feature.allowed_capabilities
        if allowed and capability not in allowed:
            return Verdict(False, f"capability {capability!r} not allowed")
        return Verdict(True)

    def quota_enabled(self) -> bool:
        return self._p.quota.enabled
