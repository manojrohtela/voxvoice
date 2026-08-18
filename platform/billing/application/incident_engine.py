"""Financial Integrity Correlation application layer (M9.20) — commands, queries, use cases.

Additive to M9.3/M9.6–M9.19: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary and dispatch discipline are reused exactly as they are. This module adds 11 commands,
14 queries, their use cases, and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Reconciliation (findings) ──read──▶ Correlation ──command──▶ owning domain (repair)
    Invoice / Payment / Revenue ─read──▶     │
    (document trace only)                    └─▶ emits billing.incident.*

**Read arrows and one write arrow, and the write arrow writes nothing financial.** Every read is of
an immutable fact another domain already decided. The single outward command is M9.19's own
`RequestFindingRepair`, which itself only asks an owning domain — so M9.20 is two removes from any
financial mutation, and a test asserts that no ledger, invoice, tax, payment or recognition
repository is reachable from this module.

## The document trace, and why it is a trace rather than a guess

`_EvidenceAssembler` turns an M9.19 finding into `FindingEvidence` — the only thing the correlation
service is allowed to see. Two fields are *traced* rather than read directly:

* a recognition finding names a schedule; **M9.18's schedule names its document**, and the basis
  carries the issued snapshot digest;
* a payment finding names an attempt; **M9.16's intent names the obligation it collects against**,
  and carries the digest it read the amount from.

Both are reads of the owning domain's own immutable facts. When either read fails the field stays
empty and the anchor falls to the next precedence level — the trace never substitutes something
weaker, because a document id nobody can prove is exactly the invented fact this milestone forbids.

## Verification is a re-read, not a checkbox

`VerifyIncident` re-reads every attached M9.19 finding and reports what the books say **now**.
`ResolveIncident` refuses unless that outcome is clean, and "clean" excludes *unreadable*: a finding
that could not be re-read is not evidence of agreement. Repair requests do not resolve anything —
there is no `REPAIR_IN_PROGRESS → RESOLVED` edge for them to travel.

## No scheduler

`ListIncidents`, `LiveIncidents` and `CorrelateFindings` are invoked from outside. Nothing here
loops, sleeps, polls or spawns; whoever decides *when* to correlate lives outside this plane,
exactly as for M9.19's `/integrity/findings` and M9.18's `/revenue/entries/due`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from billing.foundation import BillingConflict as _Conflict
from billing.foundation import (
    BillingError,
    FindingEvidence,
    FindingLink,
    ImpactClass,
    IncidentEventKind,
    IncidentSearchQuery,
    IncidentSeverity,
    IncidentStatus,
    IncidentTrigger,
    IntegrityFinding,
    IntegrityIncident,
    RelationshipKind,
    RepairOwner,
    RootCause,
    VerificationOutcome,
    derive_evidence_digest,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .reconciliation_engine import RequestFindingRepair
from .unit_of_work import UnitOfWorkFactory

#: How many times a worker re-reads and re-decides after losing a compare-and-set. Bounded: each
#: round either wins or discovers the incident moved somewhere it must not touch.
_CAS_ATTEMPTS = 5


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CorrelateFindings(_Command):
    """Correlate M9.19 findings into incidents. **The command this milestone exists for.**

    Reads findings read-only, derives each one's correlation identity from immutable evidence, and
    either attaches it to the incident that identity already names or opens one. Idempotent end to
    end: the same finding delivered twice attaches once."""

    finding_ids: tuple[str, ...] = ()
    run_id: str = ""  # correlate everything one reconciliation run found
    limit: int = 200


@dataclass(frozen=True, slots=True)
class AcknowledgeIncident(_Command):
    incident_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class InvestigateIncident(_Command):
    incident_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class RequestIncidentRepair(_Command):
    """Ask the **owning domain** to fix it, through M9.19's own repair command.

    M9.20 performs nothing and imports no domain repository. `request_key` makes the ask idempotent,
    which matters because the dispatched repair may move money two layers down."""

    incident_id: str = ""
    finding_id: str = ""
    request_key: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class ObserveIncidentRepair(_Command):
    """Record that an owning domain reported back. **Not evidence the incident is resolved.**"""

    incident_id: str = ""
    repair_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class VerifyIncident(_Command):
    """Re-read every attached finding and record what the books say now.

    Verification can legitimately fail, and when it does the incident goes back to investigating
    rather than sitting in a state that implies progress."""

    incident_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class ResolveIncident(_Command):
    """Close an incident **with evidence**.

    Refuses unless the most recent verification found every attached finding settled and nothing
    unreadable. There is no `force` flag."""

    incident_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CloseIncident(_Command):
    """Accept an incident as permanent — a **decision**, not a repair.

    Separate from resolve deliberately: resolved means the books agree, closed means somebody
    decided to live with it. Collapsing them would let "we gave up" be reported as "we fixed it"."""

    incident_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MergeIncidents(_Command):
    """**A merge as a new fact.** Neither original is rewritten.

    The merged incident moves to `MERGED` and gains a `canonical_id`; its severity, its findings and
    its history stay exactly as they were and remain readable forever."""

    canonical_id: str = ""
    merged_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CorrectCorrelation(_Command):
    """**A correction as a new fact.** The previous correlation stays in the trail.

    Detaches a finding from the incident it was wrongly attached to and re-correlates it. The old
    link is marked detached rather than deleted, and a `CorrelationCorrection` row records what was
    believed, what replaced it, and why."""

    finding_id: str = ""
    incident_id: str = ""  # the incident it is currently attached to
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ClassifyIncidentRootCause(_Command):
    """Re-derive the root cause and impact from the currently attached findings.

    Derivation, not assertion: the command takes no cause argument, because a caller-supplied cause
    would be exactly the invented fact this milestone forbids."""

    incident_id: str = ""


ALL_INCIDENT_COMMANDS = (
    CorrelateFindings,
    AcknowledgeIncident,
    InvestigateIncident,
    RequestIncidentRepair,
    ObserveIncidentRepair,
    VerifyIncident,
    ResolveIncident,
    CloseIncident,
    MergeIncidents,
    CorrectCorrelation,
    ClassifyIncidentRootCause,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetIncident(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class ListIncidents(_Query):
    """**Paginated at the database.** `offset`/`limit` reach a `LIMIT/OFFSET` over an index."""

    statuses: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    account_id: str = ""
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class LiveIncidents(_Query):
    """Unsettled incidents, worst first. **The read an external scheduler polls.**"""

    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetIncidentTimeline(_Query):
    """The audit trail. **The source of explanation — not an application log.**"""

    incident_id: str = ""
    limit: int = 500


@dataclass(frozen=True, slots=True)
class GetIncidentFindings(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class GetIncidentEvidence(_Query):
    """The evidence graph: which findings belong, and the anchor that proved each one."""

    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class ExplainIncident(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class InspectIncident(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class GetIncidentRepairs(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class GetIncidentMerges(_Query):
    incident_id: str = ""


@dataclass(frozen=True, slots=True)
class GetCorrelationCorrections(_Query):
    incident_id: str = ""
    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class GetFindingIncidents(_Query):
    """Every incident a finding was ever attached to, **including detached ones**."""

    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchIncidents(_Query):
    statuses: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    root_causes: tuple[str, ...] = ()
    impacts: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    account_id: str = ""
    correlation_key: str = ""
    anchor_value: str = ""
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class GetIncidentPosture(_Query):
    """Counts, derived on every call. **There is no stored posture and no monetary total.**"""

    limit: int = 500


ALL_INCIDENT_QUERIES = (
    GetIncident,
    ListIncidents,
    LiveIncidents,
    GetIncidentTimeline,
    GetIncidentFindings,
    GetIncidentEvidence,
    ExplainIncident,
    InspectIncident,
    GetIncidentRepairs,
    GetIncidentMerges,
    GetCorrelationCorrections,
    GetFindingIncidents,
    SearchIncidents,
    GetIncidentPosture,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IncidentView:
    id: str
    org_id: str
    correlation_key: str
    anchor_kind: str
    anchor_value: str
    status: str
    severity: str
    root_cause: str
    impact: str
    finding_count: int
    unresolved_findings: int
    relationships: tuple[str, ...]
    owners: tuple[str, ...]
    account_id: str
    evidence_digest: str
    first_detected_at: float
    last_observed_at: float
    canonical_id: str
    version: int
    closed_at: float | None = None
    closed_reason: str = ""
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FindingLinkView:
    id: str
    incident_id: str
    finding_id: str
    run_id: str
    relationship: str
    divergence: str
    severity: str
    anchor_kind: str
    anchor_value: str
    edge: str
    subject_key: str
    attached_at: float
    attached_by: str
    is_attached: bool
    detached_at: float | None = None
    detached_reason: str = ""


@dataclass(frozen=True, slots=True)
class IncidentEventView:
    id: str
    incident_id: str
    sequence: int
    kind: str
    at: float
    actor_id: str
    finding_id: str
    subject: str
    summary: str
    detail: dict


@dataclass(frozen=True, slots=True)
class EvidenceNodeView:
    id: str
    kind: str
    label: str
    detail: str


@dataclass(frozen=True, slots=True)
class EvidenceEdgeView:
    source_id: str
    target_id: str
    kind: str
    anchor: str
    evidence: str


@dataclass(frozen=True, slots=True)
class EvidenceGraphView:
    incident_id: str
    nodes: tuple[EvidenceNodeView, ...]
    edges: tuple[EvidenceEdgeView, ...]
    is_connected: bool


@dataclass(frozen=True, slots=True)
class IncidentRepairView:
    id: str
    incident_id: str
    owner: str
    action: str
    request_key: str
    finding_id: str
    reference: str
    observed: bool
    observed_at: float | None
    requested_at: float
    requested_by: str
    detail: str


@dataclass(frozen=True, slots=True)
class IncidentMergeView:
    id: str
    canonical_id: str
    merged_id: str
    reason: str
    evidence: str
    anchor_kind: str
    decided_at: float
    decided_by: str


@dataclass(frozen=True, slots=True)
class CorrelationCorrectionView:
    id: str
    finding_id: str
    previous_incident_id: str
    previous_key: str
    corrected_incident_id: str
    corrected_key: str
    reason: str
    evidence: str
    corrected_at: float
    corrected_by: str


@dataclass(frozen=True, slots=True)
class IncidentExplanationView:
    incident_id: str
    correlation_key: str
    anchor_kind: str
    anchor_value: str
    status: str
    severity: str
    root_cause: str
    impact: str
    narrative: tuple[str, ...]
    findings: tuple[str, ...]
    relationships: tuple[str, ...]
    owners: tuple[str, ...]
    first_detected_at: float
    last_observed_at: float
    evidence_digest: str
    canonical_id: str


@dataclass(frozen=True, slots=True)
class IncidentInspectionView:
    incident_id: str
    status: str
    severity: str
    finding_count: int
    links_recorded: int
    events_recorded: int
    is_counted_correctly: bool
    is_scoped: bool
    is_connected: bool
    is_severity_monotonic: bool
    unresolved_findings: int
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationView:
    incident_id: str
    checked: int
    still_divergent: int
    resolved_findings: int
    unreadable: int
    is_clean: bool
    detail: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IncidentPostureView:
    org_id: str
    incidents: int
    open_incidents: int
    acknowledged: int
    investigating: int
    repair_in_progress: int
    verifying: int
    resolved: int
    closed: int
    merged: int
    critical: int
    error: int
    warning: int
    is_clean: bool
    by_root_cause: dict
    by_impact: dict
    by_owner: dict


@dataclass(frozen=True, slots=True)
class IncidentPageView:
    incidents: tuple[IncidentView, ...]
    total: int
    limit: int
    offset: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class CorrelationReceiptView:
    """What one correlation pass returns: what it did, and to which incidents."""

    incidents: tuple[IncidentView, ...]
    correlated: int
    attached: int
    already_attached: int
    incidents_opened: int


def incident_view(
    incident: IntegrityIncident, allowed: tuple[IncidentTrigger, ...] = ()
) -> IncidentView:
    return IncidentView(
        id=incident.id,
        org_id=incident.org_id,
        correlation_key=incident.correlation_key,
        anchor_kind=incident.anchor_kind.value,
        anchor_value=incident.anchor_value,
        status=incident.status.value,
        severity=incident.severity.value,
        root_cause=incident.root_cause.value,
        impact=incident.impact.value,
        finding_count=incident.finding_count,
        unresolved_findings=incident.unresolved_findings,
        relationships=tuple(sorted({r.value for r in incident.relationships})),
        owners=tuple(sorted({o.value for o in incident.owners})),
        account_id=incident.account_id,
        evidence_digest=incident.evidence_digest,
        first_detected_at=incident.first_detected_at,
        last_observed_at=incident.last_observed_at,
        canonical_id=incident.canonical_id,
        version=incident.version,
        closed_at=incident.closed_at,
        closed_reason=incident.closed_reason,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def link_view(link: FindingLink) -> FindingLinkView:
    return FindingLinkView(
        id=link.id,
        incident_id=link.incident_id,
        finding_id=link.finding_id,
        run_id=link.run_id,
        relationship=link.relationship.value,
        divergence=link.divergence.value,
        severity=link.severity.value,
        anchor_kind=link.anchor_kind.value,
        anchor_value=link.anchor_value,
        edge=link.edge.value,
        subject_key=link.subject_key,
        attached_at=link.attached_at,
        attached_by=link.attached_by,
        is_attached=link.is_attached,
        detached_at=link.detached_at,
        detached_reason=link.detached_reason,
    )


def incident_event_view(event) -> IncidentEventView:
    return IncidentEventView(
        id=event.id,
        incident_id=event.incident_id,
        sequence=event.sequence,
        kind=event.kind.value,
        at=event.at,
        actor_id=event.actor_id,
        finding_id=event.finding_id,
        subject=event.subject,
        summary=event.summary,
        detail=dict(event.detail),
    )


def graph_view(graph) -> EvidenceGraphView:
    return EvidenceGraphView(
        incident_id=graph.incident_id,
        nodes=tuple(
            EvidenceNodeView(id=n.id, kind=n.kind, label=n.label, detail=n.detail)
            for n in graph.nodes
        ),
        edges=tuple(
            EvidenceEdgeView(
                source_id=e.source_id,
                target_id=e.target_id,
                kind=e.kind.value,
                anchor=e.anchor.value,
                evidence=e.evidence,
            )
            for e in graph.edges
        ),
        is_connected=graph.is_connected,
    )


def repair_view(repair) -> IncidentRepairView:
    return IncidentRepairView(
        id=repair.id,
        incident_id=repair.incident_id,
        owner=repair.owner.value,
        action=repair.action,
        request_key=repair.request_key,
        finding_id=repair.finding_id,
        reference=repair.reference,
        observed=repair.observed,
        observed_at=repair.observed_at,
        requested_at=repair.requested_at,
        requested_by=repair.requested_by,
        detail=repair.detail,
    )


def merge_view(merge) -> IncidentMergeView:
    return IncidentMergeView(
        id=merge.id,
        canonical_id=merge.canonical_id,
        merged_id=merge.merged_id,
        reason=merge.reason,
        evidence=merge.evidence,
        anchor_kind=merge.anchor_kind.value,
        decided_at=merge.decided_at,
        decided_by=merge.decided_by,
    )


def correction_view(correction) -> CorrelationCorrectionView:
    return CorrelationCorrectionView(
        id=correction.id,
        finding_id=correction.finding_id,
        previous_incident_id=correction.previous_incident_id,
        previous_key=correction.previous_key,
        corrected_incident_id=correction.corrected_incident_id,
        corrected_key=correction.corrected_key,
        reason=correction.reason,
        evidence=correction.evidence,
        corrected_at=correction.corrected_at,
        corrected_by=correction.corrected_by,
    )


def incident_explanation_view(explanation) -> IncidentExplanationView:
    return IncidentExplanationView(
        incident_id=explanation.incident_id,
        correlation_key=explanation.correlation_key,
        anchor_kind=explanation.anchor_kind.value,
        anchor_value=explanation.anchor_value,
        status=explanation.status.value,
        severity=explanation.severity.value,
        root_cause=explanation.root_cause.value,
        impact=explanation.impact.value,
        narrative=explanation.narrative,
        findings=explanation.findings,
        relationships=explanation.relationships,
        owners=explanation.owners,
        first_detected_at=explanation.first_detected_at,
        last_observed_at=explanation.last_observed_at,
        evidence_digest=explanation.evidence_digest,
        canonical_id=explanation.canonical_id,
    )


def verification_view(outcome: VerificationOutcome) -> VerificationView:
    return VerificationView(
        incident_id=outcome.incident_id,
        checked=outcome.checked,
        still_divergent=outcome.still_divergent,
        resolved_findings=outcome.resolved_findings,
        unreadable=outcome.unreadable,
        is_clean=outcome.is_clean,
        detail=outcome.detail,
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IncidentRepositories:
    """The six correlation seams, injected. Interfaces only — implementations in persistence.

    Six, and only six. **Nothing here reaches a reconciliation, ledger, invoice, tax, payment,
    recognition, collection or subscription table.** Findings arrive as *reads* through the context;
    the single repair leaves as M9.19's own command."""

    incidents: object
    links: object
    events: object
    merges: object
    corrections: object
    repairs: object


@dataclass(frozen=True, slots=True)
class IncidentServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    correlation: object
    lifecycle: object
    guards: object
    resolution: object
    severity: object
    root_cause: object
    graph: object
    explainer: object
    inspector: object
    posture: object
    search: object
    validator: object


class _EvidenceAssembler:
    """Turns an M9.19 finding into the **only** thing correlation is allowed to see.

    `FindingEvidence` deliberately carries no amount and no timestamp, so a future correlation rule
    that wanted one could not compile. Everything it does carry is an identifier an owning domain
    already made immutable.

    **This is the only place M9.20 touches another domain, and it only ever reads.**"""

    def __init__(self, context) -> None:
        self._ctx = context

    def evidence_for(self, finding: IntegrityFinding) -> FindingEvidence:
        ledger_source, ledger_source_id = self._ledger_cause(finding)
        document_id, revision = self._document_trace(finding)
        return FindingEvidence(
            finding_id=finding.id,
            org_id=finding.org_id,
            relationship=finding.relationship,
            divergence=finding.divergence,
            subject_key=finding.subject_key,
            source=finding.source,
            counterpart=finding.counterpart,
            document_id=document_id,
            document_revision=revision,
            ledger_source=ledger_source,
            ledger_source_id=ledger_source_id,
        )

    @staticmethod
    def _ledger_cause(finding: IntegrityFinding) -> tuple[str, str]:
        """`(source, source_id)` from whichever side is a journal entry.

        **The strongest evidence that exists**: it is the cause the Ledger itself recorded, not an
        inference about it. Absent when neither side is a ledger entry — which is the normal case
        for a *missing* posting, and the anchor then falls to the next level."""
        for ref in (finding.counterpart, finding.source):
            if ref is None or ref.kind != "journal_entry":
                continue
            if ref.source and ref.source_id:
                return ref.source, ref.source_id
        return "", ""

    def _document_trace(self, finding: IntegrityFinding) -> tuple[str, str]:
        """The immutable document identity and its issued revision digest.

        **Traced through the owning domain's own facts, never guessed.** A recognition finding names
        a schedule and M9.18's schedule names its document; a payment finding names an attempt and
        M9.16's intent names the obligation it collects against. When a trace fails the pair is
        empty and correlation falls to the next precedence level, because a document id nobody can
        prove is exactly the invented fact this milestone forbids."""
        relationship = finding.relationship
        if relationship in (
            RelationshipKind.INVOICE_LEDGER,
            RelationshipKind.INVOICE_TAX,
            RelationshipKind.INVOICE_RECOGNITION,
        ):
            # The run's subject *is* the document; the finding carries its digest where the
            # snapshot was read.
            document_id = self._run_subject(finding)
            return document_id, self._document_digest(finding.org_id, document_id)
        if relationship is RelationshipKind.RECOGNITION_LEDGER:
            return self._from_schedule(finding)
        if relationship in (
            RelationshipKind.PAYMENT_LEDGER,
            RelationshipKind.PAYMENT_INVOICE,
        ):
            return self._from_payment(finding)
        return "", ""  # pragma: no cover — the six relationships are exhaustive

    def _run_subject(self, finding: IntegrityFinding) -> str:
        engine = getattr(self._ctx, "reconciliation_engine", None)
        runs = getattr(engine, "runs", None)
        if runs is None:
            return ""
        run = runs.get(finding.org_id, finding.run_id)
        return run.scope.subject_id if run is not None else ""

    def _document_digest(self, org_id: str, document_id: str) -> str:
        if not document_id:
            return ""
        engine = getattr(self._ctx, "invoice_engine", None)
        documents = getattr(engine, "documents", None)
        if documents is None:
            return ""
        document = documents.get(org_id, document_id)
        return getattr(document, "issued_snapshot_digest", "") if document else ""

    def _from_schedule(self, finding: IntegrityFinding) -> tuple[str, str]:
        """A recognition entry → its schedule → **the document the basis names**."""
        engine = getattr(self._ctx, "revenue_engine", None)
        schedules = getattr(engine, "schedules", None)
        entries = getattr(engine, "entries", None)
        if schedules is None or entries is None:
            return "", ""
        schedule_id = ""
        source = finding.source
        if source is not None and source.kind == "recognition_entry":
            entry = entries.get(finding.org_id, source.identity)
            schedule_id = getattr(entry, "schedule_id", "") if entry else ""
        if not schedule_id:
            schedule_id = self._run_subject(finding)
        if not schedule_id:
            return "", ""
        schedule = schedules.get(finding.org_id, schedule_id)
        basis = getattr(schedule, "basis", None)
        if basis is None:
            return "", ""
        return getattr(basis, "document_id", ""), getattr(basis, "snapshot_digest", "")

    def _from_payment(self, finding: IntegrityFinding) -> tuple[str, str]:
        """A payment attempt → its intent → **the obligation the intent names**."""
        engine = getattr(self._ctx, "payment_engine", None)
        intents = getattr(engine, "intents", None)
        attempts = getattr(engine, "attempts", None)
        if intents is None:
            return "", ""
        source = finding.source
        if source is None:
            return "", ""
        if source.kind == "payment_intent" and source.identity:
            # PAYMENT_INVOICE anchors the intent on the document it collects against.
            intent = self._intent_by_subject(intents, finding.org_id, source.identity)
            if intent is not None:
                return intent.subject_id, intent.subject_digest
            return source.identity, source.digest
        if source.kind == "payment_attempt" and attempts is not None:
            attempt = attempts.get(finding.org_id, source.identity)
            intent_id = getattr(attempt, "intent_id", "") if attempt else ""
            if intent_id:
                intent = intents.get(finding.org_id, intent_id)
                if intent is not None:
                    return intent.subject_id, intent.subject_digest
        return "", ""

    @staticmethod
    def _intent_by_subject(intents, org_id: str, subject_id: str):
        finder = getattr(intents, "for_subject", None)
        if finder is None:
            return None
        found = finder(org_id, subject_id)
        return found[-1] if found else None


class _IncidentSupport:
    """Shared lookups for the command and query sides."""

    def __init__(self, *, context, repos: IncidentRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services
        self._evidence = _EvidenceAssembler(context)

    def incident_or_404(self, org_id: str, incident_id: str) -> IntegrityIncident:
        found = self._repos.incidents.get(org_id, incident_id)
        if found is None:
            raise _NotFound(f"integrity incident {incident_id!r} not found")
        return found

    def findings_repo(self):
        """M9.19's finding repository — **read-only**.

        Held as a read seam rather than injected as a writable dependency, and an architecture test
        asserts no write method on it is ever called from this module."""
        engine = getattr(self._ctx, "reconciliation_engine", None)
        findings = getattr(engine, "findings", None)
        if findings is None:
            raise _Conflict(
                "the reconciliation engine is not wired; there are no findings to correlate"
            )
        return findings

    def finding_or_404(self, org_id: str, finding_id: str) -> IntegrityFinding:
        found = self.findings_repo().get(org_id, finding_id)
        if found is None:
            raise _NotFound(f"integrity finding {finding_id!r} not found")
        return found

    def _incident_view(self, incident: IntegrityIncident) -> IncidentView:
        return incident_view(incident, self._svc.lifecycle.allowed(incident.status))


class IncidentUseCases(_IncidentSupport):
    """Correlation orchestration. **Records what happened; repairs nothing.**"""

    def __init__(
        self,
        *,
        context,
        repos: IncidentRepositories,
        factory,
        services: IncidentServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
        dispatch: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        # **The repair boundary.** M9.19's own `RequestFindingRepair`, dispatched onto the shared
        # command bus — which itself only asks an owning domain. M9.20 is two removes from any
        # financial mutation and imports no domain repository.
        self._dispatch = dispatch

    # ── correlation ─────────────────────────────────────────────────────────────

    def correlate(self, command: CorrelateFindings) -> CorrelationReceiptView:
        """**The command this milestone exists for.**

        For each finding: assemble immutable evidence, derive its correlation identity, find or open
        the incident that identity names, attach, and append an audit line. Idempotent throughout —
        the incident id is derived from the correlation key and the link id from
        `(incident, finding)`, so a finding delivered twice attaches once and a restart mid-pass
        re-derives the same ids and adds nothing."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_READ)
        self._svc.guards.guard_scoped(command.org_id)
        at = self._clock.now()
        findings = self._findings_to_correlate(command)
        touched: dict[str, IntegrityIncident] = {}
        attached = duplicated = opened = 0
        with self._uow.new() as uow:
            for finding in findings:
                incident, was_new, was_attached = self._correlate_one(
                    command, finding, at=at
                )
                touched[incident.id] = incident
                opened += 1 if was_new else 0
                attached += 1 if was_attached else 0
                duplicated += 0 if was_attached else 1
            uow.stage(
                self._event(
                    command,
                    command.run_id or "batch",
                    correlated=str(len(findings)),
                    attached=str(attached),
                    incidents=str(len(touched)),
                )
            )
        return CorrelationReceiptView(
            incidents=tuple(self._incident_view(i) for i in touched.values()),
            correlated=len(findings),
            attached=attached,
            already_attached=duplicated,
            incidents_opened=opened,
        )

    def _findings_to_correlate(
        self, command: CorrelateFindings
    ) -> tuple[IntegrityFinding, ...]:
        repo = self.findings_repo()
        if command.finding_ids:
            found = [repo.get(command.org_id, fid) for fid in command.finding_ids]
            missing = [
                fid for fid, f in zip(command.finding_ids, found, strict=True) if f is None
            ]
            if missing:
                raise _NotFound(f"integrity finding(s) not found: {sorted(missing)}")
            return tuple(f for f in found if f is not None)
        if command.run_id:
            return repo.for_run(command.org_id, command.run_id)
        return repo.open_findings(command.org_id, (), command.limit)

    def _correlate_one(
        self, command, finding: IntegrityFinding, *, at: float
    ) -> tuple[IntegrityIncident, bool, bool]:
        evidence = self._evidence.evidence_for(finding)
        anchor, _key = self._svc.correlation.key_for(evidence)
        cause = self._svc.root_cause.classify((finding.divergence,))
        built, anchor = self._factory.incident(
            evidence,
            severity=self._svc.severity.aggregate(IncidentSeverity.INFO, ()),
            root_cause=cause,
            impact=self._svc.root_cause.impact_of(cause),
            account_id=finding.account_id,
            at=at,
        )
        incident, is_new = self._repos.incidents.create(built)
        self._svc.guards.guard_same_org(incident, evidence)
        if is_new:
            self._factory.emit_opened(incident, actor_id=command.actor_id)
            self._append_event(
                incident,
                IncidentEventKind.INCIDENT_CREATED,
                at=at,
                actor_id=command.actor_id,
                subject=incident.correlation_key,
                summary=f"correlated on {anchor.kind.value}",
                detail={"anchor": anchor.value, "detail": anchor.detail},
            )
        self._svc.guards.guard_attachable(incident)
        link = self._factory.link(
            incident,
            finding,
            anchor,
            self._svc.correlation.edge_for(anchor),
            at=at,
            actor_id=command.actor_id,
        )
        stored_link, link_is_new = self._repos.links.attach(link)
        if not link_is_new:
            return incident, is_new, False
        self._factory.emit_correlated(stored_link, actor_id=command.actor_id)
        self._append_event(
            incident,
            IncidentEventKind.FINDING_ATTACHED,
            at=at,
            actor_id=command.actor_id,
            finding_id=finding.id,
            subject=finding.subject_key,
            summary=f"{finding.relationship.value}/{finding.divergence.value}",
            detail={"anchor": f"{anchor.kind.value}={anchor.value}"},
        )
        self._append_event(
            incident,
            IncidentEventKind.CORRELATION_CONFIRMED,
            at=at,
            actor_id=command.actor_id,
            finding_id=finding.id,
            subject=anchor.value,
            summary=anchor.detail,
        )
        return self._recompute(incident, at=at, actor_id=command.actor_id), is_new, True

    def _recompute(
        self, incident: IntegrityIncident, *, at: float, actor_id: str
    ) -> IntegrityIncident:
        """Re-derive counts, severity, root cause and impact from the attached links.

        Severity goes through `SeverityService.aggregate`, which never returns something lower than
        the incident already carries — an incident cannot slip off a dashboard because one of its
        findings was repaired."""
        for _ in range(_CAS_ATTEMPTS):
            current = self._repos.incidents.get(incident.org_id, incident.id) or incident
            links = self._repos.links.for_incident(incident.org_id, incident.id)
            attached = [link for link in links if link.is_attached]
            findings = self._read_findings(incident.org_id, attached)
            cause = self._svc.root_cause.classify(
                tuple(link.divergence for link in attached)
            )
            updated = replace(
                current,
                finding_count=len(attached),
                unresolved_findings=self._unresolved(findings),
                severity=self._svc.severity.aggregate(current.severity, links),
                root_cause=cause,
                impact=self._svc.root_cause.impact_of(cause),
                relationships=tuple({link.relationship for link in attached}),
                owners=tuple({self._owner_of(findings.get(link.finding_id)) for link in attached}),
                last_observed_at=at,
                evidence_digest=derive_evidence_digest(
                    (current.correlation_key, *(link.finding_id for link in attached))
                ),
                version=current.version + 1,
            )
            try:
                return self._repos.incidents.advance(
                    updated, expected_version=current.version
                )
            except BillingError:
                continue  # another worker moved it; re-read and re-derive
        raise _Conflict(  # pragma: no cover — five rounds of contention on one incident
            f"integrity incident {incident.id!r} is under sustained contention; retry"
        )

    def _read_findings(
        self, org_id: str, links: Sequence[FindingLink]
    ) -> dict[str, IntegrityFinding]:
        repo = self.findings_repo()
        found: dict[str, IntegrityFinding] = {}
        for link in links:
            finding = repo.get(org_id, link.finding_id)
            if finding is not None:
                found[link.finding_id] = finding
        return found

    @staticmethod
    def _unresolved(findings: dict[str, IntegrityFinding]) -> int:
        from billing.foundation import UNRESOLVED_FINDING_STATES

        return len([f for f in findings.values() if f.state in UNRESOLVED_FINDING_STATES])

    @staticmethod
    def _owner_of(finding: IntegrityFinding | None) -> RepairOwner:
        return finding.owner if finding is not None else RepairOwner.OPERATOR

    def classify(self, command: ClassifyIncidentRootCause) -> IncidentView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_READ)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, command.incident_id)
            self._svc.guards.guard_transitionable(incident)
            updated = self._recompute(incident, at=at, actor_id=command.actor_id)
            self._append_event(
                updated,
                IncidentEventKind.ROOT_CAUSE_CLASSIFIED,
                at=at,
                actor_id=command.actor_id,
                subject=updated.root_cause.value,
                summary=f"impact {updated.impact.value}",
            )
            uow.stage(
                self._event(
                    command,
                    updated.id,
                    root_cause=updated.root_cause.value,
                    impact=updated.impact.value,
                )
            )
            return self._incident_view(updated)

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def acknowledge(self, command: AcknowledgeIncident) -> IncidentView:
        return self._transition(
            command,
            command.incident_id,
            IncidentTrigger.ACKNOWLEDGE,
            IncidentEventKind.ACKNOWLEDGED,
            note=command.note,
            permission=P.BILLING_READ,
        )

    def investigate(self, command: InvestigateIncident) -> IncidentView:
        return self._transition(
            command,
            command.incident_id,
            IncidentTrigger.INVESTIGATE,
            IncidentEventKind.INVESTIGATION_STARTED,
            note=command.note,
            permission=P.BILLING_MANAGE,
        )

    def close(self, command: CloseIncident) -> IncidentView:
        """Accept as permanent. **Elevated permission** — it ends the investigation."""
        return self._transition(
            command,
            command.incident_id,
            IncidentTrigger.CLOSE,
            IncidentEventKind.CLOSED,
            note=command.reason,
            permission=P.BILLING_REFUND_APPROVE,
        )

    def _transition(
        self,
        command,
        incident_id: str,
        trigger: IncidentTrigger,
        event_kind: IncidentEventKind,
        *,
        note: str,
        permission,
    ) -> IncidentView:
        self._authz.require(command.actor_id, command.org_id, permission)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, incident_id)
            self._svc.guards.guard_transitionable(incident)
            moved = self._svc.lifecycle.apply(
                incident, trigger, at=at, actor_id=command.actor_id, reason=note
            )
            stored = self._repos.incidents.advance(
                moved, expected_version=incident.version
            )
            self._factory.emit_status(stored, actor_id=command.actor_id)
            self._append_event(
                stored, event_kind, at=at, actor_id=command.actor_id, summary=note
            )
            uow.stage(self._event(command, stored.id, status=stored.status.value))
            return self._incident_view(stored)

    # ── repair ──────────────────────────────────────────────────────────────────

    def request_repair(self, command: RequestIncidentRepair) -> IncidentRepairView:
        """Ask the owning domain **through M9.19's own repair command**.

        M9.20 never touches a domain repository. Acceptance is recorded, but it is *not* evidence
        the incident is resolved — the FSM has no `REPAIR_IN_PROGRESS → RESOLVED` edge, and the only
        path runs through verification."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, command.incident_id)
            self._svc.guards.guard_transitionable(incident)
            finding_id = command.finding_id or self._first_unresolved(incident)
            finding = self.finding_or_404(command.org_id, finding_id) if finding_id else None
            owner = finding.owner if finding is not None else RepairOwner.OPERATOR
            reference, accepted, detail = self._dispatch_repair(
                command, finding, request_key=command.request_key or incident.id
            )
            built = self._factory.repair(
                incident,
                owner=owner,
                action=f"{owner.value}: repair {finding_id or 'the underlying fact'}",
                request_key=command.request_key or incident.id,
                finding_id=finding_id,
                reference=reference,
                at=at,
                actor_id=command.actor_id,
                detail=detail or command.note,
            )
            stored_repair, _ = self._repos.repairs.append(built)
            moved = incident
            if self._svc.lifecycle.can(incident.status, IncidentTrigger.REQUEST_REPAIR):
                moved = self._svc.lifecycle.apply(
                    incident,
                    IncidentTrigger.REQUEST_REPAIR,
                    at=at,
                    actor_id=command.actor_id,
                )
                moved = self._repos.incidents.advance(
                    moved, expected_version=incident.version
                )
            self._append_event(
                moved,
                IncidentEventKind.REPAIR_REQUESTED,
                at=at,
                actor_id=command.actor_id,
                finding_id=finding_id,
                subject=owner.value,
                summary=stored_repair.action,
                detail={"reference": reference, "accepted": str(accepted)},
            )
            uow.stage(
                self._event(
                    command, moved.id, owner=owner.value, accepted=str(accepted)
                )
            )
            return repair_view(stored_repair)

    def _dispatch_repair(
        self, command, finding: IntegrityFinding | None, *, request_key: str
    ) -> tuple[str, bool, str]:
        """Dispatch M9.19's `RequestFindingRepair`, or name the owner and stop.

        A `BillingError` from M9.19 is a **considered refusal** — the repair was attempted and
        declined — and is recorded as such. Anything else propagates, because a bug is not a
        refusal. That is the M9.16-R1 P-6 correction, applied a third time."""
        if finding is None or self._dispatch is None:
            return "", False, "no finding names an owning domain; an operator must act"
        try:
            receipt = self._dispatch(
                RequestFindingRepair(
                    org_id=command.org_id,
                    actor_id=command.actor_id,
                    finding_id=finding.id,
                    request_key=request_key,
                    note=command.note,
                )
            )
        except BillingError as refusal:
            return "", False, str(refusal)
        return getattr(receipt, "id", ""), bool(getattr(receipt, "accepted", False)), ""

    def _first_unresolved(self, incident: IntegrityIncident) -> str:
        from billing.foundation import UNRESOLVED_FINDING_STATES

        repo = self.findings_repo()
        for link in self._repos.links.for_incident(incident.org_id, incident.id):
            if not link.is_attached:
                continue
            finding = repo.get(incident.org_id, link.finding_id)
            if finding is not None and finding.state in UNRESOLVED_FINDING_STATES:
                return finding.id
        return ""

    def observe_repair(self, command: ObserveIncidentRepair) -> IncidentRepairView:
        """Record that the owner reported back. **Still not evidence of resolution.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, command.incident_id)
            repair = self._repos.repairs.get(command.org_id, command.repair_id)
            if repair is None:
                raise _NotFound(f"repair reference {command.repair_id!r} not found")
            stored = self._repos.repairs.observe(
                replace(repair, observed=True, observed_at=at)
            )
            self._append_event(
                incident,
                IncidentEventKind.REPAIR_OBSERVED,
                at=at,
                actor_id=command.actor_id,
                subject=stored.owner.value,
                summary=command.note or stored.action,
            )
            uow.stage(self._event(command, incident.id, repair_id=stored.id))
            return repair_view(stored)

    # ── verification and resolution ─────────────────────────────────────────────

    def verify(self, command: VerifyIncident) -> VerificationView:
        """**Re-read every attached finding.** Verification can legitimately fail.

        On failure the incident goes back to `INVESTIGATING` rather than sitting in a state that
        implies progress, and the failure is recorded — a verification that found the divergence
        still there is exactly the evidence an auditor needs."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, command.incident_id)
            self._svc.guards.guard_transitionable(incident)
            if self._svc.lifecycle.can(incident.status, IncidentTrigger.VERIFY):
                moved = self._svc.lifecycle.apply(
                    incident, IncidentTrigger.VERIFY, at=at, actor_id=command.actor_id
                )
                incident = self._repos.incidents.advance(
                    moved, expected_version=incident.version
                )
                self._append_event(
                    incident,
                    IncidentEventKind.VERIFICATION_STARTED,
                    at=at,
                    actor_id=command.actor_id,
                    summary=command.note,
                )
            links = self._repos.links.for_incident(command.org_id, incident.id)
            findings = self._read_findings(command.org_id, links)
            outcome = self._svc.resolution.verify(incident, links, findings)
            if outcome.is_clean:
                self._append_event(
                    incident,
                    IncidentEventKind.VERIFIED,
                    at=at,
                    actor_id=command.actor_id,
                    summary=f"{outcome.checked} finding(s) settled",
                )
            else:
                self._append_event(
                    incident,
                    IncidentEventKind.VERIFICATION_FAILED,
                    at=at,
                    actor_id=command.actor_id,
                    summary=(
                        f"{outcome.still_divergent} still divergent, "
                        f"{outcome.unreadable} unreadable"
                    ),
                    detail={"detail": " · ".join(outcome.detail[:5])},
                )
                if self._svc.lifecycle.can(
                    incident.status, IncidentTrigger.INVESTIGATE
                ):
                    back = self._svc.lifecycle.apply(
                        incident,
                        IncidentTrigger.INVESTIGATE,
                        at=at,
                        actor_id=command.actor_id,
                    )
                    self._repos.incidents.advance(
                        back, expected_version=incident.version
                    )
            uow.stage(
                self._event(
                    command,
                    incident.id,
                    checked=str(outcome.checked),
                    divergent=str(outcome.still_divergent),
                    clean=str(outcome.is_clean),
                )
            )
            return verification_view(outcome)

    def resolve(self, command: ResolveIncident) -> IncidentView:
        """**Evidence-based resolution.** The books are re-read and must actually agree.

        `guard_verified` refuses unless every attached finding is settled *and nothing was
        unreadable* — a finding that could not be re-read is not evidence of agreement, and partial
        resolution must never masquerade as complete. There is no `force` flag."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REFUND_APPROVE)
        at = self._clock.now()
        with self._uow.new() as uow:
            incident = self.incident_or_404(command.org_id, command.incident_id)
            self._svc.guards.guard_transitionable(incident)
            links = self._repos.links.for_incident(command.org_id, incident.id)
            findings = self._read_findings(command.org_id, links)
            outcome = self._svc.resolution.verify(incident, links, findings)
            self._svc.resolution.guard_verified(incident, outcome)
            moved = self._svc.lifecycle.apply(
                incident,
                IncidentTrigger.RESOLVE,
                at=at,
                actor_id=command.actor_id,
                reason=command.note,
            )
            stored = self._repos.incidents.advance(
                moved, expected_version=incident.version
            )
            self._factory.emit_status(stored, actor_id=command.actor_id)
            self._append_event(
                stored,
                IncidentEventKind.RESOLVED,
                at=at,
                actor_id=command.actor_id,
                summary=command.note or f"{outcome.checked} finding(s) verified settled",
                detail={"checked": str(outcome.checked)},
            )
            uow.stage(self._event(command, stored.id, checked=str(outcome.checked)))
            return self._incident_view(stored)

    # ── merge and correction ────────────────────────────────────────────────────

    def merge(self, command: MergeIncidents) -> IncidentView:
        """**A merge as a new fact.** Neither original is rewritten.

        The merged incident keeps its severity, its findings and its whole history and stays
        readable forever; it simply gains a `canonical_id` and a terminal status. A destructive
        merge would destroy the answer to "what did you believe in March?"."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            canonical = self.incident_or_404(command.org_id, command.canonical_id)
            merged = self.incident_or_404(command.org_id, command.merged_id)
            self._svc.guards.guard_mergeable(canonical, merged)
            decision = self._factory.merge(
                canonical, merged, reason=command.reason, at=at, actor_id=command.actor_id
            )
            stored_merge, _ = self._repos.merges.append(decision)
            moved = self._svc.lifecycle.apply(
                merged,
                IncidentTrigger.MERGE,
                at=at,
                actor_id=command.actor_id,
                reason=command.reason,
                canonical_id=canonical.id,
            )
            stored = self._repos.incidents.advance(moved, expected_version=merged.version)
            self._factory.emit_merged(stored_merge, actor_id=command.actor_id)
            self._append_event(
                stored,
                IncidentEventKind.INCIDENT_MERGED,
                at=at,
                actor_id=command.actor_id,
                subject=canonical.id,
                summary=command.reason,
                detail={"canonical_id": canonical.id, "evidence": stored_merge.evidence},
            )
            self._append_event(
                canonical,
                IncidentEventKind.INCIDENT_MERGED,
                at=at,
                actor_id=command.actor_id,
                subject=merged.id,
                summary=f"absorbed {merged.id}",
                detail={"merged_id": merged.id},
            )
            uow.stage(
                self._event(command, stored.id, canonical_id=canonical.id)
            )
            return self._incident_view(stored)

    def correct(self, command: CorrectCorrelation) -> IncidentView:
        """**A correction as a new fact.** The previous correlation stays in the trail.

        The old link is *detached*, never deleted, and a `CorrelationCorrection` row records the
        previous key, the corrected key and the reason. An auditor reading it later sees what was
        believed, when, and on what grounds it changed."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            previous = self.incident_or_404(command.org_id, command.incident_id)
            finding = self.finding_or_404(command.org_id, command.finding_id)
            link = self._repos.links.get(
                command.org_id, self._link_id(previous.id, finding.id)
            )
            if link is None or not link.is_attached:
                raise _Conflict(
                    f"finding {finding.id!r} is not attached to incident {previous.id!r}"
                )
            # **Check before detaching.** Re-derive the correlation from current evidence first: if
            # it still lands on this incident the correction is a no-op, and detaching first would
            # leave the finding attached to nothing — a correction that concluded "actually it was
            # right" would silently orphan it. Found by the correlation smoke test.
            evidence = self._evidence.evidence_for(finding)
            _anchor, corrected_key = self._svc.correlation.key_for(evidence)
            if corrected_key == previous.correlation_key:
                raise _Conflict(
                    f"the evidence still correlates finding {finding.id!r} onto "
                    f"{previous.correlation_key!r}; there is nothing to correct. Correct the "
                    "underlying evidence, or merge this incident into the right one."
                )
            self._repos.links.detach(
                replace(link, detached_at=at, detached_reason=command.reason)
            )
            self._append_event(
                previous,
                IncidentEventKind.FINDING_DETACHED,
                at=at,
                actor_id=command.actor_id,
                finding_id=finding.id,
                summary=command.reason,
            )
            self._append_event(
                previous,
                IncidentEventKind.CORRELATION_REJECTED,
                at=at,
                actor_id=command.actor_id,
                finding_id=finding.id,
                subject=previous.correlation_key,
                summary=command.reason,
            )
            # Re-correlate from current evidence — which may open a different incident.
            corrected, _new, _attached = self._correlate_one(command, finding, at=at)
            correction = self._factory.correction(
                org_id=command.org_id,
                finding_id=finding.id,
                previous_incident_id=previous.id,
                previous_key=previous.correlation_key,
                corrected_incident_id=corrected.id,
                corrected_key=corrected.correlation_key,
                reason=command.reason,
                evidence=f"{corrected.anchor_kind.value}={corrected.anchor_value}",
                at=at,
                actor_id=command.actor_id,
            )
            stored_correction, _ = self._repos.corrections.append(correction)
            self._factory.emit_corrected(stored_correction, actor_id=command.actor_id)
            self._append_event(
                corrected,
                IncidentEventKind.CORRELATION_CORRECTED,
                at=at,
                actor_id=command.actor_id,
                finding_id=finding.id,
                subject=previous.id,
                summary=command.reason,
                detail={
                    "previous_key": previous.correlation_key,
                    "corrected_key": corrected.correlation_key,
                },
            )
            self._recompute(previous, at=at, actor_id=command.actor_id)
            uow.stage(
                self._event(
                    command,
                    corrected.id,
                    previous_incident_id=previous.id,
                    finding_id=finding.id,
                )
            )
            return self._incident_view(
                self.incident_or_404(command.org_id, corrected.id)
            )

    @staticmethod
    def _link_id(incident_id: str, finding_id: str) -> str:
        from billing.foundation import derive_link_id

        return derive_link_id(incident_id, finding_id)

    # ── internals ───────────────────────────────────────────────────────────────

    def _append_event(
        self,
        incident: IntegrityIncident,
        kind: IncidentEventKind,
        *,
        at: float,
        actor_id: str = "",
        finding_id: str = "",
        subject: str = "",
        summary: str = "",
        detail: dict[str, str] | None = None,
    ) -> None:
        sequence = self._repos.events.next_sequence(incident.org_id, incident.id)
        self._repos.events.append(
            self._factory.event(
                incident,
                kind,
                sequence=sequence,
                at=at,
                actor_id=actor_id,
                finding_id=finding_id,
                subject=subject,
                summary=summary,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data: str) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.INCIDENT_CORRELATED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class IncidentQueries(_IncidentSupport):
    """Reads. Every one org-scoped, bounded, index-backed and — for collections — paginated."""

    def __init__(self, *, context, repos, services, authz: AuthorizationOrchestrator) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    def get(self, query: GetIncident) -> IncidentView:
        self._read(query)
        return self._incident_view(self.incident_or_404(query.org_id, query.incident_id))

    def list_incidents(self, query: ListIncidents) -> IncidentPageView:
        """**Paginated at the database.** A Python slice over every incident an org ever had is
        not a performance detail at this milestone's assumed scale — it is an outage."""
        self._read(query)
        page, total = self._repos.incidents.page(
            query.org_id,
            statuses=query.statuses,
            severities=query.severities,
            account_id=query.account_id,
            limit=query.limit,
            offset=query.offset,
        )
        return IncidentPageView(
            incidents=tuple(self._incident_view(i) for i in page),
            total=total,
            limit=query.limit,
            offset=query.offset,
            has_more=query.offset + len(page) < total,
        )

    def live(self, query: LiveIncidents) -> tuple[IncidentView, ...]:
        self._read(query)
        return tuple(
            self._incident_view(i)
            for i in self._repos.incidents.live(query.org_id, query.limit)
        )

    def timeline(self, query: GetIncidentTimeline) -> tuple[IncidentEventView, ...]:
        """**The persisted audit trail — not an application log.**"""
        self._read(query)
        found = self._repos.events.for_incident(
            query.org_id, query.incident_id, query.limit
        )
        return tuple(incident_event_view(e) for e in found)

    def findings(self, query: GetIncidentFindings) -> tuple[FindingLinkView, ...]:
        self._read(query)
        found = self._repos.links.for_incident(query.org_id, query.incident_id)
        return tuple(link_view(link) for link in found)

    def evidence(self, query: GetIncidentEvidence) -> EvidenceGraphView:
        self._read(query)
        incident = self.incident_or_404(query.org_id, query.incident_id)
        links = self._repos.links.for_incident(query.org_id, incident.id)
        repairs = self._repos.repairs.for_incident(query.org_id, incident.id)
        return graph_view(self._svc.graph.build(incident, links, repairs))

    def explain(self, query: ExplainIncident) -> IncidentExplanationView:
        """*What happened?* — assembled from stored evidence, with **no domain re-read**."""
        self._read(query)
        incident = self.incident_or_404(query.org_id, query.incident_id)
        links = self._repos.links.for_incident(query.org_id, incident.id)
        events = self._repos.events.for_incident(query.org_id, incident.id)
        return incident_explanation_view(self._svc.explainer.explain(incident, links, events))

    def inspect(self, query: InspectIncident) -> IncidentInspectionView:
        self._read(query)
        incident = self.incident_or_404(query.org_id, query.incident_id)
        links = self._repos.links.for_incident(query.org_id, incident.id)
        events = self._repos.events.for_incident(query.org_id, incident.id)
        graph = self._svc.graph.build(incident, links)
        result = self._svc.inspector.inspect(incident, links, events, graph)
        return IncidentInspectionView(
            incident_id=result.incident_id,
            status=result.status.value,
            severity=result.severity.value,
            finding_count=result.finding_count,
            links_recorded=result.links_recorded,
            events_recorded=result.events_recorded,
            is_counted_correctly=result.is_counted_correctly,
            is_scoped=result.is_scoped,
            is_connected=result.is_connected,
            is_severity_monotonic=result.is_severity_monotonic,
            unresolved_findings=result.unresolved_findings,
            notes=result.notes,
        )

    def repairs(self, query: GetIncidentRepairs) -> tuple[IncidentRepairView, ...]:
        self._read(query)
        found = self._repos.repairs.for_incident(query.org_id, query.incident_id)
        return tuple(repair_view(r) for r in found)

    def merges(self, query: GetIncidentMerges) -> tuple[IncidentMergeView, ...]:
        self._read(query)
        found = list(self._repos.merges.for_canonical(query.org_id, query.incident_id))
        merged = self._repos.merges.for_merged(query.org_id, query.incident_id)
        if merged is not None:
            found.append(merged)
        return tuple(merge_view(m) for m in found)

    def corrections(
        self, query: GetCorrelationCorrections
    ) -> tuple[CorrelationCorrectionView, ...]:
        self._read(query)
        found = (
            self._repos.corrections.for_finding(query.org_id, query.finding_id)
            if query.finding_id
            else self._repos.corrections.for_incident(query.org_id, query.incident_id)
        )
        return tuple(correction_view(c) for c in found)

    def finding_incidents(self, query: GetFindingIncidents) -> tuple[FindingLinkView, ...]:
        """Every incident a finding was ever attached to, **including detached ones** — which is
        exactly what a correlation correction leaves behind for an auditor to read."""
        self._read(query)
        found = self._repos.links.for_finding(query.org_id, query.finding_id)
        return tuple(link_view(link) for link in found)

    def search(self, query: SearchIncidents) -> IncidentPageView:
        self._read(query)
        page, total = self._repos.incidents.page(
            query.org_id,
            statuses=query.statuses,
            severities=query.severities,
            account_id=query.account_id,
            limit=max(query.limit * 4, 100),
            offset=0,
        )
        result = self._svc.search.search(
            IncidentSearchQuery(
                org_id=query.org_id,
                statuses=tuple(
                    IncidentStatus(s) for s in query.statuses if _is(IncidentStatus, s)
                ),
                severities=tuple(
                    IncidentSeverity(s) for s in query.severities if _is(IncidentSeverity, s)
                ),
                root_causes=tuple(
                    RootCause(c) for c in query.root_causes if _is(RootCause, c)
                ),
                impacts=tuple(ImpactClass(i) for i in query.impacts if _is(ImpactClass, i)),
                owners=tuple(RepairOwner(o) for o in query.owners if _is(RepairOwner, o)),
                account_id=query.account_id,
                correlation_key=query.correlation_key,
                anchor_value=query.anchor_value,
                limit=query.limit,
                offset=query.offset,
            ),
            page,
            total=total,
        )
        return IncidentPageView(
            incidents=tuple(self._incident_view(i) for i in result.incidents),
            total=result.total,
            limit=result.limit,
            offset=result.offset,
            has_more=result.has_more,
        )

    def posture(self, query: GetIncidentPosture) -> IncidentPostureView:
        """**Counts, computed from the facts on every call.** There is no stored posture."""
        self._read(query)
        page, _total = self._repos.incidents.page(query.org_id, limit=query.limit, offset=0)
        result = self._svc.posture.posture(query.org_id, page)
        return IncidentPostureView(
            org_id=result.org_id,
            incidents=result.incidents,
            open_incidents=result.open_incidents,
            acknowledged=result.acknowledged,
            investigating=result.investigating,
            repair_in_progress=result.repair_in_progress,
            verifying=result.verifying,
            resolved=result.resolved,
            closed=result.closed,
            merged=result.merged,
            critical=result.critical,
            error=result.error,
            warning=result.warning,
            is_clean=result.is_clean,
            by_root_cause=dict(result.by_root_cause),
            by_impact=dict(result.by_impact),
            by_owner=dict(result.by_owner),
        )


def _is(enum_type, value: str) -> bool:
    return value in {m.value for m in enum_type}


def register_incident_use_cases(
    command_bus, query_bus, uc: IncidentUseCases, q: IncidentQueries
):
    """Wire the correlation vocabulary onto the shared buses. One handler per command."""
    command_bus.register(CorrelateFindings, uc.correlate)
    command_bus.register(AcknowledgeIncident, uc.acknowledge)
    command_bus.register(InvestigateIncident, uc.investigate)
    command_bus.register(RequestIncidentRepair, uc.request_repair)
    command_bus.register(ObserveIncidentRepair, uc.observe_repair)
    command_bus.register(VerifyIncident, uc.verify)
    command_bus.register(ResolveIncident, uc.resolve)
    command_bus.register(CloseIncident, uc.close)
    command_bus.register(MergeIncidents, uc.merge)
    command_bus.register(CorrectCorrelation, uc.correct)
    command_bus.register(ClassifyIncidentRootCause, uc.classify)
    query_bus.register(GetIncident, q.get)
    query_bus.register(ListIncidents, q.list_incidents)
    query_bus.register(LiveIncidents, q.live)
    query_bus.register(GetIncidentTimeline, q.timeline)
    query_bus.register(GetIncidentFindings, q.findings)
    query_bus.register(GetIncidentEvidence, q.evidence)
    query_bus.register(ExplainIncident, q.explain)
    query_bus.register(InspectIncident, q.inspect)
    query_bus.register(GetIncidentRepairs, q.repairs)
    query_bus.register(GetIncidentMerges, q.merges)
    query_bus.register(GetCorrelationCorrections, q.corrections)
    query_bus.register(GetFindingIncidents, q.finding_incidents)
    query_bus.register(SearchIncidents, q.search)
    query_bus.register(GetIncidentPosture, q.posture)


__all__ = [
    "ALL_INCIDENT_COMMANDS",
    "ALL_INCIDENT_QUERIES",
    "AcknowledgeIncident",
    "ClassifyIncidentRootCause",
    "CloseIncident",
    "CorrectCorrelation",
    "CorrelateFindings",
    "CorrelationCorrectionView",
    "CorrelationReceiptView",
    "EvidenceEdgeView",
    "EvidenceGraphView",
    "EvidenceNodeView",
    "ExplainIncident",
    "FindingLinkView",
    "GetCorrelationCorrections",
    "GetFindingIncidents",
    "GetIncident",
    "GetIncidentEvidence",
    "GetIncidentFindings",
    "GetIncidentMerges",
    "GetIncidentPosture",
    "GetIncidentRepairs",
    "GetIncidentTimeline",
    "IncidentEventView",
    "IncidentExplanationView",
    "IncidentInspectionView",
    "IncidentMergeView",
    "IncidentPageView",
    "IncidentPostureView",
    "IncidentQueries",
    "IncidentRepairView",
    "IncidentRepositories",
    "IncidentServices",
    "IncidentUseCases",
    "IncidentView",
    "InspectIncident",
    "InvestigateIncident",
    "ListIncidents",
    "LiveIncidents",
    "MergeIncidents",
    "ObserveIncidentRepair",
    "RequestIncidentRepair",
    "ResolveIncident",
    "SearchIncidents",
    "VerificationView",
    "VerifyIncident",
    "correction_view",
    "incident_event_view",
    "incident_explanation_view",
    "graph_view",
    "incident_view",
    "link_view",
    "merge_view",
    "register_incident_use_cases",
    "repair_view",
    "verification_view",
]
