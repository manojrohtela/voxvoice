"""Investigation factories + repository contracts (M9.21).

`InvestigationFactory` is the one place an investigation, an evidence capture, a question, an
observation, a conclusion, a timeline entry or an audit package is constructed. Same discipline as
every milestone since M9.2: validate structurally, fail fast on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and clock
seams, emit the corresponding `billing.*` fact through the injected emitter. No I/O, no persistence,
no transport, no provider, no scheduler.

## Exactly one event per business fact, and what is deliberately absent

Four `billing.*` facts are published — opened, evidence-unavailable, concluded, closed, plus the
audit package. The other fifteen timeline kinds are **not** on the bus, for the reason M9.20 gave:
the `billing.*` stream is the platform's business-fact feed, not a mirror of an audit trail. An
external consumer needs to know an investigation opened, got stuck on missing evidence, concluded
and closed. It does not need one message per investigator keystroke. The full sequence lives in
`cp_billing_investigation_event`, which is where an examiner reads it.

There is no event for a rejected command, no event for a repair (the owning domain publishes its
own), no event for a reconciliation and none for a correlation — republishing another milestone's
fact is how one occurrence comes to be counted twice.

## Why every identity is derived

* an **investigation** from `(org, incident)` — so a second open lands on the existing case rather
  than starting a rival one that could reach a different conclusion about the same incident;
* an **evidence capture** from its own content — content-addressing, so capturing an unchanged
  immutable source twice writes once, and a changed representation is a new link rather than an
  overwrite;
* an **observation** from `(investigation, kind, statement)` — including the **kind**, which is what
  stops a hypothesis being re-submitted as a fact and landing on the same row;
* a **conclusion** from `(investigation, classification, statement)`;
* a **timeline entry** from `(investigation, kind, subject, sequence)` — so a replayed command
  appends no second line;
* an **audit package** from its canonical manifest digest — so exporting an unchanged investigation
  twice yields the same package and any change yields a new one beside it.

## What no repository here can do

There is **no update method anywhere on the evidence store, the timeline or the package store**, and
no delete on anything. History is evidence, and evidence is not edited: an evidence source becoming
unavailable is a new capture appended to the chain, not a column changed on the old one.

`InvestigationRepository.advance` takes the version the caller read and is a compare-and-set, so an
investigator working from a stale view fails loudly instead of overwriting a conclusion somebody
else recorded.

And there is **no repository here for an integrity incident, a reconciliation finding, a ledger
entry, an invoice, a tax assessment, a payment or a recognition schedule.** M9.21 reads those
through the owning domain's own seams, supplied by the Application, and writes to none of them —
including M9.20's, whose incidents it references and never mutates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_incidents import IntegrityIncident, RootCause
from .commercial_investigations import (
    AuditPackage,
    CaptureMode,
    ConclusionClassification,
    EvidenceAvailability,
    EvidenceCapture,
    EvidenceGap,
    EvidenceReference,
    HypothesisStatus,
    Investigation,
    InvestigationConclusion,
    InvestigationEvent,
    InvestigationEventKind,
    InvestigationObservation,
    InvestigationPriority,
    InvestigationQuestion,
    InvestigationSearchQuery,
    InvestigationStatus,
    ObservationKind,
    PackageCompleteness,
    QuestionKind,
    canonical_digest,
    derive_audit_package_id,
    derive_conclusion_id,
    derive_evidence_id,
    derive_investigation_event_id,
    derive_investigation_id,
    derive_observation_id,
    derive_question_id,
)
from .diagnostics import BillingDiagnostics, Severity
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .investigation_rules import (
    EvidenceGuardService,
    InvestigationValidator,
)
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

#: Which `billing.*` fact each timeline kind publishes. A table, so "exactly one event per business
#: fact" is checkable by iteration rather than asserted in prose. Most kinds are absent on purpose —
#: see the module docstring.
INVESTIGATION_EVENTS: dict[InvestigationEventKind, BillingEventType] = {
    InvestigationEventKind.INVESTIGATION_OPENED: BillingEventType.INVESTIGATION_OPENED,
    InvestigationEventKind.EVIDENCE_MARKED_UNAVAILABLE: (
        BillingEventType.INVESTIGATION_EVIDENCE_UNAVAILABLE
    ),
    InvestigationEventKind.CONCLUSION_RECORDED: BillingEventType.INVESTIGATION_CONCLUDED,
    InvestigationEventKind.AUDIT_PACKAGE_CREATED: BillingEventType.AUDIT_PACKAGE_CREATED,
    InvestigationEventKind.INVESTIGATION_CLOSED: BillingEventType.INVESTIGATION_CLOSED,
}


@dataclass(frozen=True, slots=True)
class InvestigationFactory:
    """Constructs every investigation aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: InvestigationValidator = InvestigationValidator()
    guards: EvidenceGuardService = EvidenceGuardService()

    # ── the investigation ───────────────────────────────────────────────────────

    def investigation(
        self,
        incident: IntegrityIncident,
        *,
        org_id: str,
        summary: str = "",
        priority: InvestigationPriority = InvestigationPriority.NORMAL,
        opened_by: str = "",
        at: float = 0.0,
    ) -> Investigation:
        """Open a case against an incident **that already exists**.

        `incident_severity` is copied once, here, for triage display. It is never recomputed and
        never written back: M9.20 owns severity, and a second mutable copy is how two systems
        come to disagree about how bad something was."""
        moment = at or self.clock.now()
        built = Investigation(
            id=derive_investigation_id(org_id, incident.id),
            org_id=org_id,
            incident_id=incident.id,
            status=InvestigationStatus.OPEN,
            priority=priority,
            incident_severity=incident.severity,
            summary=summary,
            opened_at=moment,
            opened_by=opened_by,
            last_activity_at=moment,
        )
        self._fail_fast(self.validator.validate_investigation(built))
        return built

    def emit_opened(self, investigation: Investigation) -> None:
        self._emit(
            BillingEventType.INVESTIGATION_OPENED,
            investigation.org_id,
            investigation.id,
            incident_id=investigation.incident_id,
            priority=investigation.priority.value,
            opened_by=investigation.opened_by,
        )

    def emit_concluded(self, conclusion: InvestigationConclusion) -> None:
        self._emit(
            BillingEventType.INVESTIGATION_CONCLUDED,
            conclusion.org_id,
            conclusion.investigation_id,
            incident_id=conclusion.incident_id,
            conclusion_id=conclusion.id,
            classification=conclusion.classification.value,
        )

    def emit_closed(self, investigation: Investigation) -> None:
        self._emit(
            BillingEventType.INVESTIGATION_CLOSED,
            investigation.org_id,
            investigation.id,
            incident_id=investigation.incident_id,
            conclusion_id=investigation.conclusion_id,
            reason=investigation.closed_reason,
        )

    def emit_evidence_unavailable(self, capture: EvidenceCapture) -> None:
        self._emit(
            BillingEventType.INVESTIGATION_EVIDENCE_UNAVAILABLE,
            capture.org_id,
            capture.investigation_id,
            evidence_id=capture.id,
            locator=capture.locator,
            reason=capture.unavailable_reason,
        )

    def emit_package(self, package: AuditPackage) -> None:
        self._emit(
            BillingEventType.AUDIT_PACKAGE_CREATED,
            package.org_id,
            package.investigation_id,
            package_id=package.id,
            manifest_digest=package.manifest_digest,
            completeness=package.completeness.value,
        )

    # ── evidence ────────────────────────────────────────────────────────────────

    def snapshot(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        body: dict[str, object],
        *,
        captured_by: str = "",
        at: float = 0.0,
        previous: EvidenceCapture | None = None,
        sequence: int = 0,
    ) -> EvidenceCapture:
        """Capture what a source said **at this moment**, content-addressed.

        The digest is computed here from the canonical form and never accepted from the caller —
        a supplied digest would let a body and its attestation be written apart."""
        digest = canonical_digest(body)
        self.guards.guard_mode_matches_content(CaptureMode.EVIDENCE_SNAPSHOT, body, digest)
        self.guards.guard_chain_continues(previous, reference)
        return self._capture(
            investigation,
            reference,
            mode=CaptureMode.EVIDENCE_SNAPSHOT,
            availability=EvidenceAvailability.AVAILABLE,
            body=body,
            content_digest=digest,
            captured_by=captured_by,
            at=at,
            previous=previous,
            sequence=sequence,
        )

    def live_reference(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        *,
        captured_by: str = "",
        at: float = 0.0,
        sequence: int = 0,
    ) -> EvidenceCapture:
        """Record *where* the authoritative object lives, asserting nothing about its content.

        Structurally distinct from a snapshot: no body, no digest, and — because
        `supports_conclusion` is `False` for every live reference — it can never back a confirmed
        conclusion. A pointer at an object that may have changed since is not evidence of what was
        true when the investigation ran."""
        self.guards.guard_mode_matches_content(CaptureMode.LIVE_REFERENCE, None, "")
        return self._capture(
            investigation,
            reference,
            mode=CaptureMode.LIVE_REFERENCE,
            availability=EvidenceAvailability.AVAILABLE,
            body=None,
            content_digest="",
            captured_by=captured_by,
            at=at,
            previous=None,
            sequence=sequence,
        )

    def unavailable(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        *,
        reason: str,
        captured_by: str = "",
        at: float = 0.0,
        previous: EvidenceCapture | None = None,
        sequence: int = 0,
    ) -> EvidenceCapture:
        """Record that a source could not be retrieved. **A new link, never an edit.**

        The previous capture stays exactly as it was, so the chain reads *we held this, then we
        could not*. Replacing the old row would turn a lost source into one that never existed."""
        return self._capture(
            investigation,
            reference,
            mode=CaptureMode.LIVE_REFERENCE,
            availability=EvidenceAvailability.UNAVAILABLE,
            body=None,
            content_digest="",
            captured_by=captured_by,
            at=at,
            previous=previous,
            sequence=sequence,
            unavailable_reason=reason,
            salt="unavailable",
        )

    def redaction(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        replacement: dict[str, object],
        *,
        reason: str,
        captured_by: str = "",
        at: float = 0.0,
        previous: EvidenceCapture,
        sequence: int = 0,
    ) -> EvidenceCapture:
        """Supersede a representation with a narrower safe one, **keeping the original identity**.

        `previous_evidence_id` names what was redacted, so the original capture's identity, digest,
        author and timestamp all survive in the chain. The redaction carries the replacement
        representation and the reason.

        Note what redaction is *not* used for here: because §30 forbids a card number, a credential,
        a token or a provider secret from ever entering an evidence body, there is nothing secret to
        remove after the fact. This exists for over-broad detail, and for a source the owning domain
        later narrows."""
        digest = canonical_digest(replacement)
        self.guards.guard_chain_continues(previous, reference)
        return self._capture(
            investigation,
            reference,
            mode=CaptureMode.EVIDENCE_SNAPSHOT,
            availability=EvidenceAvailability.REDACTED,
            body=replacement,
            content_digest=digest,
            captured_by=captured_by,
            at=at,
            previous=previous,
            sequence=sequence,
            redaction_reason=reason,
        )

    def _capture(
        self,
        investigation: Investigation,
        reference: EvidenceReference,
        *,
        mode: CaptureMode,
        availability: EvidenceAvailability,
        body: dict[str, object] | None,
        content_digest: str,
        captured_by: str,
        at: float,
        previous: EvidenceCapture | None,
        sequence: int,
        unavailable_reason: str = "",
        redaction_reason: str = "",
        salt: str = "",
    ) -> EvidenceCapture:
        moment = at or self.clock.now()
        # `availability` and `salt` participate in the identity so that an unavailability record
        # about a source we once snapshotted is a *distinct* row rather than a collision with the
        # snapshot it follows. Content-addressing still holds within a mode: two identical snapshots
        # of one unchanged source converge.
        built = EvidenceCapture(
            id=derive_evidence_id(
                investigation.org_id,
                reference.domain.value,
                reference.source_type.value,
                reference.source_id,
                reference.revision,
                content_digest or f"{availability.value}:{salt}:{mode.value}",
            ),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            reference=reference,
            mode=mode,
            availability=availability,
            body=body,
            content_digest=content_digest,
            captured_at=moment,
            captured_by=captured_by,
            previous_evidence_id=previous.id if previous is not None else "",
            unavailable_reason=unavailable_reason,
            redaction_reason=redaction_reason,
            sequence=sequence,
        )
        self._fail_fast(self.validator.validate_capture(built))
        return built

    # ── questions ───────────────────────────────────────────────────────────────

    def question(
        self,
        investigation: Investigation,
        *,
        kind: QuestionKind,
        text: str,
        required: bool = False,
        asked_by: str = "",
        at: float = 0.0,
        subject: str = "",
    ) -> InvestigationQuestion:
        built = InvestigationQuestion(
            id=derive_question_id(investigation.id, kind.value, subject or text),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            kind=kind,
            text=text,
            required=required,
            asked_at=at or self.clock.now(),
            asked_by=asked_by,
        )
        self._fail_fast(self.validator.validate_question(built))
        return built

    # ── observations ────────────────────────────────────────────────────────────

    def observation(
        self,
        investigation: Investigation,
        *,
        kind: ObservationKind,
        statement: str,
        evidence_ids: tuple[str, ...] = (),
        recorded_by: str = "",
        at: float = 0.0,
        sequence: int = 0,
    ) -> InvestigationObservation:
        """A fact or a hypothesis — **separately identified**, so one cannot become the other."""
        digest = canonical_digest(statement)
        built = InvestigationObservation(
            id=derive_observation_id(investigation.id, kind.value, digest),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            kind=kind,
            statement=statement,
            statement_digest=digest,
            evidence_ids=evidence_ids,
            hypothesis_status=(
                HypothesisStatus.PROPOSED
                if kind is ObservationKind.HYPOTHESIS
                else HypothesisStatus.NOT_APPLICABLE
            ),
            recorded_at=at or self.clock.now(),
            recorded_by=recorded_by,
            sequence=sequence,
        )
        self._fail_fast(self.validator.validate_observation(built))
        return built

    # ── conclusions ─────────────────────────────────────────────────────────────

    def conclusion(
        self,
        investigation: Investigation,
        *,
        classification: ConclusionClassification,
        statement: str,
        evidence_ids: tuple[str, ...] = (),
        incident_root_cause: RootCause = RootCause.UNKNOWN,
        evidenced_root_cause: RootCause | None = None,
        supersedes: str = "",
        previous_version: int = 0,
        recorded_by: str = "",
        at: float = 0.0,
    ) -> InvestigationConclusion:
        digest = canonical_digest(statement)
        built = InvestigationConclusion(
            id=derive_conclusion_id(investigation.id, classification.value, digest),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            incident_id=investigation.incident_id,
            classification=classification,
            statement=statement,
            statement_digest=digest,
            evidence_ids=evidence_ids,
            incident_root_cause=incident_root_cause,
            evidenced_root_cause=evidenced_root_cause,
            supersedes=supersedes,
            recorded_at=at or self.clock.now(),
            recorded_by=recorded_by,
            version=previous_version + 1,
        )
        self._fail_fast(self.validator.validate_conclusion(built))
        return built

    # ── timeline ────────────────────────────────────────────────────────────────

    def event(
        self,
        investigation: Investigation,
        *,
        kind: InvestigationEventKind,
        sequence: int,
        at: float = 0.0,
        actor_id: str = "",
        subject: str = "",
        summary: str = "",
        evidence_ids: tuple[str, ...] = (),
        detail: dict[str, str] | None = None,
    ) -> InvestigationEvent:
        return InvestigationEvent(
            id=derive_investigation_event_id(investigation.id, kind.value, subject, sequence),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            sequence=sequence,
            kind=kind,
            at=at or self.clock.now(),
            actor_id=actor_id,
            subject=subject,
            summary=summary,
            evidence_ids=evidence_ids,
            detail=dict(detail or {}),
        )

    # ── the audit package ───────────────────────────────────────────────────────

    def package(
        self,
        investigation: Investigation,
        *,
        manifest: dict[str, object],
        completeness: PackageCompleteness,
        gaps: tuple[EvidenceGap, ...],
        evidence_count: int,
        observation_count: int,
        question_count: int,
        conclusion_count: int,
        timeline_length: int,
        created_by: str = "",
        at: float = 0.0,
    ) -> AuditPackage:
        """**Content-addressed by its manifest.** Same investigation state in, same package out."""
        digest = canonical_digest(manifest)
        return AuditPackage(
            id=derive_audit_package_id(investigation.org_id, investigation.id, digest),
            org_id=investigation.org_id,
            investigation_id=investigation.id,
            incident_id=investigation.incident_id,
            manifest_digest=digest,
            completeness=completeness,
            manifest=manifest,
            gaps=gaps,
            evidence_count=evidence_count,
            observation_count=observation_count,
            question_count=question_count,
            conclusion_count=conclusion_count,
            timeline_length=timeline_length,
            created_at=at or self.clock.now(),
            created_by=created_by,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics: tuple) -> None:
        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        if errors:
            raise BillingValidationError(BillingDiagnostics(tuple(errors)))

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject: str, **data: str
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject,
                data={k: v for k, v in data.items() if v not in ("", None)},
            )
        )


# ── repository contracts ────────────────────────────────────────────────────────


class InvestigationRepository(ABC):
    """The one mutable aggregate. `advance` is a compare-and-set; there is no blind replace."""

    @abstractmethod
    def create(self, investigation: Investigation) -> tuple[Investigation, bool]:
        """Insert, or return the existing case. `(investigation, created)`.

        Idempotent by derived identity: two investigators opening a case on one incident get one
        investigation, and the loser of the insert race reads the winner rather than raising."""

    @abstractmethod
    def advance(self, investigation: Investigation, *, expected_version: int) -> Investigation:
        """Compare-and-set on `version`. Raises `InvestigationStale` when the row moved."""

    @abstractmethod
    def get(self, org_id: str, investigation_id: str) -> Investigation | None: ...

    @abstractmethod
    def by_incident(self, org_id: str, incident_id: str) -> Investigation | None: ...

    @abstractmethod
    def page(self, query: InvestigationSearchQuery) -> tuple[tuple[Investigation, ...], int]:
        """Database-side pagination: a `COUNT` and a `LIMIT/OFFSET` against an index.

        Never "load them all and slice in Python" — at organizations × incidents × investigations
        that is not a performance note, it is an outage."""

    @abstractmethod
    def counts(self, org_id: str) -> dict[str, dict[str, int]]:
        """Grouped counts for the operator posture, computed by the database."""


class EvidenceRepository(ABC):
    """**Append-only. There is no update method and no delete method, by design.**

    A source becoming unavailable, redacted or superseded appends a new link naming the one it
    follows. Nothing is ever replaced, so "what did we hold first, and what changed" survives
    without depending on a backup."""

    @abstractmethod
    def append(self, capture: EvidenceCapture) -> tuple[EvidenceCapture, bool]:
        """`(capture, created)`. The same immutable source captured twice writes once."""

    @abstractmethod
    def get(self, org_id: str, evidence_id: str) -> EvidenceCapture | None: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[EvidenceCapture, ...], int]: ...

    @abstractmethod
    def all_for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[EvidenceCapture, ...]:
        """Every capture, for manifest construction. Bounded by one investigation, never by org."""

    @abstractmethod
    def latest_for_source(
        self, org_id: str, investigation_id: str, locator_prefix: str
    ) -> EvidenceCapture | None:
        """The current head of one source's custody chain."""

    @abstractmethod
    def next_sequence(self, org_id: str, investigation_id: str) -> int: ...


class InvestigationQuestionRepository(ABC):
    @abstractmethod
    def append(self, question: InvestigationQuestion) -> tuple[InvestigationQuestion, bool]: ...

    @abstractmethod
    def resolve(
        self, question: InvestigationQuestion, *, expected_version: int
    ) -> InvestigationQuestion:
        """Compare-and-set. A question's *outcome* is written once; the row is never deleted."""

    @abstractmethod
    def get(self, org_id: str, question_id: str) -> InvestigationQuestion | None: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationQuestion, ...]: ...


class InvestigationObservationRepository(ABC):
    @abstractmethod
    def append(
        self, observation: InvestigationObservation
    ) -> tuple[InvestigationObservation, bool]: ...

    @abstractmethod
    def settle(
        self, observation: InvestigationObservation, *, expected_version: int
    ) -> InvestigationObservation:
        """Compare-and-set on the hypothesis verdict. The statement itself is never rewritten."""

    @abstractmethod
    def get(self, org_id: str, observation_id: str) -> InvestigationObservation | None: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationObservation, ...]: ...

    @abstractmethod
    def next_sequence(self, org_id: str, investigation_id: str) -> int: ...


class InvestigationConclusionRepository(ABC):
    """Append-only: a conclusion is never edited, only superseded by a later one."""

    @abstractmethod
    def append(
        self, conclusion: InvestigationConclusion
    ) -> tuple[InvestigationConclusion, bool]: ...

    @abstractmethod
    def get(self, org_id: str, conclusion_id: str) -> InvestigationConclusion | None: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationConclusion, ...]: ...


class InvestigationEventRepository(ABC):
    """Append-only, totally ordered per investigation. No update, no delete."""

    @abstractmethod
    def append(self, event: InvestigationEvent) -> tuple[InvestigationEvent, bool]: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[InvestigationEvent, ...], int]: ...

    @abstractmethod
    def all_for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationEvent, ...]: ...

    @abstractmethod
    def next_sequence(self, org_id: str, investigation_id: str) -> int: ...


class AuditPackageRepository(ABC):
    """**Immutable. Append-only, no update, no delete.**

    `create` is idempotent on the content-addressed id: exporting an unchanged investigation twice
    converges on one package."""

    @abstractmethod
    def create(self, package: AuditPackage) -> tuple[AuditPackage, bool]: ...

    @abstractmethod
    def get(self, org_id: str, package_id: str) -> AuditPackage | None: ...

    @abstractmethod
    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[AuditPackage, ...]: ...

    @abstractmethod
    def latest(self, org_id: str, investigation_id: str) -> AuditPackage | None: ...
