"""ProviderRouter (M5.6B-2) — deterministic routing strategies.

Turns `(request, strategy, catalog snapshot, health snapshot, policy verdicts)` into a
**deterministic** `RoutingDecision` — an ordered candidate list, never a single irreversible
pick, so failover (`failover.py`) is "the next candidate," not a fresh decision. Every
strategy is a pure function of its inputs + a deterministic hash seed — **no `random()`
anywhere**. No I/O, no networking; scoring only reads the registry's declared metadata
(`cost_class`/`latency_class`/`region`) and the caller-supplied ranking/weighting maps —
never a live measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.errors import ConfigError

from .catalog import CapabilityCatalog
from .domain import ProviderDescriptor, ProviderRequest
from .ids import content_hash
from .policies import ProviderPolicyEngine
from .registry import ProviderRegistry


class RoutingStrategy(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"
    CHEAPEST = "cheapest"
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_QUALITY = "highest_quality"
    REGION_AWARE = "region_aware"
    ORGANIZATION_POLICY = "organization_policy"
    USER_POLICY = "user_policy"
    WEIGHTED = "weighted"
    CANARY = "canary"
    AB_TESTING = "ab_testing"
    FAILOVER = "failover"
    BACKUP_PROVIDER = "backup_provider"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """An ordered candidate list — the *entire* chain, so failover is "next candidate," not a
    re-decision. Deterministic for identical inputs."""

    candidates: tuple[str, ...]
    strategy: RoutingStrategy
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Optional strategy inputs — all declared/deterministic, never a live measurement."""

    quality_rank: dict[str, int] = field(default_factory=dict)  # lower = higher quality
    weights: dict[str, int] = field(default_factory=dict)  # name -> relative weight
    canary: str | None = None
    canary_percent: int = 0  # 0-100
    ab_variants: tuple[str, str] | None = None
    backup_of: dict[str, str] = field(default_factory=dict)  # primary -> backup name


class ProviderRouter:
    def __init__(
        self,
        registry: ProviderRegistry,
        catalog: CapabilityCatalog,
        *,
        policy_engine: ProviderPolicyEngine | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._pe = policy_engine or ProviderPolicyEngine()
        self._counter = 0

    def route(
        self,
        request: ProviderRequest,
        *,
        strategy: RoutingStrategy = RoutingStrategy.AUTO,
        context: RoutingContext | None = None,
    ) -> RoutingDecision:
        ctx = context or RoutingContext()

        if strategy is RoutingStrategy.MANUAL or request.provider:
            name = request.provider
            if not name:
                raise ConfigError("manual routing requires request.provider")
            return self._manual(name, strategy)

        eligible = self._eligible(request)
        if not eligible:
            return RoutingDecision(candidates=(), strategy=strategy, reason="no eligible provider")

        if strategy is RoutingStrategy.CHEAPEST:
            ordered = self._sort_by(eligible, lambda d: (d.cost_class, d.name))
        elif strategy is RoutingStrategy.LOWEST_LATENCY:
            ordered = self._sort_by(eligible, lambda d: (d.latency_class, d.name))
        elif strategy is RoutingStrategy.HIGHEST_QUALITY:
            ordered = self._sort_by(
                eligible, lambda d: (ctx.quality_rank.get(d.name, 1_000_000), d.name)
            )
        elif strategy is RoutingStrategy.REGION_AWARE:
            ordered = self._region_aware(eligible, request)
        elif strategy in (RoutingStrategy.ORGANIZATION_POLICY, RoutingStrategy.USER_POLICY,
                         RoutingStrategy.FAILOVER, RoutingStrategy.AUTO):
            # policy filtering already applied in `_eligible`; deterministic composite order
            ordered = self._sort_by(eligible, lambda d: (d.cost_class, d.latency_class, d.name))
        elif strategy is RoutingStrategy.WEIGHTED:
            ordered = self._weighted(eligible, ctx.weights)
        elif strategy is RoutingStrategy.CANARY:
            ordered = self._canary(eligible, request, ctx)
        elif strategy is RoutingStrategy.AB_TESTING:
            ordered = self._ab_testing(eligible, request, ctx)
        elif strategy is RoutingStrategy.BACKUP_PROVIDER:
            ordered = self._with_backup(eligible, ctx)
        else:  # pragma: no cover — exhaustive over RoutingStrategy
            raise ConfigError(f"unknown routing strategy {strategy!r}")

        return RoutingDecision(candidates=tuple(d.name for d in ordered), strategy=strategy)

    # ── eligibility (capability + health + policy) ────────────────────────────
    def _eligible(self, request: ProviderRequest) -> tuple[ProviderDescriptor, ...]:
        unavailable = set(self._registry.unavailable())
        out = []
        for d in self._registry.find(request.capability):
            if d.name in unavailable:
                continue
            if not self._pe.admit_provider(d.name).ok:
                continue
            if not self._pe.admit_region(request.scope.region).ok:
                continue
            if not self._pe.admit_compliance(d.region, request.scope.region).ok:
                continue
            if not self._pe.admit_feature(request.capability).ok:
                continue
            out.append(d)
        return tuple(out)

    def _manual(self, name: str, strategy: RoutingStrategy) -> RoutingDecision:
        if name not in self._registry.names():
            return RoutingDecision(candidates=(), strategy=strategy,
                                   reason=f"unknown provider {name!r}")
        if name in self._registry.unavailable():
            return RoutingDecision(candidates=(), strategy=strategy,
                                   reason=f"provider {name!r} unavailable")
        if not self._pe.admit_provider(name).ok:
            return RoutingDecision(candidates=(), strategy=strategy,
                                   reason=f"provider {name!r} denied by policy")
        return RoutingDecision(candidates=(name,), strategy=strategy)

    @staticmethod
    def _sort_by(descriptors, key) -> list[ProviderDescriptor]:
        return sorted(descriptors, key=key)

    def _region_aware(
        self, eligible: tuple[ProviderDescriptor, ...], request: ProviderRequest
    ) -> list[ProviderDescriptor]:
        region = request.scope.region
        if region is None:
            return self._sort_by(eligible, lambda d: (d.cost_class, d.latency_class, d.name))
        matched = [d for d in eligible if d.region == region]
        rest = [d for d in eligible if d.region != region]
        return (self._sort_by(matched, lambda d: (d.cost_class, d.name))
                + self._sort_by(rest, lambda d: (d.cost_class, d.name)))

    def _weighted(
        self, eligible: tuple[ProviderDescriptor, ...], weights: dict[str, int]
    ) -> list[ProviderDescriptor]:
        self._counter += 1
        bucket = int(content_hash("weighted", self._counter), 16)
        total = sum(max(weights.get(d.name, 1), 0) for d in eligible) or 1
        pick = bucket % total
        acc = 0
        ordered_names = sorted(eligible, key=lambda d: d.name)
        primary = ordered_names[0].name
        for d in ordered_names:
            acc += max(weights.get(d.name, 1), 0)
            if pick < acc:
                primary = d.name
                break
        rest = [d for d in ordered_names if d.name != primary]
        return [next(d for d in eligible if d.name == primary), *rest]

    def _canary(
        self, eligible: tuple[ProviderDescriptor, ...], request: ProviderRequest,
        ctx: RoutingContext,
    ) -> list[ProviderDescriptor]:
        base = self._sort_by(eligible, lambda d: (d.cost_class, d.latency_class, d.name))
        if ctx.canary is None or ctx.canary not in {d.name for d in eligible}:
            return base
        bucket = int(content_hash("canary", request.session_id), 16) % 100
        if bucket < ctx.canary_percent:
            canary_d = next(d for d in eligible if d.name == ctx.canary)
            rest = [d for d in base if d.name != ctx.canary]
            return [canary_d, *rest]
        return base

    def _ab_testing(
        self, eligible: tuple[ProviderDescriptor, ...], request: ProviderRequest,
        ctx: RoutingContext,
    ) -> list[ProviderDescriptor]:
        base = self._sort_by(eligible, lambda d: (d.cost_class, d.latency_class, d.name))
        if ctx.ab_variants is None:
            return base
        a, b = ctx.ab_variants
        names = {d.name for d in eligible}
        if a not in names or b not in names:
            return base
        bucket = int(content_hash("ab", request.session_id), 16) % 2
        chosen = a if bucket == 0 else b
        chosen_d = next(d for d in eligible if d.name == chosen)
        rest = [d for d in base if d.name != chosen]
        return [chosen_d, *rest]

    def _with_backup(
        self, eligible: tuple[ProviderDescriptor, ...], ctx: RoutingContext
    ) -> list[ProviderDescriptor]:
        ordered = self._sort_by(eligible, lambda d: (d.cost_class, d.latency_class, d.name))
        if not ordered:
            return ordered
        backup_name = ctx.backup_of.get(ordered[0].name)
        names = {d.name for d in ordered}
        if backup_name and backup_name not in names:
            backup_d = self._registry_descriptor_if_eligible(backup_name)
            if backup_d is not None:
                return [*ordered, backup_d]
        return ordered

    def _registry_descriptor_if_eligible(self, name: str) -> ProviderDescriptor | None:
        if name not in self._registry.names() or name in self._registry.unavailable():
            return None
        if not self._pe.admit_provider(name).ok:
            return None
        return self._registry.descriptor(name)
