"""Metering rules (M9.11) — validation, ingestion, normalization, aggregation, sealing, replay.

Every metering *decision* lives here: what makes a meter valid, whether an event may become
commercial usage, how a raw signal becomes billable units, where a late event lands, how a window
is folded, when a period may seal, whether a replay reproduced what was sealed, and what
reconciliation found. Layers above coordinate these; they never re-decide.

Validation follows the Foundation discipline exactly — it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically.

Five disciplines, each load-bearing for a domain where errors are silent:

* **Nothing here measures.** No clock, no socket, no runtime, no metric producer. A `SourceEvent`
  arrives already decoded; this module decides what it *means commercially* and nothing else
  (ADR-0023 M1).
* **Nothing here reads a clock.** Every temporal decision takes `at` as an argument, which is what
  makes replaying a period in 2027 produce what it produced in 2026 (§A2.4).
* **Nothing here mutates.** Every function returns new values. There is no correction-in-place
  anywhere, because a billable fact that could be edited would make an invoice unexplainable.
* **No float touches a quantity.** Normalization is an exact integer ratio with a declared
  rounding; a rounding difference in the eighth decimal is a revenue incident nobody can
  reproduce.
* **Ingestion is a pure function.** `UsageIngestionService.decide` takes the event, the meter, the
  period, and whether the record already exists, and returns a decision. It performs no I/O, so
  the same inputs always give the same outcome — the property "deterministic replay" rests on.

Pure: no I/O, no clock read, no persistence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from core.fsm import StateMachineDefinition

from .catalog import MeterAggregation
from .commercial_metering import (
    ADJUSTING_DISPOSITIONS,
    METER_LIFECYCLE,
    QUARANTINE_LIFECYCLE,
    USAGE_PERIOD_LIFECYCLE,
    WINDOW_SECONDS,
    CommercialMeter,
    CommercialUsage,
    CorrectionKind,
    DiscrepancyKind,
    LateArrivalDisposition,
    MeteringActivity,
    MeterState,
    MeterTrigger,
    Normalization,
    PeriodState,
    PeriodTrigger,
    QuarantineEntry,
    QuarantineReason,
    QuarantineState,
    QuarantineTrigger,
    ReplayResult,
    SourceEvent,
    UsageAggregateSnapshot,
    UsageCorrection,
    UsageDiscrepancy,
    UsageExport,
    UsageExportRow,
    UsagePeriod,
    UsageProvenance,
    UsageReconciliation,
    UsageSearchQuery,
    UsageSearchResult,
    UsageSnapshot,
    UsageWindow,
    WindowGranularity,
    derive_usage_id,
)
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .domain import BillingPeriod, Quantity, Scope
from .errors import BillingConflict, InvalidBillingTransition
from .metering import UsageStatus, fold
from .money import Rounding

#: Beyond this many pending quarantined events, the backlog is itself the incident: unattributed
#: usage that nobody is working through is revenue leakage with extra steps.
QUARANTINE_BACKLOG_THRESHOLD = 100

#: How far into the future an event may claim to have been observed before it is malformed rather
#: than merely early. Small clock skew between producers is normal; an hour is not.
MAX_CLOCK_SKEW_SECONDS = 300.0


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def _hash(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


# ── normalization ───────────────────────────────────────────────────────────────


class UsageNormalizer:
    """Raw signal → billable units. Exact integer arithmetic with a declared rounding.

    **No float appears in this class.** `raw * numerator / denominator` is computed with integer
    division and the meter's declared rounding applied explicitly, so the result is identical on
    every machine, every Python build, and every replay. A float would be *nearly* identical,
    which in billing is the same as wrong — and wrong in a way that only shows up as a customer
    dispute months later."""

    def normalize(self, raw_units: int, normalization: Normalization) -> int:
        if raw_units < 0:
            raise BillingConflict("a raw signal cannot be negative")
        if not normalization.is_valid:
            raise BillingConflict(
                f"invalid normalization {normalization.numerator}/{normalization.denominator}"
            )
        numerator = raw_units * normalization.numerator
        denominator = normalization.denominator
        whole, remainder = divmod(numerator, denominator)
        if remainder == 0:
            return whole
        return self._round(whole, remainder, denominator, normalization.rounding)

    @staticmethod
    def _round(whole: int, remainder: int, denominator: int, rounding: Rounding) -> int:
        """The declared rounding, applied to an exact remainder rather than to a float.

        HALF_EVEN is the one that matters at volume: rounding halves consistently upward biases
        every meter in VoxVoice's favour, which is both a trust problem and, in some
        jurisdictions, a compliance one."""
        if rounding is Rounding.FLOOR:
            return whole
        if rounding is Rounding.CEILING:
            return whole + 1
        doubled = remainder * 2
        if doubled > denominator:
            return whole + 1
        if doubled < denominator:
            return whole
        if rounding is Rounding.HALF_EVEN:
            return whole if whole % 2 == 0 else whole + 1
        return whole + 1  # HALF_UP

    def quantity(self, raw_units: int, meter: CommercialMeter) -> Quantity:
        return Quantity(self.normalize(raw_units, meter.normalization), meter.unit)


# ── late arrival ────────────────────────────────────────────────────────────────


class LateArrivalPolicy:
    """Where a late event lands. One declared answer per event, never a call-site judgement.

    The policy is deliberately conservative in one direction only: an event is **never silently
    dropped and never silently backdated**. Inside the meter's window it joins its own period;
    outside it, or once that period is sealed, it becomes an explicitly labelled adjustment in
    the current period (Blueprint §7, ADR-0023 M7)."""

    def disposition(
        self,
        meter: CommercialMeter,
        event: SourceEvent,
        *,
        period: BillingPeriod,
        period_state: PeriodState,
    ) -> LateArrivalDisposition:
        if period_state is PeriodState.SEALED:
            return LateArrivalDisposition.PERIOD_SEALED
        if period.contains(event.observed_at):
            if meter.within_late_window(period, event.received_at):
                return LateArrivalDisposition.IN_PERIOD
            return LateArrivalDisposition.LATE_OUT_OF_WINDOW
        if meter.within_late_window(period, event.received_at):
            return LateArrivalDisposition.LATE_IN_WINDOW
        return LateArrivalDisposition.LATE_OUT_OF_WINDOW

    def landing_period(
        self,
        disposition: LateArrivalDisposition,
        *,
        original: BillingPeriod,
        current: BillingPeriod | None,
    ) -> BillingPeriod:
        """Which period the record actually attaches to.

        An adjusting disposition with no current period falls back to the original rather than
        inventing one — inventing a period is how usage ends up billed in a month that never
        existed."""
        if disposition in ADJUSTING_DISPOSITIONS and current is not None and current != original:
            return current
        return original

    @staticmethod
    def requires_open_period(disposition: LateArrivalDisposition) -> bool:
        """Whether this disposition needs an *open* period to land in.

        An adjustment must never land in the period it is adjusting: that period is sealed, and
        writing into it would break the seal digest — the exact retroactive mutation ADR-0023 M7
        forbids. With no open period to receive it, the event is quarantined and stays visible
        until one exists; it is neither billed into a frozen period nor dropped."""
        return disposition in ADJUSTING_DISPOSITIONS

    @staticmethod
    def status_for(disposition: LateArrivalDisposition) -> UsageStatus:
        return (
            UsageStatus.ADJUSTMENT
            if disposition in ADJUSTING_DISPOSITIONS
            else UsageStatus.RECORDED
        )


# ── attribution ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Attribution:
    """The commercial identity a source event resolved to, or the reason it did not.

    Attribution is **total**: every event resolves to exactly one billing account or is
    quarantined (ADR-0023 M6). There is no third outcome, and the type makes that structural —
    `account_id` and `reason` are mutually exclusive by construction."""

    account_id: str = ""
    scope: Scope | None = None
    subscription_id: str = ""
    reason: QuarantineReason | None = None
    detail: str = ""

    @property
    def is_resolved(self) -> bool:
        return bool(self.account_id) and self.reason is None


class AttributionService:
    """Resolves a scope to a billing account. Decides; looks nothing up.

    The account map is **passed in** rather than fetched, which is what keeps this pure and keeps
    the metering domain from importing the accounts domain. The Application supplies the mapping
    from the repositories it already holds."""

    def attribute(
        self,
        scope: Scope | None,
        *,
        accounts_by_scope: dict[str, str],
        subscriptions_by_account: dict[str, str] | None = None,
    ) -> Attribution:
        """Resolve narrowest-first: agent → project → workspace → organization.

        Narrowest-first because a customer who has attached billing at the project level means
        it; falling back to the organization would silently bill the wrong entity in exactly the
        reseller and enterprise arrangements where it matters most."""
        if scope is None:
            return Attribution(
                reason=QuarantineReason.UNATTRIBUTABLE, detail="the event carries no scope"
            )
        for candidate in (scope.agent_id, scope.project_id, scope.workspace_id, scope.org_id):
            if candidate and candidate in accounts_by_scope:
                account_id = accounts_by_scope[candidate]
                return Attribution(
                    account_id=account_id,
                    scope=scope,
                    subscription_id=(subscriptions_by_account or {}).get(account_id, ""),
                )
        return Attribution(
            reason=QuarantineReason.UNATTRIBUTABLE,
            detail=f"no billing account is attached to {scope.narrowest_id!r} or any parent scope",
        )


# ── ingestion ───────────────────────────────────────────────────────────────────


class IngestionOutcome(StrEnum):
    """What ingesting one source event did.

    `DUPLICATE` is a **success**, not an error: at-least-once delivery means replays are normal,
    and treating one as a failure would make every retry look like an incident."""

    RECORDED = "recorded"
    ADJUSTED = "adjusted"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class IngestionDecision:
    """The pure outcome of deciding what one source event becomes.

    Carries everything the Application needs to act and nothing it needs to think about. The
    separation matters: because this is a pure function of (event, meter, period, already-seen),
    the same inputs always produce the same decision — which is precisely what makes replay
    deterministic and what lets a test prove it rather than assert it."""

    outcome: IngestionOutcome
    usage: CommercialUsage | None = None
    reason: QuarantineReason | None = None
    detail: str = ""
    disposition: LateArrivalDisposition = LateArrivalDisposition.IN_PERIOD

    @property
    def is_recorded(self) -> bool:
        return self.outcome in (IngestionOutcome.RECORDED, IngestionOutcome.ADJUSTED)

    @property
    def is_duplicate(self) -> bool:
        return self.outcome is IngestionOutcome.DUPLICATE

    @property
    def is_quarantined(self) -> bool:
        return self.outcome is IngestionOutcome.QUARANTINED


class UsageIngestionService:
    """The correctness core: source event → commercial usage, or a recorded reason why not.

    **Pure.** No I/O, no clock, no persistence. `already_recorded` and the attribution map are
    inputs, so this function's output depends only on its arguments — which is what makes
    "reprocessing a period yields identical usage records" a checkable claim.

    The decision order is deliberate, cheapest and most-certain refusals first:

    1. is the event well formed?
    2. does a meter exist for it, and is that meter the *authoritative* counter for this source?
    3. is the meter still ingestible?
    4. does the event attribute to exactly one billing account?
    5. has this `(event, meter)` already been recorded? — the exactly-once gate
    6. where does it land, given lateness and the period's state?

    Step 5 sits *after* attribution on purpose: a duplicate of an unattributable event should be
    reported as unattributable, not silently swallowed as a duplicate, or the quarantine backlog
    would under-count the real problem."""

    def __init__(
        self,
        normalizer: UsageNormalizer | None = None,
        late_policy: LateArrivalPolicy | None = None,
        attribution: AttributionService | None = None,
    ) -> None:
        self._normalizer = normalizer or UsageNormalizer()
        self._late = late_policy or LateArrivalPolicy()
        self._attribution = attribution or AttributionService()

    @property
    def attribution(self) -> AttributionService:
        """The attribution service this ingestion decides with.

        Exposed so a caller that must know the billing account *before* deciding — to read the
        period's sealing state, which is an input to the decision — resolves it with exactly the
        same service the decision will use. Resolving it a second way is how an event ends up
        attributed to one account and period-checked against another."""
        return self._attribution

    @property
    def normalizer(self) -> UsageNormalizer:
        """The normalizer this service decides with.

        Exposed so a caller computing *expected* usage for reconciliation normalizes exactly as
        ingestion did. Recomputing it with a second normalizer is how a reconciliation ends up
        reporting a discrepancy that is really a difference between two rounding
        implementations."""
        return self._normalizer

    def decide(
        self,
        event: SourceEvent,
        *,
        org_id: str,
        meter: CommercialMeter | None,
        raw_units: int,
        raw_unit: str,
        scope: Scope | None,
        period: BillingPeriod,
        period_state: PeriodState = PeriodState.OPEN,
        current_period: BillingPeriod | None = None,
        accounts_by_scope: dict[str, str] | None = None,
        subscriptions_by_account: dict[str, str] | None = None,
        already_recorded: bool = False,
        ingested_at: float = 0.0,
        ingest_run_id: str = "",
        replayed: bool = False,
    ) -> IngestionDecision:
        malformed = self._malformed(event, raw_units, ingested_at)
        if malformed is not None:
            return malformed

        if meter is None:
            return self._quarantine(
                QuarantineReason.UNKNOWN_METER,
                f"no meter counts {event.event_type!r} from {event.source.value}",
            )
        if not meter.accepts(event.source, event.event_type):
            return self._quarantine(
                QuarantineReason.NON_AUTHORITATIVE_SOURCE,
                f"meter {meter.code!r} counts {meter.source.value}, not {event.source.value}; "
                "exactly one source is authoritative per meter",
            )
        if meter.is_retired:
            return self._quarantine(
                QuarantineReason.RETIRED_METER,
                f"meter {meter.pin} is retired and no longer counts",
            )
        if raw_unit and meter.normalization.is_identity and raw_unit != meter.unit:
            return self._quarantine(
                QuarantineReason.UNIT_MISMATCH,
                f"event is in {raw_unit!r}, meter counts {meter.unit!r}, and no normalization "
                "is declared",
            )

        attribution = self._attribution.attribute(
            scope,
            accounts_by_scope=accounts_by_scope or {},
            subscriptions_by_account=subscriptions_by_account,
        )
        if not attribution.is_resolved:
            return self._quarantine(
                attribution.reason or QuarantineReason.UNATTRIBUTABLE, attribution.detail
            )

        # The exactly-once gate. Everything above is a judgement about the event; this is the
        # single fact that makes at-least-once delivery safe.
        if already_recorded:
            return IngestionDecision(
                outcome=IngestionOutcome.DUPLICATE,
                detail=f"({event.event_id}, {meter.code}) is already recorded",
            )

        disposition = self._late.disposition(meter, event, period=period, period_state=period_state)
        landing = self._late.landing_period(disposition, original=period, current=current_period)
        quantity = self._normalizer.quantity(raw_units, meter)
        usage = CommercialUsage(
            id=derive_usage_id(org_id, event.event_id, meter.code),
            org_id=org_id,
            account_id=attribution.account_id,
            meter_code=meter.code,
            meter_version=meter.version,
            quantity=quantity,
            scope=attribution.scope or scope,  # type: ignore[arg-type]
            period=landing,
            source=event,
            provenance=UsageProvenance(
                meter_pin=meter.pin,
                source_kind=event.source,
                ingested_at=ingested_at,
                ingest_run_id=ingest_run_id,
                normalization=meter.normalization,
                raw_units=raw_units,
                raw_unit=raw_unit or meter.unit,
                disposition=disposition,
                replayed=replayed,
            ),
            status=self._late.status_for(disposition),
            subscription_id=attribution.subscription_id,
            recorded_at=ingested_at,
        )
        return IngestionDecision(
            outcome=(
                IngestionOutcome.ADJUSTED
                if disposition in ADJUSTING_DISPOSITIONS
                else IngestionOutcome.RECORDED
            ),
            usage=usage,
            disposition=disposition,
        )

    def _malformed(self, event: SourceEvent, raw_units: int, at: float) -> IngestionDecision | None:
        if not event.event_id:
            return self._quarantine(
                QuarantineReason.MALFORMED_EVENT,
                "the event carries no id, so it cannot be counted exactly once",
            )
        if raw_units < 0:
            return self._quarantine(
                QuarantineReason.INVALID_QUANTITY, f"negative raw signal {raw_units}"
            )
        if at and event.observed_at > at + MAX_CLOCK_SKEW_SECONDS:
            return self._quarantine(
                QuarantineReason.FUTURE_EVENT,
                f"observed at {event.observed_at}, which is beyond acceptable clock skew",
            )
        return None

    @staticmethod
    def _quarantine(reason: QuarantineReason, detail: str) -> IngestionDecision:
        return IngestionDecision(outcome=IngestionOutcome.QUARANTINED, reason=reason, detail=detail)


# ── validation ──────────────────────────────────────────────────────────────────


class MeteringValidator:
    """Structural and referential validation for the metering domain. Never raises."""

    def validate_meter(self, meter: CommercialMeter) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not meter.org_id:
            out.append(_err("meter.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not meter.code:
            out.append(_err("meter.code", C.EMPTY_IDENTITY, "a meter must have a code"))
        if not meter.unit:
            out.append(
                _err(
                    "meter.unit",
                    C.INVALID_UNIT,
                    "a meter must declare the unit it counts in",
                    "the unit is what makes a quantity meaningful to rating and to a customer",
                )
            )
        if meter.version < 1:
            out.append(_err("meter.version", C.INVALID_CODE, "versions start at 1 and increase"))
        if not meter.normalization.is_valid:
            out.append(
                _err(
                    "meter.normalization",
                    C.INVALID_NORMALIZATION,
                    f"{meter.normalization.numerator}/{meter.normalization.denominator} is not a "
                    "positive ratio",
                    "normalization is exact integer arithmetic; no float touches a quantity",
                )
            )
        if meter.late_arrival_window_seconds < 0:
            out.append(
                _err(
                    "meter.late_arrival_window_seconds",
                    C.INVALID_LATE_ARRIVAL_WINDOW,
                    "a late-arrival window cannot be negative",
                )
            )
        elif meter.late_arrival_window_seconds == 0:
            out.append(
                _warn(
                    "meter.late_arrival_window_seconds",
                    C.INVALID_LATE_ARRIVAL_WINDOW,
                    "a zero window makes every late event an adjustment",
                    "intended for meters that must never be backdated; unusual otherwise",
                )
            )
        for dupe in _duplicates(meter.accepted_event_types):
            out.append(
                _err(
                    "meter.accepted_event_types",
                    C.DUPLICATE_CODE,
                    f"event type {dupe!r} is listed twice",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_meters(self, meters: Sequence[CommercialMeter]) -> BillingDiagnostics:
        """Cross-meter coherence, including the one that prevents double counting: two live
        meters must not claim the same `(source, event type)`."""
        out: list[Diagnostic] = []
        for dupe in _duplicates(m.code for m in meters):
            out.append(
                _err(
                    "meters",
                    C.DUPLICATE_METER_CODE,
                    f"meter code {dupe!r} is defined more than once",
                    "changing a meter publishes a new version of it, not a second meter",
                )
            )
        # Claimants include **drafts**, not just ingestible meters. Defect found at M9.11: an
        # earlier draft compared only live meters, so a second meter claiming a taken signal was
        # accepted while it was a draft and nothing re-checked at activation — letting two live
        # meters count the same event. Only a retired meter releases its claim.
        live = [m for m in meters if not m.is_retired]
        for i, first in enumerate(live):
            for second in live[i + 1 :]:
                if first.source is not second.source:
                    continue
                shared = self._shared_types(first, second)
                if shared:
                    out.append(
                        _err(
                            "meters",
                            C.NON_AUTHORITATIVE_SOURCE,
                            f"meters {first.code!r} and {second.code!r} both count "
                            f"{sorted(shared) or [first.source.value]} from "
                            f"{first.source.value}",
                            "exactly one meter is authoritative per signal, or the same usage is "
                            "billed twice",
                        )
                    )
        for meter in meters:
            out.extend(self.validate_meter(meter).findings)
        return BillingDiagnostics.of(out)

    @staticmethod
    def _shared_types(first: CommercialMeter, second: CommercialMeter) -> set[str]:
        """An empty allow-list means "any type from this source", which overlaps with
        everything — the case a naive set intersection would miss entirely."""
        if not first.accepted_event_types or not second.accepted_event_types:
            return set(first.accepted_event_types) | set(second.accepted_event_types) or {"*"}
        return set(first.accepted_event_types) & set(second.accepted_event_types)

    def validate_event(self, event: SourceEvent, *, at: float | None = None) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not event.event_id:
            out.append(
                _err(
                    "event.event_id",
                    C.EMPTY_SOURCE_EVENT,
                    "a source event must carry an id",
                    "the id is the exactly-once key; without it billing cannot be idempotent",
                )
            )
        if not event.event_type:
            out.append(_err("event.event_type", C.EMPTY_SOURCE_EVENT, "an event type is required"))
        if event.observed_at <= 0:
            out.append(
                _err("event.observed_at", C.EMPTY_SOURCE_EVENT, "an observation time is required")
            )
        if event.received_at and event.received_at < event.observed_at:
            out.append(
                _warn(
                    "event.received_at",
                    C.EMPTY_SOURCE_EVENT,
                    "the event claims to have been delivered before it was observed",
                    "usually producer clock skew; lateness is clamped to zero",
                )
            )
        if at is not None and event.observed_at > at + MAX_CLOCK_SKEW_SECONDS:
            out.append(
                _err(
                    "event.observed_at",
                    C.FUTURE_SOURCE_EVENT,
                    "the event is observed too far in the future to be counted",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_usage(self, usage: CommercialUsage) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not usage.org_id:
            out.append(_err("usage.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not usage.account_id and usage.status is not UsageStatus.QUARANTINED:
            out.append(
                _err(
                    "usage.account_id",
                    C.UNATTRIBUTABLE_USAGE,
                    "billable usage must resolve to exactly one billing account",
                    "unattributable usage is quarantined, never billed and never dropped",
                )
            )
        if not usage.source.event_id:
            out.append(
                _err(
                    "usage.source.event_id",
                    C.EMPTY_SOURCE_EVENT,
                    "usage must name the source event it was derived from",
                )
            )
        if usage.meter_version < 1:
            out.append(
                _err(
                    "usage.meter_version",
                    C.METERING_PROVENANCE_INTEGRITY,
                    "usage pins the meter *version* it was counted under",
                    "the pin is what makes an old invoice explainable after the meter moves on",
                )
            )
        if usage.provenance.meter_pin != usage.meter_pin:
            out.append(
                _err(
                    "usage.provenance.meter_pin",
                    C.METERING_PROVENANCE_INTEGRITY,
                    f"provenance says {usage.provenance.meter_pin}, record says {usage.meter_pin}",
                )
            )
        if usage.quantity.unit and usage.provenance.raw_unit == "":
            out.append(
                _warn(
                    "usage.provenance.raw_unit",
                    C.METERING_PROVENANCE_INTEGRITY,
                    "no raw unit recorded, so the normalization cannot be re-derived",
                )
            )
        if usage.status is UsageStatus.ADJUSTMENT and not usage.provenance.was_late:
            out.append(
                _warn(
                    "usage.status",
                    C.INVALID_CORRECTION,
                    "an adjustment whose provenance says it arrived on time",
                )
            )
        expected = derive_usage_id(usage.org_id, usage.source.event_id, usage.meter_code)
        if usage.id != expected:
            out.append(
                _err(
                    "usage.id",
                    C.METERING_PROVENANCE_INTEGRITY,
                    "the usage id is not the derived identity of its source event",
                    "ids are derived so that replay reproduces byte-identical usage",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_usage_set(self, usage: Sequence[CommercialUsage]) -> BillingDiagnostics:
        """The one invariant that cannot be recovered from: **no two records for one source
        event**. Everything else in metering can be re-run; a double-billed event cannot be
        un-billed without a credit note and a conversation."""
        out: list[Diagnostic] = []
        seen: dict[tuple[str, str], str] = {}
        for record in usage:
            key = record.idempotency_key
            if key in seen:
                out.append(
                    _err(
                        f"usage[{record.id}]",
                        C.DUPLICATE_USAGE_RECORD,
                        f"source event {key[0]!r} already produced usage {seen[key]!r} for meter "
                        f"{key[1]!r}",
                        "exactly-once was violated; this is a financial-integrity incident",
                    )
                )
            else:
                seen[key] = record.id
        return BillingDiagnostics.of(out)

    def validate_correction(self, correction: UsageCorrection) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not correction.corrects_usage_id:
            out.append(
                _err(
                    "correction.corrects_usage_id",
                    C.INVALID_CORRECTION,
                    "a correction must name the usage it corrects",
                )
            )
        if not correction.unit:
            out.append(_err("correction.unit", C.INVALID_UNIT, "a unit is required"))
        if correction.delta_units == 0:
            out.append(
                _err(
                    "correction.delta_units",
                    C.INVALID_CORRECTION,
                    "a correction of zero changes nothing",
                )
            )
        if not correction.reason and not correction.reason_code:
            out.append(
                _err(
                    "correction.reason",
                    C.INVALID_CORRECTION,
                    "a correction must say why",
                    "an unexplained change to a billed figure is indistinguishable from an error",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_period(self, period: UsagePeriod) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not period.org_id:
            out.append(_err("usage_period.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not period.account_id:
            out.append(
                _err(
                    "usage_period.account_id",
                    C.EMPTY_IDENTITY,
                    "a usage period belongs to a billing account",
                )
            )
        if period.is_sealed and not period.seal_digest:
            out.append(
                _err(
                    "usage_period.seal_digest",
                    C.SEAL_DIGEST_MISMATCH,
                    "a sealed period must record the digest of what it sealed",
                    "without it, a later replay cannot prove it reproduced the same usage",
                )
            )
        if period.is_sealed and period.sealed_at is None:
            out.append(
                _err(
                    "usage_period.sealed_at",
                    C.INVALID_USAGE_PERIOD,
                    "a sealed period must record when it was sealed",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_quarantine_backlog(self, pending: Sequence[QuarantineEntry]) -> BillingDiagnostics:
        """A backlog is itself the incident: unattributed usage nobody is working through is
        revenue leakage with extra steps."""
        if len(pending) <= QUARANTINE_BACKLOG_THRESHOLD:
            return BillingDiagnostics.empty()
        return BillingDiagnostics.of(
            [
                _warn(
                    "quarantine",
                    C.QUARANTINE_BACKLOG,
                    f"{len(pending)} events are quarantined and unresolved",
                    "each one is usage that was neither billed nor discarded",
                )
            ]
        )


# ── lifecycles ──────────────────────────────────────────────────────────────────


class MeterLifecycleService:
    """Applies meter lifecycle triggers through the shared FSM. Illegal edges are rejected with a
    typed error, never coerced into an adjacent state."""

    def __init__(self, definition: StateMachineDefinition = METER_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: MeterState, trigger: MeterTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: MeterState, trigger: MeterTrigger) -> MeterState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"meter cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: MeterState) -> tuple[MeterTrigger, ...]:
        return tuple(t for t in MeterTrigger if self.can(current, t))

    def apply(self, meter: CommercialMeter, trigger: MeterTrigger, *, at: float) -> CommercialMeter:
        state = self.next_state(meter.state, trigger)
        changes: dict = {"state": state, "updated_at": at}
        if trigger is MeterTrigger.ACTIVATE:
            changes["activated_at"] = meter.activated_at or at
        if trigger is MeterTrigger.RETIRE:
            changes["retired_at"] = at
        return replace(meter, **changes)

    def guard_no_conflict(self, meter: CommercialMeter, others: Sequence[CommercialMeter]) -> None:
        """Refuse to activate a meter that would then share a signal with another live one.

        Definition-time validation catches the common case; this catches the one it cannot —
        two drafts created before either was activated. Activation is the moment the claim
        becomes real, so it is the moment that must also be checked."""
        findings = MeteringValidator().validate_meters(
            [*[m for m in others if m.id != meter.id], meter]
        )
        conflicting = [f for f in findings.errors if f.code is C.NON_AUTHORITATIVE_SOURCE]
        if conflicting:
            raise BillingConflict(conflicting[0].message)

    def guard_ingestible(self, meter: CommercialMeter) -> None:
        if not meter.is_ingestible:
            raise BillingConflict(
                f"meter {meter.pin} is {meter.state.value} and does not count usage"
            )


class UsagePeriodLifecycleService:
    """Applies usage-period triggers. **Sealing is a one-way door** — the FSM has no edge out of
    `sealed`, and this service adds no way around it either."""

    def __init__(self, definition: StateMachineDefinition = USAGE_PERIOD_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: PeriodState, trigger: PeriodTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: PeriodState, trigger: PeriodTrigger) -> PeriodState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"usage period cannot {trigger.value} from {current.value}; legal: {legal}"
            )
        return edge.target

    def allowed(self, current: PeriodState) -> tuple[PeriodTrigger, ...]:
        return tuple(t for t in PeriodTrigger if self.can(current, t))

    def apply(
        self,
        period: UsagePeriod,
        trigger: PeriodTrigger,
        *,
        at: float,
        seal_digest: str = "",
        sealed_by: str = "",
    ) -> UsagePeriod:
        state = self.next_state(period.state, trigger)
        changes: dict = {"state": state, "updated_at": at}
        if trigger is PeriodTrigger.BEGIN_CLOSE:
            changes["closing_at"] = at
        if trigger is PeriodTrigger.SEAL:
            if not seal_digest:
                raise BillingConflict(
                    "a period cannot be sealed without the digest of what it sealed"
                )
            changes["sealed_at"] = at
            changes["sealed_by"] = sealed_by
            changes["seal_digest"] = seal_digest
        return replace(period, **changes)

    def guard_open(self, period: UsagePeriod) -> None:
        """Refuse an in-period write to a period that is no longer taking them. The caller's
        alternative is an adjustment, which is a different and *visible* thing."""
        if not period.accepts_in_period_usage:
            raise BillingConflict(
                f"usage period {period.period.start}–{period.period.end} is "
                f"{period.state.value}; corrections after this point are adjustments, never "
                "retroactive mutations"
            )


class QuarantineLifecycleService:
    """Applies quarantine triggers. Both exits are terminal and both are recorded — a quarantined
    event is never deleted, because unattributable usage must stay visible (ADR-0023 M6)."""

    def __init__(self, definition: StateMachineDefinition = QUARANTINE_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: QuarantineState, trigger: QuarantineTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: QuarantineState, trigger: QuarantineTrigger) -> QuarantineState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"quarantined event cannot {trigger.value} from {current.value}; legal: {legal}"
            )
        return edge.target

    def allowed(self, current: QuarantineState) -> tuple[QuarantineTrigger, ...]:
        return tuple(t for t in QuarantineTrigger if self.can(current, t))

    def apply(
        self,
        entry: QuarantineEntry,
        trigger: QuarantineTrigger,
        *,
        at: float,
        actor_id: str = "",
        note: str = "",
        resulting_usage_id: str = "",
    ) -> QuarantineEntry:
        state = self.next_state(entry.state, trigger)
        return replace(
            entry,
            state=state,
            resolved_at=at,
            resolved_by=actor_id,
            resolution_note=note or entry.resolution_note,
            resulting_usage_id=resulting_usage_id or entry.resulting_usage_id,
        )


# ── aggregation ─────────────────────────────────────────────────────────────────


class UsageAggregator:
    """Deterministic folding of commercial usage into windows.

    Aggregation is **incremental-safe**: folding the same records in any order produces the same
    total and the same digest, so an aggregate rebuilt after a restart equals the one maintained
    continuously. That equality is what lets the number a customer sees mid-period be the number
    they are billed on (Blueprint §24).

    Reduction is M9.2's `fold` — one definition of how an aggregation reduces, not two."""

    def window_for(
        self, granularity: WindowGranularity, *, at: float, period: BillingPeriod
    ) -> UsageWindow:
        """The window containing an instant. PERIOD is the billing period itself; HOUR and DAY
        are anchored to the **period start**, not to the epoch, so a customer's daily buckets
        line up with their billing day rather than with UTC midnight."""
        if granularity is WindowGranularity.PERIOD:
            return UsageWindow(granularity, period.start, period.end)
        length = WINDOW_SECONDS[granularity]
        offset = max(at - period.start, 0.0)
        index = int(offset // length)
        start = period.start + index * length
        return UsageWindow(granularity, start, min(start + length, period.end))

    def aggregate(
        self,
        usage: Sequence[CommercialUsage],
        *,
        aggregate_id: str,
        org_id: str,
        account_id: str,
        meter_code: str,
        scope: Scope,
        period: BillingPeriod,
        window: UsageWindow,
        aggregation: MeterAggregation,
        unit: str,
        at: float = 0.0,
        sealed: bool = False,
    ) -> UsageAggregateSnapshot:
        billable = self._in_window(usage, meter_code=meter_code, window=window)
        quantity = (
            fold(aggregation, tuple(u.quantity for u in billable))
            if billable
            else Quantity.zero(unit)
        )
        return UsageAggregateSnapshot(
            id=aggregate_id,
            org_id=org_id,
            account_id=account_id,
            meter_code=meter_code,
            scope=scope,
            window=window,
            period=period,
            aggregation=aggregation,
            quantity=quantity,
            record_count=len(billable),
            adjustment_count=sum(1 for u in billable if u.is_adjustment),
            source_digest=self.digest(billable),
            sealed=sealed,
            computed_at=at,
        )

    @staticmethod
    def _in_window(
        usage: Sequence[CommercialUsage], *, meter_code: str, window: UsageWindow
    ) -> tuple[CommercialUsage, ...]:
        """Windowed on `observed_at`, not on when Billing recorded it.

        This is the choice that makes a late event count in the hour it happened rather than the
        hour it arrived — which is what a customer looking at a usage chart expects, and what
        makes a chart comparable across a metering outage."""
        return tuple(
            sorted(
                (
                    u
                    for u in usage
                    if u.meter_code == meter_code
                    and u.is_billable
                    and window.contains(u.source.observed_at)
                ),
                key=lambda u: (u.source.observed_at, u.id),
            )
        )

    @staticmethod
    def digest(usage: Sequence[CommercialUsage]) -> str:
        """A deterministic fingerprint of a record set, independent of iteration order."""
        return _hash(sorted(u.digest for u in usage))

    def snapshot(
        self,
        usage: Sequence[CommercialUsage],
        aggregates: Sequence[UsageAggregateSnapshot],
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        state: PeriodState = PeriodState.OPEN,
        quarantined_count: int = 0,
        at: float = 0.0,
    ) -> UsageSnapshot:
        billable = [u for u in usage if u.is_billable]
        return UsageSnapshot(
            org_id=org_id,
            account_id=account_id,
            period=period,
            at=at,
            aggregates=tuple(sorted(aggregates, key=lambda a: (a.meter_code, a.window.start))),
            record_count=len(billable),
            adjustment_count=sum(1 for u in billable if u.is_adjustment),
            quarantined_count=quarantined_count,
            state=state,
            digest=self.digest(billable),
        )


# ── sealing ─────────────────────────────────────────────────────────────────────


class UsageSealingService:
    """Computes the seal digest and enforces the one-way door.

    The digest is over the *commercial content* of every billable record in the period — ids,
    quantities, periods, statuses — and deliberately excludes ingestion timestamps and run ids,
    which legitimately differ between an original ingestion and a replay of it. Getting that
    boundary right is the whole point: too much in the digest and every replay looks like a
    mismatch; too little and a real difference hides."""

    def __init__(self, aggregator: UsageAggregator | None = None) -> None:
        self._aggregator = aggregator or UsageAggregator()

    def seal_digest(self, usage: Sequence[CommercialUsage]) -> str:
        return self._aggregator.digest([u for u in usage if u.is_billable])

    def verify(self, period: UsagePeriod, usage: Sequence[CommercialUsage]) -> bool:
        """Whether a period's recorded content still matches what it sealed. A `False` here is a
        financial-integrity incident, and the reason the digest exists at all."""
        if not period.is_sealed:
            return True
        return period.seal_digest == self.seal_digest(usage)

    def guard_not_sealed(self, period: UsagePeriod) -> None:
        if period.is_sealed:
            raise BillingConflict(
                f"usage period {period.period.start}–{period.period.end} is sealed; a sealed "
                "period is never reopened — corrections become adjustments or credit notes"
            )


# ── replay ──────────────────────────────────────────────────────────────────────


class UsageReplayService:
    """Deterministically re-derives commercial usage from source events.

    Replay is the same pure `UsageIngestionService.decide` the live path uses — not a second
    implementation that could drift from it. That identity is the point: a replay that used
    different code would prove nothing about the original.

    Duplicates encountered during replay are *counted*, not applied, so replaying a period that
    was already ingested produces zero new records and an identical digest."""

    def __init__(self, ingestion: UsageIngestionService | None = None) -> None:
        self._ingestion = ingestion or UsageIngestionService()
        self._sealing = UsageSealingService()

    def replay(
        self,
        events: Sequence[tuple[SourceEvent, int, str, Scope | None]],
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        meters_by_code: dict[str, CommercialMeter],
        meter_for_event,
        accounts_by_scope: dict[str, str],
        existing: Sequence[CommercialUsage] = (),
        period_state: PeriodState = PeriodState.OPEN,
        seal_digest: str = "",
        at: float = 0.0,
        ingest_run_id: str = "",
    ) -> ReplayResult:
        """Re-derive a period. `events` are `(event, raw_units, raw_unit, scope)` tuples exactly
        as ingestion received them — replay reads the same inputs, not a summary of them."""
        seen = {u.idempotency_key for u in existing}
        produced: list[CommercialUsage] = []
        duplicates = 0
        quarantined = 0
        for event, raw_units, raw_unit, scope in sorted(
            events, key=lambda e: (e[0].observed_at, e[0].event_id)
        ):
            meter = meter_for_event(event, meters_by_code)
            decision = self._ingestion.decide(
                event,
                org_id=org_id,
                meter=meter,
                raw_units=raw_units,
                raw_unit=raw_unit,
                scope=scope,
                period=period,
                period_state=period_state,
                accounts_by_scope=accounts_by_scope,
                already_recorded=(meter is not None and event.identity(meter.code) in seen),
                ingested_at=at,
                ingest_run_id=ingest_run_id,
                replayed=True,
            )
            if decision.is_duplicate:
                duplicates += 1
            elif decision.is_quarantined:
                quarantined += 1
            elif decision.usage is not None:
                produced.append(decision.usage)
                seen.add(decision.usage.idempotency_key)

        # The comparison set is what the period *would contain* after this replay: everything
        # already recorded plus everything newly derived. Comparing only the new records would
        # make a fully-replayed period look empty.
        combined = tuple(existing) + tuple(produced)
        return ReplayResult(
            org_id=org_id,
            account_id=account_id,
            period=period,
            at=at,
            events_replayed=len(events),
            usage_produced=len(produced),
            duplicates_skipped=duplicates,
            quarantined=quarantined,
            replay_digest=self._sealing.seal_digest(combined),
            seal_digest=seal_digest,
            usage=tuple(produced),
        )


# ── reconciliation ──────────────────────────────────────────────────────────────


class UsageReconciliationService:
    """Compares the authoritative event log against recorded commercial usage.

    Run continuously rather than at period end (R-1). Every discrepancy kind has a different
    remedy, so they are reported distinctly rather than as one "mismatch" — and
    `DUPLICATE_USAGE` is called out separately because it is the only one that cannot be fixed by
    re-running anything."""

    def reconcile(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        expected: Sequence[tuple[str, str, int]],  # (event_id, meter_code, expected_units)
        recorded: Sequence[CommercialUsage],
        at: float = 0.0,
    ) -> UsageReconciliation:
        by_key: dict[tuple[str, str], list[CommercialUsage]] = {}
        for record in recorded:
            by_key.setdefault(record.idempotency_key, []).append(record)

        discrepancies: list[UsageDiscrepancy] = []
        expected_keys: set[tuple[str, str]] = set()
        for event_id, meter_code, units in sorted(expected):
            key = (event_id, meter_code)
            expected_keys.add(key)
            found = by_key.get(key, [])
            if not found:
                discrepancies.append(
                    UsageDiscrepancy(
                        kind=DiscrepancyKind.MISSING_USAGE,
                        event_id=event_id,
                        meter_code=meter_code,
                        expected=str(units),
                        detail="the event log has this event but Billing recorded no usage",
                    )
                )
                continue
            if len(found) > 1:
                discrepancies.append(
                    UsageDiscrepancy(
                        kind=DiscrepancyKind.DUPLICATE_USAGE,
                        event_id=event_id,
                        meter_code=meter_code,
                        usage_id=",".join(sorted(u.id for u in found)),
                        detail="exactly-once was violated; this is a financial-integrity incident",
                    )
                )
            record = found[0]
            if record.quantity.units != units:
                discrepancies.append(
                    UsageDiscrepancy(
                        kind=DiscrepancyKind.QUANTITY_MISMATCH,
                        event_id=event_id,
                        meter_code=meter_code,
                        usage_id=record.id,
                        expected=str(units),
                        actual=str(record.quantity.units),
                    )
                )

        for key, records in sorted(by_key.items()):
            if key not in expected_keys:
                discrepancies.append(
                    UsageDiscrepancy(
                        kind=DiscrepancyKind.ORPHAN_USAGE,
                        event_id=key[0],
                        meter_code=key[1],
                        usage_id=records[0].id,
                        detail="Billing recorded usage for an event the log does not contain",
                    )
                )

        return UsageReconciliation(
            org_id=org_id,
            account_id=account_id,
            period=period,
            at=at,
            events_examined=len(expected),
            usage_examined=len(recorded),
            discrepancies=tuple(
                sorted(discrepancies, key=lambda d: (d.kind.value, d.event_id, d.meter_code))
            ),
        )


# ── inspection ──────────────────────────────────────────────────────────────────


class UsageInspector:
    """Explains one usage record, and reports what is structurally wrong with a period.

    Inspection and diagnostics are one class because they are the same act at two altitudes: one
    explains a single billable fact, the other sweeps a period for the shapes that make an
    invoice indefensible."""

    def __init__(self, validator: MeteringValidator | None = None) -> None:
        self._validator = validator or MeteringValidator()
        self._sealing = UsageSealingService()

    def explain(self, usage: CommercialUsage) -> tuple[str, ...]:
        """How this record came to say what it says, as ordered sentences.

        Returned as prose rather than a structure because its audience is a support engineer on a
        billing dispute, not a program — and because every fact in it is already available
        structurally on the record itself."""
        p = usage.provenance
        lines = [
            f"source event {usage.source.event_id} ({usage.source.event_type}) from "
            f"{p.source_kind.value}, observed at {usage.source.observed_at}",
            f"counted by meter {p.meter_pin}",
        ]
        if p.was_normalized:
            lines.append(
                f"normalized {p.raw_units} {p.raw_unit} × {p.normalization.numerator}/"
                f"{p.normalization.denominator} ({p.normalization.rounding.value}) = "
                f"{usage.quantity.units} {usage.quantity.unit}"
            )
        else:
            lines.append(f"recorded as {usage.quantity.units} {usage.quantity.unit}, unscaled")
        lines.append(
            f"attributed to account {usage.account_id} at scope {usage.scope.narrowest_id}"
        )
        if p.was_late:
            lines.append(
                f"arrived late ({p.disposition.value}); billed in period "
                f"{usage.period.start}–{usage.period.end} as {usage.status.value}"
            )
        else:
            lines.append(f"billed in period {usage.period.start}–{usage.period.end}")
        if usage.corrects_usage_id:
            lines.append(f"corrects usage {usage.corrects_usage_id}")
        if p.replayed:
            lines.append("produced by a replay; identical to the original by construction")
        return tuple(lines)

    def diagnose(
        self,
        *,
        period: UsagePeriod,
        usage: Sequence[CommercialUsage],
        quarantined: Sequence[QuarantineEntry] = (),
        meters: Sequence[CommercialMeter] = (),
    ) -> BillingDiagnostics:
        """Everything structurally wrong with a period's metering. Reports *all* findings,
        deterministically ordered, and never raises."""
        out: list[Diagnostic] = list(self._validator.validate_period(period).findings)
        out.extend(self._validator.validate_usage_set(usage).findings)
        out.extend(
            self._validator.validate_quarantine_backlog(
                [q for q in quarantined if q.is_pending]
            ).findings
        )
        if meters:
            out.extend(self._validator.validate_meters(meters).findings)
        for record in usage:
            out.extend(self._validator.validate_usage(record).findings)
        if period.is_sealed and not self._sealing.verify(period, usage):
            out.append(
                _err(
                    "usage_period.seal_digest",
                    C.SEAL_DIGEST_MISMATCH,
                    "the period's content no longer matches what it sealed",
                    "a financial-integrity incident: sealed usage has changed since sealing",
                )
            )
        pending = [q for q in quarantined if q.is_pending]
        if pending and not out:
            out.append(
                _warn(
                    "quarantine",
                    C.USAGE_RECONCILIATION_GAP,
                    f"{len(pending)} events are neither billed nor discarded",
                )
            )
        return BillingDiagnostics.of(out)


# ── search + export ─────────────────────────────────────────────────────────────


class UsageSearchService:
    """Deterministic usage filtering. No scoring, no ranking, no index — the same records and the
    same query always produce the same rows in the same order.

    Ordering is by `(observed_at, usage id)`: chronological, because that is how usage is read,
    and tie-broken on a derived id, because that is stable across replays."""

    def search(
        self, usage: Sequence[CommercialUsage], query: UsageSearchQuery
    ) -> UsageSearchResult:
        matched = [u for u in usage if self._matches(u, query)]
        matched.sort(key=lambda u: (u.source.observed_at, u.id))
        start = max(query.offset, 0)
        page = tuple(matched[start : start + max(query.limit, 0)])
        return UsageSearchResult(usage=page, total=len(matched))

    def _matches(self, usage: CommercialUsage, query: UsageSearchQuery) -> bool:
        return (
            self._text_matches(
                query.text, usage.meter_code, usage.source.event_id, usage.subscription_id
            )
            and (not query.account_id or usage.account_id == query.account_id)
            and (not query.meter_codes or usage.meter_code in query.meter_codes)
            and (not query.statuses or usage.status in query.statuses)
            and (not query.scope_id or query.scope_id in self._scope_ids(usage.scope))
            and (not query.subscription_id or usage.subscription_id == query.subscription_id)
            and (not query.event_id or usage.source.event_id == query.event_id)
            and (query.period_start is None or usage.period.start == query.period_start)
            and (query.period_end is None or usage.period.end == query.period_end)
            and (query.observed_from is None or usage.source.observed_at >= query.observed_from)
            and (query.observed_until is None or usage.source.observed_at < query.observed_until)
            and (not query.billable_only or usage.is_billable)
            and (not query.adjustments_only or usage.is_adjustment)
            and (not query.late_only or usage.provenance.was_late)
        )

    @staticmethod
    def _scope_ids(scope: Scope) -> tuple[str, ...]:
        return tuple(
            s for s in (scope.org_id, scope.workspace_id, scope.project_id, scope.agent_id) if s
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (value or "").casefold() for value in fields)


class UsageExportService:
    """Builds a finance-grade export of a period's commercial usage.

    Deterministically ordered and digested, so two exports of an unchanged sealed period are
    byte-identical — which is what lets finance diff one month against the next and trust the
    difference. Nothing here writes a file: the export is a value, and turning it into bytes is
    transport's job."""

    def export(
        self,
        usage: Sequence[CommercialUsage],
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        sealed: bool = False,
        at: float = 0.0,
    ) -> UsageExport:
        rows = tuple(
            sorted(
                (self._row(u) for u in usage),
                key=lambda r: (r.meter_pin, r.usage_id),
            )
        )
        return UsageExport(
            org_id=org_id,
            account_id=account_id,
            period=period,
            generated_at=at,
            rows=rows,
            digest=_hash(r.digest for r in rows),
            sealed=sealed,
        )

    @staticmethod
    def _row(usage: CommercialUsage) -> UsageExportRow:
        return UsageExportRow(
            usage_id=usage.id,
            account_id=usage.account_id,
            meter_pin=usage.meter_pin,
            units=usage.quantity.units,
            unit=usage.quantity.unit,
            status=usage.status.value,
            period_start=usage.period.start,
            period_end=usage.period.end,
            observed_at=usage.source.observed_at,
            recorded_at=usage.recorded_at,
            scope_id=usage.scope.narrowest_id,
            subscription_id=usage.subscription_id,
            event_id=usage.source.event_id,
            disposition=usage.provenance.disposition.value,
            corrects_usage_id=usage.corrects_usage_id,
            digest=usage.digest,
        )


# ── corrections ─────────────────────────────────────────────────────────────────


class UsageCorrectionService:
    """Turns a correction into the append-only records that express it.

    A correction never edits the record it corrects. It produces a **new** usage record carrying
    the delta with `ADJUSTMENT` status, pointing back at the original — so the original figure,
    the change, and the reason are all separately readable. That is what an auditor asks for and
    what a single overwritten number can never provide."""

    def apply(
        self,
        original: CommercialUsage,
        correction: UsageCorrection,
        *,
        usage_id: str,
        at: float,
        period: BillingPeriod | None = None,
    ) -> CommercialUsage:
        if correction.corrects_usage_id != original.id:
            raise BillingConflict(
                f"correction {correction.id!r} does not correct usage {original.id!r}"
            )
        if correction.unit != original.quantity.unit:
            raise BillingConflict(
                f"correction is in {correction.unit!r}, usage is in {original.quantity.unit!r}"
            )
        units = correction.delta_units
        if units < 0 and abs(units) > original.quantity.units:
            raise BillingConflict(
                f"correction withdraws {abs(units)} but only {original.quantity.units} was "
                "recorded; a correction cannot produce negative usage"
            )
        landing = period or correction.period or original.period
        return replace(
            original,
            id=usage_id,
            quantity=Quantity(abs(units), original.quantity.unit),
            period=landing,
            status=UsageStatus.ADJUSTMENT,
            corrects_usage_id=original.id,
            recorded_at=at,
            provenance=replace(
                original.provenance,
                ingested_at=at,
                disposition=LateArrivalDisposition.PERIOD_SEALED
                if landing != original.period
                else original.provenance.disposition,
            ),
        )

    @staticmethod
    def kind_for(delta_units: int, *, period_changed: bool) -> CorrectionKind:
        """What this correction *is*, from what it does. Named distinctly because the three mean
        different things to an auditor: a correction fixes what was measured, an adjustment moves
        when it was billed, and a reversal withdraws it entirely."""
        if period_changed:
            return CorrectionKind.ADJUSTMENT
        return CorrectionKind.REVERSAL if delta_units < 0 else CorrectionKind.CORRECTION


# ── history ─────────────────────────────────────────────────────────────────────


class MeteringHistoryService:
    """Orders metering activity deterministically. Pure — it composes, it does not store."""

    def ordered(self, activity: Sequence[MeteringActivity]) -> tuple[MeteringActivity, ...]:
        return tuple(sorted(activity, key=lambda a: (a.at, a.id)))

    def of_action(
        self, activity: Sequence[MeteringActivity], action
    ) -> tuple[MeteringActivity, ...]:
        return tuple(a for a in self.ordered(activity) if a.action is action)
