"""Period Scope & Consolidation application layer (M9.23) — commands, queries, use cases.

Additive to M9.3/M9.6–M9.22: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary and dispatch discipline are reused exactly as they are. This module adds 11 commands, 14
queries, their use cases and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Accounts (M9.8) ─────read──▶  Scope resolution  ─▶ ScopeSnapshot
    Ledger / Invoice / Tax / Payment / Revenue ──read──▶ Consolidation ──▶ billing.consolidation.*
    Reconciliation / Integrity ────────────────read──▶        │

**Every arrow is a read, and there is no outward command.** M9.20 dispatched M9.19's repair
request; M9.21, M9.22 and M9.23 dispatch nothing, and `dispatch` is not injected. A consolidation
is a derived view of facts other domains own — it cannot ask one of them to change so the total
comes out rounder.

An architecture test asserts that no account, ledger, invoice, tax, payment, recognition,
collection, rating, metering, entitlement, subscription, reconciliation, incident, investigation or
reporting repository is *written* from this module, and that `_ConsolidationSourceReader` calls
only read verbs.

## Collection is batched, and the batching is deterministic

`CollectConsolidationSources` reads one page of scope members in **account-id order**, binds their
sources, and advances `collection_cursor`. Three consequences:

* 100K accounts do not have to fit in one transaction, one request or one process;
* a restart mid-collection resumes at the cursor rather than re-reading or skipping;
* two workers collecting concurrently write the same binding rows, because a binding's identity
  derives from `(run, account, domain, artifact)` — so the race is a no-op rather than a double
  count.

## Reading a source is pinning it, not copying it

`_ConsolidationSourceReader` resolves each domain for each member account and returns a
`SourceBinding` carrying the revision it read at. Three outcomes, and the third is the one this
milestone exists to protect:

* **RESOLVED** — the domain answered and had facts;
* **EMPTY** — the domain answered and genuinely had nothing. A legitimate zero;
* **UNAVAILABLE** — the domain could not be read, or cannot answer for the declared period basis.
  This never becomes zero, never becomes empty and never disappears: it carries `amount_minor is
  None`, raises a named exception, and stops completeness being `COMPLETE`.

A `BillingError` from an owning domain is a legitimate "cannot read". Anything else propagates —
the M9.16-R1 P-6 correction, applied here as in M9.21 and M9.22.

## No scheduler

`ResolveScope`, `CollectConsolidationSources`, `ValidateConsolidation` and `FinalizeConsolidation`
are invoked from outside. Nothing here loops, sleeps, polls or spawns.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from billing.foundation import (
    MAX_SCOPE_MEMBERS,
    SOURCE_CAPABILITIES,
    AccountContribution,
    BillingError,
    BillingPeriod,
    BillingValidationError,
    ConsolidationEventKind,
    ConsolidationException,
    ConsolidationExceptionKind,
    ConsolidationExceptionState,
    ConsolidationPeriod,
    ConsolidationRun,
    ConsolidationSearchQuery,
    ConsolidationSeverity,
    ConsolidationSnapshot,
    ConsolidationStatus,
    ConsolidationTrigger,
    ConsolidationValidation,
    EvidenceDomain,
    EvidenceSourceType,
    Money,
    PeriodBasis,
    ScopeCurrencyMode,
    ScopeDefinition,
    ScopeMode,
    ScopeSnapshot,
    SourceBinding,
    SourceCompleteness,
    SourceStatus,
    consolidation_source_digest,
    derive_consolidation_period_id,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

_CAS_ATTEMPTS = 5

#: How many member accounts one `CollectConsolidationSources` call binds. Bounded so a
#: hundred-thousand-account scope is a hundred requests rather than one that times out.
DEFAULT_COLLECTION_BATCH = 100
MAX_COLLECTION_BATCH = 500


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublishScopeDefinition(_Command):
    """Publish a scope rule **at a version**. Immutable thereafter.

    Re-publishing a version with different content is refused: consolidations already bound to it
    must keep covering the accounts they covered."""

    scope_code: str = ""
    version: int = 1
    mode: ScopeMode = ScopeMode.SINGLE_ACCOUNT
    title: str = ""
    anchor_account_id: str = ""
    member_account_ids: tuple[str, ...] = ()
    excluded_account_ids: tuple[str, ...] = ()
    currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED
    declared_currency: object = None
    period_basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD
    required_domains: tuple[EvidenceDomain, ...] = ()
    integrity_blocks_finalization: bool = False


@dataclass(frozen=True, slots=True)
class OpenConsolidationPeriod(_Command):
    basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD
    period_key: str = ""
    start: float = 0.0
    end: float = 0.0
    aligned_source_id: str = ""


@dataclass(frozen=True, slots=True)
class ResolveScope(_Command):
    """Resolve a scope definition into an immutable membership.

    Idempotent by identity: resolving an unchanged hierarchy twice produces one snapshot."""

    scope_definition_id: str = ""


@dataclass(frozen=True, slots=True)
class OpenConsolidation(_Command):
    scope_snapshot_id: str = ""
    period_id: str = ""


@dataclass(frozen=True, slots=True)
class CollectConsolidationSources(_Command):
    """Bind one batch of member accounts' sources. Called repeatedly until collection completes."""

    consolidation_id: str = ""
    batch_size: int = DEFAULT_COLLECTION_BATCH


@dataclass(frozen=True, slots=True)
class CompleteCollection(_Command):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class ValidateConsolidation(_Command):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class FinalizeConsolidation(_Command):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class RecollectConsolidation(_Command):
    """Send a blocked or ready run back to collection after its sources were fixed."""

    consolidation_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AcknowledgeConsolidationException(_Command):
    """Record that somebody saw a problem. **Does not unblock the consolidation.**"""

    exception_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class OpenConsolidationCorrection(_Command):
    """Open a new consolidation that supersedes a finalized one. Both survive."""

    consolidation_id: str = ""
    reason: str = ""


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetScopeDefinition(_Query):
    scope_definition_id: str = ""


@dataclass(frozen=True, slots=True)
class ListScopeDefinitions(_Query):
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetScopeSnapshot(_Query):
    scope_snapshot_id: str = ""


@dataclass(frozen=True, slots=True)
class ListScopeMembers(_Query):
    scope_snapshot_id: str = ""
    page: int = 1
    page_size: int = 100


@dataclass(frozen=True, slots=True)
class ListConsolidationPeriods(_Query):
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetConsolidation(_Query):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchConsolidations(_Query):
    scope_definition_id: str = ""
    period_key: str = ""
    statuses: tuple[ConsolidationStatus, ...] = ()
    corrections_only: bool = False
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True, slots=True)
class ListSourceBindings(_Query):
    consolidation_id: str = ""
    page: int = 1
    page_size: int = 100


@dataclass(frozen=True, slots=True)
class ListConsolidationExceptions(_Query):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetConsolidationValidation(_Query):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetConsolidationSnapshot(_Query):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetConsolidationProvenance(_Query):
    consolidation_id: str = ""


@dataclass(frozen=True, slots=True)
class GetConsolidationTimeline(_Query):
    consolidation_id: str = ""
    limit: int = 500


@dataclass(frozen=True, slots=True)
class GetConsolidationPosture(_Query):
    pass


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScopeDefinitionView:
    id: str
    scope_code: str
    version: int
    mode: str
    title: str
    anchor_account_id: str
    member_account_ids: tuple[str, ...]
    excluded_account_ids: tuple[str, ...]
    currency_mode: str
    declared_currency: str | None
    period_basis: str
    required_domains: tuple[str, ...]
    integrity_blocks_finalization: bool
    definition_digest: str
    inclusion_rule: str
    exclusion_rule: str
    hierarchy: str


@dataclass(frozen=True, slots=True)
class ScopeMemberView:
    account_id: str
    inclusion: str
    account_revision: str
    currency: str | None
    depth: int
    hierarchy_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExcludedAccountView:
    account_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class ScopeSnapshotView:
    id: str
    scope_definition_id: str
    scope_version: int
    mode: str
    hierarchy: str
    member_count: int
    excluded: tuple[ExcludedAccountView, ...]
    currency_mode: str
    declared_currency: str | None
    currencies: tuple[str, ...]
    membership_digest: str
    snapshot_digest: str
    effective_at: float


@dataclass(frozen=True, slots=True)
class ConsolidationPeriodView:
    id: str
    basis: str
    period_key: str
    start: float
    end: float
    aligned_source_id: str


@dataclass(frozen=True, slots=True)
class ConsolidatedAmountView:
    domain: str
    currency: str
    amount_minor: int
    contributing_accounts: int
    contributing_records: int


@dataclass(frozen=True, slots=True)
class ConsolidationView:
    id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_id: str
    period_basis: str
    period_key: str
    status: str
    completeness: str
    currency_mode: str
    declared_currency: str | None
    amounts: tuple[ConsolidatedAmountView, ...]
    member_count: int
    collected_count: int
    source_count: int
    unavailable_source_count: int
    exception_count: int
    blocking_exception_count: int
    consolidation_digest: str
    corrects: str
    superseded_by: str
    correction_reason: str
    allowed_triggers: tuple[str, ...]
    version: int
    finalized_at: float | None


@dataclass(frozen=True, slots=True)
class SourceBindingView:
    id: str
    account_id: str
    domain: str
    artifact: str
    read_name: str
    status: str
    completeness: str
    period_key: str
    period_applied: bool
    source_revision: str
    source_digest: str
    record_count: int
    amount_minor: int | None
    currency: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ConsolidationExceptionView:
    id: str
    kind: str
    severity: str
    state: str
    subject: str
    diagnostic: str
    account_id: str
    domain: str | None
    period_key: str
    created_at: float
    acknowledged_at: float | None
    acknowledged_by: str
    acknowledgement_note: str
    is_blocking: bool


@dataclass(frozen=True, slots=True)
class ConsolidationCheckView:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ConsolidationValidationView:
    consolidation_id: str
    passed: bool
    checks: tuple[ConsolidationCheckView, ...]
    validated_at: float
    validated_by: str


@dataclass(frozen=True, slots=True)
class ConsolidationSnapshotView:
    id: str
    consolidation_id: str
    consolidation_digest: str
    scope_snapshot_id: str
    scope_snapshot_digest: str
    period_key: str
    representation: str
    source_digest: str
    completeness: str
    finalized_at: float
    finalized_by: str


@dataclass(frozen=True, slots=True)
class AccountContributionView:
    account_id: str
    inclusion: str
    resolved_sources: int
    empty_sources: int
    unavailable_sources: int
    record_count: int
    currency: str | None
    contributed: bool


@dataclass(frozen=True, slots=True)
class ConsolidationProvenanceView:
    consolidation_id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_basis: str
    period_key: str
    hierarchy: str
    contributions: tuple[AccountContributionView, ...]
    excluded: tuple[ExcludedAccountView, ...]
    domains: tuple[str, ...]
    consolidation_digest: str
    corrects: str
    superseded_by: str


@dataclass(frozen=True, slots=True)
class ConsolidationEventView:
    id: str
    kind: str
    sequence: int
    subject: str
    detail: str
    actor: str
    occurred_at: float


@dataclass(frozen=True, slots=True)
class ConsolidationPostureView:
    total_runs: int
    by_status: dict
    by_completeness: dict
    open_exceptions: int
    blocking_exceptions: int
    scope_definitions: int


def scope_definition_view(d: ScopeDefinition) -> ScopeDefinitionView:
    rule = d.rule
    return ScopeDefinitionView(
        id=d.id,
        scope_code=d.scope_code,
        version=d.version,
        mode=d.mode.value,
        title=d.title,
        anchor_account_id=d.anchor_account_id,
        member_account_ids=d.member_account_ids,
        excluded_account_ids=d.excluded_account_ids,
        currency_mode=d.currency_mode.value,
        declared_currency=str(d.declared_currency) if d.declared_currency else None,
        period_basis=d.period_basis.value,
        required_domains=tuple(x.value for x in d.required_domains),
        integrity_blocks_finalization=d.integrity_blocks_finalization,
        definition_digest=d.definition_digest,
        inclusion_rule=rule.inclusion,
        exclusion_rule=rule.exclusion,
        hierarchy=rule.hierarchy.value,
    )


def scope_snapshot_view(s: ScopeSnapshot) -> ScopeSnapshotView:
    return ScopeSnapshotView(
        id=s.id,
        scope_definition_id=s.scope_definition_id,
        scope_version=s.scope_version,
        mode=s.mode.value,
        hierarchy=s.hierarchy.value,
        member_count=s.member_count,
        excluded=tuple(
            ExcludedAccountView(e.account_id, e.reason.value, e.detail) for e in s.excluded
        ),
        currency_mode=s.currency_mode.value,
        declared_currency=str(s.declared_currency) if s.declared_currency else None,
        currencies=tuple(str(c) for c in s.currencies),
        membership_digest=s.membership_digest,
        snapshot_digest=s.snapshot_digest,
        effective_at=s.effective_at,
    )


def consolidation_period_view(p: ConsolidationPeriod) -> ConsolidationPeriodView:
    return ConsolidationPeriodView(
        id=p.id,
        basis=p.basis.value,
        period_key=p.period_key,
        start=p.window.start,
        end=p.window.end,
        aligned_source_id=p.aligned_source_id,
    )


def consolidation_view(
    run: ConsolidationRun, allowed: tuple[ConsolidationTrigger, ...] = ()
) -> ConsolidationView:
    return ConsolidationView(
        id=run.id,
        scope_snapshot_id=run.scope_snapshot_id,
        scope_definition_id=run.scope_definition_id,
        scope_version=run.scope_version,
        period_id=run.period_id,
        period_basis=run.period_basis.value,
        period_key=run.period_key,
        status=run.status.value,
        completeness=run.completeness.value,
        currency_mode=run.currency_mode.value,
        declared_currency=str(run.declared_currency) if run.declared_currency else None,
        amounts=tuple(
            ConsolidatedAmountView(
                domain=a.domain.value,
                currency=str(a.currency),
                amount_minor=a.minor,
                contributing_accounts=a.contributing_accounts,
                contributing_records=a.contributing_records,
            )
            for a in run.amounts
        ),
        member_count=run.member_count,
        collected_count=run.collected_count,
        source_count=run.source_count,
        unavailable_source_count=run.unavailable_source_count,
        exception_count=run.exception_count,
        blocking_exception_count=run.blocking_exception_count,
        consolidation_digest=run.consolidation_digest,
        corrects=run.corrects,
        superseded_by=run.superseded_by,
        correction_reason=run.correction_reason,
        allowed_triggers=tuple(t.value for t in allowed),
        version=run.version,
        finalized_at=run.finalized_at,
    )


def source_binding_view(b: SourceBinding) -> SourceBindingView:
    return SourceBindingView(
        id=b.id,
        account_id=b.account_id,
        domain=b.domain.value,
        artifact=b.artifact.value,
        read_name=b.read_name,
        status=b.status.value,
        completeness=b.completeness.value,
        period_key=b.period_key,
        period_applied=b.period_applied,
        source_revision=b.source_revision,
        source_digest=b.source_digest,
        record_count=b.record_count,
        # **Never a zero for a gap.** An unavailable source carries no figure at all.
        amount_minor=b.amount_minor,
        currency=str(b.currency) if b.currency else None,
        detail=b.detail,
    )


def consolidation_exception_view(e: ConsolidationException) -> ConsolidationExceptionView:
    return ConsolidationExceptionView(
        id=e.id,
        kind=e.kind.value,
        severity=e.severity.value,
        state=e.state.value,
        subject=e.subject,
        diagnostic=e.diagnostic,
        account_id=e.account_id,
        domain=e.domain.value if e.domain else None,
        period_key=e.period_key,
        created_at=e.created_at,
        acknowledged_at=e.acknowledged_at,
        acknowledged_by=e.acknowledged_by,
        acknowledgement_note=e.acknowledgement_note,
        is_blocking=e.is_blocking,
    )


def consolidation_validation_view(v: ConsolidationValidation) -> ConsolidationValidationView:
    return ConsolidationValidationView(
        consolidation_id=v.consolidation_id,
        passed=v.passed,
        checks=tuple(ConsolidationCheckView(c.name, c.passed, c.detail) for c in v.checks),
        validated_at=v.validated_at,
        validated_by=v.validated_by,
    )


def consolidation_snapshot_view(s: ConsolidationSnapshot) -> ConsolidationSnapshotView:
    return ConsolidationSnapshotView(
        id=s.id,
        consolidation_id=s.consolidation_id,
        consolidation_digest=s.consolidation_digest,
        scope_snapshot_id=s.scope_snapshot_id,
        scope_snapshot_digest=s.scope_snapshot_digest,
        period_key=s.period_key,
        representation=s.representation,
        source_digest=s.source_digest,
        completeness=s.completeness.value,
        finalized_at=s.finalized_at,
        finalized_by=s.finalized_by,
    )


def account_contribution_view(c: AccountContribution) -> AccountContributionView:
    return AccountContributionView(
        account_id=c.account_id,
        inclusion=c.inclusion.value,
        resolved_sources=c.resolved_sources,
        empty_sources=c.empty_sources,
        unavailable_sources=c.unavailable_sources,
        record_count=c.record_count,
        currency=str(c.currency) if c.currency else None,
        contributed=c.contributed,
    )


# ── source reading ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _SourceOutcome:
    """What one domain returned for one account. **Three outcomes, never two.**"""

    status: SourceStatus
    amounts: tuple[Money, ...] = ()
    record_count: int = 0
    revision: str = ""
    reason: str = ""
    completeness: SourceCompleteness = SourceCompleteness.SOURCE_COMPLETE


class _ConsolidationSourceReader:
    """Resolves the domains a scope requires, per account. **The only place M9.23 touches another
    domain.**

    Every method reads. None writes, and none recomputes: a `Money` that reaches a consolidated
    aggregate came out of the owning aggregate exactly as that domain wrote it, and the only thing
    done to it downstream is integer addition of same-currency minor units.

    A domain with no period-scoped read returns `UNAVAILABLE` with the reason recorded in
    `SOURCE_CAPABILITIES` — **not an account-wide figure labelled as a period one**, which would be
    the silent approximation the mandate forbids."""

    def __init__(self, context) -> None:
        self._ctx = context

    def read(
        self, domain: EvidenceDomain, org_id: str, account_id: str, period_key: str
    ) -> _SourceOutcome:
        capability = SOURCE_CAPABILITIES.get(domain)
        if capability is None or not capability.is_available:
            return _SourceOutcome(
                status=SourceStatus.UNAVAILABLE,
                reason=(
                    capability.unavailable_reason
                    if capability
                    else f"no audited source contract exists for {domain.value}"
                ),
                completeness=SourceCompleteness.SOURCE_ABSENT,
            )
        reader = self._DOMAIN_READERS.get(domain)
        if reader is None:  # pragma: no cover - guarded by the capability table above
            return _SourceOutcome(
                status=SourceStatus.UNAVAILABLE,
                reason=f"{domain.value} declares a capability but publishes no reader",
                completeness=SourceCompleteness.SOURCE_ABSENT,
            )
        try:
            return reader(self, org_id, account_id, period_key)
        except BillingError as exc:
            # A refusal from an owning domain is a legitimate "cannot read". Anything else
            # propagates — the M9.16-R1 P-6 correction.
            return _SourceOutcome(
                status=SourceStatus.UNAVAILABLE,
                reason=f"{domain.value} refused the read: {exc}",
                completeness=SourceCompleteness.SOURCE_ABSENT,
            )

    def _seam(self, engine_attr: str, repo_attr: str):
        engine = getattr(self._ctx, engine_attr, None)
        if engine is None:
            return None
        return getattr(engine, repo_attr, None)

    def _unwired(self, seam: str) -> _SourceOutcome:
        return _SourceOutcome(
            status=SourceStatus.UNAVAILABLE,
            reason=f"the {seam} seam is not wired into this application",
            completeness=SourceCompleteness.SOURCE_ABSENT,
        )

    def _read_ledger(self, org_id: str, account_id: str, period_key: str) -> _SourceOutcome:
        entries = self._seam("ledger_engine", "entries")
        if entries is None:
            return self._unwired("ledger")
        found = tuple(entries.for_period(org_id, account_id, period_key))
        if not found:
            return _SourceOutcome(status=SourceStatus.EMPTY, revision=period_key)
        amounts = tuple(
            a for a in (getattr(e, "amount", None) for e in found) if isinstance(a, Money)
        )
        return _SourceOutcome(
            status=SourceStatus.RESOLVED,
            amounts=amounts,
            record_count=len(found),
            revision=_revision(found),
            completeness=(
                SourceCompleteness.SOURCE_COMPLETE
                if len(amounts) == len(found)
                else SourceCompleteness.SOURCE_PARTIAL
            ),
        )

    def _read_invoice(self, org_id: str, account_id: str, period_key: str) -> _SourceOutcome:
        documents = self._seam("invoice_engine", "documents")
        if documents is None:
            return self._unwired("invoice")
        # **The issued document, read as the document.** An invoice is never reconstructed from
        # ledger, rating, tax or payment — those would be a different figure wearing its name.
        found = tuple(
            d
            for d in documents.for_period(org_id, account_id, period_key)
            if getattr(d, "issued_revision", 0)
        )
        if not found:
            return _SourceOutcome(status=SourceStatus.EMPTY, revision=period_key)
        amounts = tuple(
            a for a in (getattr(d, "total", None) for d in found) if isinstance(a, Money)
        )
        return _SourceOutcome(
            status=SourceStatus.RESOLVED,
            amounts=amounts,
            record_count=len(found),
            revision=_revision(found),
            completeness=(
                SourceCompleteness.SOURCE_COMPLETE
                if len(amounts) == len(found)
                else SourceCompleteness.SOURCE_PARTIAL
            ),
        )

    def _read_reconciliation(
        self, org_id: str, account_id: str, period_key: str
    ) -> _SourceOutcome:
        findings = self._seam("reconciliation_engine", "findings")
        if findings is None:
            return self._unwired("reconciliation")
        found = tuple(findings.for_account(org_id, account_id))
        if not found:
            return _SourceOutcome(status=SourceStatus.EMPTY, revision=period_key)
        return _SourceOutcome(
            status=SourceStatus.RESOLVED,
            record_count=len(found),
            revision=_revision(found),
            # An account-keyed read cannot claim period completeness.
            completeness=SourceCompleteness.SOURCE_PARTIAL,
        )

    def _read_integrity(self, org_id: str, account_id: str, period_key: str) -> _SourceOutcome:
        incidents = self._seam("incident_engine", "incidents")
        if incidents is None:
            return self._unwired("integrity")
        found = tuple(incidents.live(org_id))
        if not found:
            return _SourceOutcome(status=SourceStatus.EMPTY, revision=period_key)
        return _SourceOutcome(
            status=SourceStatus.RESOLVED,
            record_count=len(found),
            revision=_revision(found),
            completeness=SourceCompleteness.SOURCE_PARTIAL,
        )

    #: Dispatch table rather than a chain of `if`s, so "which domains does M9.23 read?" is one
    #: readable mapping and adding a domain without an audited capability is impossible.
    _DOMAIN_READERS = {
        EvidenceDomain.LEDGER: _read_ledger,
        EvidenceDomain.INVOICE: _read_invoice,
        EvidenceDomain.RECONCILIATION: _read_reconciliation,
        EvidenceDomain.INTEGRITY: _read_integrity,
    }


def _revision(records) -> str:
    """A stable revision string for a set of source records: how many, and the newest id.

    Deliberately not a timestamp — a revision that moved because the clock moved would make every
    re-collection look like a source change."""
    ids = sorted(str(getattr(r, "id", "")) for r in records)
    return f"{len(ids)}:{ids[-1] if ids else '-'}"


def _artifact_for(domain: EvidenceDomain) -> EvidenceSourceType:
    capability = SOURCE_CAPABILITIES.get(domain)
    return capability.artifact if capability else EvidenceSourceType.LEDGER_ENTRY


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConsolidationRepositories:
    """The nine M9.23 seams, injected. Interfaces only — implementations in persistence.

    Nine, and only nine. **Nothing here reaches an account, ledger, invoice, tax, payment,
    recognition, collection, reconciliation, incident, investigation or reporting table for a
    write.** Those domains are read through the context, and every one of those reads is a read.

    `accounts` is the one exception in shape and not in direction: it is M9.8's account book,
    injected as `ScopeAccountReader` — a contract with no `add`, no `replace` and no
    `assign_parent`, so this milestone cannot change a hierarchy it consumes."""

    definitions: object
    snapshots: object
    periods: object
    consolidations: object
    bindings: object
    exceptions: object
    validations: object
    results: object
    events: object
    accounts: object


@dataclass(frozen=True, slots=True)
class ConsolidationServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    resolution: object
    periods: object
    attribution: object
    aggregation: object
    completeness: object
    exceptions: object
    validation: object
    lifecycle: object
    guards: object
    provenance: object
    validator: object


class _ConsolidationSupport:
    """Shared lookups for the command and query sides."""

    def __init__(self, *, context, repos: ConsolidationRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services
        self._reader = _ConsolidationSourceReader(context)

    def definition_or_404(self, org_id: str, definition_id: str) -> ScopeDefinition:
        found = self._repos.definitions.get(org_id, definition_id)
        if found is None:
            raise _NotFound(f"scope definition {definition_id!r} not found")
        return found

    def snapshot_or_404(self, org_id: str, snapshot_id: str) -> ScopeSnapshot:
        found = self._repos.snapshots.get(org_id, snapshot_id)
        if found is None:
            raise _NotFound(f"scope snapshot {snapshot_id!r} not found")
        return found

    def period_or_404(self, org_id: str, period_id: str) -> ConsolidationPeriod:
        found = self._repos.periods.get(org_id, period_id)
        if found is None:
            raise _NotFound(f"consolidation period {period_id!r} not found")
        return found

    def run_or_404(self, org_id: str, consolidation_id: str) -> ConsolidationRun:
        found = self._repos.consolidations.get(org_id, consolidation_id)
        if found is None:
            raise _NotFound(f"consolidation {consolidation_id!r} not found")
        return found

    def exception_or_404(self, org_id: str, exception_id: str) -> ConsolidationException:
        found = self._repos.exceptions.get(org_id, exception_id)
        if found is None:
            raise _NotFound(f"consolidation exception {exception_id!r} not found")
        return found


class ConsolidationUseCases(_ConsolidationSupport):
    """Consolidation orchestration. **Derives a scoped view; creates no financial fact.**"""

    def __init__(
        self,
        *,
        context,
        repos: ConsolidationRepositories,
        factory,
        services: ConsolidationServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── scope ───────────────────────────────────────────────────────────────────

    def publish_scope(self, command: PublishScopeDefinition) -> ScopeDefinitionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_SCOPE_MANAGE)
        with self._uow.new() as uow:
            built = self._factory.scope_definition(
                org_id=command.org_id,
                scope_code=command.scope_code,
                version=command.version,
                mode=command.mode,
                title=command.title,
                anchor_account_id=command.anchor_account_id,
                member_account_ids=command.member_account_ids,
                excluded_account_ids=command.excluded_account_ids,
                currency_mode=command.currency_mode,
                declared_currency=command.declared_currency,
                period_basis=command.period_basis,
                required_domains=command.required_domains,
                integrity_blocks_finalization=command.integrity_blocks_finalization,
                published_by=command.actor_id,
            )
            diagnostics = self._svc.validator.validate(built)
            if diagnostics.has_errors:
                raise BillingValidationError(diagnostics)
            existing = self._repos.definitions.get(command.org_id, built.id)
            # **A published version means one thing forever.** Re-publishing it with different
            # content would silently change which accounts every bound consolidation covered.
            self._svc.guards.guard_scope_publishable(existing, built)
            stored = self._repos.definitions.publish(built)
            uow.stage(self._event(command, stored.id, version=str(stored.version)))
            return scope_definition_view(stored)

    def open_period(self, command: OpenConsolidationPeriod) -> ConsolidationPeriodView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_GENERATE)
        with self._uow.new() as uow:
            period = ConsolidationPeriod(
                id=self._period_id(command.org_id, command.basis, command.period_key),
                org_id=command.org_id,
                basis=command.basis,
                period_key=command.period_key,
                window=BillingPeriod(start=command.start, end=command.end),
                aligned_source_id=command.aligned_source_id,
            )
            stored = self._repos.periods.create(period)
            uow.stage(self._event(command, stored.id, period_key=stored.period_key))
            return consolidation_period_view(stored)

    @staticmethod
    def _period_id(org_id: str, basis: PeriodBasis, period_key: str) -> str:
        return derive_consolidation_period_id(org_id, basis.value, period_key)

    def resolve_scope(self, command: ResolveScope) -> ScopeSnapshotView:
        """Resolve a scope into an immutable membership.

        **The one place M9.8's hierarchy is read**, and it is read through `ScopeAccountReader` —
        a contract with no write method at all. For the anchored modes the subtree is walked by
        repeated indexed `children_of` reads rather than by loading the organization; for
        `ORGANIZATION` mode every row *is* the answer and no Python filter follows."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_SCOPE_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            definition = self.definition_or_404(command.org_id, command.scope_definition_id)
            known = self._accounts_for(definition)
            snapshot = self._svc.resolution.resolve(
                definition, known, at=at, resolved_by=command.actor_id
            )
            stored = self._repos.snapshots.create(snapshot)
            uow.stage(
                self._event(command, stored.id, members=str(stored.member_count))
            )
            return scope_snapshot_view(stored)

    def _accounts_for(self, definition: ScopeDefinition):
        """The working set a mode needs, read the narrowest way the mode allows.

        Deliberately **not** one `all(org)` for every mode: an anchored subtree walks indexed
        `children_of` reads bounded by M9.8's own depth ceiling, so a hundred-account reseller tree
        inside a hundred-thousand-account organization costs a hundred indexed reads rather than a
        full scan. `PAYER_GROUP` and `ORGANIZATION` genuinely need the organization, and say so."""
        accounts = self._repos.accounts
        org_id = definition.org_id
        if definition.mode is ScopeMode.SINGLE_ACCOUNT:
            found = accounts.get(org_id, definition.anchor_account_id)
            return (found,) if found else ()
        if definition.mode is ScopeMode.ACCOUNT_SET:
            out = []
            for account_id in definition.member_account_ids:
                found = accounts.get(org_id, account_id)
                if found is not None:
                    out.append(found)
            return tuple(out)
        if definition.mode is ScopeMode.PARENT_GROUP:
            anchor = accounts.get(org_id, definition.anchor_account_id)
            if anchor is None:
                return ()
            out = [anchor]
            frontier = [anchor]
            seen = {anchor.id}
            depth = 0
            while frontier and depth < MAX_SCOPE_MEMBERS:
                nxt = []
                for account in frontier:
                    for child in accounts.children_of(org_id, account.id):
                        if child.id in seen:
                            continue
                        seen.add(child.id)
                        out.append(child)
                        nxt.append(child)
                frontier = nxt
                depth += 1
            return tuple(out)
        # PAYER_GROUP and ORGANIZATION need the organization's accounts: the payer graph has no
        # indexed reverse read, and an organization scope *is* every account.
        return accounts.all(org_id)

    # ── consolidation ───────────────────────────────────────────────────────────

    def open_consolidation(self, command: OpenConsolidation) -> ConsolidationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_GENERATE)
        with self._uow.new() as uow:
            snapshot = self.snapshot_or_404(command.org_id, command.scope_snapshot_id)
            period = self.period_or_404(command.org_id, command.period_id)
            definition = self.definition_or_404(command.org_id, snapshot.scope_definition_id)
            self._svc.guards.guard_single_org(
                command.org_id, snapshot.org_id, period.org_id, definition.org_id
            )
            # Two period concepts are never interchangeable — refused, not approximated.
            self._svc.periods.guard_period_matches(definition, period)
            built = self._factory.consolidation(
                definition=definition,
                snapshot=snapshot,
                period=period,
                opened_by=command.actor_id,
            )
            stored = self._repos.consolidations.create(built)
            self._append(uow, stored, ConsolidationEventKind.CONSOLIDATION_OPENED, command.actor_id)
            if stored.status is ConsolidationStatus.DRAFT:
                stored = self._advance(
                    stored, ConsolidationTrigger.RESOLVE_SCOPE, uow, command.actor_id
                )
                stored = self._advance(
                    stored, ConsolidationTrigger.BEGIN_COLLECTION, uow, command.actor_id
                )
            uow.stage(self._event(command, stored.id, period_key=stored.period_key))
            return self._view(stored)

    def collect(self, command: CollectConsolidationSources) -> ConsolidationView:
        """Bind one batch of member accounts' sources.

        Members are read in **account-id order** after the stored cursor, so a restart resumes
        rather than re-reads or skips, and two workers racing on one batch write the same binding
        rows rather than double-counting."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_GENERATE)
        at = self._clock.now()
        batch = max(1, min(command.batch_size or DEFAULT_COLLECTION_BATCH, MAX_COLLECTION_BATCH))
        with self._uow.new() as uow:
            run = self.run_or_404(command.org_id, command.consolidation_id)
            self._svc.guards.guard_not_finalized(run)
            if run.status is not ConsolidationStatus.COLLECTING:
                raise _refused(
                    f"consolidation {run.id} is {run.status.value}; only a COLLECTING run binds "
                    "sources"
                )
            snapshot = self.snapshot_or_404(command.org_id, run.scope_snapshot_id)
            definition = self.definition_or_404(command.org_id, run.scope_definition_id)
            account_ids = self._repos.snapshots.member_ids(
                command.org_id, snapshot.id, after=run.collection_cursor, limit=batch
            )
            cursor = run.collection_cursor
            for account_id in account_ids:
                self._bind_account(run, definition, snapshot, account_id, at, uow, command.actor_id)
                cursor = account_id
            bound = self._repos.bindings.bound_accounts(command.org_id, run.id)
            counts = self._repos.bindings.counts(command.org_id, run.id)
            updated = replace(
                run,
                collection_cursor=cursor,
                collected_count=bound,
                source_count=sum(counts.values()),
                unavailable_source_count=counts.get(SourceStatus.UNAVAILABLE.value, 0),
            )
            stored = self._store(updated, run.version)
            uow.stage(self._event(command, stored.id, collected=str(stored.collected_count)))
            return self._view(stored)

    def _bind_account(
        self, run, definition, snapshot, account_id: str, at: float, uow, actor: str
    ) -> None:
        """Bind every required domain for one account. **No domain is skipped silently.**"""
        for domain in definition.required_domains:
            capability = SOURCE_CAPABILITIES.get(domain)
            period_applied = self._svc.periods.period_applied(domain, definition.period_basis)
            # A missing period read is only a *gap* for a domain that is supposed to have one. An
            # operational queue read never had a period, and blocking on that would make the
            # integrity boundary unusable — see `SourceCapability.period_expected`.
            if not period_applied and self._svc.periods.period_expected(domain):
                reason = self._svc.periods.unsupported_reason(domain, definition.period_basis)
                self._raise_exception(
                    run,
                    ConsolidationExceptionKind.PERIOD_NOT_SUPPORTED,
                    f"{domain.value}:{definition.period_basis.value}",
                    reason,
                    at,
                    uow,
                    account_id=account_id,
                    domain=domain,
                )
            outcome = self._reader.read(domain, run.org_id, account_id, run.period_key)
            amount_minor, currency = self._fold(
                outcome, run, definition, at, uow, account_id, domain
            )
            binding = self._factory.binding(
                run=run,
                account_id=account_id,
                domain=domain,
                artifact=_artifact_for(domain),
                read_name=capability.read_name if capability else "",
                status=outcome.status,
                completeness=outcome.completeness,
                period_applied=period_applied,
                source_revision=outcome.revision,
                source_digest=outcome.revision,
                record_count=outcome.record_count,
                amount_minor=amount_minor,
                currency=currency,
                detail=outcome.reason,
            )
            self._repos.bindings.bind(binding)
            if outcome.status is SourceStatus.UNAVAILABLE:
                self._raise_exception(
                    run,
                    ConsolidationExceptionKind.SOURCE_UNAVAILABLE,
                    f"{account_id}:{domain.value}",
                    outcome.reason or f"{domain.value} could not be read for {account_id}",
                    at,
                    uow,
                    account_id=account_id,
                    domain=domain,
                )
            elif outcome.completeness is SourceCompleteness.SOURCE_PARTIAL:
                self._raise_exception(
                    run,
                    ConsolidationExceptionKind.SOURCE_INCOMPLETE,
                    f"{account_id}:{domain.value}",
                    f"{domain.value} answered for the account but not for the period",
                    at,
                    uow,
                    account_id=account_id,
                    domain=domain,
                    severity=ConsolidationSeverity.ADVISORY,
                )

    def _fold(self, outcome, run, definition, at, uow, account_id, domain):
        """Reduce a domain's own amounts to one same-currency figure, or to **nothing at all**.

        Nothing here computes a financial fact: the `Money` values arrived from the owning
        aggregate and the only operation applied is integer addition within one currency. A mixed
        currency raises a named exception and contributes **no figure** — not a zero, not a
        partial, not a converted total."""
        if outcome.status is not SourceStatus.RESOLVED or not outcome.amounts:
            return None, None
        currencies = {a.currency for a in outcome.amounts}
        if len(currencies) > 1:
            self._raise_exception(
                run,
                ConsolidationExceptionKind.CURRENCY_MISMATCH,
                f"{account_id}:{domain.value}",
                f"{domain.value} returned "
                + ", ".join(sorted(str(c) for c in currencies))
                + " for one account; this domain performs no currency conversion",
                at,
                uow,
                account_id=account_id,
                domain=domain,
            )
            return None, None
        currency = next(iter(currencies))
        if (
            definition.currency_mode is ScopeCurrencyMode.SINGLE_DECLARED
            and currency is not definition.declared_currency
        ):
            self._raise_exception(
                run,
                ConsolidationExceptionKind.CURRENCY_UNSUPPORTED,
                f"{account_id}:{domain.value}",
                f"the scope declares {definition.declared_currency} and {domain.value} returned "
                f"{currency}; there is no approved conversion rule",
                at,
                uow,
                account_id=account_id,
                domain=domain,
            )
            return None, None
        total = self._svc.aggregation._sum_minor(outcome.amounts)
        return total.minor, currency

    def _raise_exception(
        self,
        run,
        kind: ConsolidationExceptionKind,
        subject: str,
        diagnostic: str,
        at: float,
        uow,
        *,
        account_id: str = "",
        domain: EvidenceDomain | None = None,
        severity: ConsolidationSeverity | None = None,
    ) -> None:
        built = self._svc.exceptions.build(
            run,
            kind,
            subject,
            diagnostic,
            at=at,
            account_id=account_id,
            domain=domain,
            severity=severity,
        )
        self._repos.exceptions.raise_exception(built)
        self._append(
            uow,
            run,
            ConsolidationEventKind.EXCEPTION_RAISED,
            "",
            subject=built.id,
            detail=kind.value,
        )

    def complete_collection(self, command: CompleteCollection) -> ConsolidationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_GENERATE)
        with self._uow.new() as uow:
            run = self.run_or_404(command.org_id, command.consolidation_id)
            self._svc.guards.guard_not_finalized(run)
            bound = self._repos.bindings.bound_accounts(command.org_id, run.id)
            if bound < run.member_count:
                raise _refused(
                    f"consolidation {run.id} has bound {bound} of {run.member_count} members; "
                    "collection is incomplete and a partial scope is never treated as the whole"
                )
            stored = self._advance(
                run, ConsolidationTrigger.COMPLETE_COLLECTION, uow, command.actor_id
            )
            uow.stage(self._event(command, stored.id))
            return self._view(stored)

    def validate(self, command: ValidateConsolidation) -> ConsolidationValidationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_VALIDATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            run = self.run_or_404(command.org_id, command.consolidation_id)
            self._svc.guards.guard_not_finalized(run)
            snapshot = self.snapshot_or_404(command.org_id, run.scope_snapshot_id)
            definition = self.definition_or_404(command.org_id, run.scope_definition_id)
            bindings = self._repos.bindings.for_consolidation(command.org_id, run.id)
            exceptions = self._repos.exceptions.for_consolidation(command.org_id, run.id)
            # Aggregation happens here, over pinned bindings — never over a live read.
            amounts = self._svc.aggregation.aggregate(bindings, snapshot)
            completeness = self._svc.completeness.assess(run, snapshot, bindings, exceptions)
            digest = consolidation_source_digest(tuple(bindings))
            verdict = self._svc.validation.validate(
                replace(run, completeness=completeness),
                definition,
                snapshot,
                bindings,
                exceptions,
                at=at,
                validated_by=command.actor_id,
                open_integrity_findings=self._open_integrity(command.org_id, bindings),
            )
            self._repos.validations.record(verdict)
            blocking = self._repos.exceptions.blocking_count(command.org_id, run.id)
            updated = replace(
                run,
                amounts=amounts,
                completeness=completeness,
                consolidation_digest=digest,
                exception_count=len(exceptions),
                blocking_exception_count=blocking,
            )
            stored = self._store(updated, run.version)
            trigger = (
                ConsolidationTrigger.PASS_VALIDATION
                if verdict.passed
                else ConsolidationTrigger.FAIL_VALIDATION
            )
            stored = self._advance(stored, trigger, uow, command.actor_id)
            uow.stage(self._event(command, stored.id, passed=str(verdict.passed).lower()))
            return consolidation_validation_view(verdict)

    @staticmethod
    def _open_integrity(org_id: str, bindings) -> int:
        """M9.19/M9.20's **own** verdict, read from the pinned bindings.

        Never a second reconciliation and never a re-interpretation: the count is what the
        integrity domains recorded, and a consolidation may surface it but never resolve it."""
        return sum(
            b.record_count
            for b in bindings
            if b.domain in (EvidenceDomain.RECONCILIATION, EvidenceDomain.INTEGRITY)
            and b.status is SourceStatus.RESOLVED
        )

    def finalize(self, command: FinalizeConsolidation) -> ConsolidationSnapshotView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_FINALIZE)
        with self._uow.new() as uow:
            run = self.run_or_404(command.org_id, command.consolidation_id)
            validation = self._repos.validations.current(command.org_id, run.id)
            exceptions = self._repos.exceptions.for_consolidation(command.org_id, run.id)
            # Re-read inside the transaction and refused on any of three grounds.
            self._svc.guards.guard_finalizable(run, validation, exceptions)
            snapshot = self.snapshot_or_404(command.org_id, run.scope_snapshot_id)
            bindings = self._repos.bindings.for_consolidation(command.org_id, run.id)
            digest = consolidation_source_digest(tuple(bindings))
            if run.consolidation_digest and digest != run.consolidation_digest:
                raise _refused(
                    f"consolidation {run.id} was validated against source revision "
                    f"{run.consolidation_digest[:12]} and now reads {digest[:12]}; re-collect "
                    "rather than finalize a figure nobody validated"
                )
            finalized = self._advance(run, ConsolidationTrigger.FINALIZE, uow, command.actor_id)
            finalized = self._store(
                replace(
                    finalized,
                    finalized_at=self._clock.now(),
                    finalized_by=command.actor_id,
                ),
                finalized.version,
            )
            artifact = self._factory.snapshot(
                run=finalized,
                scope_snapshot=snapshot,
                source_digest=digest,
                amounts=finalized.amounts,
                finalized_by=command.actor_id,
            )
            stored = self._repos.results.create(artifact)
            uow.stage(self._event(command, run.id, digest=stored.consolidation_digest))
            return consolidation_snapshot_view(stored)

    def recollect(self, command: RecollectConsolidation) -> ConsolidationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_GENERATE)
        with self._uow.new() as uow:
            run = self.run_or_404(command.org_id, command.consolidation_id)
            self._svc.guards.guard_not_finalized(run)
            reset = replace(run, collection_cursor="", collected_count=0)
            stored = self._store(reset, run.version)
            stored = self._advance(
                stored, ConsolidationTrigger.RECOLLECT, uow, command.actor_id
            )
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return self._view(stored)

    def acknowledge(self, command: AcknowledgeConsolidationException) -> ConsolidationExceptionView:
        """Record that somebody saw a problem.

        **This does not unblock the consolidation.** `ConsolidationExceptionService.blocking` does
        not filter acknowledged exceptions out, deliberately: somebody having looked at a gap is a
        different fact from the gap being closed."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_VALIDATE)
        with self._uow.new() as uow:
            found = self.exception_or_404(command.org_id, command.exception_id)
            self._svc.exceptions.guard_acknowledgeable(found)
            acknowledged = replace(
                found,
                state=ConsolidationExceptionState.ACKNOWLEDGED,
                acknowledged_at=self._clock.now(),
                acknowledged_by=command.actor_id,
                acknowledgement_note=command.note,
            )
            stored = self._repos.exceptions.acknowledge(acknowledged)
            run = self.run_or_404(command.org_id, found.consolidation_id)
            self._append(
                uow,
                run,
                ConsolidationEventKind.EXCEPTION_ACKNOWLEDGED,
                command.actor_id,
                subject=stored.id,
            )
            uow.stage(self._event(command, stored.id, kind=stored.kind.value))
            return consolidation_exception_view(stored)

    def open_correction(self, command: OpenConsolidationCorrection) -> ConsolidationView:
        """Open a consolidation that supersedes a finalized one. **Both survive.**

        The original keeps every figure, its digest, its snapshot and its finalized state, gaining
        only a back-reference. A correction whose scope and source revisions are both unmoved is
        refused — it would compute the identity of a consolidation that already exists."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONSOLIDATION_CORRECT)
        at = self._clock.now()
        with self._uow.new() as uow:
            original = self.run_or_404(command.org_id, command.consolidation_id)
            self._svc.guards.guard_correctable(original)
            definition = self.definition_or_404(command.org_id, original.scope_definition_id)
            period = self.period_or_404(command.org_id, original.period_id)
            known = self._accounts_for(definition)
            snapshot = self._svc.resolution.resolve(
                definition, known, at=at, resolved_by=command.actor_id
            )
            self._svc.guards.guard_correction_has_moved(
                original, snapshot.id, original.consolidation_digest
            )
            stored_snapshot = self._repos.snapshots.create(snapshot)
            built = self._factory.consolidation(
                definition=definition,
                snapshot=stored_snapshot,
                period=period,
                opened_by=command.actor_id,
                corrects=original.id,
                correction_reason=command.reason,
            )
            correction = self._repos.consolidations.create(built)
            self._store(replace(original, superseded_by=correction.id), original.version)
            self._append(
                uow,
                correction,
                ConsolidationEventKind.CORRECTION_OPENED,
                command.actor_id,
                subject=original.id,
                detail=command.reason,
            )
            correction = self._advance(
                correction, ConsolidationTrigger.RESOLVE_SCOPE, uow, command.actor_id
            )
            correction = self._advance(
                correction, ConsolidationTrigger.BEGIN_COLLECTION, uow, command.actor_id
            )
            uow.stage(self._event(command, correction.id, corrects=original.id))
            return self._view(correction)

    # ── internals ───────────────────────────────────────────────────────────────

    def _advance(self, run: ConsolidationRun, trigger, uow, actor: str) -> ConsolidationRun:
        target = self._svc.lifecycle.next_state(run.status, trigger)
        stored = self._store(replace(run, status=target), run.version)
        self._append(uow, stored, _TRIGGER_EVENTS[trigger], actor, detail=trigger.value)
        return stored

    def _store(self, run: ConsolidationRun, expected_version: int) -> ConsolidationRun:
        """Compare-and-set on `version`. `ConsolidationStale` propagates: a slow worker must not
        overwrite a finalization somebody else performed."""
        return self._repos.consolidations.advance(
            replace(run, version=expected_version + 1), expected_version
        )

    def _append(
        self, uow, run: ConsolidationRun, kind, actor: str, *, subject: str = "", detail: str = ""
    ) -> None:
        sequence = self._repos.events.next_sequence(run.org_id, run.id)
        self._repos.events.append(
            self._factory.event(
                run_id=run.id,
                org_id=run.org_id,
                kind=kind,
                sequence=sequence,
                subject=subject or run.id,
                detail=detail,
                actor=actor,
            )
        )

    def _view(self, run: ConsolidationRun) -> ConsolidationView:
        return consolidation_view(run, self._svc.lifecycle.allowed(run.status))

    def _event(self, command, subject: str, **data: str) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.CONSOLIDATION_ADVANCED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject,
            data=dict(data),
        )


_TRIGGER_EVENTS = {
    ConsolidationTrigger.RESOLVE_SCOPE: ConsolidationEventKind.SCOPE_RESOLVED,
    ConsolidationTrigger.BEGIN_COLLECTION: ConsolidationEventKind.COLLECTION_STARTED,
    ConsolidationTrigger.COMPLETE_COLLECTION: ConsolidationEventKind.COLLECTION_COMPLETED,
    ConsolidationTrigger.PASS_VALIDATION: ConsolidationEventKind.VALIDATION_PASSED,
    ConsolidationTrigger.FAIL_VALIDATION: ConsolidationEventKind.VALIDATION_FAILED,
    ConsolidationTrigger.RECOLLECT: ConsolidationEventKind.RECOLLECTION_STARTED,
    ConsolidationTrigger.FINALIZE: ConsolidationEventKind.CONSOLIDATION_FINALIZED,
}


def _refused(message: str):
    from billing.foundation import ConsolidationRefused

    return ConsolidationRefused(message)


class ConsolidationQueries(_ConsolidationSupport):
    """Read side. Every method requires `BILLING_CONSOLIDATION_READ` and is org-scoped by key."""

    def __init__(self, *, context, repos, services, authz: AuthorizationOrchestrator) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_CONSOLIDATION_READ)

    def get_scope(self, query: GetScopeDefinition) -> ScopeDefinitionView:
        self._read(query)
        return scope_definition_view(
            self.definition_or_404(query.org_id, query.scope_definition_id)
        )

    def list_scopes(self, query: ListScopeDefinitions) -> tuple[ScopeDefinitionView, ...]:
        self._read(query)
        return tuple(
            scope_definition_view(d)
            for d in self._repos.definitions.all(query.org_id, query.limit)
        )

    def get_snapshot(self, query: GetScopeSnapshot) -> ScopeSnapshotView:
        self._read(query)
        return scope_snapshot_view(self.snapshot_or_404(query.org_id, query.scope_snapshot_id))

    def list_members(self, query: ListScopeMembers) -> tuple[tuple[ScopeMemberView, ...], int]:
        """**Paginated at the database.** A hundred-thousand-account scope shows ten rows without
        loading a hundred thousand."""
        self._read(query)
        members, total = self._repos.snapshots.members(
            query.org_id, query.scope_snapshot_id, query.page, query.page_size
        )
        return (
            tuple(
                ScopeMemberView(
                    account_id=m.account_id,
                    inclusion=m.inclusion.value,
                    account_revision=m.account_revision,
                    currency=str(m.currency) if m.currency else None,
                    depth=m.depth,
                    hierarchy_path=m.hierarchy_path,
                )
                for m in members
            ),
            total,
        )

    def list_periods(self, query: ListConsolidationPeriods) -> tuple[ConsolidationPeriodView, ...]:
        self._read(query)
        return tuple(
            consolidation_period_view(p)
            for p in self._repos.periods.all(query.org_id, query.limit)
        )

    def get(self, query: GetConsolidation) -> ConsolidationView:
        self._read(query)
        run = self.run_or_404(query.org_id, query.consolidation_id)
        return consolidation_view(run, self._svc.lifecycle.allowed(run.status))

    def search(self, query: SearchConsolidations):
        self._read(query)
        result = self._repos.consolidations.search(
            ConsolidationSearchQuery(
                org_id=query.org_id,
                scope_definition_id=query.scope_definition_id,
                period_key=query.period_key,
                statuses=query.statuses,
                corrections_only=query.corrections_only,
                page=query.page,
                page_size=query.page_size,
            )
        )
        return (
            tuple(
                consolidation_view(r, self._svc.lifecycle.allowed(r.status)) for r in result.runs
            ),
            result.total,
            result.pages,
        )

    def list_bindings(self, query: ListSourceBindings) -> tuple[tuple[SourceBindingView, ...], int]:
        self._read(query)
        rows, total = self._repos.bindings.page(
            query.org_id, query.consolidation_id, query.page, query.page_size
        )
        return tuple(source_binding_view(b) for b in rows), total

    def list_exceptions(
        self, query: ListConsolidationExceptions
    ) -> tuple[ConsolidationExceptionView, ...]:
        self._read(query)
        return tuple(
            consolidation_exception_view(e)
            for e in self._repos.exceptions.for_consolidation(
                query.org_id, query.consolidation_id
            )
        )

    def get_validation(self, query: GetConsolidationValidation) -> ConsolidationValidationView:
        self._read(query)
        found = self._repos.validations.current(query.org_id, query.consolidation_id)
        if found is None:
            raise _NotFound(f"consolidation {query.consolidation_id!r} has not been validated")
        return consolidation_validation_view(found)

    def get_result(self, query: GetConsolidationSnapshot) -> ConsolidationSnapshotView:
        self._read(query)
        found = self._repos.results.for_consolidation(query.org_id, query.consolidation_id)
        if found is None:
            raise _NotFound(f"consolidation {query.consolidation_id!r} is not finalized")
        return consolidation_snapshot_view(found)

    def get_provenance(self, query: GetConsolidationProvenance) -> ConsolidationProvenanceView:
        self._read(query)
        run = self.run_or_404(query.org_id, query.consolidation_id)
        snapshot = self.snapshot_or_404(query.org_id, run.scope_snapshot_id)
        bindings = self._repos.bindings.for_consolidation(query.org_id, run.id)
        p = self._svc.provenance.build(run, snapshot, bindings)
        return ConsolidationProvenanceView(
            consolidation_id=p.consolidation_id,
            scope_snapshot_id=p.scope_snapshot_id,
            scope_definition_id=p.scope_definition_id,
            scope_version=p.scope_version,
            period_basis=p.period_basis.value,
            period_key=p.period_key,
            hierarchy=p.hierarchy.value,
            contributions=tuple(account_contribution_view(c) for c in p.contributions),
            excluded=tuple(
                ExcludedAccountView(e.account_id, e.reason.value, e.detail) for e in p.excluded
            ),
            domains=tuple(d.value for d in p.domains),
            consolidation_digest=p.consolidation_digest,
            corrects=p.corrects,
            superseded_by=p.superseded_by,
        )

    def timeline(self, query: GetConsolidationTimeline) -> tuple[ConsolidationEventView, ...]:
        self._read(query)
        return tuple(
            ConsolidationEventView(
                id=e.id,
                kind=e.kind.value,
                sequence=e.sequence,
                subject=e.subject,
                detail=e.detail,
                actor=e.actor,
                occurred_at=e.occurred_at,
            )
            for e in self._repos.events.timeline(
                query.org_id, query.consolidation_id, query.limit
            )
        )

    def posture(self, query: GetConsolidationPosture) -> ConsolidationPostureView:
        self._read(query)
        p = self._repos.consolidations.posture(query.org_id)
        return ConsolidationPostureView(
            total_runs=p.total_runs,
            by_status=dict(p.by_status),
            by_completeness=dict(p.by_completeness),
            open_exceptions=p.open_exceptions,
            blocking_exceptions=p.blocking_exceptions,
            scope_definitions=p.scope_definitions,
        )


# ── registration ────────────────────────────────────────────────────────────────


ALL_CONSOLIDATION_COMMANDS: tuple[type, ...] = (
    PublishScopeDefinition,
    OpenConsolidationPeriod,
    ResolveScope,
    OpenConsolidation,
    CollectConsolidationSources,
    CompleteCollection,
    ValidateConsolidation,
    FinalizeConsolidation,
    RecollectConsolidation,
    AcknowledgeConsolidationException,
    OpenConsolidationCorrection,
)

ALL_CONSOLIDATION_QUERIES: tuple[type, ...] = (
    GetScopeDefinition,
    ListScopeDefinitions,
    GetScopeSnapshot,
    ListScopeMembers,
    ListConsolidationPeriods,
    GetConsolidation,
    SearchConsolidations,
    ListSourceBindings,
    ListConsolidationExceptions,
    GetConsolidationValidation,
    GetConsolidationSnapshot,
    GetConsolidationProvenance,
    GetConsolidationTimeline,
    GetConsolidationPosture,
)


def register_consolidation_use_cases(
    command_bus, query_bus, uc: ConsolidationUseCases, q: ConsolidationQueries
):
    """Wire the consolidation vocabulary onto the shared buses. One handler per message.

    **Deny-by-default holds for every one of these messages**: each handler's first statement is an
    `AuthorizationOrchestrator.require`, and a message with no explicit permission would fail the
    architecture test that walks this table."""
    command_bus.register(PublishScopeDefinition, uc.publish_scope)
    command_bus.register(OpenConsolidationPeriod, uc.open_period)
    command_bus.register(ResolveScope, uc.resolve_scope)
    command_bus.register(OpenConsolidation, uc.open_consolidation)
    command_bus.register(CollectConsolidationSources, uc.collect)
    command_bus.register(CompleteCollection, uc.complete_collection)
    command_bus.register(ValidateConsolidation, uc.validate)
    command_bus.register(FinalizeConsolidation, uc.finalize)
    command_bus.register(RecollectConsolidation, uc.recollect)
    command_bus.register(AcknowledgeConsolidationException, uc.acknowledge)
    command_bus.register(OpenConsolidationCorrection, uc.open_correction)
    query_bus.register(GetScopeDefinition, q.get_scope)
    query_bus.register(ListScopeDefinitions, q.list_scopes)
    query_bus.register(GetScopeSnapshot, q.get_snapshot)
    query_bus.register(ListScopeMembers, q.list_members)
    query_bus.register(ListConsolidationPeriods, q.list_periods)
    query_bus.register(GetConsolidation, q.get)
    query_bus.register(SearchConsolidations, q.search)
    query_bus.register(ListSourceBindings, q.list_bindings)
    query_bus.register(ListConsolidationExceptions, q.list_exceptions)
    query_bus.register(GetConsolidationValidation, q.get_validation)
    query_bus.register(GetConsolidationSnapshot, q.get_result)
    query_bus.register(GetConsolidationProvenance, q.get_provenance)
    query_bus.register(GetConsolidationTimeline, q.timeline)
    query_bus.register(GetConsolidationPosture, q.posture)


__all__ = [
    "ALL_CONSOLIDATION_COMMANDS",
    "ALL_CONSOLIDATION_QUERIES",
    "DEFAULT_COLLECTION_BATCH",
    "MAX_COLLECTION_BATCH",
    "AccountContributionView",
    "AcknowledgeConsolidationException",
    "CollectConsolidationSources",
    "CompleteCollection",
    "ConsolidatedAmountView",
    "ConsolidationEventView",
    "ConsolidationExceptionView",
    "ConsolidationPeriodView",
    "ConsolidationPostureView",
    "ConsolidationProvenanceView",
    "ConsolidationQueries",
    "ConsolidationRepositories",
    "ConsolidationServices",
    "ConsolidationSnapshotView",
    "ConsolidationUseCases",
    "ConsolidationValidationView",
    "ConsolidationView",
    "ExcludedAccountView",
    "FinalizeConsolidation",
    "GetConsolidation",
    "GetConsolidationPosture",
    "GetConsolidationProvenance",
    "GetConsolidationSnapshot",
    "GetConsolidationTimeline",
    "GetConsolidationValidation",
    "GetScopeDefinition",
    "GetScopeSnapshot",
    "ListConsolidationExceptions",
    "ListConsolidationPeriods",
    "ListScopeDefinitions",
    "ListScopeMembers",
    "ListSourceBindings",
    "OpenConsolidation",
    "OpenConsolidationCorrection",
    "OpenConsolidationPeriod",
    "PublishScopeDefinition",
    "RecollectConsolidation",
    "ResolveScope",
    "ScopeDefinitionView",
    "ScopeMemberView",
    "ScopeSnapshotView",
    "SearchConsolidations",
    "SourceBindingView",
    "ValidateConsolidation",
    "register_consolidation_use_cases",
]
