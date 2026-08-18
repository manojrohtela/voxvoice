"""Pure domain services (M9.2) — stateless, deterministic, side-effect free.

Every service here is a **pure function of its arguments**: no I/O, no clock reads (time
arrives as a parameter or via an injected `Clock`), no persistence, no networking, no
randomness. Two calls with identical inputs produce identical outputs — which is what
makes a billing run reproducible and a period re-derivable from the event log
(ADR-0023 RS2).

Services own **business rules only** (ADR-0023 §Decision 10). They own no state: a
service instance holds configuration (a policy, a clock seam), never mutable data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .catalog import CommercialCatalog, FeatureKind, MeterAggregation, PlanFeature
from .contracts import Contract
from .domain import BillingInterval, BillingPeriod, Quantity
from .entitlement import (
    GRANT_PRECEDENCE,
    Entitlement,
    EntitlementGate,
    EntitlementGrant,
    EntitlementSnapshot,
    GateClassification,
    GrantSource,
)
from .errors import InvalidBillingTransition
from .invoicing import ChargeLine, ChargeLineKind, InvoiceNumber, InvoiceTotals
from .metering import UsageRecord, fold
from .money import Currency, Money, Rounding
from .seams import Clock
from .subscription import (
    CONTRACTED_ENTITLEMENT_STATES,
    ChangeTiming,
    ProrationBehaviour,
    ProrationOutcome,
    Subscription,
    SubscriptionState,
    SubscriptionTrigger,
)

#: Nominal period lengths in seconds. Deterministic by construction: a billing period is a
#: declared length, not a calendar computation, so a run started at any instant produces
#: the same boundaries. Calendar-aligned anniversaries are a later, additive refinement
#: behind this same service.
#: Sentinel used when ranking an unlimited allowance against a finite one.
_UNLIMITED = 1 << 62

INTERVAL_SECONDS: dict[BillingInterval, int] = {
    BillingInterval.MONTHLY: 30 * 86400,
    BillingInterval.YEARLY: 365 * 86400,
}


class PeriodService:
    """Computes billing periods. Charges attach to a period, never to "now"."""

    def period_for(self, interval: BillingInterval, *, start: float) -> BillingPeriod:
        return BillingPeriod(start, start + INTERVAL_SECONDS[interval])

    def next_period(self, period: BillingPeriod, interval: BillingInterval) -> BillingPeriod:
        return self.period_for(interval, start=period.end)

    def periods_between(
        self, interval: BillingInterval, *, start: float, until: float
    ) -> tuple[BillingPeriod, ...]:
        """Deterministic enumeration — used by renewal and by re-derivation of history."""
        out: list[BillingPeriod] = []
        cursor = start
        while cursor < until:
            p = self.period_for(interval, start=cursor)
            out.append(p)
            cursor = p.end
        return tuple(out)

    def trial_end(self, *, start: float, trial_seconds: int) -> float | None:
        return start + trial_seconds if trial_seconds > 0 else None


class SubscriptionLifecycleService:
    """Validates subscription transitions against the declared table.

    Owns *whether* a transition is legal and what it produces; it never persists and never
    emits. Illegal edges raise `InvalidBillingTransition` — rejected, never coerced."""

    def __init__(self, definition: StateMachineDefinition) -> None:
        self._definition = definition

    def can(self, state: SubscriptionState, trigger: SubscriptionTrigger) -> bool:
        return self._definition.get(state, trigger) is not None

    def next_state(
        self, state: SubscriptionState, trigger: SubscriptionTrigger
    ) -> SubscriptionState:
        edge = self._definition.get(state, trigger)
        if edge is None:
            raise InvalidBillingTransition(
                f"subscription cannot {trigger.value} from {state.value}; "
                f"legal triggers: {sorted(t.value for t in self._definition.triggers_from(state))}"
            )
        return edge.target

    def apply(
        self, subscription: Subscription, trigger: SubscriptionTrigger, *, at: float
    ) -> Subscription:
        """Return a **new** subscription in the next state. The input is never mutated —
        aggregates are immutable, so history is always reconstructible."""
        target = self.next_state(subscription.state, trigger)
        changes: dict[str, object] = {"state": target}
        if target is SubscriptionState.ACTIVE and subscription.started_at is None:
            changes["started_at"] = at
        if target is SubscriptionState.CANCELLED:
            changes["cancelled_at"] = at
        if target is SubscriptionState.EXPIRED:
            changes["ended_at"] = at
        return replace(subscription, **changes)  # type: ignore[arg-type]


class ProrationService:
    """Decides the timing and financial behaviour of a mid-period change, and computes the
    exact prorated amount with integer arithmetic under a declared rounding policy.

    Blueprint §6.3 encoded literally: upgrades are immediate and charged for the
    remainder; downgrades default to period end; an *immediate* downgrade issues a credit,
    never a silent refund."""

    def decide(
        self,
        *,
        period: BillingPeriod,
        at: float,
        is_expansion: bool,
        immediate_downgrade: bool = False,
    ) -> ProrationOutcome:
        elapsed = period.elapsed_at(at)
        remaining = period.remaining_at(at)
        denominator = int(period.duration)
        numerator = int(remaining)
        if is_expansion:
            return ProrationOutcome(
                ChangeTiming.IMMEDIATE,
                ProrationBehaviour.CHARGE_REMAINDER,
                elapsed,
                remaining,
                numerator,
                denominator,
                "expansion takes effect immediately and is charged for the remainder",
            )
        if immediate_downgrade:
            return ProrationOutcome(
                ChangeTiming.IMMEDIATE,
                ProrationBehaviour.CREDIT_REMAINDER,
                elapsed,
                remaining,
                numerator,
                denominator,
                "immediate contraction credits the unused remainder (never a silent refund)",
            )
        return ProrationOutcome(
            ChangeTiming.PERIOD_END,
            ProrationBehaviour.NONE,
            elapsed,
            remaining,
            0,
            denominator,
            "contraction defers to period end so paid-for capability is not lost",
        )

    def amount(
        self, delta: Money, outcome: ProrationOutcome, *, rounding: Rounding = Rounding.HALF_UP
    ) -> Money:
        """The prorated amount for a price delta. Exact integer maths; the sign follows the
        behaviour, so a credit is never accidentally booked as a charge."""
        if not outcome.is_prorated or outcome.denominator == 0:
            return Money.zero(delta.currency)
        prorated = delta.scaled(outcome.numerator, outcome.denominator, rounding)
        if outcome.behaviour is ProrationBehaviour.CREDIT_REMAINDER:
            return -prorated
        return prorated


class EntitlementResolver:
    """Composes grants into an immutable snapshot. **Decides; never enforces.**

    Nothing in this class takes a principal, a role, or a request — entitlement is
    commercial capability, and authorization stays entirely with §A11.2 RBAC
    (ADR-0023 §11.3). Composition precedence is declared (`GRANT_PRECEDENCE`), never
    inferred from insertion order, so resolution is deterministic."""

    def __init__(self, gates: tuple[EntitlementGate, ...]) -> None:
        self._gates = gates

    def from_plan(
        self, features: tuple[PlanFeature, ...], catalog: CommercialCatalog
    ) -> tuple[Entitlement, ...]:
        out: list[Entitlement] = []
        for pf in features:
            feature = catalog.feature(pf.feature_code)
            kind = feature.kind if feature else FeatureKind.FLAG
            out.append(
                Entitlement(
                    code=pf.feature_code,
                    kind=kind,
                    enabled=pf.enabled,
                    allowance=pf.allowance,
                    allows_overage=pf.allows_overage,
                    source=GrantSource.PLAN,
                )
            )
        return tuple(sorted(out))

    def compose(self, grants: tuple[EntitlementGrant, ...]) -> tuple[Entitlement, ...]:
        """Highest-precedence grant wins per code; ties break on the more generous
        allowance, then deterministically on the grant id."""
        best: dict[str, tuple[int, int, str, Entitlement]] = {}
        for g in grants:
            if not g.is_active:
                continue
            e = g.entitlement
            rank = GRANT_PRECEDENCE[g.source]
            # unlimited (allowance None) is the most generous — a sentinel keeps the
            # comparison a plain integer ordering rather than a special case.
            generosity = _UNLIMITED if e.allowance is None else e.allowance
            current = best.get(e.code)
            if (
                current is None
                or (rank, generosity) > (current[0], current[1])
                or ((rank, generosity) == (current[0], current[1]) and g.id < current[2])
            ):
                best[e.code] = (rank, generosity, g.id, replace(e, source=g.source))
        return tuple(sorted(v[3] for v in best.values()))

    def contract_for_state(
        self, entitlements: tuple[Entitlement, ...], state: SubscriptionState
    ) -> tuple[Entitlement, ...]:
        """Entitlement contraction under PAST_DUE / PAUSED / SUSPENDED. Capability is
        reduced, reversibly — **customer data is never destroyed** (ADR-0023 E6/PY8)."""
        if state not in CONTRACTED_ENTITLEMENT_STATES:
            return entitlements
        return tuple(
            replace(e, enabled=False, allows_overage=False)
            if e.kind is FeatureKind.FLAG
            else replace(e, allowance=0, allows_overage=False)
            for e in entitlements
        )

    def snapshot(
        self,
        *,
        snapshot_id: str,
        org_id: str,
        account_id: str,
        version: int,
        issued_at: float,
        entitlements: tuple[Entitlement, ...],
        subscription_id: str = "",
        max_staleness_seconds: float = 3600.0,
    ) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            id=snapshot_id,
            org_id=org_id,
            account_id=account_id,
            version=version,
            issued_at=issued_at,
            entitlements=entitlements,
            gates=self._gates,
            subscription_id=subscription_id,
            max_staleness_seconds=max_staleness_seconds,
        )


@dataclass(frozen=True, slots=True)
class QuotaVerdict:
    """The *information* an enforcing plane needs to make its own decision.

    Deliberately not an authorization answer: Billing states what is entitled, what is
    consumed, and how the gate is classified. The plane that owns the write decides
    (ADR-0023 §11.1/E1)."""

    code: str
    allowance: int | None
    consumed: int
    allows_overage: bool
    classification: GateClassification | None
    within_allowance: bool
    stale: bool

    @property
    def would_exceed(self) -> bool:
        return not self.within_allowance and not self.allows_overage


class QuotaService:
    """Derives quota verdicts from a snapshot and observed consumption. Pure."""

    WARN_AT_BASIS_POINTS = 8000  # 80% — warn before the wall (ADR-0023 E5)

    def verdict(
        self, snapshot: EntitlementSnapshot, code: str, *, consumed: int, at: float
    ) -> QuotaVerdict:
        e = snapshot.entitlement(code)
        allowance = None if e is None else e.allowance
        within = allowance is None or consumed <= allowance
        return QuotaVerdict(
            code=code,
            allowance=allowance,
            consumed=consumed,
            allows_overage=bool(e and e.allows_overage),
            classification=snapshot.classification(code),
            within_allowance=within,
            stale=snapshot.is_stale_at(at),
        )

    def should_warn(self, verdict: QuotaVerdict) -> bool:
        if verdict.allowance is None or verdict.allowance == 0:
            return False
        return verdict.consumed * 10000 >= verdict.allowance * self.WARN_AT_BASIS_POINTS


class MeteringService:
    """Deterministic derivation over usage records. Measures nothing (ADR-0023 M1)."""

    def deduplicate(self, records: tuple[UsageRecord, ...]) -> tuple[UsageRecord, ...]:
        """Idempotency made explicit: the first record per `(event_id, meter)` wins, and
        input order does not change the output set."""
        seen: set[tuple[str, str]] = set()
        out: list[UsageRecord] = []
        for r in sorted(records, key=lambda r: (r.source.event_id, r.meter_code, r.id)):
            if r.idempotency_key in seen:
                continue
            seen.add(r.idempotency_key)
            out.append(r)
        return tuple(out)

    def total(
        self, records: tuple[UsageRecord, ...], aggregation: MeterAggregation, *, unit: str
    ) -> Quantity:
        billable = [r.quantity for r in self.deduplicate(records) if r.is_billable]
        if not billable:
            return Quantity.zero(unit)
        return fold(aggregation, tuple(billable))


class InvoiceTotalsService:
    """Computes invoice totals in the **declared order** (Blueprint §8.2):

        line discount → subscription discount → contract discount → credits → tax

    Order matters financially, so it is part of the architecture rather than an
    implementation detail; this is the one place it is applied."""

    def totals(
        self,
        lines: tuple[ChargeLine, ...],
        *,
        currency: Currency,
        credit_applied: Money | None = None,
    ) -> InvoiceTotals:
        zero = Money.zero(currency)
        for line in lines:
            if line.amount.currency is not currency:
                raise ValueError(f"charge line in {line.amount.currency}, invoice is {currency}")
        subtotal = zero
        discounts = zero
        taxes = zero
        for line in lines:
            if line.kind is ChargeLineKind.DISCOUNT:
                discounts = discounts + line.amount
            elif line.kind is ChargeLineKind.TAX:
                taxes = taxes + line.amount
            elif line.kind is not ChargeLineKind.CREDIT:
                subtotal = subtotal + line.amount
        credit = credit_applied or zero
        if credit.currency is not currency:
            raise ValueError("applied credit currency differs from the invoice currency")
        total = subtotal + discounts + taxes
        due = total - credit
        return InvoiceTotals(
            subtotal=subtotal,
            discount_total=discounts,
            tax_total=taxes,
            credit_applied=credit,
            total=total,
            amount_due=due if due.minor > 0 else zero,
        )


class InvoiceNumberFormatter:
    """Formats an allocated sequence into a stable, human-readable number.

    Allocation (gapless, exactly-once) is a persistence concern behind
    `InvoiceNumberAllocator`; the *format* is declared here so no layer invents its own."""

    def format(self, *, entity_code: str, jurisdiction: str, sequence: int) -> InvoiceNumber:
        parts = [entity_code.upper()]
        if jurisdiction:
            parts.append(jurisdiction.upper())
        parts.append(f"{sequence:06d}")
        return InvoiceNumber(entity_code, jurisdiction, sequence, "-".join(parts))


class ContractService:
    """Pure contract arithmetic: drawdown, true-up, and renewal timing. Contract terms are
    VoxVoice facts — no provider ever sees them (M9.0C §14)."""

    def true_up(self, contract: Contract) -> Money | None:
        """The shortfall owed at term end for a minimum-spend commitment, or None."""
        c = contract.commitment
        if c is None or not c.true_up_at_term_end:
            return None
        return c.shortfall

    def renews_at(self, contract: Contract) -> float | None:
        if contract.term is None or not contract.term.auto_renew:
            return None
        return contract.term.ends_at

    def renewal_amount(self, base: Money, contract: Contract) -> Money:
        """Uplift applied in exact basis points — no float touches a renewal price."""
        if contract.term is None or contract.term.uplift_basis_points == 0:
            return base
        return base.scaled(10000 + contract.term.uplift_basis_points, 10000, Rounding.HALF_UP)


class ClockReader:
    """The one place the Foundation reads time, and only through an injected seam.

    Every other service takes time as a parameter, so a billing run's inputs are fully
    explicit and reproducible."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def now(self) -> float:
        return self._clock.now()
