"""Commercial Catalog (M9.6 domain, defined at M9.2) — plans, tiers, features, meters.

ADR-0023 §5.1: Billing is the sole writer of plan, feature, and **meter definitions**.
A meter definition *names* its one authoritative source; it never becomes one — Billing
measures nothing (M1–M3). Catalog and prices are deliberately separate objects so that a
price change is never a plan change (Blueprint §8).

Pure, immutable, versioned data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import Tier
from .money import Rounding


class MeterAggregation(StrEnum):
    """How a period's usage is reduced to a billable quantity. Declared, never implied —
    aggregation changes the ledger, so it is part of the definition (Blueprint §5)."""

    SUM = "sum"
    MAX = "max"  # e.g. peak concurrency
    TIME_WEIGHTED = "time_weighted"  # e.g. byte-months of storage
    UNIQUE_COUNT = "unique_count"


class MeterSource(StrEnum):
    """The **one** authoritative producer of a meter's signal (ADR-0023 M2). These name
    existing `metric.*` producers; Billing consumes them read-only and never emits them."""

    RUNTIME_SESSION = "runtime.session"
    PROVIDER_STT = "provider.stt"
    PROVIDER_TTS = "provider.tts"
    PROVIDER_LLM = "provider.llm"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    WORKFLOW = "workflow"
    DEPLOYMENT = "deployment"
    CONTROL_PLANE_API = "control_plane.api"
    # runtime observability (producer: `platform/observability/turn_latency.py` — M10.10,
    # ADR-0039 Amendment 1 MB14) — additive vocabulary extension under AR-32; no existing
    # member's name or value changes. Named separately rather than folded into
    # RUNTIME_SESSION because `CommercialMeter.accepts()` matches on source **plus** an
    # allow-list that is empty ⇒ "any type from the authoritative source": reusing an
    # existing member would let `metric.turn` be silently counted by an unrelated meter.
    #   DEFINED here; the decoder that stamps it is a separately authorized step.
    OBSERVABILITY = "observability"


class FeatureKind(StrEnum):
    """What a catalog feature expresses once it becomes an entitlement."""

    FLAG = "flag"  # on/off capability (SSO, white-label, marketplace publishing)
    LIMIT = "limit"  # structural cap (workspaces, projects, agents, seats)
    RATE = "rate"  # per-window cap (API req/min, concurrent sessions)
    QUOTA = "quota"  # consumption allowance tied to a meter


@dataclass(frozen=True, slots=True)
class MeterDefinition:
    """A billable dimension. Immutable and versioned: changing unit, aggregation, or
    rounding creates a new version, because each of them changes what a customer owes."""

    id: str
    org_id: str  # "" = platform-wide (public catalog); set = contract-scoped meter
    code: str
    display_name: str
    unit: str
    aggregation: MeterAggregation
    source: MeterSource
    rounding: Rounding = Rounding.HALF_UP
    version: int = 1
    description: str = ""


@dataclass(frozen=True, slots=True)
class Feature:
    """A capability a plan confers. Becomes an entitlement dimension at M9.9."""

    id: str
    code: str
    display_name: str
    kind: FeatureKind
    meter_code: str = ""  # required when kind is QUOTA
    description: str = ""


@dataclass(frozen=True, slots=True)
class PlanFeature:
    """A feature *as configured on a plan*: enabled, and (for limits/rates/quotas) the
    numeric allowance. `None` means unlimited; `0` means explicitly none."""

    feature_code: str
    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False


@dataclass(frozen=True, slots=True)
class Plan:
    """A commercial plan — the customer-visible offer. Immutable and versioned; a
    published plan version is never edited (existing subscriptions pin to it)."""

    id: str
    org_id: str  # "" = public catalog; set = contract-scoped plan (enterprise)
    code: str
    display_name: str
    tier: Tier
    version: int = 1
    published: bool = False
    deprecated: bool = False
    features: tuple[PlanFeature, ...] = ()
    metered_codes: tuple[str, ...] = ()  # meter codes this plan bills
    description: str = ""

    @property
    def is_public(self) -> bool:
        return self.org_id == ""

    @property
    def is_available(self) -> bool:
        return self.published and not self.deprecated

    def feature(self, code: str) -> PlanFeature | None:
        return next((f for f in self.features if f.feature_code == code), None)


@dataclass(frozen=True, slots=True)
class CommercialCatalog:
    """An immutable, self-consistent snapshot of plans, features, and meters. Rating,
    entitlement, and invoicing all resolve against a catalog *snapshot*, never against
    mutable global state — the ADR-0019 injected-immutable-snapshot discipline."""

    plans: tuple[Plan, ...] = ()
    features: tuple[Feature, ...] = ()
    meters: tuple[MeterDefinition, ...] = ()
    version: int = 1

    def plan(self, code: str) -> Plan | None:
        return next((p for p in self.plans if p.code == code), None)

    def feature(self, code: str) -> Feature | None:
        return next((f for f in self.features if f.code == code), None)

    def meter(self, code: str) -> MeterDefinition | None:
        return next((m for m in self.meters if m.code == code), None)

    @property
    def available_plans(self) -> tuple[Plan, ...]:
        return tuple(p for p in self.plans if p.is_available)
