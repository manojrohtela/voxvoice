"""Rating Engine domain (M9.12) — commercial charge calculation, and nothing else.

Six milestones built the vocabulary this one consumes. M9.6 said **what is sold**, M9.7 said
**what one unit costs**, M9.8 said **who we bill**, M9.9 said **what was agreed**, M9.10 said
**what is included**, M9.11 said **what was consumed**. Rating is the arithmetic that joins them:
*this agreement, on this pricing version, over this period, against this usage → these charges.*

**Rating owns** charge calculation, charge breakdown, charge provenance, minimum commitments,
included-quantity resolution, overage, charge-level reductions, rating runs, rerating, rating
snapshots, comparison, explanation, and append-only rating history.

**Rating owns no** usage, price, catalog, subscription, entitlement, ledger, credit, invoice,
payment, refund, tax, or authorization. It **measures nothing** and **prices nothing**: every
input is an immutable, already-published artefact, and a test asserts this module never resolves
a price, folds a meter, or reads a clock.

Four properties are structural rather than conventional.

* **Every input is immutable and version-pinned.** A `RatingBasis` names a catalog version, a
  pricing version, a subscription version, an entitlement snapshot version, and a usage digest.
  Nothing rating reads can change underneath it, which is what makes "rerating a historical
  period never changes historical inputs" a property of the *shape* of the data rather than a
  discipline someone has to remember.
* **The run id is derived from the basis digest.** Identical inputs therefore produce the
  identical run id, so rerating with unchanged inputs is a no-op at the database's unique
  constraint rather than a second row that happens to agree. Changed inputs produce a *different*
  id — a new run, appended beside the old one. This is M9.11's derived-id device, reused for the
  same reason: a claim about determinism nobody can compare byte-for-byte is a claim that decays.
* **A charge carries its own breakdown and its own provenance.** Not a reference to them. A
  customer disputing ₹4,120 is answered from the charge itself — which rate, from which book at
  which version, times which quantity, from which usage records, less which reductions — without
  re-running anything.
* **History is appended to, never rewritten.** A finalized run has no edge out of it. Rerating a
  finalized period produces a *new* run that supersedes nothing: the finalized figure stays
  exactly as it was billed, and the difference is expressed as a `RatingDelta` an adjustment can
  be raised from. Editing a figure a customer has already been invoiced for is the one thing this
  domain must be incapable of.

Money is `Money` throughout — integer minor units, declared rounding, no float anywhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .domain import BillingInterval, BillingPeriod
from .money import Currency, Money
from .price_books import AppliedDiscount, DiscountKind
from .pricing import PriceShape

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_rating_run_id(
    org_id: str, subscription_id: str, period_start: float, period_end: float, basis_digest: str
) -> str:
    """The deterministic identity of one rating run.

    Derived from the **basis digest**, not minted, so that rating the same period from the same
    inputs twice produces the same id and the database's unique constraint makes the second
    attempt a no-op. A sequence-minted id would make "idempotent rerating" a claim that only
    holds when nobody retries, and retries are normal.

    The corollary is the useful half: if any input moved — a late usage record, a corrected
    price, an amended agreement — the digest differs, the id differs, and a *new* run is appended
    beside the old one. Rerating therefore cannot overwrite the figure it is revising."""
    return (
        "rat_" + _digest(org_id, subscription_id, f"{period_start}:{period_end}", basis_digest)[:32]
    )


def derive_charge_id(rating_run_id: str, item_id: str, price_pin: str, kind: str) -> str:
    """A charge is identified by what produced it, so the same charge in a rerun compares
    directly against its predecessor rather than by position in a list."""
    return "chg_" + _digest(rating_run_id, item_id, price_pin, kind)[:32]


# ── lifecycle ───────────────────────────────────────────────────────────────────


class RatingState(StrEnum):
    """The lifecycle of one rating run."""

    DRAFT = "draft"
    RATED = "rated"
    FINALIZED = "finalized"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"


class RatingTrigger(StrEnum):
    RATE = "rate"
    FINALIZE = "finalize"
    SUPERSEDE = "supersede"
    DISCARD = "discard"


# The rating lifecycle. Four absences, each a decision:
#
#   * **no edge out of FINALIZED.** A finalized run is what an invoice was raised from
#     (ADR-0023 M7/IN5). Revising it is a *new* run plus a credit note, never a mutation — and
#     the absence of the edge is what makes that structural rather than procedural;
#   * no edge out of SUPERSEDED. A superseded run stays exactly as it was rated, because the
#     question it answers is "what did we think this period cost, before we knew better?";
#   * no edge out of DISCARDED, and no path from DISCARDED back to DRAFT — an abandoned rating
#     attempt is a fact, not a draft to be resumed;
#   * **there is no deletion state at all.** ADR-0023 RS1: rating output is append-only.
RATING_LIFECYCLE: StateMachineDefinition = define(
    [
        (RatingState.DRAFT, RatingTrigger.RATE, RatingState.RATED),
        (RatingState.DRAFT, RatingTrigger.DISCARD, RatingState.DISCARDED),
        (RatingState.RATED, RatingTrigger.FINALIZE, RatingState.FINALIZED),
        (RatingState.RATED, RatingTrigger.SUPERSEDE, RatingState.SUPERSEDED),
        (RatingState.RATED, RatingTrigger.DISCARD, RatingState.DISCARDED),
    ]
)

#: States whose figures are still provisional — a rerate may replace them rather than adjust them.
PROVISIONAL_RATING_STATES: frozenset[RatingState] = frozenset(
    {RatingState.DRAFT, RatingState.RATED}
)
#: States after which the figure is a historical fact. Nothing may edit one of these, ever.
SETTLED_RATING_STATES: frozenset[RatingState] = frozenset(
    {RatingState.FINALIZED, RatingState.SUPERSEDED, RatingState.DISCARDED}
)


class RerateReason(StrEnum):
    """Why a period was rated again. A closed vocabulary because "why is this figure different
    from the one you sent me last week?" is a question with a small number of honest answers,
    and free text would let the interesting ones hide."""

    LATE_USAGE = "late_usage"
    USAGE_CORRECTION = "usage_correction"
    PRICING_CORRECTION = "pricing_correction"
    ENTITLEMENT_CHANGE = "entitlement_change"
    AGREEMENT_AMENDMENT = "agreement_amendment"
    COMMITMENT_TRUE_UP = "commitment_true_up"
    MANUAL_REVIEW = "manual_review"


class RatingMode(StrEnum):
    """Whether a rating result is a durable commercial fact or a question.

    `PREVIEW` results are never persisted and never finalized — a preview that could become the
    billed figure would make "what would this cost?" indistinguishable from "this is what it
    costs", and the two must be separable when a customer asks."""

    AUTHORITATIVE = "authoritative"
    PREVIEW = "preview"


# ── charge shapes ───────────────────────────────────────────────────────────────


class ChargeKind(StrEnum):
    """The commercial shape of one calculated charge.

    Named distinctly from M9.2's `ChargeLineKind`, which is the *invoice line* vocabulary and
    stays exactly as it is — the same call M9.6 made for `Product` beside `Plan`. The two agree
    where they overlap and differ where an invoice does not care: an invoice line does not
    distinguish graduated from volume pricing, and a rating breakdown must."""

    FLAT = "flat"
    SEAT = "seat"
    USAGE = "usage"
    OVERAGE = "overage"
    TIERED = "tiered"
    VOLUME = "volume"
    PACKAGE = "package"
    ONE_OFF = "one_off"
    MINIMUM_COMMITMENT = "minimum_commitment"
    ADJUSTMENT = "adjustment"


#: Shapes whose amount is a rate multiplied by a quantity that may exceed one. The distinction is
#: load-bearing rather than cosmetic: a fixed-sum reduction folded into a *unit* rate would be
#: multiplied by the quantity, which is not what a fixed sum means. See `DiscountRebasingPolicy`.
EXTENDED_CHARGE_KINDS: frozenset[ChargeKind] = frozenset(
    {
        ChargeKind.SEAT,
        ChargeKind.USAGE,
        ChargeKind.OVERAGE,
        ChargeKind.TIERED,
        ChargeKind.VOLUME,
        ChargeKind.PACKAGE,
    }
)

#: How a `PriceShape` (M9.7 — what a price *is*) becomes a `ChargeKind` (what was *charged*).
#: Declared as a table so the mapping is one fact in one place (§A2.5) rather than a decision
#: rebuilt at each call site. `ALLOWANCE_OVERAGE` is absent deliberately: it produces *two*
#: charges — an included allowance at zero and an overage — which no single-valued map can say.
SHAPE_TO_CHARGE_KIND: dict[PriceShape, ChargeKind] = {
    PriceShape.FLAT_RECURRING: ChargeKind.FLAT,
    PriceShape.PER_SEAT: ChargeKind.SEAT,
    PriceShape.PER_UNIT: ChargeKind.USAGE,
    PriceShape.TIERED_GRADUATED: ChargeKind.TIERED,
    PriceShape.VOLUME: ChargeKind.VOLUME,
    PriceShape.PACKAGE: ChargeKind.PACKAGE,
    PriceShape.ONE_OFF: ChargeKind.ONE_OFF,
    PriceShape.COMMITTED: ChargeKind.FLAT,
}


class ComponentKind(StrEnum):
    """One step of a charge's arithmetic. The breakdown is a list of these, and their amounts
    sum exactly to the charge — a breakdown that only *approximately* explains its total is
    worse than none, because it invites the reader to trust it."""

    BASE = "base"
    SEATS = "seats"
    UNITS = "units"
    INCLUDED = "included"
    OVERAGE_UNITS = "overage_units"
    TIER_BAND = "tier_band"
    TIER_FLAT_FEE = "tier_flat_fee"
    PACKAGE_BLOCKS = "package_blocks"
    DISCOUNT = "discount"
    COMMITMENT_SHORTFALL = "commitment_shortfall"
    REBASE = "rebase"
    DELTA = "delta"


class IncludedUnitsSource(StrEnum):
    """Where a charge's included allowance came from. Recorded on every metered charge, because
    "why was I charged for 400 minutes when my plan includes 1000?" is answered by which of the
    two authorities was in force — and a silent default would make it unanswerable."""

    NONE = "none"
    PRICE = "price"
    ENTITLEMENT = "entitlement"


@dataclass(frozen=True, slots=True)
class ChargeComponent:
    """One line of a charge's arithmetic: what was counted, at what rate, for how much.

    `units` and `unit_amount` are optional because not every step has them — a fixed-sum
    reduction has an amount and no rate, an included allowance has units and no amount."""

    kind: ComponentKind
    label: str
    amount: Money
    units: int = 0
    unit_amount: Money | None = None
    detail: str = ""

    @property
    def is_reduction(self) -> bool:
        return self.amount.is_negative


# ── provenance ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChargeProvenance:
    """Everything needed to **re-derive** one charge, stored rather than referenced.

    Every field here names a *frozen* artefact: published versions, pinned price codes, immutable
    usage ids. A provenance that pointed at current definitions would silently change what it
    claims about the past — which is precisely the failure that makes a two-year-old invoice
    unexplainable, and precisely what M9.6/M9.7/M9.9/M9.10/M9.11 made immutable to prevent."""

    price_code: str
    price_version: int = 0
    price_book_code: str = ""
    price_book_version: int = 0
    catalog_version: int | None = None
    pricing_version: int | None = None
    subscription_id: str = ""
    subscription_version: int = 0
    item_id: str = ""
    meter_code: str = ""
    meter_pin: str = ""
    usage_ids: tuple[str, ...] = ()
    usage_digest: str = ""
    entitlement_version: int = 0
    included_units_source: IncludedUnitsSource = IncludedUnitsSource.NONE
    override_applied: bool = False
    resolution_path: tuple[str, ...] = ()

    @property
    def price_pin(self) -> str:
        """The stable identity of the rate this charge used: `code@version`."""
        return f"{self.price_code}@{self.price_version}"

    @property
    def book_pin(self) -> str:
        return f"{self.price_book_code}@{self.price_book_version}"

    @property
    def is_metered(self) -> bool:
        return bool(self.meter_code)

    @property
    def usage_record_count(self) -> int:
        return len(self.usage_ids)


# ── the charge ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatedCharge:
    """**The** commercial output of rating: one immutable, fully explained, fully attributed
    amount.

    Named `RatedCharge` beside M9.2's `ChargeLine`, which stays exactly as it is and which the
    invoicing spine still consumes. The difference is deliberate: a `ChargeLine` is what appears
    on a document, and a `RatedCharge` is the calculation that produced it, breakdown and
    provenance included. Collapsing the two would mean either an invoice line carrying tier-band
    arithmetic a customer never asked for, or a rating result that cannot explain itself.

    Immutable by construction. There is no method here that amends an amount, and there is no
    repository method that could — a revised charge is a *new* charge in a *new* run."""

    id: str
    org_id: str
    account_id: str
    kind: ChargeKind
    description: str
    currency: Currency
    quantity: int = 0
    unit: str = ""
    list_amount: Money | None = None
    gross_amount: Money | None = None
    discount_amount: Money | None = None
    net_amount: Money | None = None
    components: tuple[ChargeComponent, ...] = ()
    discounts: tuple[AppliedDiscount, ...] = ()
    provenance: ChargeProvenance = field(default_factory=lambda: ChargeProvenance(price_code=""))
    interval: BillingInterval | None = None
    shape: PriceShape | None = None

    @property
    def amount(self) -> Money:
        """What this charge actually costs. Zero rather than `None` for an empty charge — a
        caller totalling charges should not have to distinguish the two."""
        return self.net_amount if self.net_amount is not None else Money.zero(self.currency)

    @property
    def is_zero(self) -> bool:
        return self.amount.is_zero

    @property
    def is_discounted(self) -> bool:
        return bool(self.discounts)

    @property
    def component_total(self) -> Money:
        """The sum of the breakdown. Equal to `amount` for every charge this domain produces —
        `RatingValidator` checks it, because a breakdown that does not add up is a breakdown that
        will be believed anyway."""
        total = Money.zero(self.currency)
        for component in self.components:
            total = total + component.amount
        return total

    @property
    def breaks_down_correctly(self) -> bool:
        return self.component_total == self.amount

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of this charge's **commercial content**.

        Excludes nothing a customer is billed on, and includes nothing that legitimately differs
        between a rating run and a replay of it — there is no timestamp and no run id in here.
        Two runs producing equal digests is therefore a *proof* of identical commercial outcome
        rather than a hope."""
        return _digest(
            self.id,
            self.org_id,
            self.account_id,
            self.kind.value,
            self.currency.value,
            str(self.quantity),
            self.unit,
            str(self.amount.minor),
            self.provenance.price_pin,
            self.provenance.book_pin,
            self.provenance.usage_digest,
            ",".join(f"{c.kind.value}:{c.amount.minor}:{c.units}" for c in self.components),
            ",".join(f"{d.code}:{d.reduction.minor}" for d in self.discounts),
        )

    @property
    def comparison_key(self) -> tuple[str, str, str]:
        """What makes "the same charge" the same across two runs: the item, the rate it used,
        and the shape. Not the id — the id embeds the run, so ids never match across runs — and
        not the position, which would make an inserted charge look like every charge changing."""
        return (self.provenance.item_id, self.provenance.price_pin, self.kind.value)


# ── minimum commitments ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MinimumCommitment:
    """A floor a customer agreed to spend in a period, and what it covers.

    A commitment is *not* a price and *not* a discount: it never changes what anything costs. It
    adds a true-up charge when the covered spend falls short, which is why it is applied after
    every other charge and never before one — applying it earlier would make the shortfall depend
    on the order charges happened to be calculated in.

    `price_codes` empty means the whole agreement counts toward it."""

    id: str
    org_id: str
    account_id: str
    code: str
    amount: Money
    subscription_id: str = ""
    price_codes: tuple[str, ...] = ()
    interval: BillingInterval = BillingInterval.MONTHLY
    contract_id: str = ""
    description: str = ""
    active: bool = True
    effective_from: float = 0.0
    effective_until: float | None = None
    created_at: float = 0.0
    retired_at: float | None = None

    def covers_price(self, price_code: str) -> bool:
        return not self.price_codes or price_code in self.price_codes

    def is_live(self, at: float) -> bool:
        return (
            self.active
            and self.retired_at is None
            and self.effective_from <= at
            and (self.effective_until is None or at < self.effective_until)
        )

    def shortfall(self, consumed: Money) -> Money:
        """What is owed to reach the floor. Never negative — a customer who spent *more* than
        they committed does not get the difference back, and expressing that as a negative charge
        would quietly turn a commitment into a credit."""
        if consumed.currency is not self.amount.currency:
            return Money.zero(self.amount.currency)
        gap = self.amount.minor - consumed.minor
        return Money(gap, self.amount.currency) if gap > 0 else Money.zero(self.amount.currency)


# ── the basis ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatingBasis:
    """The **complete, immutable** input to one rating run — and the reason rating is
    reproducible.

    Every field is either a scalar fact or a *version pin* naming a frozen artefact. There is no
    live reference anywhere in here: no repository, no clock, no "current" anything. Two runs
    constructed from equal bases are guaranteed to be rating identical inputs, which is what
    makes `digest` a usable proof instead of a decoration.

    `usage_digest` is a fingerprint of the exact usage record set, not a count. A count would
    match after a correction that replaced one record with another of the same quantity — the
    exact case where the charge legitimately changes and the basis must say so."""

    org_id: str
    account_id: str
    subscription_id: str
    period: BillingPeriod
    currency: Currency
    catalog_version: int
    pricing_version: int
    subscription_version: int = 1
    entitlement_version: int = 0
    usage_digest: str = ""
    usage_record_count: int = 0
    price_pins: tuple[str, ...] = ()
    seat_counts: tuple[tuple[str, int], ...] = ()
    metered_units: tuple[tuple[str, int], ...] = ()
    commitment_codes: tuple[str, ...] = ()
    coupon_code: str = ""
    promotion_codes: tuple[str, ...] = ()
    at: float = 0.0

    @property
    def digest(self) -> str:
        """The fingerprint the run id is derived from.

        `at` is deliberately **excluded**: it is when rating ran, not what it rated. Including it
        would make every rerun a different basis and destroy the idempotency the derived id
        exists to provide."""
        return _digest(
            self.org_id,
            self.account_id,
            self.subscription_id,
            f"{self.period.start}:{self.period.end}",
            self.currency.value,
            f"catalog:{self.catalog_version}",
            f"pricing:{self.pricing_version}",
            f"agreement:{self.subscription_version}",
            f"entitlement:{self.entitlement_version}",
            self.usage_digest,
            str(self.usage_record_count),
            ",".join(self.price_pins),
            ",".join(f"{c}:{n}" for c, n in self.seat_counts),
            ",".join(f"{m}:{u}" for m, u in self.metered_units),
            ",".join(self.commitment_codes),
            self.coupon_code,
            ",".join(self.promotion_codes),
        )

    @property
    def is_metered(self) -> bool:
        return bool(self.metered_units)

    def says_the_same_as(self, other: RatingBasis) -> bool:
        """Whether two bases would rate identically. The whole rerate decision, in one call."""
        return self.digest == other.digest


# ── the result ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatingResult:
    """What one agreement costs for one period: the charges, their totals, and the proof.

    Derived from the basis and the charges, never stored independently of them (§A2.5) — the
    totals here are computed from the charge list, so a header that disagreed with its body is
    not expressible.

    Carries no tax, no credit application, and no amount due. Those belong to invoicing, and a
    rating result that pre-empted them would make the invoice's arithmetic unverifiable."""

    org_id: str
    account_id: str
    subscription_id: str
    period: BillingPeriod
    currency: Currency
    basis: RatingBasis
    charges: tuple[RatedCharge, ...] = ()
    mode: RatingMode = RatingMode.AUTHORITATIVE
    at: float = 0.0

    @property
    def subtotal(self) -> Money:
        """The sum of every charge before charge-level reductions."""
        total = Money.zero(self.currency)
        for charge in self.charges:
            total = total + (charge.gross_amount or Money.zero(self.currency))
        return total

    @property
    def discount_total(self) -> Money:
        total = Money.zero(self.currency)
        for charge in self.charges:
            total = total + (charge.discount_amount or Money.zero(self.currency))
        return total

    @property
    def total(self) -> Money:
        total = Money.zero(self.currency)
        for charge in self.charges:
            total = total + charge.amount
        return total

    @property
    def charge_count(self) -> int:
        return len(self.charges)

    @property
    def is_zero(self) -> bool:
        return self.total.is_zero

    @property
    def is_preview(self) -> bool:
        return self.mode is RatingMode.PREVIEW

    @property
    def kinds(self) -> tuple[ChargeKind, ...]:
        return tuple(sorted({c.kind for c in self.charges}))

    @property
    def is_hybrid(self) -> bool:
        """More than one commercial shape in one period — the case that exists because real
        customers buy a platform fee *and* seats *and* usage together."""
        return len(self.kinds) > 1

    def charge(self, charge_id: str) -> RatedCharge | None:
        return next((c for c in self.charges if c.id == charge_id), None)

    def by_kind(self, kind: ChargeKind) -> tuple[RatedCharge, ...]:
        return tuple(c for c in self.charges if c.kind is kind)

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of the whole commercial outcome.

        Excludes `at` and `mode` for the same reason a charge digest excludes its timestamp:
        *when* rating ran and *why it was asked* are not part of what the customer owes. Two runs
        with equal digests billed the same thing, and that is checkable by comparing two
        strings."""
        return _digest(
            self.org_id,
            self.account_id,
            self.subscription_id,
            f"{self.period.start}:{self.period.end}",
            self.currency.value,
            self.basis.digest,
            str(self.total.minor),
            *[c.digest for c in self.charges],
        )

    @property
    def breaks_down_correctly(self) -> bool:
        return all(c.breaks_down_correctly for c in self.charges)


# ── runs, snapshots, history ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatingRun:
    """One attempt to rate one agreement for one period, and its verdict.

    The id is derived from the basis digest (see `derive_rating_run_id`), which makes the run
    *itself* idempotent: rating unchanged inputs twice is one row.

    `supersedes` records provenance when this run replaced a still-provisional earlier one.
    `adjusts` records it when the earlier run was already **finalized** — a distinction with
    teeth: a superseded run is a revision of something not yet billed, and an adjusted one is a
    difference from something that was. Conflating them would let an invoiced figure look
    revisable."""

    id: str
    org_id: str
    account_id: str
    subscription_id: str
    period: BillingPeriod
    currency: Currency
    basis_digest: str
    state: RatingState = RatingState.DRAFT
    sequence: int = 1
    result: RatingResult | None = None
    reason: RerateReason | None = None
    supersedes: str = ""
    adjusts: str = ""
    superseded_by: str = ""
    detail: str = ""
    rated_at: float = 0.0
    rated_by: str = ""
    finalized_at: float | None = None
    finalized_by: str = ""
    updated_at: float = 0.0

    @property
    def is_provisional(self) -> bool:
        return self.state in PROVISIONAL_RATING_STATES

    @property
    def is_settled(self) -> bool:
        return self.state in SETTLED_RATING_STATES

    @property
    def is_finalized(self) -> bool:
        return self.state is RatingState.FINALIZED

    @property
    def is_rerate(self) -> bool:
        return bool(self.supersedes or self.adjusts)

    @property
    def total(self) -> Money:
        return self.result.total if self.result else Money.zero(self.currency)

    @property
    def charge_count(self) -> int:
        return self.result.charge_count if self.result else 0

    @property
    def result_digest(self) -> str:
        return self.result.digest if self.result else ""


@dataclass(frozen=True, slots=True)
class RatingSnapshot:
    """An immutable, self-contained record of what a run decided, taken at finalization.

    Self-contained in the same sense a catalog, pricing, or subscription version is: it embeds
    the whole result, so reading it never depends on what a later run did. That is what makes
    "what exactly did we bill this customer for March, and how was it calculated?" answerable
    from one row years later."""

    id: str
    org_id: str
    account_id: str
    subscription_id: str
    rating_run_id: str
    period: BillingPeriod
    sequence: int = 1
    result: RatingResult | None = None
    result_digest: str = ""
    basis_digest: str = ""
    taken_at: float = 0.0
    taken_by: str = ""
    reason: str = ""

    @property
    def total(self) -> Money | None:
        return self.result.total if self.result else None

    @property
    def charge_count(self) -> int:
        return self.result.charge_count if self.result else 0


class DifferenceKind(StrEnum):
    """How one charge differs between two runs."""

    ADDED = "added"
    REMOVED = "removed"
    INCREASED = "increased"
    DECREASED = "decreased"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class RatingDifference:
    """One charge's difference between two runs, with enough of both sides to explain it."""

    kind: DifferenceKind
    comparison_key: tuple[str, str, str]
    description: str = ""
    before: Money | None = None
    after: Money | None = None
    delta: Money | None = None
    before_quantity: int = 0
    after_quantity: int = 0
    reason: str = ""

    @property
    def is_material(self) -> bool:
        return self.kind is not DifferenceKind.UNCHANGED


@dataclass(frozen=True, slots=True)
class RatingDelta:
    """The whole difference between two runs — the answer to "why is this month's figure not
    the one you quoted?".

    Produced by comparison, never by subtraction of two totals alone: a delta of zero across two
    materially different charge sets is a real and interesting outcome, and a single number would
    hide it."""

    org_id: str
    account_id: str
    subscription_id: str
    period: BillingPeriod
    before_run_id: str
    after_run_id: str
    currency: Currency
    differences: tuple[RatingDifference, ...] = ()
    total_before: Money | None = None
    total_after: Money | None = None
    at: float = 0.0

    @property
    def total_delta(self) -> Money:
        if self.total_before is None or self.total_after is None:
            return Money.zero(self.currency)
        return self.total_after - self.total_before

    @property
    def is_material(self) -> bool:
        return any(d.is_material for d in self.differences)

    @property
    def material(self) -> tuple[RatingDifference, ...]:
        return tuple(d for d in self.differences if d.is_material)

    @property
    def charges_changed(self) -> int:
        return len(self.material)


@dataclass(frozen=True, slots=True)
class ChargeExplanation:
    """One charge, in sentences.

    Generated from the charge's own components and provenance — never from a separate store of
    prose that could drift from the arithmetic it describes. If the explanation and the amount
    ever disagree, it is because the charge changed, and the explanation changes with it."""

    charge_id: str
    kind: ChargeKind
    description: str
    amount: Money
    lines: tuple[str, ...] = ()
    provenance_lines: tuple[str, ...] = ()

    @property
    def line_count(self) -> int:
        return len(self.lines)


@dataclass(frozen=True, slots=True)
class RatingExplanation:
    """A whole run, in sentences — what was charged, why, and from what."""

    rating_run_id: str
    subscription_id: str
    period: BillingPeriod
    currency: Currency
    total: Money
    charges: tuple[ChargeExplanation, ...] = ()
    basis_lines: tuple[str, ...] = ()

    @property
    def charge_count(self) -> int:
        return len(self.charges)


class RatingAction(StrEnum):
    """What happened to rating. The activity log's closed vocabulary."""

    COMMITMENT_DEFINED = "commitment.defined"
    COMMITMENT_RETIRED = "commitment.retired"
    RATED = "rating.rated"
    RERATED = "rating.rerated"
    FINALIZED = "rating.finalized"
    SUPERSEDED = "rating.superseded"
    DISCARDED = "rating.discarded"
    ADJUSTED = "rating.adjusted"
    SNAPSHOT_TAKEN = "rating.snapshot_taken"


@dataclass(frozen=True, slots=True)
class RatingActivity:
    """One append-only entry in rating's own history: who rated what, when, and what it came to.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log, this is rating's ordered, queryable history, and a billing screen should
    not have to filter the whole revenue stream to render "why did March change twice?"."""

    id: str
    org_id: str
    action: RatingAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    subscription_id: str = ""
    rating_run_id: str = ""
    period_start: float | None = None
    period_end: float | None = None
    total_minor: int = 0
    currency: Currency | None = None
    reason: str = ""
    detail: str = ""


# ── search + inspection ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatingSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same runs and the same
    query always come back in the same order, which is what a billing screen and a reproducible
    test both need."""

    text: str = ""  # case-insensitive substring over subscription, run id, detail
    account_id: str = ""
    subscription_id: str = ""
    states: tuple[RatingState, ...] = ()
    kinds: tuple[ChargeKind, ...] = ()
    reasons: tuple[RerateReason, ...] = ()
    period_start: float | None = None
    period_end: float | None = None
    min_total_minor: int | None = None
    max_total_minor: int | None = None
    rerates_only: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class RatingSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    runs: tuple[RatingRun, ...] = ()
    total: int = 0


@dataclass(frozen=True, slots=True)
class ChargeInspection:
    """A structural read of one charge: does it add up, is it fully attributed, is anything
    about it unexplainable? Used by the diagnostics surface and by finalization."""

    charge_id: str
    kind: ChargeKind
    amount: Money
    breaks_down_correctly: bool = True
    has_provenance: bool = True
    has_usage_attribution: bool = True
    component_count: int = 0
    discount_count: int = 0
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        return self.breaks_down_correctly and self.has_provenance


@dataclass(frozen=True, slots=True)
class RatingInspection:
    """A whole run, inspected. `is_sound` is what finalization requires: a run that cannot
    explain its own arithmetic must not become the figure on an invoice."""

    rating_run_id: str
    subscription_id: str
    period: BillingPeriod
    state: RatingState
    total: Money
    charges: tuple[ChargeInspection, ...] = ()
    basis_digest: str = ""
    result_digest: str = ""
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        return all(c.is_sound for c in self.charges)

    @property
    def unsound(self) -> tuple[str, ...]:
        return tuple(c.charge_id for c in self.charges if not c.is_sound)


@dataclass(frozen=True, slots=True)
class RerateVerdict:
    """Whether a period needs rating again, and why — a *read*, so asking is free of effect.

    `required` false with an existing run is the interesting answer: it means the inputs have not
    moved, and rating again would produce byte-identical output. Saying so explicitly is what
    lets a nightly job skip work instead of rewriting the same figure every night."""

    subscription_id: str
    period: BillingPeriod
    required: bool
    current_run_id: str = ""
    current_basis_digest: str = ""
    proposed_basis_digest: str = ""
    current_state: RatingState | None = None
    reason: RerateReason | None = None
    detail: str = ""

    @property
    def would_adjust_finalized(self) -> bool:
        """Whether acting on this verdict produces an *adjustment* rather than a revision —
        the case where a customer has already been billed and a credit note is the remedy."""
        return self.required and self.current_state is RatingState.FINALIZED


#: The reduction shapes that are a *fixed sum* rather than a proportion. The distinction drives
#: `DiscountRebasingPolicy`: a proportion scales correctly when multiplied by a quantity, and a
#: fixed sum does not.
FIXED_SUM_DISCOUNT_KINDS: frozenset[DiscountKind] = frozenset(
    {
        DiscountKind.FIXED_AMOUNT,
        DiscountKind.BUNDLE,
        DiscountKind.COMMITMENT,
    }
)


__all__ = [
    "EXTENDED_CHARGE_KINDS",
    "FIXED_SUM_DISCOUNT_KINDS",
    "PROVISIONAL_RATING_STATES",
    "RATING_LIFECYCLE",
    "SETTLED_RATING_STATES",
    "SHAPE_TO_CHARGE_KIND",
    "ChargeComponent",
    "ChargeExplanation",
    "ChargeInspection",
    "ChargeKind",
    "ChargeProvenance",
    "ComponentKind",
    "DifferenceKind",
    "IncludedUnitsSource",
    "MinimumCommitment",
    "RatedCharge",
    "RatingAction",
    "RatingActivity",
    "RatingBasis",
    "RatingDelta",
    "RatingDifference",
    "RatingExplanation",
    "RatingInspection",
    "RatingMode",
    "RatingResult",
    "RatingRun",
    "RatingSearchQuery",
    "RatingSearchResult",
    "RatingSnapshot",
    "RatingState",
    "RatingTrigger",
    "RerateReason",
    "RerateVerdict",
    "derive_charge_id",
    "derive_rating_run_id",
]
