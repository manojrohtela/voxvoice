"""Reconciliation factories + repository contracts (M9.19).

`IntegrityFactory` is the one place a run, a finding, a repair request, a resolution or an audit
line is constructed. Same discipline as every milestone since M9.2: validate structurally, fail fast
on error-severity findings (`BillingValidationError`, §A2.6), compose an immutable aggregate from
the
injected id and clock seams, emit the corresponding `billing.*` fact through the injected emitter.
No I/O, no persistence, no transport, no provider, no scheduler.

**Exactly one event per business fact**, and two are deliberately absent:

* there is no `billing.integrity.matched`. Agreement is a *counter on the run*, not news. Publishing
  it would emit one event per compared fact and drown the stream in non-events;
* there is no `billing.integrity.repaired`. M9.19 never repairs anything, so it has nothing to
  announce. The owning domain publishes its own repair — `billing.ledger.entry_recorded`,
  `billing.revenue.recognized` — and a second type here would let one repair be counted twice by
  anyone aggregating the stream. The same rule kept `billing.revenue.posted` out of M9.18.

## Why identities are derived rather than minted

* a **run** is derived from `(org, relationship, scope, evidence digest)` — what it looked at. Two
  workers reconciling the same facts compute the same id and one loses a primary-key race; a re-run
  over unchanged evidence lands on the same row and writes nothing; a re-run *after a repair* has
  different evidence and is therefore genuinely a new observation;
* a **finding** is derived from `(run, relationship, subject, divergence)`. **This is the
  duplicate-finding defence**: two workers detecting the same mismatch collide on a primary key, and
  a restart mid-run re-derives the same ids and adds nothing;
* a **repair request** is derived from `(finding, owner, action, request key)`, so two workers
  asking the same owner for the same fix ask once;
* a **resolution** is derived from `(finding, request key)`, so two concurrent "this is fixed"
  submissions are one resolution rather than two claims about the same finding.

## What no repository here can do

There is no `update` on `IntegrityResolutionRepository`, no `delete` anywhere, and no method that
amends a recorded divergence. `IntegrityFindingRepository.advance` takes the version the caller read
and is a compare-and-set: a worker that decided from a stale view fails loudly rather than
overwriting a resolution somebody else recorded.

And there is no repository here for a payment, an invoice, a ledger entry, a tax assessment or a
recognition schedule — M9.19 reads those through the *owning domain's* seams, supplied by the
Application, and writes to none of them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_reconciliation import (
    DivergenceKind,
    FactRef,
    FindingSeverity,
    FindingState,
    IntegrityActivity,
    IntegrityFinding,
    IntegrityResolution,
    IntegrityRun,
    Observation,
    ReconciliationScope,
    RelationshipKind,
    RepairOwner,
    RepairRequest,
    RunState,
    derive_evidence_digest,
    derive_finding_id,
    derive_repair_request_id,
    derive_resolution_id,
    derive_run_id,
)
from .errors import BillingConflict, BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .reconciliation_rules import (
    ReconciliationValidator,
    max_severity,
    severity_of,
    verdict_of,
)
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_INTEGRITY_ACTIVITY = "ial"

#: Which `billing.*` fact a finding's severity publishes on detection. A table so "exactly one event
#: per business fact" is checkable by iteration rather than asserted in prose.
#:
#: `INFO` is absent because an informational observation is not a finding at all — it never reaches
#: this table. `WARNING` and above each publish, and the split matters downstream: an alerting rule
#: that pages on `billing.integrity.critical` should not also page on a period disposition that
#: M9.13 made on purpose.
SEVERITY_EVENTS: dict[FindingSeverity, BillingEventType] = {
    FindingSeverity.WARNING: BillingEventType.INTEGRITY_DIVERGENCE_DETECTED,
    FindingSeverity.ERROR: BillingEventType.INTEGRITY_DIVERGENCE_DETECTED,
    FindingSeverity.CRITICAL: BillingEventType.INTEGRITY_CRITICAL_DIVERGENCE,
}


@dataclass(frozen=True, slots=True)
class IntegrityFactory:
    """Constructs every reconciliation aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: ReconciliationValidator = ReconciliationValidator()

    # ── the run ─────────────────────────────────────────────────────────────────

    def run(
        self,
        scope: ReconciliationScope,
        sources: tuple[FactRef, ...],
        counterparts: tuple[FactRef, ...],
        *,
        at: float = 0.0,
        triggered_by: str = "",
    ) -> IntegrityRun:
        """Open one evaluation over a pinned set of evidence.

        The digest is taken **before** anything is compared, over exactly the facts that were read.
        That ordering is what makes the run reproducible: the id records what was looked at, not
        what was concluded, so two workers reading the same rows open the same run even if they
        would reach different conclusions — and then they cannot, because the comparison is a pure
        function of the same input."""
        self._fail_fast(self.validator.validate_scope(scope))
        for ref in (*sources, *counterparts):
            self._fail_fast(self.validator.validate_fact(ref))
        digest = derive_evidence_digest(
            tuple(r.evidence_key() for r in (*sources, *counterparts))
        )
        now = at or self.clock.now()
        return IntegrityRun(
            id=derive_run_id(scope.org_id, scope.relationship.value, scope.key, digest),
            org_id=scope.org_id,
            scope=scope,
            evidence_digest=digest,
            state=RunState.EVALUATING,
            started_at=now,
            triggered_by=triggered_by,
        )

    def complete(
        self, run: IntegrityRun, observations: tuple[Observation, ...], *, at: float
    ) -> IntegrityRun:
        """Close the run with its counts. `compared == matched + diverged`, always.

        Counts, never money. There is no total-divergent-value here and there never will be: the
        moment such a figure exists somebody reports it, and reconciliation has quietly become a
        second set of books."""
        diverged = [o for o in observations if o.is_divergent]
        completed = IntegrityRun(
            id=run.id,
            org_id=run.org_id,
            scope=run.scope,
            evidence_digest=run.evidence_digest,
            state=RunState.COMPLETED,
            compared=len(observations),
            matched=len(observations) - len(diverged),
            diverged=len(diverged),
            max_severity=max_severity(observations),
            started_at=run.started_at,
            completed_at=at,
            triggered_by=run.triggered_by,
            note=run.note,
        )
        self._emit(
            BillingEventType.INTEGRITY_RUN_COMPLETED,
            org_id=run.org_id,
            subject_id=run.id,
            data={
                "relationship": run.relationship.value,
                "compared": str(completed.compared),
                "matched": str(completed.matched),
                "diverged": str(completed.diverged),
                "max_severity": completed.max_severity.value,
                "evidence_digest": run.evidence_digest,
            },
        )
        return completed

    def failed(self, run: IntegrityRun, *, reason: str, at: float) -> IntegrityRun:
        """The evidence could not be read, so **no conclusion was reached**.

        A failed run is not a clean run. Recording it as `COMPLETED` with zero divergences is how a
        broken reader gets mistaken for healthy books, which is the quietest possible way for this
        engine to fail."""
        return IntegrityRun(
            id=run.id,
            org_id=run.org_id,
            scope=run.scope,
            evidence_digest=run.evidence_digest,
            state=RunState.FAILED,
            started_at=run.started_at,
            completed_at=at,
            triggered_by=run.triggered_by,
            note=reason,
        )

    # ── the finding ─────────────────────────────────────────────────────────────

    def finding(
        self,
        run: IntegrityRun,
        observation: Observation,
        *,
        owner: RepairOwner,
        at: float,
    ) -> IntegrityFinding:
        """One divergence as a durable, explainable row.

        `severity` is written here, once, from `SEVERITY_BY_KIND`, and no method anywhere changes
        it afterwards. Both sides' facts are copied in verbatim so the finding explains itself
        without re-reading a domain that has since moved on."""
        if observation.divergence is None:  # pragma: no cover — callers filter first
            raise BillingConflict("a matched observation is not a finding")
        account = ""
        for ref in (observation.source, observation.counterpart):
            if ref is not None and ref.account_id:
                account = ref.account_id
                break
        return IntegrityFinding(
            id=derive_finding_id(
                run.id,
                run.relationship.value,
                observation.subject_key,
                observation.divergence.value,
            ),
            org_id=run.org_id,
            run_id=run.id,
            relationship=run.relationship,
            subject_key=observation.subject_key,
            divergence=observation.divergence,
            severity=severity_of(observation.divergence),
            owner=owner,
            state=FindingState.OPEN,
            source=observation.source,
            counterpart=observation.counterpart,
            expected=observation.expected,
            observed=observation.observed,
            detail=observation.detail,
            account_id=account,
            detected_at=at,
            version=0,
            verdict=verdict_of(observation.divergence),
        )

    def emit_finding(self, finding: IntegrityFinding, *, actor_id: str = "") -> None:
        event_type = SEVERITY_EVENTS.get(finding.severity)
        if event_type is None:
            return
        self._emit(
            event_type,
            org_id=finding.org_id,
            subject_id=finding.id,
            actor_id=actor_id,
            data={
                "relationship": finding.relationship.value,
                "divergence": finding.divergence.value,
                "severity": finding.severity.value,
                "owner": finding.owner.value,
                "subject": finding.subject_key,
                "run_id": finding.run_id,
            },
        )

    def emit_state(self, finding: IntegrityFinding, *, actor_id: str = "") -> None:
        mapping = {
            FindingState.ACKNOWLEDGED: BillingEventType.INTEGRITY_FINDING_ACKNOWLEDGED,
            FindingState.REPAIR_REQUESTED: BillingEventType.INTEGRITY_REPAIR_REQUESTED,
            FindingState.RESOLVED: BillingEventType.INTEGRITY_FINDING_RESOLVED,
            FindingState.CLOSED: BillingEventType.INTEGRITY_FINDING_CLOSED,
        }
        event_type = mapping.get(finding.state)
        if event_type is None:  # pragma: no cover — OPEN publishes through emit_finding
            return
        self._emit(
            event_type,
            org_id=finding.org_id,
            subject_id=finding.id,
            actor_id=actor_id,
            data={
                "state": finding.state.value,
                "relationship": finding.relationship.value,
                "divergence": finding.divergence.value,
                "owner": finding.owner.value,
            },
        )

    # ── repair ──────────────────────────────────────────────────────────────────

    def repair_request(
        self,
        finding: IntegrityFinding,
        *,
        action: str,
        request_key: str,
        at: float,
        actor_id: str = "",
        dispatched_command: str = "",
        accepted: bool = False,
        detail: str = "",
    ) -> RepairRequest:
        """**A request, not a repair.** The owner performs it; M9.19 records that it was asked."""
        return RepairRequest(
            id=derive_repair_request_id(
                finding.id, finding.owner.value, action, request_key
            ),
            org_id=finding.org_id,
            finding_id=finding.id,
            owner=finding.owner,
            action=action,
            request_key=request_key,
            dispatched_command=dispatched_command,
            accepted=accepted,
            detail=detail,
            requested_at=at,
            requested_by=actor_id,
        )

    # ── resolution ──────────────────────────────────────────────────────────────

    def resolution(
        self,
        finding: IntegrityFinding,
        *,
        run_id: str,
        action: str,
        request_key: str,
        source: FactRef | None,
        counterpart: FactRef | None,
        at: float,
        actor_id: str = "",
        source_command_id: str = "",
        repair_request_id: str = "",
        note: str = "",
    ) -> IntegrityResolution:
        """**Evidence that the divergence was addressed**, digested over the *new* facts.

        The digest is what makes "this was fixed" a claim backed by the books rather than an
        assertion somebody typed: it covers the resulting source and counterpart, so a later reader
        can compare it against what a rerun found."""
        refs = tuple(r.evidence_key() for r in (source, counterpart) if r is not None)
        return IntegrityResolution(
            id=derive_resolution_id(finding.id, request_key),
            org_id=finding.org_id,
            finding_id=finding.id,
            run_id=run_id,
            owner=finding.owner,
            action=action,
            request_key=request_key,
            repair_request_id=repair_request_id,
            source_command_id=source_command_id,
            resulting_source=source,
            resulting_counterpart=counterpart,
            evidence_digest=derive_evidence_digest(refs),
            resolved_at=at,
            resolved_by=actor_id,
            note=note,
        )

    # ── audit ───────────────────────────────────────────────────────────────────

    def activity(
        self,
        run_id: str,
        org_id: str,
        *,
        kind: str,
        summary: str = "",
        actor_id: str = "",
        finding_id: str = "",
        detail: dict[str, str] | None = None,
        at: float = 0.0,
    ) -> IntegrityActivity:
        return IntegrityActivity(
            id=self.ids.new(PREFIX_INTEGRITY_ACTIVITY),
            org_id=org_id,
            run_id=run_id,
            at=at or self.clock.now(),
            kind=kind,
            summary=summary,
            actor_id=actor_id,
            finding_id=finding_id,
            detail=dict(detail or {}),
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        *,
        org_id: str,
        subject_id: str,
        actor_id: str = "",
        data: dict[str, str] | None = None,
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data or {}),
            )
        )


# ── repository contracts ────────────────────────────────────────────────────────


class IntegrityRunRepository(ABC):
    """Runs, arbitrated by the database.

    `create` is an insert on an id derived from the evidence digest, so two workers reconciling the
    same facts produce one run. There is no `delete`: a run is the record that somebody looked."""

    @abstractmethod
    def create(self, run: IntegrityRun) -> tuple[IntegrityRun, bool]:
        """Insert if absent. Returns `(run, is_new)`; the stored one when it already exists."""

    @abstractmethod
    def complete(self, run: IntegrityRun) -> IntegrityRun:
        """Record the counts and the terminal state. Called once per run by its winner."""

    @abstractmethod
    def get(self, org_id: str, run_id: str) -> IntegrityRun | None: ...

    @abstractmethod
    def recent(
        self, org_id: str, relationship: str = "", limit: int = 100
    ) -> tuple[IntegrityRun, ...]: ...

    @abstractmethod
    def for_subject(
        self, org_id: str, subject_id: str, limit: int = 100
    ) -> tuple[IntegrityRun, ...]: ...


class IntegrityFindingRepository(ABC):
    """Divergences. Append-only apart from the narrow lifecycle columns."""

    @abstractmethod
    def append(self, finding: IntegrityFinding) -> tuple[IntegrityFinding, bool]:
        """Insert on the derived id. **The duplicate-finding defence**: a second detection of the
        same divergence in the same run returns the stored row rather than a second finding."""

    @abstractmethod
    def advance(
        self, finding: IntegrityFinding, *, expected_version: int
    ) -> IntegrityFinding:
        """Conditional update. Raises `FindingStale` when the stored version has moved — which is
        exactly what happens when a stale worker tries to write over somebody else's resolution."""

    @abstractmethod
    def get(self, org_id: str, finding_id: str) -> IntegrityFinding | None: ...

    @abstractmethod
    def for_run(self, org_id: str, run_id: str) -> tuple[IntegrityFinding, ...]: ...

    @abstractmethod
    def open_findings(
        self, org_id: str, severities: tuple[str, ...] = (), limit: int = 200
    ) -> tuple[IntegrityFinding, ...]:
        """Unresolved divergences, worst first. **The read an operator's queue calls** — and the
        read an external scheduler polls, because this package runs no scheduler of its own."""

    @abstractmethod
    def by_owner(
        self, org_id: str, owner: str, limit: int = 200
    ) -> tuple[IntegrityFinding, ...]: ...

    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[IntegrityFinding, ...]: ...

    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[IntegrityFinding, ...]: ...


class RepairRequestRepository(ABC):
    """Requests dispatched to owning domains. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(self, request: RepairRequest) -> tuple[RepairRequest, bool]: ...

    @abstractmethod
    def get(self, org_id: str, request_id: str) -> RepairRequest | None: ...

    @abstractmethod
    def for_finding(self, org_id: str, finding_id: str) -> tuple[RepairRequest, ...]: ...


class IntegrityResolutionRepository(ABC):
    """Resolution evidence. Append-only; **there is no update path at all.**

    Historical divergence stays auditable because nothing here can rewrite it — a resolution is a
    new row that points at a finding, never an edit that makes the finding look like it never
    happened."""

    @abstractmethod
    def append(self, resolution: IntegrityResolution) -> tuple[IntegrityResolution, bool]: ...

    @abstractmethod
    def get(self, org_id: str, resolution_id: str) -> IntegrityResolution | None: ...

    @abstractmethod
    def for_finding(self, org_id: str, finding_id: str) -> tuple[IntegrityResolution, ...]: ...


class IntegrityActivityRepository(ABC):
    """The audit trail an accountant reads."""

    @abstractmethod
    def append(self, activity: IntegrityActivity) -> IntegrityActivity: ...

    @abstractmethod
    def for_run(
        self, org_id: str, run_id: str, limit: int = 200
    ) -> tuple[IntegrityActivity, ...]: ...


#: Which relationship a divergence belongs to when a caller only has the finding. Derived from the
#: matrix rather than stored, so it cannot drift from it.
def owner_for(relationship: RelationshipKind) -> RepairOwner:
    from .commercial_reconciliation import RECONCILIATION_MATRIX

    return RECONCILIATION_MATRIX[relationship].owner


def owner_for_divergence(
    relationship: RelationshipKind, divergence: DivergenceKind
) -> RepairOwner:
    """Who fixes this, given both the pair and what went wrong.

    The matrix names a default owner per relationship, but two divergence classes override it,
    and both overrides are judgements worth stating:

    * an **orphan** — a counterpart nothing caused — is never the source domain's problem, because
      the source domain has no record of it to fix. It goes to `OPERATOR`, who must decide whether
      it is a fabricated fact or evidence M9.19 failed to read;
    * **malformed or stale evidence** is a reader problem, not a books problem. Sending it to a
      financial domain would ask them to repair something that is not broken."""
    if divergence in (DivergenceKind.UNEXPECTED_COUNTERPART, DivergenceKind.DUPLICATE_SOURCE):
        return RepairOwner.OPERATOR
    if divergence in (DivergenceKind.EVIDENCE_MALFORMED, DivergenceKind.STALE_EVIDENCE):
        return RepairOwner.OPERATOR
    return owner_for(relationship)


__all__ = [
    "PREFIX_INTEGRITY_ACTIVITY",
    "SEVERITY_EVENTS",
    "IntegrityActivityRepository",
    "IntegrityFactory",
    "IntegrityFindingRepository",
    "IntegrityResolutionRepository",
    "IntegrityRunRepository",
    "RepairRequestRepository",
    "owner_for",
    "owner_for_divergence",
]
