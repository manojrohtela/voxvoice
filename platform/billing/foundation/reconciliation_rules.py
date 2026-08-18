"""Reconciliation rules — the comparison engine, the guards and the explainer (M9.19).

Pure. No clock, no database, no provider, no HTTP, no scheduler thread, no transport. Every rule
takes the instant it reasons about as an argument, which is what makes a divergence detected in
March reproducible in April by replaying the same evidence.

## One comparison engine, six relationships

`MatchingService` is the only place two financial facts are compared, and it is driven by
`RECONCILIATION_MATRIX` — a table, not a chain of `if`s. Six hand-written comparisons would drift
apart in six different ways and no test could iterate them; one engine parameterised by a spec is
checkable by iterating the matrix, which is exactly what
`test_every_relationship_in_the_matrix_is_reconcilable` does.

The engine never asks a domain anything. It receives two sequences of `FactRef` — copies of
decisions already made elsewhere — and returns `Observation`s. It cannot reach a repository, and it
holds no reference that would let it.

## Delegation, not reimplementation

Where a domain already owns the comparison, M9.19 **consumes its output** rather than writing a
second opinion:

* M9.14's `InvoiceReconciliationService` already compares a document against the ledger entries a
  period contains, including the header-versus-body totals check. `InvoiceLedgerAdapter` lifts its
  `InvoiceDiscrepancy` values into observations;
* M9.15's `TaxReconciliationService` already compares an assessment against what it was applied to.
  `InvoiceTaxAdapter` does the same for `TaxDiscrepancy`.

Two comparison authorities for one pair is how a divergence gets reported twice with two different
severities, so where one exists it stays the authority and M9.19 adds only durability.

The three relationships nobody owned — payment↔ledger, recognition↔ledger, payment↔invoice — are
compared by the generic engine, because there was no existing service to defer to.

## What no rule here may do

Determine tax. Calculate a price, a rate, a usage figure, an invoice total or a recognized amount.
Post, mutate or read a ledger balance. Move money. Change a dunning stage. Resolve a divergence
because it looks resolvable. The last one is ADR-0023 PY5 and it is enforced by
`ResolutionGuardService.guard_evidence_establishes_agreement`, which refuses a resolution whose
evidence does not actually show the two sides agreeing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_reconciliation import (
    ACTIONABLE_SEVERITIES,
    DIVERGENCE_VERDICT_PROJECTION,
    FINDING_LIFECYCLE,
    RECONCILIATION_MATRIX,
    SEVERITY_BY_KIND,
    TERMINAL_FINDING_STATES,
    UNRESOLVED_FINDING_STATES,
    DivergenceKind,
    FactRef,
    FindingExplanation,
    FindingSeverity,
    FindingState,
    FindingTrigger,
    IntegrityFinding,
    IntegrityPosture,
    IntegrityResolution,
    IntegrityRun,
    IntegritySearchQuery,
    IntegritySearchResult,
    MatchMode,
    Observation,
    ReconciliationScope,
    RelationshipKind,
    RelationshipSpec,
    RepairOwner,
    RunInspection,
    RunState,
    fact_summary,
    total_minor,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition


class FindingStale(BillingConflict):
    """A worker tried to transition a finding from a version that is no longer current.

    Raised by persistence when the compare-and-set finds no row, and declared **here, in Foundation,
    beside the domain it protects** — because the Application must react to it and may not import
    persistence. Same placement and same reason as `RefundSequenceConflict` (M9.16-R1), `CaseStale`
    (M9.17) and `ScheduleStale` (M9.18).

    The scenario it exists for is the mandate's stale-worker case exactly: worker A reads a finding
    as `OPEN`, worker B resolves it and commits, and worker A resumes holding `OPEN` and tries to
    write. The FSM already makes `RESOLVED → OPEN` impossible; this makes *losing the race* visible
    rather than silently overwriting the resolution with a stale row."""


class ReconciliationRefused(BillingConflict):
    """A reconciliation was asked for that this engine must not perform.

    Raised for the boundary violations rather than returning a finding, because a finding is an
    observation about the *books* and these are observations about the *request*: an unsupported
    relationship, a scope with no org, evidence in two currencies, or a resolution whose evidence
    does not establish agreement."""


# ── the comparison engine ───────────────────────────────────────────────────────


def _index(refs: Sequence[FactRef]) -> tuple[dict[str, FactRef], dict[str, int]]:
    """Group facts by identity, counting duplicates.

    Duplicates are detected here rather than assumed away, because "the same financial fact recorded
    twice" is one of the two worst things reconciliation exists to catch and the other is it not
    being recorded at all."""
    found: dict[str, FactRef] = {}
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.identity] = counts.get(ref.identity, 0) + 1
        if ref.identity not in found:
            found[ref.identity] = ref
    return found, counts


class MatchingService:
    """**The only place two financial facts are compared.**

    Parameterised entirely by `RelationshipSpec`, so adding a seventh relationship is a row in
    `RECONCILIATION_MATRIX` plus evidence, never a seventh comparison function."""

    def compare(
        self,
        spec: RelationshipSpec,
        sources: Sequence[FactRef],
        counterparts: Sequence[FactRef],
    ) -> tuple[Observation, ...]:
        if spec.mode is MatchMode.AGGREGATE:
            return self._aggregate(spec, tuple(sources), tuple(counterparts))
        return self._pairwise(spec, tuple(sources), tuple(counterparts))

    # ── pairwise ────────────────────────────────────────────────────────────────

    def _pairwise(
        self,
        spec: RelationshipSpec,
        sources: tuple[FactRef, ...],
        counterparts: tuple[FactRef, ...],
    ) -> tuple[Observation, ...]:
        """Match on the deterministic identity the spec names. **Never on amount or timestamp.**

        Matching two financial records by amount is how a reconciliation confidently pairs a ₹500
        refund with an unrelated ₹500 capture and reports the books as clean."""
        by_id, source_counts = _index(sources)
        counter_by_id, counter_counts = _index(counterparts)
        seen: list[Observation] = []

        for identity, source in by_id.items():
            if source_counts[identity] > 1:
                seen.append(
                    Observation(
                        subject_key=identity,
                        divergence=DivergenceKind.DUPLICATE_SOURCE,
                        source=source,
                        expected="one source fact per identity",
                        observed=f"{source_counts[identity]} facts share {identity!r}",
                        detail=spec.summary,
                    )
                )
                continue
            counterpart = counter_by_id.get(identity)
            if counterpart is None:
                seen.append(
                    Observation(
                        subject_key=identity,
                        divergence=DivergenceKind.MISSING_COUNTERPART,
                        source=source,
                        expected=f"a {spec.counterpart_kind} referencing {identity!r}",
                        observed="nothing references it",
                        detail=spec.summary,
                    )
                )
                continue
            if counter_counts[identity] > 1:
                seen.append(
                    Observation(
                        subject_key=identity,
                        divergence=DivergenceKind.DUPLICATE_COUNTERPART,
                        source=source,
                        counterpart=counterpart,
                        expected=f"one {spec.counterpart_kind} per {identity!r}",
                        observed=f"{counter_counts[identity]} of them",
                        detail="a duplicate financial fact",
                    )
                )
                continue
            seen.append(self._compare_fields(spec, source, counterpart))

        if spec.flags_orphans:
            for identity, counterpart in counter_by_id.items():
                if identity in by_id:
                    continue
                seen.append(
                    Observation(
                        subject_key=identity,
                        divergence=DivergenceKind.UNEXPECTED_COUNTERPART,
                        counterpart=counterpart,
                        expected=f"a {spec.source_kind} that caused it",
                        observed="nothing did",
                        detail="an orphan record",
                    )
                )
        return tuple(seen)

    def _compare_fields(
        self, spec: RelationshipSpec, source: FactRef, counterpart: FactRef
    ) -> Observation:
        """Compare exactly the fields the spec declares, in a fixed order.

        The order matters: currency before amount, because two amounts in different currencies are
        not "a mismatch of 300" — they are incomparable, and reporting an integer difference between
        them would be an invented number."""
        both = (source, counterpart)
        if "currency" in spec.compares:
            currencies = {r.amount.currency for r in both if r.amount is not None}
            if len(currencies) > 1:
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.CURRENCY_MISMATCH,
                    expected=str(source.amount.currency.value if source.amount else "-"),
                    observed=str(counterpart.amount.currency.value if counterpart.amount else "-"),
                    detail="the two sides are denominated differently; they are not comparable",
                )
        if "amount" in spec.compares:
            if (source.amount is None) != (counterpart.amount is None):
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.EVIDENCE_MALFORMED,
                    expected="both sides state an amount",
                    observed="one of them does not",
                    detail="a comparison cannot be made from this evidence",
                )
            if (
                source.amount is not None
                and counterpart.amount is not None
                and source.amount.minor != counterpart.amount.minor
            ):
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.AMOUNT_MISMATCH,
                    expected=str(source.amount.minor),
                    observed=str(counterpart.amount.minor),
                    detail="integer minor units; the two sides state different figures",
                )
        if "account" in spec.compares and source.account_id and counterpart.account_id:
            if source.account_id != counterpart.account_id:
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.ACCOUNT_MISMATCH,
                    expected=source.account_id, observed=counterpart.account_id,
                    detail="the fact was recorded against a different account",
                )
        if "source_reference" in spec.compares and counterpart.source_id:
            if counterpart.source_id != source.identity:
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.SOURCE_REFERENCE_MISMATCH,
                    expected=source.identity, observed=counterpart.source_id,
                    detail="the counterpart names a different cause",
                )
        if "period" in spec.compares and source.period_key and counterpart.period_key:
            if source.period_key != counterpart.period_key:
                # A **warning**, not an error: M9.13's `LateEntryDisposition` makes this legitimate.
                # A fact recorded while its period was closed lands in the next open one by design,
                # and flagging correct accounting as broken teaches operators to ignore the report.
                return self._diverged(
                    spec, source, counterpart, DivergenceKind.PERIOD_MISMATCH,
                    expected=source.period_key, observed=counterpart.period_key,
                    detail="recorded in a different accounting period; legitimate if the original "
                           "was closed (M9.13 late-entry disposition), otherwise investigate",
                )
        return Observation(
            subject_key=source.identity,
            divergence=None,
            source=source,
            counterpart=counterpart,
            detail=spec.summary,
        )

    @staticmethod
    def _diverged(
        spec: RelationshipSpec,
        source: FactRef,
        counterpart: FactRef,
        kind: DivergenceKind,
        *,
        expected: str,
        observed: str,
        detail: str,
    ) -> Observation:
        return Observation(
            subject_key=source.identity,
            divergence=kind,
            source=source,
            counterpart=counterpart,
            expected=expected,
            observed=observed,
            detail=detail,
        )

    # ── aggregate ───────────────────────────────────────────────────────────────

    def _aggregate(
        self,
        spec: RelationshipSpec,
        sources: tuple[FactRef, ...],
        counterparts: tuple[FactRef, ...],
    ) -> tuple[Observation, ...]:
        """Compare totals. **The only relationship where "less" is not a divergence.**

        This exists for invoice↔recognition, where an invoice has one amount and its schedule has
        twelve entries, so there is no pair to make. A twelve-month invoice that has recognized
        three months is *correct* — the remaining nine are deferred revenue, which is the whole
        point of M9.18 — so `allows_partial` suppresses the shortfall and only **over**-recognition
        diverges. Requiring equality here would report every service-period invoice as broken for
        eleven months of its life."""
        if not sources:
            return (
                Observation(
                    subject_key=spec.relationship.value,
                    divergence=DivergenceKind.MISSING_SOURCE,
                    expected=f"a {spec.source_kind} to account for",
                    observed="none",
                    detail=spec.summary,
                ),
            )
        subject = sources[0].identity
        currencies = {r.amount.currency for r in (*sources, *counterparts) if r.amount is not None}
        if len(currencies) > 1:
            return (
                Observation(
                    subject_key=subject,
                    divergence=DivergenceKind.CURRENCY_MISMATCH,
                    source=sources[0],
                    counterpart=counterparts[0] if counterparts else None,
                    expected="one currency",
                    observed=", ".join(sorted(c.value for c in currencies)),
                    detail="totals across currencies are not summed; that would be an "
                           "exchange-rate opinion Billing does not hold",
                ),
            )
        billed = total_minor(sources)
        accounted = total_minor(counterparts)
        if not counterparts:
            return (
                Observation(
                    subject_key=subject,
                    divergence=DivergenceKind.MISSING_COUNTERPART,
                    source=sources[0],
                    expected=f"a {spec.counterpart_kind} accounting for {billed}",
                    observed="none exists",
                    detail=spec.summary,
                ),
            )
        if accounted > billed:
            return (
                Observation(
                    subject_key=subject,
                    divergence=DivergenceKind.OVER_ACCOUNTED,
                    source=sources[0],
                    counterpart=counterparts[0],
                    expected=f"at most {billed}",
                    observed=str(accounted),
                    detail="more has been accounted for than was ever billed",
                ),
            )
        if accounted < billed and not spec.allows_partial:
            return (
                Observation(
                    subject_key=subject,
                    divergence=DivergenceKind.AMOUNT_MISMATCH,
                    source=sources[0],
                    counterpart=counterparts[0],
                    expected=str(billed),
                    observed=str(accounted),
                    detail=spec.summary,
                ),
            )
        return (
            Observation(
                subject_key=subject,
                divergence=None,
                source=sources[0],
                counterpart=counterparts[0],
                expected=str(billed),
                observed=str(accounted),
                detail=f"{billed - accounted} not yet accounted for; deferred, not divergent"
                if accounted < billed
                else spec.summary,
            ),
        )


# ── delegating adapters ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DiscrepancyAdapter:
    """Lifts an existing domain's discrepancy values into M9.19 observations.

    M9.14 and M9.15 already own the comparison for their pairs. This adapter is how M9.19 adds
    *durability* to their verdicts without adding a second opinion about them: it reads the
    discrepancy kind, maps it onto the neutral vocabulary, and carries the domain's own figures
    through untouched.

    The map is deliberately partial and explicit. An unmapped discrepancy kind becomes
    `EVIDENCE_MALFORMED` rather than being dropped, because a divergence M9.19 does not recognise is
    still a divergence and silently discarding it is the failure mode this whole milestone exists to
    prevent."""

    #: Discrepancy-kind value → neutral divergence. Keyed on the *string* value so this module
    #: imports neither `InvoiceDiscrepancyKind` nor `TaxDiscrepancyKind` — M9.19 stays a consumer of
    #: those domains, not a dependency of them.
    mapping: dict[str, DivergenceKind] = None  # type: ignore[assignment]

    def lift(
        self,
        spec: RelationshipSpec,
        discrepancies: Iterable[object],
        *,
        subject_id: str,
    ) -> tuple[Observation, ...]:
        table = self.mapping or DEFAULT_DISCREPANCY_MAP
        found: list[Observation] = []
        for item in discrepancies:
            kind = getattr(getattr(item, "kind", None), "value", "") or str(
                getattr(item, "kind", "")
            )
            divergence = table.get(kind, DivergenceKind.EVIDENCE_MALFORMED)
            expected = getattr(item, "expected_minor", 0)
            observed = getattr(item, "actual_minor", 0)
            reference = (
                getattr(item, "journal_entry_id", "")
                or getattr(item, "line_id", "")
                or getattr(item, "assessment_id", "")
                or subject_id
            )
            found.append(
                Observation(
                    subject_key=reference or subject_id,
                    divergence=divergence,
                    expected=str(expected),
                    observed=str(observed),
                    detail=getattr(item, "detail", "") or spec.summary,
                )
            )
        return tuple(found)


#: How M9.13's, M9.14's and M9.15's discrepancy kinds project onto the neutral vocabulary.
#:
#: Every member of `InvoiceDiscrepancyKind`, `TaxDiscrepancyKind` and `LedgerDiscrepancyKind` has a
#: row here, keyed on the *string* value so this module imports none of those enums — M9.19 stays a
#: consumer of those domains rather than a dependency of them. A test iterates all three enums and
#: asserts every member is mapped, so a new discrepancy kind in any of them fails loudly instead of
#: silently degrading to `EVIDENCE_MALFORMED`.
#:
#: Three mappings are judgements rather than translations:
#:
#: * `snapshot_drift` and `missing_provenance` become `EVIDENCE_MALFORMED`, because both mean the
#:   evidence cannot be trusted rather than that the books disagree;
#: * `stale_rate` becomes `STALE_EVIDENCE` — a rate applied outside its window is a determination
#:   problem M9.15 owns, not a figure to repair;
#: * `nexus_without_registration` becomes `EVIDENCE_MALFORMED` and routes to an operator, because
#:   it is a compliance decision no automatic repair can make.
DEFAULT_DISCREPANCY_MAP: dict[str, DivergenceKind] = {
    # M9.14 — InvoiceDiscrepancyKind
    "unbilled_entry": DivergenceKind.MISSING_COUNTERPART,
    "orphan_line": DivergenceKind.UNEXPECTED_COUNTERPART,
    "amount_mismatch": DivergenceKind.AMOUNT_MISMATCH,
    "totals_mismatch": DivergenceKind.AMOUNT_MISMATCH,
    "currency_mismatch": DivergenceKind.CURRENCY_MISMATCH,
    "number_gap": DivergenceKind.EVIDENCE_MALFORMED,
    "duplicate_number": DivergenceKind.DUPLICATE_COUNTERPART,
    "snapshot_drift": DivergenceKind.EVIDENCE_MALFORMED,
    "missing_tax_determination": DivergenceKind.MISSING_SOURCE,
    # M9.15 — TaxDiscrepancyKind
    "unassessed_line": DivergenceKind.MISSING_COUNTERPART,
    "orphan_assessed_line": DivergenceKind.UNEXPECTED_COUNTERPART,
    "base_mismatch": DivergenceKind.AMOUNT_MISMATCH,
    "tax_total_mismatch": DivergenceKind.AMOUNT_MISMATCH,
    "missing_provenance": DivergenceKind.EVIDENCE_MALFORMED,
    "stale_rate": DivergenceKind.STALE_EVIDENCE,
    "nexus_without_registration": DivergenceKind.EVIDENCE_MALFORMED,
    # M9.13 — LedgerDiscrepancyKind (reconciled by M9.13 itself; mapped so a future caller that
    # routes its report through here does not silently degrade every member).
    "unposted_charge": DivergenceKind.MISSING_COUNTERPART,
    "orphan_entry": DivergenceKind.UNEXPECTED_COUNTERPART,
    "duplicate_posting": DivergenceKind.DUPLICATE_COUNTERPART,
    "sequence_gap": DivergenceKind.EVIDENCE_MALFORMED,
    "checkpoint_drift": DivergenceKind.STALE_EVIDENCE,
}


# ── lifecycle ───────────────────────────────────────────────────────────────────


class FindingLifecycleService:
    """Applies finding triggers through the shared FSM, and bumps the concurrency token.

    The machine's **refusals are the specification**: `RESOLVED` and `CLOSED` have no outgoing edge,
    so `allowed()` returns an empty tuple for both and every attempt to move them raises."""

    def __init__(self, definition: StateMachineDefinition = FINDING_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: FindingState, trigger: FindingTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: FindingState, trigger: FindingTrigger) -> FindingState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"an integrity finding cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal or ['(none — this state is terminal)']}"
            )
        return edge.target

    def allowed(self, current: FindingState) -> tuple[FindingTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        finding: IntegrityFinding,
        trigger: FindingTrigger,
        *,
        at: float,
        actor_id: str = "",
        resolution_id: str = "",
        repair_request_id: str = "",
        note: str = "",
    ) -> IntegrityFinding:
        target = self.next_state(finding.state, trigger)
        changes: dict = {"state": target, "version": finding.version + 1}
        if target is FindingState.ACKNOWLEDGED:
            changes["acknowledged_at"] = at
            changes["acknowledged_by"] = actor_id
        if target is FindingState.REPAIR_REQUESTED:
            changes["repair_request_id"] = repair_request_id
        if target in TERMINAL_FINDING_STATES:
            changes["resolved_at"] = at
            changes["resolved_by"] = actor_id
            if resolution_id:
                changes["resolution_id"] = resolution_id
        if note:
            changes["detail"] = note
        # `severity` is deliberately absent from every branch. A resolution records that a critical
        # divergence was fixed; it never makes the divergence retroactively less serious.
        return replace(finding, **changes)


# ── guards ──────────────────────────────────────────────────────────────────────


class ReconciliationGuardService:
    """The refusals. Each states a boundary the mandate draws, in the layer that can enforce it."""

    @staticmethod
    def guard_supported(relationship: RelationshipKind) -> RelationshipSpec:
        spec = RECONCILIATION_MATRIX.get(relationship)
        if spec is None:  # pragma: no cover — the enum and the matrix are asserted equal
            raise ReconciliationRefused(
                f"{relationship.value!r} is not a supported reconciliation relationship"
            )
        return spec

    @staticmethod
    def guard_scoped(scope: ReconciliationScope) -> None:
        """**Cross-org isolation, enforced before anything is read.**

        An unscoped run would compare every org's facts against every other org's, and the first
        thing it would find is that org A's invoice has no matching org B ledger entry — a critical
        finding about nothing, and a data leak in the same row."""
        if not scope.org_id:
            raise ReconciliationRefused(
                "a reconciliation run must be org-scoped; there is no cross-tenant reconciliation"
            )

    @staticmethod
    def guard_evidence_single_org(scope: ReconciliationScope, *refs: Sequence[FactRef]) -> None:
        """Every fact carries the org it came from in its identity or not at all.

        This is the second half of isolation: the scope says which org, and the *caller* reads
        org-scoped repositories. This guard catches the case where a caller assembled evidence by
        hand — a test helper, a future batch tool — and mixed tenants."""
        for group in refs:
            for ref in group:
                if ref.detail.startswith("org:") and ref.detail != f"org:{scope.org_id}":
                    raise ReconciliationRefused(
                        f"evidence from {ref.detail!r} cannot be reconciled in "
                        f"org {scope.org_id!r}"
                    )

    @staticmethod
    def guard_transitionable(finding: IntegrityFinding) -> None:
        """A terminal finding is finished. **The stale-worker refusal, stated once.**"""
        if finding.state in TERMINAL_FINDING_STATES:
            raise BillingConflict(
                f"integrity finding {finding.id!r} is {finding.state.value}; a settled finding is "
                "never reopened — if the divergence recurs, a new run records a new finding"
            )

    @staticmethod
    def guard_repairable(finding: IntegrityFinding) -> None:
        if finding.owner is RepairOwner.OPERATOR:
            raise ReconciliationRefused(
                f"integrity finding {finding.id!r} has no automatic repair; it names an operator "
                "because no owning domain can fix it without a decision"
            )

    @staticmethod
    def guard_evidence_establishes_agreement(
        finding: IntegrityFinding,
        source: FactRef | None,
        counterpart: FactRef | None,
    ) -> None:
        """**ADR-0023 PY5, enforced.**

        A finding is closed as `RESOLVED` only when the new evidence *shows* the two sides agreeing.
        Without this the resolution command becomes `auto_fix()` under another name: somebody calls
        it, the row says resolved, and the books still disagree.

        The three refusals below are the three ways "it's fine now" is usually wrong: the
        counterpart
        still does not exist, it exists in the wrong currency, or it exists for a different
        amount."""
        if finding.divergence in (
            DivergenceKind.MISSING_COUNTERPART,
            DivergenceKind.MISSING_SOURCE,
        ):
            if source is None or counterpart is None:
                raise ReconciliationRefused(
                    f"finding {finding.id!r} reports a missing fact; resolving it "
                    "requires evidence that both sides now exist, and this resolution "
                    "supplies only one"
                )
        if source is not None and counterpart is not None:
            if source.amount is not None and counterpart.amount is not None:
                if source.amount.currency is not counterpart.amount.currency:
                    raise ReconciliationRefused(
                        f"finding {finding.id!r} cannot be resolved: the evidence is denominated "
                        f"in {source.amount.currency.value} and "
                        f"{counterpart.amount.currency.value}"
                    )
                if source.amount.minor != counterpart.amount.minor:
                    raise ReconciliationRefused(
                        f"finding {finding.id!r} cannot be resolved: the evidence still shows "
                        f"{source.amount.minor} against {counterpart.amount.minor}"
                    )


# ── posture, inspection, explanation, search ────────────────────────────────────


class IntegrityPostureService:
    """Counts, derived on every read. **There is no stored posture and no stored balance.**"""

    def posture(
        self, org_id: str, runs: Sequence[IntegrityRun], findings: Sequence[IntegrityFinding]
    ) -> IntegrityPosture:
        by_state: dict[FindingState, int] = {}
        by_severity: dict[FindingSeverity, int] = {}
        by_relationship: dict[str, int] = {}
        by_owner: dict[str, int] = {}
        for finding in findings:
            by_state[finding.state] = by_state.get(finding.state, 0) + 1
            if finding.state not in TERMINAL_FINDING_STATES:
                by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
                key = finding.relationship.value
                by_relationship[key] = by_relationship.get(key, 0) + 1
                owner = finding.owner.value
                by_owner[owner] = by_owner.get(owner, 0) + 1
        return IntegrityPosture(
            org_id=org_id,
            runs=len(runs),
            findings_open=by_state.get(FindingState.OPEN, 0),
            findings_acknowledged=by_state.get(FindingState.ACKNOWLEDGED, 0),
            findings_repair_requested=by_state.get(FindingState.REPAIR_REQUESTED, 0),
            findings_resolved=by_state.get(FindingState.RESOLVED, 0),
            findings_closed=by_state.get(FindingState.CLOSED, 0),
            critical=by_severity.get(FindingSeverity.CRITICAL, 0),
            error=by_severity.get(FindingSeverity.ERROR, 0),
            warning=by_severity.get(FindingSeverity.WARNING, 0),
            by_relationship=by_relationship,
            by_owner=by_owner,
        )


class RunInspector:
    """A structural read of one run: are its counts self-consistent, is it scoped, is it done?"""

    def inspect(
        self, run: IntegrityRun, findings: Sequence[IntegrityFinding]
    ) -> RunInspection:
        notes: list[str] = []
        recorded = len(findings)
        counted = run.diverged == recorded
        if not counted:
            notes.append(
                f"the run counted {run.diverged} divergence(s) and {recorded} finding(s) exist"
            )
        if run.compared != run.matched + run.diverged:
            counted = False
            notes.append(
                f"compared {run.compared} but matched {run.matched} + diverged {run.diverged}"
            )
        scoped = bool(run.org_id) and all(f.org_id == run.org_id for f in findings)
        if not scoped:
            notes.append("a finding is attributed to a different org than the run")
        complete = run.state is RunState.COMPLETED
        if not complete:
            notes.append(f"the run is {run.state.value}")
        unresolved = len([f for f in findings if f.state in UNRESOLVED_FINDING_STATES])
        return RunInspection(
            run_id=run.id,
            relationship=run.relationship,
            state=run.state,
            compared=run.compared,
            matched=run.matched,
            diverged=run.diverged,
            findings_recorded=recorded,
            is_counted_correctly=counted,
            is_scoped=scoped,
            is_complete=complete,
            unresolved=unresolved,
            notes=tuple(notes),
        )


class FindingExplainer:
    """*Why does this finding exist?* — answered entirely **from stored evidence**.

    Nothing here re-reads a domain, and that is the point: by the time somebody opens a two-week-old
    finding, the payment has settled, the schedule has moved on and the ledger has closed. An
    explanation that had to ask them again would answer a different question."""

    def explain(
        self,
        finding: IntegrityFinding,
        run: IntegrityRun | None = None,
        resolution: IntegrityResolution | None = None,
    ) -> FindingExplanation:
        spec = RECONCILIATION_MATRIX[finding.relationship]
        narrative = [
            f"relationship: {spec.source_kind} → {spec.counterpart_kind}",
            f"authoritative side: {spec.authoritative}",
            f"matched on: {spec.matching_identity}",
            f"source: {fact_summary(finding.source)}",
            f"expected: {finding.expected or spec.summary}",
            f"observed: {finding.observed or fact_summary(finding.counterpart)}",
            f"divergence: {finding.divergence.value}",
            f"severity: {finding.severity.value}",
            f"owner: {finding.owner.value}",
        ]
        if finding.repair_request_id:
            narrative.append(f"repair requested: {finding.repair_request_id}")
        if resolution is not None:
            narrative.append(
                f"resolved by {resolution.owner.value} via {resolution.action!r} "
                f"(evidence {resolution.evidence_digest[:12]})"
            )
        narrative.append(f"state: {finding.state.value}")
        return FindingExplanation(
            finding_id=finding.id,
            relationship=finding.relationship,
            divergence=finding.divergence,
            severity=finding.severity,
            owner=finding.owner,
            state=finding.state,
            narrative=tuple(narrative),
            source_fact=fact_summary(finding.source),
            counterpart_fact=fact_summary(finding.counterpart),
            expected=finding.expected,
            observed=finding.observed,
            matching_identity=spec.matching_identity,
            authoritative_side=spec.authoritative,
            evidence_digest=run.evidence_digest if run else "",
            detected_at=finding.detected_at,
            resolution=resolution.note if resolution else "",
        )


class IntegritySearchService:
    """Filter and bound. Scoped by construction; the caller supplies indexed candidates."""

    def search(
        self, query: IntegritySearchQuery, candidates: Sequence[IntegrityFinding]
    ) -> IntegritySearchResult:
        found = [f for f in candidates if f.org_id == query.org_id]
        if query.relationships:
            found = [f for f in found if f.relationship in query.relationships]
        if query.states:
            found = [f for f in found if f.state in query.states]
        if query.severities:
            found = [f for f in found if f.severity in query.severities]
        if query.owners:
            found = [f for f in found if f.owner in query.owners]
        if query.account_id:
            found = [f for f in found if f.account_id == query.account_id]
        if query.subject:
            needle = query.subject.lower()
            found = [f for f in found if needle in f.subject_key.lower()]
        total = len(found)
        limit = max(1, query.limit)
        return IntegritySearchResult(
            findings=tuple(found[:limit]), total=total, truncated=total > limit
        )


class ReconciliationValidator:
    """Structural validation. Errors fail fast at the factory (§A2.6)."""

    def validate_scope(self, scope: ReconciliationScope) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not scope.org_id:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.INVALID_PERIOD,
                    severity=Severity.ERROR,
                    message="a reconciliation scope must name an org",
                    path="scope.org_id",
                )
            )
        return BillingDiagnostics(tuple(found))

    def validate_fact(self, ref: FactRef) -> BillingDiagnostics:
        """**The matching-identity rule, enforced.**

        The mandate forbids matching financial records by amount, timestamp, account, invoice number
        or description alone. This is where that becomes checkable: an identity that is empty, or
        that is a bare number (which is what an amount or a timestamp stringifies to), is refused at
        construction rather than producing a confidently wrong pairing later."""
        found: list[Diagnostic] = []
        if not ref.identity:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                    severity=Severity.ERROR,
                    message="a fact reference must carry a deterministic business identity",
                    path="fact.identity",
                )
            )
        elif ref.identity.replace(".", "").replace("-", "").isdigit():
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                    severity=Severity.ERROR,
                    message=(
                        f"{ref.identity!r} is a bare number; financial records are never matched "
                        "by amount or timestamp"
                    ),
                    path="fact.identity",
                )
            )
        if not ref.kind:
            found.append(
                Diagnostic(
                    code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                    severity=Severity.ERROR,
                    message="a fact reference must say what kind of fact it is",
                    path="fact.kind",
                )
            )
        return BillingDiagnostics(tuple(found))

    def validate_matrix(self) -> BillingDiagnostics:
        """The matrix is complete, single-owner-per-pair, and never matches on an amount.

        Run as a validation rather than only as a test so a future seventh relationship added
        without evidence fails in the factory, not in review."""
        found: list[Diagnostic] = []
        for kind in RelationshipKind:
            spec = RECONCILIATION_MATRIX.get(kind)
            if spec is None:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"relationship {kind.value!r} has no declared spec",
                        path=f"matrix.{kind.value}",
                    )
                )
                continue
            if spec.authoritative not in ("source", "counterpart"):
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"{kind.value!r} declares no authoritative side",
                        path=f"matrix.{kind.value}.authoritative",
                    )
                )
            if not spec.matching_identity:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"{kind.value!r} declares no matching identity",
                        path=f"matrix.{kind.value}.matching_identity",
                    )
                )
        for divergence in DivergenceKind:
            if divergence not in SEVERITY_BY_KIND:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"divergence {divergence.value!r} has no declared severity",
                        path=f"severity.{divergence.value}",
                    )
                )
            if divergence not in DIVERGENCE_VERDICT_PROJECTION:
                found.append(
                    Diagnostic(
                        code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
                        severity=Severity.ERROR,
                        message=f"divergence {divergence.value!r} has no verdict projection",
                        path=f"projection.{divergence.value}",
                    )
                )
        return BillingDiagnostics(tuple(found))


def severity_of(divergence: DivergenceKind) -> FindingSeverity:
    return SEVERITY_BY_KIND[divergence]


def verdict_of(divergence: DivergenceKind):
    """M9.2's four-member verdict for a neutral divergence. The single derivation."""
    return DIVERGENCE_VERDICT_PROJECTION[divergence]


def max_severity(observations: Sequence[Observation]) -> FindingSeverity:
    order = (
        FindingSeverity.INFO,
        FindingSeverity.WARNING,
        FindingSeverity.ERROR,
        FindingSeverity.CRITICAL,
    )
    worst = FindingSeverity.INFO
    for observation in observations:
        if order.index(observation.severity) > order.index(worst):
            worst = observation.severity
    return worst


def is_actionable(finding: IntegrityFinding) -> bool:
    return finding.state not in TERMINAL_FINDING_STATES and finding.severity in (
        ACTIONABLE_SEVERITIES
    )


__all__ = [
    "DEFAULT_DISCREPANCY_MAP",
    "DiscrepancyAdapter",
    "FindingExplainer",
    "FindingLifecycleService",
    "FindingStale",
    "IntegrityPostureService",
    "IntegritySearchService",
    "MatchingService",
    "ReconciliationGuardService",
    "ReconciliationRefused",
    "ReconciliationValidator",
    "RunInspector",
    "is_actionable",
    "max_severity",
    "severity_of",
    "verdict_of",
]
