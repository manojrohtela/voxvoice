"""Reporting domain services (M9.22) — transformations, validation, completeness, refusals.

Pure, deterministic, clock-free and I/O-free, exactly as every Foundation module since M9.2. Every
temporal decision arrives as an `at` parameter; nothing here calls `datetime.now()`, opens a socket,
touches a session or asks who the caller is.

## The one place money is touched, and how narrowly

`ReportingTransformationService` is the only module in this milestone that adds two figures
together, and it can only do so through `SUM_SAME_CURRENCY`, which:

* accepts `Money` values the owning domains already decided;
* **refuses a mixed-currency input** rather than converting it — there is no FX rate anywhere in
  this milestone, from a provider, a table or a constant;
* refuses when any contributing source was unavailable, because a sum over a partial set is a
  smaller number presented as a complete one;
* returns `Money`, never a float.

There is no `AVERAGE`, no `ALLOCATE`, no `APPLY_RATE`, no `NET_OF` and no balance folding. Each of
those is a financial rule owned by M9.13, M9.15 or M9.18, and re-deriving one here would make this
a second engine whose answer can disagree with theirs.

## Validation is not reconciliation

`ReportValidationService` asks questions about the **report**: are mandatory fields populated, did
required sources resolve, do the source periods match the reporting period, is the currency basis
satisfied, does the definition's own contract hold. It never compares two financial domains against
each other — that is M9.19's question, and this milestone *reads its answer* rather than computing a
second one that could differ.

## Missing is never zero

`ReportCompletenessService` and the transformation service agree on one rule and enforce it
separately: a field whose source could not be read has **no value**, status `SOURCE_UNAVAILABLE`,
and an exception naming the source. Reporting it as `0` would be the single most damaging thing
this milestone could do, because a regulator cannot tell a zero that means *nothing happened* from
a zero that means *we could not look*.
"""

from __future__ import annotations

from dataclasses import replace

from core.fsm import StateMachineDefinition

from .commercial_investigations import EvidenceDomain, canonical_digest
from .commercial_reporting import (
    LIVE_REPORT_STATES,
    REPORT_LIFECYCLE,
    SATISFYING_SOURCE_STATUS,
    SEVERITY_BY_EXCEPTION,
    TERMINAL_REPORT_STATES,
    UNSETTLED_INTEGRITY,
    CurrencyBasis,
    EvidenceCompleteness,
    ExceptionSeverity,
    ExceptionState,
    RegulatoryReport,
    ReportCompleteness,
    ReportDefinition,
    ReportedIntegrity,
    ReportException,
    ReportExceptionKind,
    ReportFieldSpec,
    ReportFieldValue,
    ReportingPeriod,
    ReportInputManifest,
    ReportSourceRef,
    ReportStatus,
    ReportTransformation,
    ReportTrigger,
    SourceStatus,
    ValidationCheck,
    ValidationResult,
    ValueStatus,
)
from .diagnostics import BillingDiagnosticCode, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .money import Money

# ── errors ──────────────────────────────────────────────────────────────────────


class ReportStale(BillingConflict):
    """A worker tried to advance a report from a version that is no longer current.

    Raised by persistence when the compare-and-set matches no row, and declared **here, in
    Foundation, beside the domain it protects** — the Application must react to it and may not
    import persistence. Same placement and same reason as `RefundSequenceConflict` (M9.16-R1),
    `CaseStale` (M9.17), `ScheduleStale` (M9.18), `FindingStale` (M9.19), `IncidentStale` (M9.20)
    and `InvestigationStale` (M9.21).

    The scenario: worker A reads a report as `READY` version 4, worker B finalizes it to version 5,
    and worker A resumes and writes. Losing that race must be loud — a filing silently overwritten
    is a regulatory artifact that says something nobody approved."""


class ReportRefused(BillingConflict):
    """A reporting action was asked for that this engine must not perform.

    A refusal about the *request*, not an observation about the books — which is why it raises
    rather than becoming a report field. Finalizing with a blocking exception, editing a finalized
    report, correcting a report whose inputs have not moved, or reaching across an organization
    boundary all land here."""


class UnsupportedReportRule(BillingConflict):
    """A report asked for something no versioned reporting rule covers.

    Raised rather than approximated. Combining two currencies with no approved conversion rule, or
    applying a transformation the definition never declared, would put a number in front of a
    regulator that no Billing domain owns."""


# ── transformations ─────────────────────────────────────────────────────────────


class ReportingTransformationService:
    """**The only place in this milestone that combines figures, and it barely does.**

    Every method takes facts the owning domains already decided and applies exactly the
    transformation the definition declared. Nothing here is reusable as a financial business rule:
    the service is reachable only through a `ReportFieldSpec`, and a test asserts no other module
    imports it."""

    def apply(
        self,
        spec: ReportFieldSpec,
        *,
        values: tuple[object, ...],
        amounts: tuple[Money, ...],
        locators: tuple[str, ...],
        source_status: SourceStatus,
        definition: ReportDefinition,
    ) -> ReportFieldValue:
        """Compute one field, or say honestly why it has no value."""
        if source_status is SourceStatus.UNAVAILABLE:
            # **The rule this milestone exists to protect.** No zero, no empty string, no default.
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.SOURCE_UNAVAILABLE,
                transformation=spec.transformation,
                source_locators=locators,
                detail=(
                    f"{spec.domain.value} could not be read for this period; the field has no "
                    "value. It is not zero — a zero here would be indistinguishable from a period "
                    "in which nothing happened"
                ),
            )

        if spec.transformation is ReportTransformation.SUM_SAME_CURRENCY:
            return self._sum(spec, amounts, locators, definition)
        if spec.transformation is ReportTransformation.COUNT:
            return self._number(spec, len(values), locators)
        if spec.transformation is ReportTransformation.DISTINCT_COUNT:
            return self._number(spec, len({str(v) for v in values}), locators)
        if spec.transformation in (ReportTransformation.EARLIEST, ReportTransformation.LATEST):
            return self._instant(spec, values, locators)
        if spec.transformation is ReportTransformation.CLASSIFY:
            return self._classify(spec, values, locators)
        return self._copy(spec, values, locators)

    # ── the monetary one ────────────────────────────────────────────────────────

    @staticmethod
    def _sum(
        spec: ReportFieldSpec,
        amounts: tuple[Money, ...],
        locators: tuple[str, ...],
        definition: ReportDefinition,
    ) -> ReportFieldValue:
        """Add integer minor units of facts that **already share a currency**.

        Two refusals rather than a conversion:

        * more than one currency among the inputs → `UNSUPPORTED`, reported as
          `MULTI_CURRENCY_UNSUPPORTED`. There is no FX rate in this milestone, and inventing one
          would produce a figure no Billing domain owns;
        * a `SINGLE_DECLARED` definition whose inputs are in a different currency → `UNSUPPORTED`.
          Silently reporting the source currency under a declared-currency heading is worse than
          reporting nothing.

        An empty input is a legitimate zero — but only because the source *resolved* and returned
        nothing. An unavailable source never reaches this method."""
        currencies = {a.currency for a in amounts}
        if len(currencies) > 1:
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.UNSUPPORTED,
                transformation=spec.transformation,
                source_locators=locators,
                detail=(
                    "sources span "
                    + ", ".join(sorted(str(c) for c in currencies))
                    + " and no approved conversion rule exists; this milestone performs no "
                    "currency conversion"
                ),
            )
        declared = definition.declared_currency
        if definition.currency_basis is CurrencyBasis.SINGLE_DECLARED and declared is not None:
            if currencies and next(iter(currencies)) != declared:
                found = next(iter(currencies))
                return ReportFieldValue(
                    key=spec.key,
                    status=ValueStatus.UNSUPPORTED,
                    transformation=spec.transformation,
                    source_locators=locators,
                    detail=(
                        f"the definition declares {declared} and the sources are in {found}; "
                        "reporting one under the other's heading would misstate the filing"
                    ),
                )
            currency = declared
        else:
            currency = next(iter(currencies)) if currencies else None

        if currency is None:
            # Nothing to add and no currency to name it in. A zero with no currency is not a
            # monetary fact, so the field is `NOT_APPLICABLE` rather than `0`.
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.NOT_APPLICABLE,
                transformation=spec.transformation,
                source_locators=locators,
                detail="the sources resolved and contained no monetary facts for this period",
            )
        # Integer minor units only. `Money.minor` is what the owning domain wrote; there is no
        # float, no rounding and no rate anywhere on this line.
        total = Money(minor=sum(a.minor for a in amounts), currency=currency)
        return ReportFieldValue(
            key=spec.key,
            status=ValueStatus.PRESENT,
            transformation=spec.transformation,
            amount=total,
            currency=currency,
            source_locators=locators,
            detail=f"sum of {len(amounts)} authoritative amount(s) in {currency}",
        )

    # ── the non-monetary ones ───────────────────────────────────────────────────

    @staticmethod
    def _number(spec: ReportFieldSpec, n: int, locators: tuple[str, ...]) -> ReportFieldValue:
        return ReportFieldValue(
            key=spec.key,
            status=ValueStatus.PRESENT,
            transformation=spec.transformation,
            number=n,
            source_locators=locators,
        )

    @staticmethod
    def _instant(
        spec: ReportFieldSpec, values: tuple[object, ...], locators: tuple[str, ...]
    ) -> ReportFieldValue:
        instants = [float(v) for v in values if isinstance(v, (int, float))]
        if not instants:
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.NOT_APPLICABLE,
                transformation=spec.transformation,
                source_locators=locators,
                detail="the sources resolved and carried no timestamp for this field",
            )
        chosen = (
            min(instants) if spec.transformation is ReportTransformation.EARLIEST else max(instants)
        )
        # Carried as integer microseconds so a digest over it is reproducible — the same rule
        # M9.21 applies to every timestamp that reaches a canonical form.
        return ReportFieldValue(
            key=spec.key,
            status=ValueStatus.PRESENT,
            transformation=spec.transformation,
            number=int(round(chosen * 1_000_000)),
            source_locators=locators,
            detail="integer microseconds",
        )

    @staticmethod
    def _classify(
        spec: ReportFieldSpec, values: tuple[object, ...], locators: tuple[str, ...]
    ) -> ReportFieldValue:
        """Map a source state onto the regulator's vocabulary, **using the definition's own map**.

        An unmapped state is `UNSUPPORTED`, not a guess and not a passthrough: the whole point of a
        versioned classification map is that a state nobody has decided how to report is visible as
        such rather than filed under whatever the source happened to call it."""
        mapping = dict(spec.classification_map)
        observed = [str(v) for v in values]
        unmapped = sorted({o for o in observed if o not in mapping})
        if unmapped:
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.UNSUPPORTED,
                transformation=spec.transformation,
                source_locators=locators,
                detail=(
                    "the definition declares no reporting classification for source state(s) "
                    + ", ".join(repr(u) for u in unmapped)
                ),
            )
        classified = sorted({mapping[o] for o in observed})
        if not classified:
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.NOT_APPLICABLE,
                transformation=spec.transformation,
                source_locators=locators,
            )
        return ReportFieldValue(
            key=spec.key,
            status=ValueStatus.PRESENT,
            transformation=spec.transformation,
            text=", ".join(classified),
            source_locators=locators,
        )

    @staticmethod
    def _copy(
        spec: ReportFieldSpec, values: tuple[object, ...], locators: tuple[str, ...]
    ) -> ReportFieldValue:
        if not values:
            return ReportFieldValue(
                key=spec.key,
                status=ValueStatus.NOT_APPLICABLE,
                transformation=spec.transformation,
                source_locators=locators,
                detail="the source resolved and carried no value for this field",
            )
        return ReportFieldValue(
            key=spec.key,
            status=ValueStatus.PRESENT,
            transformation=spec.transformation,
            text=", ".join(sorted({str(v) for v in values})),
            source_locators=locators,
        )


# ── completeness ────────────────────────────────────────────────────────────────


class ReportCompletenessService:
    """Decides `COMPLETE` / `INCOMPLETE` / `BLOCKED`. **On availability, never on optimism.**"""

    @staticmethod
    def assess(
        definition: ReportDefinition,
        manifest: ReportInputManifest,
        values: tuple[ReportFieldValue, ...],
    ) -> ReportCompleteness:
        """`BLOCKED` when a *required* source could not be read or a *mandatory* field has no
        value. `INCOMPLETE` when something optional is missing. `COMPLETE` otherwise.

        The distinction matters operationally: incomplete may still be filed if the definition
        allows it, blocked never may — and collapsing the two would let a compliance officer file
        something that is missing a source a regulator demanded."""
        by_domain = {s.domain: s for s in manifest.sources}
        for domain in definition.required_sources:
            source = by_domain.get(domain)
            if source is None or not source.satisfies:
                return ReportCompleteness.BLOCKED

        by_key = {v.key: v for v in values}
        for spec in definition.mandatory_fields:
            value = by_key.get(spec.key)
            if value is None or not value.is_present:
                # A mandatory field whose value is `NOT_APPLICABLE` is still absent from the
                # filing. The definition said it was mandatory; the sources disagreed.
                return ReportCompleteness.BLOCKED

        optional_missing = any(
            not v.is_present for v in values if v.key not in {s.key for s in
                                                              definition.mandatory_fields}
        )
        return (
            ReportCompleteness.INCOMPLETE if optional_missing else ReportCompleteness.COMPLETE
        )

    @staticmethod
    def evidence(
        definition: ReportDefinition, manifest: ReportInputManifest
    ) -> EvidenceCompleteness:
        """Whether every source the definition **requires** actually resolved.

        Separate from field completeness, because a report can have every field populated while one
        required source silently contributed nothing — and a regulator asks about both."""
        by_domain = {s.domain: s for s in manifest.sources}
        for domain in definition.required_sources:
            source = by_domain.get(domain)
            if source is None or not source.satisfies:
                return EvidenceCompleteness.EVIDENCE_INCOMPLETE
        return EvidenceCompleteness.EVIDENCE_COMPLETE


# ── exception derivation ────────────────────────────────────────────────────────


class ReportExceptionService:
    """Turns observed problems into **named** exceptions. Never a generic "report failed"."""

    #: Which exception a missing domain raises. A table so an operator gets a diagnosis naming the
    #: domain to go and fix, rather than one message covering nine different problems.
    MISSING_BY_DOMAIN: dict[EvidenceDomain, ReportExceptionKind] = {
        EvidenceDomain.TAX: ReportExceptionKind.MISSING_TAX_EVIDENCE,
        EvidenceDomain.REVENUE: ReportExceptionKind.MISSING_RECOGNITION_EVIDENCE,
        EvidenceDomain.PAYMENT: ReportExceptionKind.MISSING_PAYMENT_EVIDENCE,
    }

    def for_manifest(
        self, definition: ReportDefinition, manifest: ReportInputManifest
    ) -> tuple[tuple[ReportExceptionKind, str, str], ...]:
        """`(kind, subject, diagnostic)` for every source problem. Nothing is omitted."""
        out: list[tuple[ReportExceptionKind, str, str]] = []
        by_domain = {s.domain: s for s in manifest.sources}
        for domain in definition.required_sources:
            source = by_domain.get(domain)
            if source is None:
                out.append(
                    (
                        ReportExceptionKind.MISSING_SOURCE,
                        domain.value,
                        f"the definition requires {domain.value} and the run resolved no source "
                        "for it",
                    )
                )
                continue
            if source.status is SourceStatus.UNAVAILABLE:
                kind = self.MISSING_BY_DOMAIN.get(
                    domain, ReportExceptionKind.SOURCE_UNAVAILABLE
                )
                out.append(
                    (
                        kind,
                        source.locator,
                        source.unavailable_reason
                        or f"{domain.value} could not be read for this period; the report records "
                        "the gap rather than reporting zero",
                    )
                )
        return tuple(out)

    @staticmethod
    def for_values(
        definition: ReportDefinition, values: tuple[ReportFieldValue, ...]
    ) -> tuple[tuple[ReportExceptionKind, str, str], ...]:
        out: list[tuple[ReportExceptionKind, str, str]] = []
        mandatory = {s.key for s in definition.mandatory_fields}
        for value in values:
            if value.status is ValueStatus.UNSUPPORTED:
                kind = (
                    ReportExceptionKind.MULTI_CURRENCY_UNSUPPORTED
                    if "conversion rule" in value.detail
                    else ReportExceptionKind.UNSUPPORTED_CURRENCY
                    if "declares" in value.detail
                    else ReportExceptionKind.UNSUPPORTED_REPORT_DEFINITION
                )
                out.append((kind, value.key, value.detail))
            elif value.key in mandatory and not value.is_present:
                out.append(
                    (
                        ReportExceptionKind.MANDATORY_FIELD_MISSING,
                        value.key,
                        f"field {value.key!r} is mandatory under this definition and has no "
                        f"value ({value.status.value})",
                    )
                )
        return tuple(out)

    @staticmethod
    def for_integrity(
        definition: ReportDefinition, posture: ReportedIntegrity
    ) -> tuple[tuple[ReportExceptionKind, str, str], ...]:
        """An unsettled integrity record is recorded whether or not it blocks.

        A definition that does not block still files *with the exception attached* — because
        "we filed while a divergence was open" is a fact a regulator is entitled to see, and
        omitting it because it was not blocking would be the omission §35.14 hunts for."""
        if posture not in UNSETTLED_INTEGRITY:
            return ()
        kind = (
            ReportExceptionKind.INDETERMINATE_INTEGRITY
            if posture is ReportedIntegrity.INDETERMINATE
            else ReportExceptionKind.OPEN_INTEGRITY_INCIDENT
        )
        detail = (
            "the integrity record could not be read for this period, so the report cannot state "
            "that the books agreed"
            if posture is ReportedIntegrity.INDETERMINATE
            else f"the integrity record reports {posture.value} for this period"
        )
        return ((kind, posture.value, detail),)

    @staticmethod
    def severity(kind: ReportExceptionKind) -> ExceptionSeverity:
        return SEVERITY_BY_EXCEPTION[kind]


# ── validation ──────────────────────────────────────────────────────────────────


class ReportValidationService:
    """**Validation, not reconciliation.**

    Every check asks about the report: mandatory fields, required sources, period alignment,
    currency basis, definition contract. None compares two financial domains against each other —
    the integrity check *reads M9.19/M9.20's own verdict* rather than recomputing it, and a test
    asserts no reconciliation vocabulary appears in this module."""

    def validate(
        self,
        report: RegulatoryReport,
        *,
        definition: ReportDefinition,
        period: ReportingPeriod,
        manifest: ReportInputManifest,
        exceptions: tuple[ReportException, ...],
        at: float,
        actor_id: str = "",
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []

        checks.append(
            ValidationCheck(
                name="definition_version_pinned",
                passed=report.definition_version == definition.version,
                subject=str(report.definition_version),
                detail=(
                    "the report is bound to the definition version that produced it; a later "
                    "version never reinterprets it"
                ),
            )
        )
        checks.append(
            ValidationCheck(
                name="period_basis_matches_definition",
                passed=period.basis is definition.period_basis,
                subject=period.period_key,
                detail=(
                    f"definition consumes {definition.period_basis.value}; period is "
                    f"{period.basis.value}"
                ),
            )
        )
        checks.append(
            ValidationCheck(
                name="reporting_period_closed",
                passed=period.is_closed,
                subject=period.period_key,
                detail="a filing is made for a closed reporting period",
            )
        )
        checks.append(
            ValidationCheck(
                name="manifest_pinned_to_this_report",
                passed=manifest.id == report.manifest_id
                and manifest.source_digest == report.manifest_digest,
                subject=report.manifest_id,
                detail="the report's identity derives from this manifest's digest",
            )
        )

        by_domain = {s.domain: s for s in manifest.sources}
        for domain in definition.required_sources:
            source = by_domain.get(domain)
            checks.append(
                ValidationCheck(
                    name=f"required_source_resolved:{domain.value}",
                    passed=source is not None and source.satisfies,
                    subject=domain.value,
                    detail=(
                        "unresolved"
                        if source is None
                        else f"{source.status.value} · {source.locator}"
                    ),
                )
            )

        by_key = {v.key: v for v in report.values}
        for spec in definition.mandatory_fields:
            value = by_key.get(spec.key)
            checks.append(
                ValidationCheck(
                    name=f"mandatory_field_present:{spec.key}",
                    passed=value is not None and value.is_present,
                    subject=spec.key,
                    detail="absent" if value is None else value.status.value,
                )
            )

        checks.append(
            ValidationCheck(
                name="currency_basis_satisfied",
                passed=not any(v.status is ValueStatus.UNSUPPORTED for v in report.values),
                subject=definition.currency_basis.value,
                detail=(
                    "no field was left unsupported by a currency the definition cannot report"
                ),
            )
        )

        if definition.integrity_blocks_finalization:
            checks.append(
                ValidationCheck(
                    name="integrity_settled",
                    passed=report.integrity_posture not in UNSETTLED_INTEGRITY,
                    subject=report.integrity_posture.value,
                    detail=(
                        "this definition refuses to file while the integrity record is unsettled; "
                        "the verdict is M9.19/M9.20's, read rather than recomputed"
                    ),
                )
            )

        blocking = [e for e in exceptions if e.blocks_finalization]
        checks.append(
            ValidationCheck(
                name="no_blocking_exception",
                passed=not blocking,
                subject=str(len(blocking)),
                detail=", ".join(sorted({e.kind.value for e in blocking})) or "none",
            )
        )

        return ValidationResult(
            report_id=report.id,
            org_id=report.org_id,
            passed=all(c.passed for c in checks),
            checks=tuple(checks),
            validated_at=at,
            validated_by=actor_id,
            definition_version=definition.version,
        )


# ── lifecycle ───────────────────────────────────────────────────────────────────


class ReportLifecycleService:
    """Applies triggers through the shared FSM and bumps the concurrency token.

    **The machine's refusals are the specification.** `FINALIZED` has no outgoing edge, so
    `allowed()` returns an empty tuple for it and every attempt to move it raises. No state except
    `READY` has an edge to `FINALIZED`, so a filing cannot skip validation."""

    def __init__(self, definition: StateMachineDefinition = REPORT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: ReportStatus, trigger: ReportTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: ReportStatus, trigger: ReportTrigger) -> ReportStatus:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a report cannot {trigger.value} from {current.value}; legal triggers: "
                f"{legal or ['(none — this report is finalized)']}"
            )
        return edge.target

    def allowed(self, current: ReportStatus) -> tuple[ReportTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        report: RegulatoryReport,
        trigger: ReportTrigger,
        *,
        at: float,
        actor_id: str = "",
    ) -> RegulatoryReport:
        target = self.next_state(report.status, trigger)
        changes: dict = {"status": target, "version": report.version + 1}
        if target is ReportStatus.VALIDATING:
            changes["prepared_at"] = at
        if trigger in (ReportTrigger.PASS_VALIDATION, ReportTrigger.FAIL_VALIDATION):
            changes["validated_at"] = at
        if target is ReportStatus.FINALIZED:
            changes["finalized_at"] = at
            changes["finalized_by"] = actor_id
        # `definition_version`, `manifest_id` and `manifest_digest` are absent from every branch.
        # A transition records what happened to the run; it never re-pins its inputs.
        return replace(report, **changes)


# ── guards ──────────────────────────────────────────────────────────────────────


class ReportGuardService:
    """The refusals. Each states a boundary the mandate draws, in the layer that can enforce it."""

    @staticmethod
    def guard_scoped(org_id: str) -> None:
        if not org_id:
            raise ReportRefused(
                "a report must be org-scoped; there is no cross-tenant reporting"
            )

    @staticmethod
    def guard_same_org(report: RegulatoryReport, org_id: str) -> None:
        if report.org_id != org_id:
            raise ReportRefused(
                f"report {report.id!r} belongs to org {report.org_id!r} and cannot be reached "
                f"from org {org_id!r}"
            )

    @staticmethod
    def guard_mutable(report: RegulatoryReport) -> None:
        """**A finalized report is never edited.**

        Not re-prepared, not re-validated, not amended. A correction opens a *new* report with its
        own pinned inputs, and both stay readable — rewriting a filing is not a correction, it is a
        disappearance."""
        if report.is_finalized:
            raise ReportRefused(
                f"report {report.id!r} is finalized and is never edited. File a correction: it "
                "opens a new report with its own pinned inputs, and both remain readable so an "
                "examiner can see what changed between versions"
            )

    @staticmethod
    def guard_definition_publishable(
        existing: ReportDefinition | None, proposed_digest: str
    ) -> None:
        """Re-publishing a version must mean the same thing it meant the first time.

        If it did not, every report already bound to that version would have been produced by a
        definition nobody can now read — which is the quiet failure §4 forbids. A changed rule gets
        a new version."""
        if existing is None:
            return
        if existing.definition_digest != proposed_digest:
            raise ReportRefused(
                f"report definition {existing.report_code!r} v{existing.version} is already "
                "published with different content. A reporting rule that changes gets a **new "
                "version**; reports already bound to this one must keep meaning what they meant"
            )

    @staticmethod
    def guard_finalizable(
        report: RegulatoryReport,
        *,
        validation: ValidationResult | None,
        exceptions: tuple[ReportException, ...],
    ) -> None:
        """**The §22 refusal: a report that cannot satisfy mandatory evidence must not finalize.**

        Three checks, on freshly-read rows inside the transaction. The FSM already refuses to reach
        `FINALIZED` from anywhere but `READY`; this catches the rest — a validation that never ran
        or no longer passes, a blocking exception raised since, and a blocked completeness."""
        if validation is None or not validation.passed:
            failed = (
                ", ".join(c.name for c in validation.failures) if validation else "never ran"
            )
            raise ReportRefused(
                f"report {report.id!r} cannot be finalized: validation {failed}. A filing that "
                "skipped validation is a filing nobody checked"
            )
        blocking = [e for e in exceptions if e.blocks_finalization]
        if blocking:
            raise ReportRefused(
                f"report {report.id!r} cannot be finalized: "
                + "; ".join(
                    f"{e.kind.value} on {e.subject}" for e in sorted(blocking, key=lambda e: e.id)
                )
                + ". Acknowledging an exception records that somebody saw it — it does not make "
                "the problem go away. Re-prepare once the source is available"
            )
        if report.completeness is ReportCompleteness.BLOCKED:
            raise ReportRefused(
                f"report {report.id!r} is {report.completeness.value}: a required source could "
                "not be read or a mandatory field has no value. Missing evidence is never filed "
                "as zero"
            )

    @staticmethod
    def guard_correction_has_moved(
        original: RegulatoryReport, proposed_manifest_digest: str
    ) -> None:
        """A correction whose inputs have not moved corrects nothing.

        Without this the derived identity would compute the id of a report that already exists, and
        the caller would receive the original back believing a correction had been filed. The same
        no-op failure M9.20's D-1 recorded, refused up front here rather than discovered later."""
        if original.manifest_digest == proposed_manifest_digest:
            raise ReportRefused(
                f"report {original.id!r} was produced from exactly these inputs; there is nothing "
                "to correct. A correction exists because a source moved or the definition version "
                "changed — if neither has, the filed report is still the right one"
            )

    @staticmethod
    def guard_correctable(original: RegulatoryReport) -> None:
        """Only a finalized report is corrected. A draft is simply re-prepared."""
        if not original.is_finalized:
            raise ReportRefused(
                f"report {original.id!r} is {original.status.value}, not finalized; re-prepare it "
                "rather than correcting it. A correction supersedes something already filed"
            )
        if original.corrected_by:
            raise ReportRefused(
                f"report {original.id!r} has already been corrected by {original.corrected_by!r}; "
                "correct that one instead, so the chain of filings stays linear"
            )

    @staticmethod
    def guard_packageable(report: RegulatoryReport) -> None:
        if not report.is_finalized:
            raise ReportRefused(
                f"report {report.id!r} is {report.status.value}; only a finalized report is "
                "packaged for submission"
            )

    @staticmethod
    def guard_period_reportable(period: ReportingPeriod, definition: ReportDefinition) -> None:
        if period.basis is not definition.period_basis:
            raise ReportRefused(
                f"definition {definition.report_code!r} consumes {definition.period_basis.value} "
                f"and period {period.period_key!r} is a {period.basis.value}. These are different "
                "concepts and Billing owns four of them; filing one as the other would be wrong "
                "in a way nobody notices until an audit"
            )


class ExceptionGuardService:
    """**An exception is never silently cleared.**"""

    @staticmethod
    def guard_acknowledgeable(exception: ReportException) -> None:
        if exception.state is not ExceptionState.OPEN:
            raise ReportRefused(
                f"report exception {exception.id!r} is {exception.state.value}; re-acknowledging "
                "would overwrite who saw it first and when"
            )


# ── read services ───────────────────────────────────────────────────────────────


class ProvenanceService:
    """Assembles the §25 answer **from stored rows alone**.

    Nothing here reads a current source. Every field comes from the pinned manifest, the definition
    version the run recorded, and the snapshot — which is what makes the answer stable after every
    source has moved."""

    @staticmethod
    def traces(
        definition: ReportDefinition, report: RegulatoryReport
    ) -> tuple[tuple[str, str, str, str, str, str, tuple[str, ...], str], ...]:
        by_key = {v.key: v for v in report.values}
        out = []
        for spec in sorted(definition.fields, key=lambda f: f.key):
            value = by_key.get(spec.key)
            out.append(
                (
                    spec.key,
                    spec.label,
                    spec.domain.value,
                    spec.source_type.value,
                    spec.transformation.value,
                    value.status.value if value else "absent",
                    value.source_locators if value else (),
                    value.detail if value else "the run computed no value for this field",
                )
            )
        return tuple(out)


class ReportDiffService:
    """What changed between two filings of one report. **The question a correction exists to
    answer**, and one no other milestone can."""

    @staticmethod
    def compare(
        earlier: RegulatoryReport,
        later: RegulatoryReport,
        *,
        earlier_manifest: ReportInputManifest | None = None,
        later_manifest: ReportInputManifest | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
        """`(changed, added, removed, changed_sources, definition_version_changed)`.

        Values are compared by their **canonical digest**, so a field is "changed" only when its
        content genuinely differs — not because two equal amounts were constructed differently."""

        def fingerprint(v: ReportFieldValue) -> str:
            return canonical_digest(
                {
                    "status": v.status.value,
                    "transformation": v.transformation.value,
                    "amount_minor": None if v.amount is None else v.amount.minor,
                    "currency": None if v.currency is None else str(v.currency),
                    "number": v.number,
                    "text": v.text,
                }
            )

        before = {v.key: fingerprint(v) for v in earlier.values}
        after = {v.key: fingerprint(v) for v in later.values}
        changed = tuple(sorted(k for k in before.keys() & after.keys() if before[k] != after[k]))
        added = tuple(sorted(after.keys() - before.keys()))
        removed = tuple(sorted(before.keys() - after.keys()))

        changed_sources: tuple[str, ...] = ()
        if earlier_manifest is not None and later_manifest is not None:
            old = {s.locator.split("@")[0]: s for s in earlier_manifest.sources}
            new = {s.locator.split("@")[0]: s for s in later_manifest.sources}
            changed_sources = tuple(
                sorted(
                    key
                    for key in old.keys() | new.keys()
                    if key not in old
                    or key not in new
                    or old[key].digest != new[key].digest
                    or old[key].revision != new[key].revision
                )
            )
        return (
            changed,
            added,
            removed,
            changed_sources,
            earlier.definition_version != later.definition_version,
        )


class ReportPostureService:
    """Fleet-wide counts for a compliance queue. **Counts only** — never a figure."""

    @staticmethod
    def live_states() -> frozenset[ReportStatus]:
        return LIVE_REPORT_STATES

    @staticmethod
    def terminal_states() -> frozenset[ReportStatus]:
        return TERMINAL_REPORT_STATES


# ── validation of the domain's own tables ───────────────────────────────────────


class ReportingValidator:
    """Structural validation. Returns diagnostics; the factory decides what is fatal (§A2.6)."""

    def validate_definition(self, definition: ReportDefinition) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not definition.org_id:
            out.append(self._error("org_id", "a report definition must be org-scoped"))
        if not definition.report_code:
            out.append(self._error("report_code", "a report definition must name its report"))
        if definition.version < 1:
            out.append(self._error("version", "definition versions start at 1"))
        if not definition.fields:
            out.append(
                self._error("fields", "a report definition with no fields reports nothing")
            )
        keys = [f.key for f in definition.fields]
        if len(keys) != len(set(keys)):
            out.append(self._error("fields", "field keys must be unique within a definition"))
        if (
            definition.currency_basis is CurrencyBasis.SINGLE_DECLARED
            and definition.declared_currency is None
        ):
            out.append(
                self._error(
                    "declared_currency",
                    "a SINGLE_DECLARED definition must name the currency it reports in; "
                    "otherwise there is no basis against which a source currency is checked",
                )
            )
        if (
            definition.currency_basis is not CurrencyBasis.SINGLE_DECLARED
            and definition.declared_currency is not None
        ):
            out.append(
                self._error(
                    "declared_currency",
                    "a declared currency is meaningful only under SINGLE_DECLARED",
                )
            )
        if definition.monetary_fields and definition.currency_basis is (
            CurrencyBasis.NOT_APPLICABLE
        ):
            out.append(
                self._error(
                    "currency_basis",
                    "the definition has monetary fields, so it must declare a currency basis; "
                    "combining currencies implicitly is never permitted",
                )
            )
        for spec in definition.fields:
            if spec.transformation is ReportTransformation.CLASSIFY and not spec.classification_map:
                out.append(
                    self._error(
                        f"fields.{spec.key}",
                        "a CLASSIFY field must declare its classification map; an unversioned "
                        "mapping is a regulatory rule hidden in code",
                    )
                )
        return tuple(out)

    def validate_period(self, period: ReportingPeriod) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not period.org_id:
            out.append(self._error("org_id", "a reporting period must be org-scoped"))
        if not period.period_key:
            out.append(self._error("period_key", "a reporting period must be labelled"))
        return tuple(out)

    def validate_report(self, report: RegulatoryReport) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not report.org_id:
            out.append(self._error("org_id", "a report must be org-scoped"))
        if not report.manifest_digest:
            out.append(
                self._error(
                    "manifest_digest",
                    "a report's identity derives from its input manifest; without a digest it "
                    "cannot be reproduced",
                )
            )
        if report.definition_version < 1:
            out.append(
                self._error(
                    "definition_version",
                    "a report is bound to the definition version that produced it",
                )
            )
        return tuple(out)

    def validate_source(self, source: ReportSourceRef) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not source.source_id:
            out.append(
                self._error(
                    "source_id",
                    "every source reference must be machine-resolvable; there is no free-text "
                    "source in this milestone",
                )
            )
        if source.status is SourceStatus.UNAVAILABLE and not source.unavailable_reason:
            out.append(
                self._error(
                    "unavailable_reason",
                    "an unavailable source must say why; a gap with no reason cannot be acted on",
                )
            )
        if source.status in SATISFYING_SOURCE_STATUS and source.unavailable_reason:
            out.append(
                self._error(
                    "unavailable_reason",
                    "a resolved source carries no unavailability reason",
                )
            )
        return tuple(out)

    @staticmethod
    def _error(field_name: str, message: str) -> Diagnostic:
        return Diagnostic(
            code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
            severity=Severity.ERROR,
            message=message,
            path=f"reporting.{field_name}",
        )


__all__ = [
    "ReportCompletenessService",
    "ExceptionGuardService",
    "ProvenanceService",
    "ReportDiffService",
    "ReportExceptionService",
    "ReportGuardService",
    "ReportLifecycleService",
    "ReportPostureService",
    "ReportRefused",
    "ReportStale",
    "ReportValidationService",
    "ReportingTransformationService",
    "ReportingValidator",
    "UnsupportedReportRule",
]
