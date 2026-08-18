"""Stateless structural validation for billing inputs (M9.2).

Hand-rolled and dependency-free (mirrors `product.validation` / `api.foundation.
validation`). Every `validate_*` method **never raises** — it returns
`BillingDiagnostics`; factories and services decide whether to fail fast on
error-severity findings (§A2.6).

Structural only: shape, format, money discipline, scope consistency, and the invariants
ADR-0023 makes non-negotiable (integer minor units, currency pairing, non-negative
quantities, contiguous price tiers, classified entitlement gates, bounded refunds).
Uniqueness and existence checks need repositories and therefore live in services — they
are not structural properties of a single input.

Deterministic: identical input always yields the identical, canonically-ordered findings.
"""

from __future__ import annotations

import re

from .catalog import FeatureKind, MeterDefinition, Plan
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .domain import Scope
from .entitlement import EntitlementSnapshot
from .invoicing import ChargeLine, Invoice
from .money import Currency, Money
from .pricing import Price, PriceBook, PriceShape
from .refunds import Refund, RefundPolicy
from .subscription import Subscription
from .tax import TaxProfile, TaxRegime

#: Codes: lowercase alphanumerics and single underscores/hyphens, 1–64 chars. Codes are
#: identifiers customers and invoices both see, so the shape is deliberately narrow.
_CODE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
#: GSTIN: 2-digit state code, 10-char PAN, entity digit, 'Z', checksum.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
#: EU VAT: 2-letter country prefix + 2–13 alphanumerics.
_VAT = re.compile(r"^[A-Z]{2}[0-9A-Z]{2,13}$")

_MAX_CODE_LEN = 64


def is_valid_code(code: str) -> bool:
    return bool(_CODE.match(code)) and len(code) <= _MAX_CODE_LEN


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _require_identity(out: list[Diagnostic], path: str, value: str, what: str) -> None:
    if not value:
        out.append(_err(path, C.EMPTY_IDENTITY, f"{what} must be non-empty", f"supply a {what}"))


class BillingValidator:
    """Stateless structural validator. Each method returns a diagnostics set."""

    # ── scope / identity ────────────────────────────────────────────────────────
    def validate_scope(self, scope: Scope, *, org_id: str) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "scope.org_id", scope.org_id, "org_id")
        if org_id and scope.org_id and scope.org_id != org_id:
            out.append(
                _err(
                    "scope.org_id",
                    C.CROSS_ORG_REFERENCE,
                    f"scope org {scope.org_id!r} differs from caller org {org_id!r}",
                    "org isolation is structural (§A11.2); never cross tenants",
                )
            )
        return BillingDiagnostics.of(out)

    # ── money ───────────────────────────────────────────────────────────────────
    def validate_amount(
        self,
        amount: Money,
        *,
        path: str,
        currency: Currency | None = None,
        allow_negative: bool = False,
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not allow_negative and amount.is_negative:
            out.append(
                _err(
                    path,
                    C.NEGATIVE_AMOUNT,
                    f"{path} must not be negative ({amount})",
                    "discounts and credits are expressed as negative *lines*, not negative charges",
                )
            )
        if currency is not None and amount.currency is not currency:
            out.append(
                _err(
                    f"{path}.currency",
                    C.CURRENCY_MISMATCH,
                    f"{path} is {amount.currency}, expected {currency}",
                    "a currency is fixed per subscription; conversion is presentation only",
                )
            )
        return BillingDiagnostics.of(out)

    # ── catalog ─────────────────────────────────────────────────────────────────
    def validate_meter(self, meter: MeterDefinition) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "meter.code", meter.code, "meter code")
        if meter.code and not is_valid_code(meter.code):
            out.append(
                _err("meter.code", C.INVALID_CODE, f"meter code {meter.code!r} is malformed")
            )
        if not meter.unit:
            out.append(_err("meter.unit", C.MISSING_REQUIRED_FIELD, "a billable unit is required"))
        if not meter.source:
            out.append(
                _err(
                    "meter.source",
                    C.MISSING_METER_SOURCE,
                    "a meter must name its one authoritative source",
                    "ADR-0023 M2: two sources for one meter is a defect, not a configuration",
                )
            )
        if not meter.display_name:
            out.append(_warn("meter.display_name", C.INVALID_NAME, "meter has no display name"))
        return BillingDiagnostics.of(out)

    def validate_plan(
        self,
        plan: Plan,
        *,
        known_features: frozenset[str] = frozenset(),
        known_meters: frozenset[str] = frozenset(),
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "plan.code", plan.code, "plan code")
        if plan.code and not is_valid_code(plan.code):
            out.append(_err("plan.code", C.INVALID_CODE, f"plan code {plan.code!r} is malformed"))
        if not plan.display_name:
            out.append(_warn("plan.display_name", C.INVALID_NAME, "plan has no display name"))
        seen: set[str] = set()
        for i, pf in enumerate(plan.features):
            if pf.feature_code in seen:
                out.append(
                    _err(
                        f"plan.features[{i}]",
                        C.DUPLICATE_CODE,
                        f"feature {pf.feature_code!r} listed twice",
                    )
                )
            seen.add(pf.feature_code)
            if known_features and pf.feature_code not in known_features:
                out.append(
                    _err(
                        f"plan.features[{i}]",
                        C.INVALID_CODE,
                        f"unknown feature {pf.feature_code!r}",
                    )
                )
            if pf.allowance is not None and pf.allowance < 0:
                out.append(
                    _err(
                        f"plan.features[{i}].allowance",
                        C.INVALID_QUANTITY,
                        "allowance must be >= 0",
                    )
                )
        dupes = {m for m in plan.metered_codes if plan.metered_codes.count(m) > 1}
        for m in sorted(dupes):
            out.append(_err("plan.metered_codes", C.DUPLICATE_METER, f"meter {m!r} listed twice"))
        if known_meters:
            for m in sorted(set(plan.metered_codes) - known_meters):
                out.append(_err("plan.metered_codes", C.UNKNOWN_METER, f"unknown meter {m!r}"))
        return BillingDiagnostics.of(out)

    # ── pricing ─────────────────────────────────────────────────────────────────
    def validate_price(
        self, price: Price, *, currency: Currency | None = None
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "price.code", price.code, "price code")
        if price.code and not is_valid_code(price.code):
            out.append(
                _err("price.code", C.INVALID_CODE, f"price code {price.code!r} is malformed")
            )
        if currency is not None and price.currency is not currency:
            out.append(
                _err(
                    "price.currency",
                    C.CURRENCY_MISMATCH,
                    f"price is {price.currency}, price book is {currency}",
                    "a price book is single-currency by construction",
                )
            )
        eff = price.effective
        if eff.effective_until is not None and eff.effective_until <= eff.effective_from:
            out.append(
                _err(
                    "price.effective",
                    C.INVALID_EFFECTIVE_RANGE,
                    "effective_until must be after effective_from",
                )
            )
        out.extend(self._validate_price_shape(price).findings)
        return BillingDiagnostics.of(out)

    def _validate_price_shape(self, price: Price) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        shape = price.shape
        if price.is_recurring or shape is PriceShape.ONE_OFF:
            if price.amount is None:
                out.append(
                    _err("price.amount", C.INVALID_PRICE_SHAPE, f"{shape.value} requires an amount")
                )
            elif price.amount.is_negative:
                out.append(
                    _err("price.amount", C.NEGATIVE_AMOUNT, "a price amount must not be negative")
                )
            if price.is_recurring and price.interval is None:
                out.append(
                    _err(
                        "price.interval",
                        C.INVALID_PRICE_SHAPE,
                        f"{shape.value} requires a billing interval",
                    )
                )
        if price.is_metered and not price.meter_code:
            out.append(
                _err("price.meter_code", C.INVALID_PRICE_SHAPE, f"{shape.value} must name a meter")
            )
        if (
            shape in (PriceShape.PER_UNIT, PriceShape.ALLOWANCE_OVERAGE)
            and price.unit_amount is None
        ):
            out.append(
                _err(
                    "price.unit_amount",
                    C.INVALID_PRICE_SHAPE,
                    f"{shape.value} requires a unit amount",
                )
            )
        if shape is PriceShape.ALLOWANCE_OVERAGE and price.included_units < 0:
            out.append(
                _err("price.included_units", C.INVALID_QUANTITY, "included_units must be >= 0")
            )
        if shape is PriceShape.PACKAGE and price.package_size < 1:
            out.append(_err("price.package_size", C.INVALID_QUANTITY, "package_size must be >= 1"))
        if shape is PriceShape.CREDIT and price.credit_units_per_amount < 1:
            out.append(
                _err(
                    "price.credit_units_per_amount",
                    C.INVALID_QUANTITY,
                    "credit conversion must be >= 1",
                )
            )
        if shape in (PriceShape.TIERED_GRADUATED, PriceShape.VOLUME):
            out.extend(self._validate_tiers(price).findings)
        elif price.tiers:
            out.append(
                _warn(
                    "price.tiers",
                    C.INVALID_PRICE_SHAPE,
                    f"{shape.value} ignores the declared tiers",
                )
            )
        return BillingDiagnostics.of(out)

    def _validate_tiers(self, price: Price) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not price.tiers:
            out.append(
                _err("price.tiers", C.INVALID_PRICE_SHAPE, f"{price.shape.value} requires tiers")
            )
            return BillingDiagnostics.of(out)
        previous: int | None = 0
        for i, tier in enumerate(price.tiers):
            if tier.unit_price.currency is not price.currency:
                out.append(
                    _err(
                        f"price.tiers[{i}].unit_price",
                        C.CURRENCY_MISMATCH,
                        "tier currency differs from price",
                    )
                )
            if tier.unit_price.is_negative:
                out.append(
                    _err(
                        f"price.tiers[{i}].unit_price", C.NEGATIVE_AMOUNT, "tier price must be >= 0"
                    )
                )
            if previous is None:
                out.append(
                    _err(
                        f"price.tiers[{i}]",
                        C.OVERLAPPING_PRICE_TIERS,
                        "no tier may follow the open-ended tier",
                        "the tier with up_to=None must be last",
                    )
                )
                continue
            if tier.up_to is not None and tier.up_to <= previous:
                out.append(
                    _err(
                        f"price.tiers[{i}].up_to",
                        C.NON_CONTIGUOUS_PRICE_TIERS,
                        f"tier bound {tier.up_to} must exceed the previous bound {previous}",
                        "tiers must be contiguous and ascending",
                    )
                )
            previous = tier.up_to
        if previous is not None:
            out.append(
                _warn(
                    "price.tiers",
                    C.NON_CONTIGUOUS_PRICE_TIERS,
                    "tiers are bounded; usage beyond the last tier is unpriced",
                    "add a final tier with up_to=None",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_price_book(self, book: PriceBook) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "price_book.code", book.code, "price book code")
        seen: set[tuple[str, int]] = set()
        for i, p in enumerate(book.prices):
            out.extend(self.validate_price(p, currency=book.currency).findings)
            key = (p.code, p.version)
            if key in seen:
                out.append(
                    _err(
                        f"price_book.prices[{i}]",
                        C.DUPLICATE_CODE,
                        f"price {p.code!r} v{p.version} listed twice",
                    )
                )
            seen.add(key)
            if p.price_book_id and p.price_book_id != book.id:
                out.append(
                    _err(
                        f"price_book.prices[{i}].price_book_id",
                        C.CROSS_ORG_REFERENCE,
                        "price belongs to another book",
                    )
                )
        return BillingDiagnostics.of(out)

    # ── subscriptions ───────────────────────────────────────────────────────────
    def validate_subscription(self, sub: Subscription) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "subscription.org_id", sub.org_id, "org_id")
        _require_identity(out, "subscription.account_id", sub.account_id, "account_id")
        _require_identity(out, "subscription.plan_code", sub.plan_code, "plan code")
        if sub.plan_version < 1:
            out.append(
                _err(
                    "subscription.plan_version", C.UNKNOWN_PLAN, "a pinned plan version is required"
                )
            )
        if sub.price_book_version < 1:
            out.append(
                _err(
                    "subscription.price_book_version",
                    C.UNKNOWN_PRICE,
                    "a pinned price book version is required",
                )
            )
        if not sub.items:
            out.append(
                _warn(
                    "subscription.items",
                    C.MISSING_REQUIRED_FIELD,
                    "subscription has no priced items",
                )
            )
        for i, item in enumerate(sub.items):
            if item.quantity < 0:
                out.append(
                    _err(
                        f"subscription.items[{i}].quantity",
                        C.INVALID_SEAT_COUNT,
                        "quantity must be >= 0",
                    )
                )
            if not item.price_code:
                out.append(
                    _err(
                        f"subscription.items[{i}].price_code",
                        C.EMPTY_IDENTITY,
                        "a price code is required",
                    )
                )
        if (
            sub.trial_end is not None
            and sub.current_period
            and sub.trial_end < sub.current_period.start
        ):
            out.append(
                _err(
                    "subscription.trial_end",
                    C.INVALID_PERIOD,
                    "trial ends before the period starts",
                )
            )
        return BillingDiagnostics.of(out)

    # ── entitlements ────────────────────────────────────────────────────────────
    def validate_snapshot(self, snapshot: EntitlementSnapshot) -> BillingDiagnostics:
        """ADR-0023 E3: **every gate must carry a classification.** An entitlement with no
        declared gate is an incomplete design and is reported as an error, not a warning."""
        out: list[Diagnostic] = []
        _require_identity(out, "snapshot.org_id", snapshot.org_id, "org_id")
        if snapshot.version < 1:
            out.append(
                _err("snapshot.version", C.INVALID_QUANTITY, "snapshot version must be >= 1")
            )
        for code in snapshot.unclassified_gates:
            out.append(
                _err(
                    f"snapshot.gates[{code}]",
                    C.UNCLASSIFIED_GATE,
                    f"entitlement {code!r} has no gate classification",
                    "declare fail_open / fail_closed / degrade (ADR-0023 E3)",
                )
            )
        seen: set[str] = set()
        for e in snapshot.entitlements:
            if e.code in seen:
                out.append(
                    _err(
                        f"snapshot.entitlements[{e.code}]", C.DUPLICATE_ENTITLEMENT, "granted twice"
                    )
                )
            seen.add(e.code)
            if e.allowance is not None and e.allowance < 0:
                out.append(
                    _err(
                        f"snapshot.entitlements[{e.code}].allowance",
                        C.INVALID_QUANTITY,
                        "allowance must be >= 0",
                    )
                )
            if e.kind is FeatureKind.QUOTA and e.allowance is None and not e.allows_overage:
                out.append(
                    _warn(
                        f"snapshot.entitlements[{e.code}]",
                        C.INVALID_QUANTITY,
                        "unlimited quota without declared overage",
                        "state an allowance or mark the plan as permitting overage",
                    )
                )
        if snapshot.max_staleness_seconds <= 0:
            out.append(
                _err(
                    "snapshot.max_staleness_seconds",
                    C.INVALID_QUANTITY,
                    "a positive staleness bound is required",
                    "enforcing planes need a bound to apply their fail-open/closed rule",
                )
            )
        return BillingDiagnostics.of(out)

    # ── invoicing ───────────────────────────────────────────────────────────────
    def validate_invoice(self, invoice: Invoice) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "invoice.org_id", invoice.org_id, "org_id")
        _require_identity(out, "invoice.account_id", invoice.account_id, "account_id")
        if not invoice.lines:
            out.append(
                _warn(
                    "invoice.lines",
                    C.EMPTY_INVOICE,
                    "invoice has no lines",
                    "zero-amount invoices are still issued (ADR-0023 IN7)",
                )
            )
        for i, line in enumerate(invoice.lines):
            out.extend(
                self.validate_charge_line(
                    line, currency=invoice.currency, path=f"invoice.lines[{i}]"
                ).findings
            )
        if invoice.is_issued and invoice.number is None:
            out.append(
                _err(
                    "invoice.number",
                    C.INVALID_INVOICE_NUMBER,
                    "an issued invoice must carry a number",
                    "numbering is gapless and allocated exactly once (ADR-0023 IN4)",
                )
            )
        if invoice.is_issued and invoice.issued_at is None:
            out.append(
                _err(
                    "invoice.issued_at",
                    C.MISSING_REQUIRED_FIELD,
                    "an issued invoice must carry its issue time",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_charge_line(
        self, line: ChargeLine, *, currency: Currency, path: str = "charge_line"
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if line.amount.currency is not currency:
            out.append(
                _err(
                    f"{path}.amount",
                    C.CURRENCY_MISMATCH,
                    f"line is {line.amount.currency}, invoice is {currency}",
                )
            )
        if line.quantity is not None and line.quantity.units < 0:
            out.append(_err(f"{path}.quantity", C.INVALID_QUANTITY, "quantity must be >= 0"))
        if not line.description:
            out.append(
                _warn(
                    f"{path}.description",
                    C.MISSING_REQUIRED_FIELD,
                    "line has no description",
                    "a customer must be able to read why they were charged",
                )
            )
        return BillingDiagnostics.of(out)

    # ── refunds ─────────────────────────────────────────────────────────────────
    def validate_refund(
        self, refund: Refund, *, captured: Money, already_refunded: Money, policy: RefundPolicy
    ) -> BillingDiagnostics:
        """Bounded by construction: never more than captured, never duplicated, and never
        self-approved (separation of duties, ADR-0023 PY9)."""
        out: list[Diagnostic] = []
        out.extend(
            self.validate_amount(
                refund.amount, path="refund.amount", currency=captured.currency
            ).findings
        )
        if refund.amount.currency is captured.currency:
            if (already_refunded + refund.amount).minor > captured.minor:
                out.append(
                    _err(
                        "refund.amount",
                        C.REFUND_EXCEEDS_CAPTURED,
                        f"refund {refund.amount} plus prior {already_refunded} "
                        f"exceeds captured {captured}",
                        "a refund can never exceed what was captured",
                    )
                )
            if not policy.allow_partial and refund.amount.minor != captured.minor:
                out.append(
                    _err(
                        "refund.amount",
                        C.INVALID_QUANTITY,
                        "partial refunds are not permitted by policy",
                    )
                )
        if not refund.request_key:
            out.append(
                _err(
                    "refund.request_key",
                    C.EMPTY_IDENTITY,
                    "an idempotency key is required",
                    "one refund per request identity — never a duplicate",
                )
            )
        if refund.approval and refund.approval.approver_id == refund.requester_id:
            out.append(
                _err(
                    "refund.approval.approver_id",
                    C.CROSS_ORG_REFERENCE,
                    "the approver must differ from the requester",
                    "separation of duties is required above the policy threshold",
                )
            )
        if policy.requires_approval(refund.amount) and refund.approval is None:
            out.append(
                _warn(
                    "refund.approval",
                    C.MISSING_REQUIRED_FIELD,
                    "this amount requires approval before processing",
                )
            )
        return BillingDiagnostics.of(out)

    # ── tax ─────────────────────────────────────────────────────────────────────
    def validate_tax_profile(self, profile: TaxProfile) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _require_identity(out, "tax_profile.org_id", profile.org_id, "org_id")
        if profile.regime is not TaxRegime.NONE and not profile.country:
            out.append(
                _err(
                    "tax_profile.country",
                    C.MISSING_TAX_PROFILE,
                    "a country is required to determine tax",
                )
            )
        if profile.regime is TaxRegime.INDIA_GST and not profile.place_of_supply:
            out.append(
                _err(
                    "tax_profile.place_of_supply",
                    C.MISSING_TAX_PROFILE,
                    "GST needs a place of supply to split CGST/SGST vs IGST",
                )
            )
        for i, reg in enumerate(profile.registrations):
            path = f"tax_profile.registrations[{i}].number"
            if reg.regime is TaxRegime.INDIA_GST and not _GSTIN.match(reg.number):
                out.append(
                    _err(path, C.INVALID_TAX_REGISTRATION, f"GSTIN {reg.number!r} is malformed")
                )
            elif reg.regime in (TaxRegime.EU_VAT, TaxRegime.UK_VAT) and not _VAT.match(reg.number):
                out.append(
                    _err(path, C.INVALID_TAX_REGISTRATION, f"VAT id {reg.number!r} is malformed")
                )
            if not reg.validated:
                out.append(
                    _warn(
                        path,
                        C.INVALID_TAX_REGISTRATION,
                        "registration has not been validated",
                        "validation is an adapter call; its result is recorded, never assumed",
                    )
                )
        return BillingDiagnostics.of(out)
