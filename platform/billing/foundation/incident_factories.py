"""Incident factories + repository contracts (M9.20).

`IncidentFactory` is the one place an incident, a finding link, an audit event, a merge, a
correlation correction or a repair reference is constructed. Same discipline as every milestone
since M9.2: validate structurally, fail fast on error-severity findings (`BillingValidationError`,
§A2.6), compose an immutable aggregate from the injected id and clock seams, emit the corresponding
`billing.*` fact through the injected emitter. No I/O, no persistence, no transport, no provider,
no scheduler.

**Exactly one event per business fact**, and three are deliberately absent:

* there is no `billing.incident.finding_reconciled`. M9.19 already publishes what a reconciliation
  found; a second type here would let one divergence be counted twice by anyone aggregating;
* there is no `billing.incident.repaired`. M9.20 repairs nothing. The owning domain publishes its
  own repair, and republishing it would make one fix look like two;
* there is no event for a **rejected** command. A refusal is not a business fact, and emitting one
  would make an audit trail in which nothing happened indistinguishable from one in which something
  did. Rejections surface as raised errors and as the Application's own `use_case.rejected`.

## Why identities are derived rather than minted

* an **incident** is derived from `(org, correlation key)`, and the correlation key is itself a hash
  over immutable financial identifiers. Two workers correlating the same finding compute the same
  incident id and one loses a primary-key race, rather than both opening an incident about one
  cause;
* a **link** is derived from `(incident, finding)`. **This is the duplicate-attachment defence**:
  the same finding delivered twice attaches once, and a restart mid-correlation re-derives the same
  id and adds nothing;
* an **audit event** is derived from `(incident, kind, subject, sequence)`, so a replayed command
  appends no second line to the trail;
* a **merge** is derived from `(org, canonical, merged)` and a **correction** from
  `(org, finding, previous key, corrected key)`, so two operators reaching the same conclusion
  record it once.

## What no repository here can do

There is no `update` on `IncidentEventRepository`, `IncidentMergeRepository` or
`CorrelationCorrectionRepository`, and no `delete` anywhere — history is evidence, and evidence is
not edited. `IncidentRepository.advance` takes the version the caller read and is a compare-and-set:
a worker that decided from a stale view fails loudly rather than overwriting a conclusion somebody
else reached.

And there is **no repository here for a reconciliation finding, a ledger entry, an invoice, a tax
assessment, a payment or a recognition schedule.** M9.20 reads those through the owning domain's own
seams, supplied by the Application, and writes to none of them — including M9.19's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_incidents import (
    AnchorKind,
    CorrelationAnchor,
    CorrelationCorrection,
    EdgeKind,
    FindingLink,
    ImpactClass,
    IncidentEvent,
    IncidentEventKind,
    IncidentMerge,
    IncidentRepair,
    IncidentSeverity,
    IncidentStatus,
    IntegrityIncident,
    RootCause,
    derive_correlation_correction_id,
    derive_incident_event_id,
    derive_incident_evidence_digest,
    derive_incident_id,
    derive_incident_repair_id,
    derive_link_id,
    derive_merge_id,
)
from .commercial_reconciliation import IntegrityFinding, RepairOwner
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .incident_rules import CorrelationService, FindingEvidence, IncidentValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

#: Which `billing.*` fact each audit event publishes. A table so "exactly one event per business
#: fact" is checkable by iteration rather than asserted in prose.
#:
#: Most audit kinds are **absent** from this table on purpose. The `billing.*` stream is the
#: platform's business-fact feed, not a mirror of the audit trail: an external consumer needs to
#: know an incident opened, merged, resolved or was corrected — it does not need one message per
#: investigation click. The full sequence lives in `cp_billing_integrity_event`, which is where an
#: auditor reads it.
INCIDENT_EVENTS: dict[IncidentEventKind, BillingEventType] = {
    IncidentEventKind.INCIDENT_CREATED: BillingEventType.INCIDENT_OPENED,
    IncidentEventKind.FINDING_ATTACHED: BillingEventType.INCIDENT_FINDING_CORRELATED,
    IncidentEventKind.RESOLVED: BillingEventType.INCIDENT_RESOLVED,
    IncidentEventKind.CLOSED: BillingEventType.INCIDENT_CLOSED,
    IncidentEventKind.INCIDENT_MERGED: BillingEventType.INCIDENT_MERGED,
    IncidentEventKind.CORRELATION_CORRECTED: BillingEventType.INCIDENT_CORRELATION_CORRECTED,
}


@dataclass(frozen=True, slots=True)
class IncidentFactory:
    """Constructs every correlation aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: IncidentValidator = IncidentValidator()
    correlation: CorrelationService = CorrelationService()

    # ── the incident ────────────────────────────────────────────────────────────

    def incident(
        self,
        evidence: FindingEvidence,
        *,
        severity: IncidentSeverity,
        root_cause: RootCause,
        impact: ImpactClass,
        account_id: str = "",
        at: float = 0.0,
    ) -> tuple[IntegrityIncident, CorrelationAnchor]:
        """Open one incident around a correlation key.

        The key is computed **before** anything is written, from immutable identifiers only, so two
        workers correlating the same finding compute the same id and the database arbitrates. The
        anchor is returned alongside because the link needs to record *which* evidence proved the
        correlation — an incident that cannot say why a finding belongs to it is a bucket, not an
        explanation."""
        self._fail_fast(self.validator.validate_evidence(evidence))
        anchor, key = self.correlation.key_for(evidence)
        now = at or self.clock.now()
        built = IntegrityIncident(
            id=derive_incident_id(evidence.org_id, key),
            org_id=evidence.org_id,
            correlation_key=key,
            anchor_kind=anchor.kind,
            anchor_value=anchor.value,
            status=IncidentStatus.OPEN,
            severity=severity,
            root_cause=root_cause,
            impact=impact,
            finding_count=0,
            relationships=(evidence.relationship,),
            account_id=account_id,
            evidence_digest=derive_incident_evidence_digest((key,)),
            first_detected_at=now,
            last_observed_at=now,
            version=0,
        )
        return built, anchor

    def emit_opened(self, incident: IntegrityIncident, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.INCIDENT_OPENED,
            org_id=incident.org_id,
            subject_id=incident.id,
            actor_id=actor_id,
            data={
                "correlation_key": incident.correlation_key,
                "anchor": f"{incident.anchor_kind.value}={incident.anchor_value}",
                "severity": incident.severity.value,
                "root_cause": incident.root_cause.value,
                "impact": incident.impact.value,
            },
        )

    def emit_status(self, incident: IntegrityIncident, *, actor_id: str = "") -> None:
        mapping = {
            IncidentStatus.RESOLVED: BillingEventType.INCIDENT_RESOLVED,
            IncidentStatus.CLOSED: BillingEventType.INCIDENT_CLOSED,
            IncidentStatus.MERGED: BillingEventType.INCIDENT_MERGED,
        }
        event_type = mapping.get(incident.status)
        if event_type is None:
            return  # investigation states are audit-trail news, not platform business facts
        self._emit(
            event_type,
            org_id=incident.org_id,
            subject_id=incident.id,
            actor_id=actor_id,
            data={
                "status": incident.status.value,
                "severity": incident.severity.value,
                "root_cause": incident.root_cause.value,
                "canonical_id": incident.canonical_id,
                "reason": incident.closed_reason,
            },
        )

    # ── the link ────────────────────────────────────────────────────────────────

    def link(
        self,
        incident: IntegrityIncident,
        finding: IntegrityFinding,
        anchor: CorrelationAnchor,
        edge: EdgeKind,
        *,
        at: float,
        actor_id: str = "",
    ) -> FindingLink:
        """Attach one finding, **recording the anchor that proved it belongs**.

        The anchor is stored on the link rather than only on the incident, because a correlation
        correction changes what the incident is anchored on and the *original* justification for
        each attachment must survive that. An auditor asking "why did you believe this finding
        belonged here in March?" reads this row."""
        return FindingLink(
            id=derive_link_id(incident.id, finding.id),
            org_id=incident.org_id,
            incident_id=incident.id,
            finding_id=finding.id,
            run_id=finding.run_id,
            relationship=finding.relationship,
            divergence=finding.divergence,
            severity=finding.severity,
            anchor_kind=anchor.kind,
            anchor_value=anchor.value,
            edge=edge,
            subject_key=finding.subject_key,
            attached_at=at,
            attached_by=actor_id,
        )

    def emit_correlated(self, link: FindingLink, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.INCIDENT_FINDING_CORRELATED,
            org_id=link.org_id,
            subject_id=link.incident_id,
            actor_id=actor_id,
            data={
                "finding_id": link.finding_id,
                "relationship": link.relationship.value,
                "divergence": link.divergence.value,
                "anchor": f"{link.anchor_kind.value}={link.anchor_value}",
                "edge": link.edge.value,
            },
        )

    # ── the audit trail ─────────────────────────────────────────────────────────

    def event(
        self,
        incident: IntegrityIncident,
        kind: IncidentEventKind,
        *,
        sequence: int,
        at: float,
        actor_id: str = "",
        finding_id: str = "",
        subject: str = "",
        summary: str = "",
        detail: dict[str, str] | None = None,
    ) -> IncidentEvent:
        """One immutable line of the trail. **The source of explanation, not the log.**

        `sequence` is supplied by the caller from the stored count, so the trail has a total order
        that survives a restart — and the id is derived from it, so a replayed command appends
        nothing twice."""
        return IncidentEvent(
            id=derive_incident_event_id(incident.id, kind.value, subject or finding_id, sequence),
            org_id=incident.org_id,
            incident_id=incident.id,
            sequence=sequence,
            kind=kind,
            at=at,
            actor_id=actor_id,
            finding_id=finding_id,
            subject=subject,
            summary=summary,
            detail=dict(detail or {}),
        )

    # ── merge and correction ────────────────────────────────────────────────────

    def merge(
        self,
        canonical: IntegrityIncident,
        merged: IntegrityIncident,
        *,
        reason: str,
        at: float,
        actor_id: str = "",
    ) -> IncidentMerge:
        """**A merge as a new fact.** Neither original is rewritten.

        The merged incident moves to `MERGED` and gains a `canonical_id`; its severity, its findings
        and its history stay exactly as they were, and it remains readable forever. An auditor sees
        what was believed at the time and what superseded it — which a destructive merge
        destroys."""
        return IncidentMerge(
            id=derive_merge_id(canonical.org_id, canonical.id, merged.id),
            org_id=canonical.org_id,
            canonical_id=canonical.id,
            merged_id=merged.id,
            reason=reason,
            evidence=f"{merged.anchor_kind.value}={merged.anchor_value} → "
            f"{canonical.anchor_kind.value}={canonical.anchor_value}",
            anchor_kind=canonical.anchor_kind,
            decided_at=at,
            decided_by=actor_id,
        )

    def correction(
        self,
        *,
        org_id: str,
        finding_id: str,
        previous_incident_id: str,
        previous_key: str,
        corrected_incident_id: str,
        corrected_key: str,
        reason: str,
        evidence: str,
        at: float,
        actor_id: str = "",
    ) -> CorrelationCorrection:
        """**A correction as a new fact.** The previous correlation stays in the trail.

        Carries both keys deliberately: an auditor's question is never "what is the correlation?"
        but "what did you believe, when, and on what grounds did you change your mind?"."""
        return CorrelationCorrection(
            id=derive_correlation_correction_id(org_id, finding_id, previous_key, corrected_key),
            org_id=org_id,
            finding_id=finding_id,
            previous_incident_id=previous_incident_id,
            previous_key=previous_key,
            corrected_incident_id=corrected_incident_id,
            corrected_key=corrected_key,
            reason=reason,
            evidence=evidence,
            corrected_at=at,
            corrected_by=actor_id,
        )

    def emit_merged(self, merge: IncidentMerge, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.INCIDENT_MERGED,
            org_id=merge.org_id,
            subject_id=merge.merged_id,
            actor_id=actor_id,
            data={
                "canonical_id": merge.canonical_id,
                "reason": merge.reason,
                "evidence": merge.evidence,
            },
        )

    def emit_corrected(self, correction: CorrelationCorrection, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.INCIDENT_CORRELATION_CORRECTED,
            org_id=correction.org_id,
            subject_id=correction.corrected_incident_id,
            actor_id=actor_id,
            data={
                "finding_id": correction.finding_id,
                "previous_incident_id": correction.previous_incident_id,
                "previous_key": correction.previous_key,
                "corrected_key": correction.corrected_key,
                "reason": correction.reason,
            },
        )

    # ── repair reference ────────────────────────────────────────────────────────

    def repair(
        self,
        incident: IntegrityIncident,
        *,
        owner: RepairOwner,
        action: str,
        request_key: str,
        finding_id: str = "",
        reference: str = "",
        at: float,
        actor_id: str = "",
        detail: str = "",
    ) -> IncidentRepair:
        """**A reference, not a repair.**

        M9.20 records that an owning domain was asked and under which reference. It performs
        nothing, and `observed` later records only that the owner reported back — never that the
        incident is resolved. Only verification says that."""
        return IncidentRepair(
            id=derive_incident_repair_id(incident.id, owner.value, action, request_key),
            org_id=incident.org_id,
            incident_id=incident.id,
            owner=owner,
            action=action,
            request_key=request_key,
            finding_id=finding_id,
            reference=reference,
            requested_at=at,
            requested_by=actor_id,
            detail=detail,
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


class IncidentRepository(ABC):
    """Incidents, arbitrated by the database.

    `create` is an insert on an id derived from the correlation key, so two workers correlating the
    same finding produce one incident. `advance` is a compare-and-set on `version`, so a worker
    holding a stale read cannot overwrite a conclusion somebody else reached. **There is no
    delete**: an incident is the record that Billing noticed."""

    @abstractmethod
    def create(self, incident: IntegrityIncident) -> tuple[IntegrityIncident, bool]:
        """Insert if absent. Returns `(incident, is_new)`; the stored one when it already exists."""

    @abstractmethod
    def advance(
        self, incident: IntegrityIncident, *, expected_version: int
    ) -> IntegrityIncident:
        """Conditional update. Raises `IncidentStale` when the stored version has moved."""

    @abstractmethod
    def get(self, org_id: str, incident_id: str) -> IntegrityIncident | None: ...

    @abstractmethod
    def by_correlation_key(self, org_id: str, key: str) -> IntegrityIncident | None:
        """The read correlation performs before opening anything. Indexed and org-scoped."""

    @abstractmethod
    def page(
        self,
        org_id: str,
        *,
        statuses: tuple[str, ...] = (),
        severities: tuple[str, ...] = (),
        account_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[IntegrityIncident, ...], int]:
        """**One page and the total.** Pagination is a database concern, not a Python slice over
        every incident an org ever had — see the scale note in the milestone doc."""

    @abstractmethod
    def live(self, org_id: str, limit: int = 200) -> tuple[IntegrityIncident, ...]:
        """Incidents still awaiting somebody. The read an operator's queue and an external
        scheduler both call — M9.20 runs no scheduler of its own."""


class FindingLinkRepository(ABC):
    """Finding→incident attachments. Append-only apart from the narrow detach column."""

    @abstractmethod
    def attach(self, link: FindingLink) -> tuple[FindingLink, bool]:
        """Insert on the derived id. **The duplicate-attachment defence**: the same finding
        delivered twice attaches once and returns the stored link."""

    @abstractmethod
    def detach(self, link: FindingLink) -> FindingLink:
        """Mark detached. **The row is never deleted** — "this was once believed to belong here"
        is exactly what an auditor asks about after a correlation correction."""

    @abstractmethod
    def get(self, org_id: str, link_id: str) -> FindingLink | None: ...

    @abstractmethod
    def for_incident(self, org_id: str, incident_id: str) -> tuple[FindingLink, ...]: ...

    @abstractmethod
    def for_finding(self, org_id: str, finding_id: str) -> tuple[FindingLink, ...]:
        """Every incident a finding was ever attached to, including detached ones."""


class IncidentEventRepository(ABC):
    """The audit trail. **Append-only; there is no update and no delete.**"""

    @abstractmethod
    def append(self, event: IncidentEvent) -> tuple[IncidentEvent, bool]: ...

    @abstractmethod
    def for_incident(
        self, org_id: str, incident_id: str, limit: int = 500
    ) -> tuple[IncidentEvent, ...]:
        """The timeline, in sequence order. **The source of explanation, not the log.**"""

    @abstractmethod
    def next_sequence(self, org_id: str, incident_id: str) -> int:
        """The next position in this incident's total order, from a **stored count**.

        Not `max(sequence) + 1` derived on the fly: a derived maximum hands a number back after a
        rollback, and two events that both occupied position 7 make the trail ambiguous — the same
        rule M9.13 applies to ledger sequences."""


class IncidentMergeRepository(ABC):
    """Merge decisions. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(self, merge: IncidentMerge) -> tuple[IncidentMerge, bool]: ...

    @abstractmethod
    def for_canonical(self, org_id: str, canonical_id: str) -> tuple[IncidentMerge, ...]: ...

    @abstractmethod
    def for_merged(self, org_id: str, merged_id: str) -> IncidentMerge | None: ...


class CorrelationCorrectionRepository(ABC):
    """Correlation corrections. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(
        self, correction: CorrelationCorrection
    ) -> tuple[CorrelationCorrection, bool]: ...

    @abstractmethod
    def for_finding(
        self, org_id: str, finding_id: str
    ) -> tuple[CorrelationCorrection, ...]: ...

    @abstractmethod
    def for_incident(
        self, org_id: str, incident_id: str
    ) -> tuple[CorrelationCorrection, ...]: ...


class IncidentRepairRepository(ABC):
    """Repair references. Append-only apart from the observation flag."""

    @abstractmethod
    def append(self, repair: IncidentRepair) -> tuple[IncidentRepair, bool]: ...

    @abstractmethod
    def observe(self, repair: IncidentRepair) -> IncidentRepair:
        """Record that the owning domain reported back. **Not evidence of resolution.**"""

    @abstractmethod
    def get(self, org_id: str, repair_id: str) -> IncidentRepair | None: ...

    @abstractmethod
    def for_incident(self, org_id: str, incident_id: str) -> tuple[IncidentRepair, ...]: ...


def anchor_of(kind: AnchorKind, value: str, detail: str = "") -> CorrelationAnchor:
    """Construct an anchor. Exists so callers never build one from a literal enum member and a
    string they assembled themselves — the resolvers in `CorrelationService` are the only supported
    source, and this keeps the type's construction visible in one place."""
    return CorrelationAnchor(kind=kind, value=value, detail=detail)


__all__ = [
    "INCIDENT_EVENTS",
    "CorrelationCorrectionRepository",
    "FindingLinkRepository",
    "IncidentEventRepository",
    "IncidentFactory",
    "IncidentMergeRepository",
    "IncidentRepairRepository",
    "IncidentRepository",
    "anchor_of",
]
