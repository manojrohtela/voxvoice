"""Investigation domain services (M9.21) — the refusals, the custody chain, the manifest.

Pure, deterministic, clock-free and I/O-free, exactly as every Foundation module since M9.2. Every
temporal decision arrives as an `at` parameter; nothing here calls `datetime.now()`, opens a socket,
touches a session or asks who the caller is.

## What this module is for

`commercial_investigations` declares the shapes. This declares the **behaviour that the shapes alone
cannot enforce**, and almost all of it is refusal:

* a snapshot that does not match its own digest is not evidence;
* a live reference that carries a body is a snapshot pretending to be a pointer, and the reverse;
* an observed fact with nothing behind it is a hypothesis wearing the wrong label;
* a `CONFIRMED` conclusion with no supporting snapshot is an assertion, not a finding;
* an investigation whose required questions have no outcome does not close.

The mandate calls several of these "must never". A comment cannot enforce "must never"; a guard that
raises can, and a test that asserts the guard raises is the proof.

## Where financial arithmetic would have gone

Nowhere. `EvidenceComparisonService` can say *these two representations have different digests* —
which is a statement about bytes, not about money. It cannot say *A is ₹200 and B is ₹150, so the
impact is ₹50*, because computing that would make this module a second opinion on a figure the
Ledger owns. `Money` is not imported into this milestone at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_incidents import IntegrityIncident, RootCause
from .commercial_investigations import (
    INVESTIGATION_LIFECYCLE,
    REQUIRED_QUESTIONS,
    SUPPORTING_AVAILABILITY,
    TERMINAL_INVESTIGATION_STATES,
    AuditPackage,
    CaptureMode,
    ClosureAssessment,
    ConclusionClassification,
    CustodyChain,
    CustodyLink,
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
    InvestigationStatus,
    InvestigationTrigger,
    ObservationKind,
    PackageCompleteness,
    QuestionStatus,
    canonical_digest,
    canonical_form,
    canonical_instant,
)
from .diagnostics import BillingDiagnosticCode, Diagnostic, Severity
from .errors import (
    BillingConflict,
    InvalidBillingTransition,
)

# ── errors ──────────────────────────────────────────────────────────────────────


class InvestigationStale(BillingConflict):
    """A worker tried to advance an investigation from a version that is no longer current.

    Raised by persistence when the compare-and-set matches no row, and declared **here, in
    Foundation, beside the domain it protects** — the Application must react to it and may not
    import persistence. Same placement and same reason as `RefundSequenceConflict` (M9.16-R1),
    `CaseStale` (M9.17), `ScheduleStale` (M9.18), `FindingStale` (M9.19) and `IncidentStale`
    (M9.20).

    The scenario this exists for: investigator A reads a case as `INVESTIGATING` version 6, B
    records the conclusion and closes it to version 8, and A resumes and writes. Losing that race
    must be loud — an investigation silently overwritten is an audit record that says something
    nobody concluded."""


class InvestigationRefused(BillingConflict):
    """An investigation action was asked for that this engine must not perform.

    A refusal about the *request*, not an observation about the books — which is why it raises
    rather than becoming a timeline entry. Opening a second investigation on a closed one,
    closing with required questions outstanding, concluding `CONFIRMED` with no evidence, or
    reaching across an organization boundary all land here."""


class EvidenceRefused(BillingConflict):
    """A capture was offered that cannot stand as evidence.

    A body whose digest does not match it, a live reference carrying content, a snapshot carrying
    none, or a custody chain that would fork. Each would produce an audit package that looks
    authoritative and is not, which is strictly worse than one that admits a gap."""


# ── canonicalization guard ──────────────────────────────────────────────────────


class CanonicalizationService:
    """The single canonical serializer, exposed as a service so callers cannot reach for a second.

    There is exactly one algorithm in this milestone — `canonical_form` — and the snapshot digest,
    the observation identity, the conclusion identity and the audit-package digest all reduce to it.
    Two canonicalizers is how the same evidence comes to have two identities."""

    @staticmethod
    def form(value: object) -> str:
        return canonical_form(value)

    @staticmethod
    def digest(value: object) -> str:
        return canonical_digest(value)

    @staticmethod
    def instant(at: float) -> str:
        """A timestamp fit to appear inside a digest: integer microseconds, never a float."""
        return canonical_instant(at)


# ── evidence ────────────────────────────────────────────────────────────────────


class EvidenceGuardService:
    """Where a reference is kept from becoming a snapshot, and a snapshot from becoming a lie."""

    @staticmethod
    def guard_mode_matches_content(
        mode: CaptureMode, body: dict[str, object] | None, content_digest: str
    ) -> None:
        """The structural separation of the two capture modes, enforced in both directions.

        A live reference with a body would be a snapshot that nobody can date; a snapshot without
        one would be a pointer claiming to be a historical record. Both are refused at
        construction, and because the evidence store has **no update path at all**, neither can
        happen afterwards — a capture's mode is fixed for the life of the row."""
        if mode is CaptureMode.LIVE_REFERENCE:
            if body is not None:
                raise EvidenceRefused(
                    "a LIVE_REFERENCE carries no captured representation; it points at the current "
                    "authoritative object and asserts nothing about its content. Capture it as an "
                    "EVIDENCE_SNAPSHOT if the representation is what matters"
                )
            if content_digest:
                raise EvidenceRefused(
                    "a LIVE_REFERENCE has no content digest; a digest would claim that a pointer "
                    "attests to content it does not hold"
                )
            return
        if body is None:
            raise EvidenceRefused(
                "an EVIDENCE_SNAPSHOT must carry the representation it captured; a snapshot "
                "with no body is a live reference wearing the label of historical evidence"
            )

    @staticmethod
    def guard_digest_matches_body(body: dict[str, object] | None, content_digest: str) -> None:
        """The tamper check. Recomputed from the canonical form, never trusted as supplied.

        This is what makes a snapshot *evidence* rather than a copy: an auditor can recompute the
        digest from the stored body and see that the two agree."""
        if body is None:
            return
        expected = canonical_digest(body)
        if content_digest != expected:
            raise EvidenceRefused(
                f"the captured representation does not match its digest ({content_digest[:12]!r} "
                f"declared, {expected[:12]!r} computed); evidence whose digest cannot be "
                "recomputed from its content proves nothing"
            )

    @staticmethod
    def guard_same_org(investigation: Investigation, org_id: str) -> None:
        """Cross-organization evidence attachment, refused at the point of attachment.

        Every identity in this milestone is org-first, so two organizations cannot compute the same
        evidence id from byte-identical content. This catches the other direction: a caller holding
        an id from one tenant reaching for an investigation in another."""
        if investigation.org_id != org_id:
            raise InvestigationRefused(
                f"investigation {investigation.id!r} belongs to org {investigation.org_id!r}; "
                f"evidence from org {org_id!r} cannot be attached to it"
            )

    @staticmethod
    def guard_capturable(investigation: Investigation) -> None:
        """A closed investigation takes no new evidence.

        Evidence arriving after closure is evidence about something else — it belongs to whatever
        incident and investigation covers it now. Appending it here would change what a closed audit
        record says after it was signed."""
        if investigation.is_terminal:
            raise InvestigationRefused(
                f"investigation {investigation.id!r} is {investigation.status.value}; a closed "
                "investigation takes no new evidence, because that would change what a signed "
                "audit record says. Evidence about a recurrence belongs to its own investigation"
            )

    @staticmethod
    def guard_chain_continues(
        previous: EvidenceCapture | None, reference: EvidenceReference
    ) -> None:
        """A custody chain follows one source. A link naming a different source is not a link."""
        if previous is None:
            return
        if previous.reference.locator.split("@")[0] != reference.locator.split("@")[0]:
            raise EvidenceRefused(
                f"custody chain break: {previous.locator!r} cannot be followed by "
                f"{reference.locator!r}; a chain of custody follows one source"
            )


class CustodyService:
    """Assembles chains of custody from appended captures. Reads; never writes, never replaces."""

    @staticmethod
    def _source_key(capture: EvidenceCapture) -> str:
        """Chains are keyed on the source **without** its revision.

        Deliberately: a document moving from revision 1 to revision 2 is exactly the change an
        auditor wants to see *within* one chain. Keying on the revision would put each version in a
        chain of its own and lose the fact that anything changed."""
        return capture.reference.locator.split("@")[0]

    def chains(self, captures: tuple[EvidenceCapture, ...]) -> tuple[CustodyChain, ...]:
        grouped: dict[str, list[EvidenceCapture]] = {}
        for capture in captures:
            grouped.setdefault(self._source_key(capture), []).append(capture)
        out: list[CustodyChain] = []
        for key in sorted(grouped):
            ordered = sorted(grouped[key], key=lambda c: (c.sequence, c.id))
            out.append(CustodyChain(locator=key, links=tuple(self.link(c) for c in ordered)))
        return tuple(out)

    @staticmethod
    def link(capture: EvidenceCapture) -> CustodyLink:
        return CustodyLink(
            evidence_id=capture.id,
            locator=capture.locator,
            mode=capture.mode,
            availability=capture.availability,
            content_digest=capture.content_digest,
            captured_at=capture.captured_at,
            captured_by=capture.captured_by,
            previous_evidence_id=capture.previous_evidence_id,
            reason=capture.unavailable_reason or capture.redaction_reason,
        )

    def current(self, captures: tuple[EvidenceCapture, ...]) -> dict[str, EvidenceCapture]:
        """The newest capture per source. What "is this evidence available *now*" means."""
        latest: dict[str, EvidenceCapture] = {}
        for capture in sorted(captures, key=lambda c: (c.sequence, c.id)):
            latest[self._source_key(capture)] = capture
        return latest

    def gaps(self, captures: tuple[EvidenceCapture, ...]) -> tuple[EvidenceGap, ...]:
        """Every source whose current state is not retrievable. **Named, never omitted.**

        `last_known_available_at` comes from the most recent *earlier* capture that was
        available, so a gap says not just "missing" but "we held this on the fourteenth and lost
        it since" — which is the difference between an audit finding and a shrug."""
        out: list[EvidenceGap] = []
        by_source: dict[str, list[EvidenceCapture]] = {}
        for capture in sorted(captures, key=lambda c: (c.sequence, c.id)):
            by_source.setdefault(self._source_key(capture), []).append(capture)
        for key in sorted(by_source):
            history = by_source[key]
            current = history[-1]
            if current.availability in SUPPORTING_AVAILABILITY:
                continue
            last_seen = 0.0
            for earlier in history[:-1]:
                if earlier.availability in SUPPORTING_AVAILABILITY:
                    last_seen = earlier.captured_at
            out.append(
                EvidenceGap(
                    locator=key,
                    reason=current.unavailable_reason
                    or current.redaction_reason
                    or f"evidence is {current.availability.value}",
                    availability=current.availability,
                    last_known_available_at=last_seen,
                    # A superseded representation does not block anything: a newer capture of the
                    # same source is the evidence, and it is in this package too. An *unavailable*
                    # one does — it is the case where "could not retrieve" must not read as "fine".
                    blocks_conclusion=current.availability is EvidenceAvailability.UNAVAILABLE,
                    evidence_id=current.id,
                )
            )
        return tuple(out)


class EvidenceComparisonService:
    """Compares evidence **identities**, never figures.

    It can state that two captures of one source hold different representations — a fact about
    digests. It cannot state what the difference is worth: that is a financial interpretation,
    and it stays with the domain that owns the figure. There is no subtraction in this milestone."""

    @staticmethod
    def differ(first: EvidenceCapture, second: EvidenceCapture) -> bool:
        if not (first.content_digest and second.content_digest):
            return False  # a live reference attests to no content; absence is not difference
        return first.content_digest != second.content_digest

    @staticmethod
    def describe(first: EvidenceCapture, second: EvidenceCapture) -> str:
        if not (first.content_digest and second.content_digest):
            return "no comparison: at least one side is a live reference and attests to no content"
        if first.content_digest == second.content_digest:
            return f"{first.locator} and {second.locator} hold an identical representation"
        return (
            f"{first.locator} and {second.locator} hold different representations "
            f"({first.content_digest[:12]} vs {second.content_digest[:12]}). What the difference "
            "means financially is decided by the domain that owns the figure, not here"
        )


# ── lifecycle ───────────────────────────────────────────────────────────────────


class InvestigationLifecycleService:
    """Applies triggers through the shared FSM and bumps the concurrency token.

    **The machine's refusals are the specification.** `CLOSED` has no outgoing edge, so `allowed()`
    returns an empty tuple for it and every attempt to reopen raises. No working state has an edge
    to `CLOSED`, so closure is reachable only through `CONCLUDED`."""

    def __init__(self, definition: StateMachineDefinition = INVESTIGATION_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: InvestigationStatus, trigger: InvestigationTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(
        self, current: InvestigationStatus, trigger: InvestigationTrigger
    ) -> InvestigationStatus:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"an investigation cannot {trigger.value} from {current.value}; legal triggers: "
                f"{legal or ['(none — this investigation is closed)']}"
            )
        return edge.target

    def allowed(self, current: InvestigationStatus) -> tuple[InvestigationTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        investigation: Investigation,
        trigger: InvestigationTrigger,
        *,
        at: float,
        actor_id: str = "",
        reason: str = "",
        priority: InvestigationPriority | None = None,
        conclusion_id: str = "",
    ) -> Investigation:
        target = self.next_state(investigation.status, trigger)
        changes: dict = {
            "status": target,
            "version": investigation.version + 1,
            "last_activity_at": at,
        }
        if priority is not None:
            changes["priority"] = priority
        if conclusion_id:
            changes["conclusion_id"] = conclusion_id
        if target in TERMINAL_INVESTIGATION_STATES:
            changes["closed_at"] = at
            changes["closed_by"] = actor_id
            changes["closed_reason"] = reason or trigger.value
        # `incident_severity` is absent from every branch. M9.20 owns severity; an investigation
        # moving through its own workflow never restates how bad the incident was.
        return replace(investigation, **changes)


# ── guards ──────────────────────────────────────────────────────────────────────


class InvestigationGuardService:
    """The refusals. Each states a boundary the mandate draws, in the layer that can enforce it."""

    @staticmethod
    def guard_scoped(org_id: str) -> None:
        if not org_id:
            raise InvestigationRefused(
                "an investigation must be org-scoped; there is no cross-tenant investigation"
            )

    @staticmethod
    def guard_incident_investigable(incident: IntegrityIncident, org_id: str) -> None:
        """**An investigation cannot exist without an incident.**

        Not a validation nicety: an investigation with no incident is a container for a narrative
        that no observation supports, and it is exactly what a free-text case tracker becomes. The
        incident must also belong to the same organization — the org check is here as well as in the
        derived identity, because two defences against a cross-tenant read is the right number."""
        if incident.org_id != org_id:
            raise InvestigationRefused(
                f"integrity incident {incident.id!r} belongs to org {incident.org_id!r} and cannot "
                f"be investigated from org {org_id!r}"
            )

    @staticmethod
    def guard_active(investigation: Investigation) -> None:
        if investigation.is_terminal:
            raise InvestigationRefused(
                f"investigation {investigation.id!r} is {investigation.status.value}; a closed "
                "investigation is never reopened and its record is never rewritten. A recurrence "
                "gets its own incident and its own investigation"
            )

    @staticmethod
    def guard_recordable(investigation: Investigation) -> None:
        """Questions, observations and conclusions need a case that is actually being worked."""
        if investigation.is_terminal:
            raise InvestigationRefused(
                f"investigation {investigation.id!r} is closed; nothing further is recorded "
                "against a closed audit record"
            )
        if investigation.status is InvestigationStatus.OPEN:
            raise InvestigationRefused(
                f"investigation {investigation.id!r} has not been triaged; triage it and start it "
                "before recording findings against it"
            )


class ObservationGuardService:
    """**Where a hypothesis is kept from becoming a fact.**"""

    @staticmethod
    def guard_fact_is_evidence_backed(
        kind: ObservationKind, supporting: tuple[EvidenceCapture, ...]
    ) -> None:
        """An observed fact must cite evidence that actually supports it.

        "Supports" is narrower than "was cited": a live reference attests to no content, and an
        unavailable capture attests to nothing at all. A statement backed only by those is a
        hypothesis, and recording it as a fact is precisely the substitution the mandate forbids.

        A hypothesis is held to no such bar — that is what makes it a hypothesis. It may cite the
        pointer that prompted the thought, or nothing."""
        if kind is not ObservationKind.OBSERVED_FACT:
            return
        if not any(capture.supports_conclusion for capture in supporting):
            raise InvestigationRefused(
                "an OBSERVED_FACT must cite at least one available evidence snapshot. A statement "
                "with nothing behind it is a HYPOTHESIS — record it as one, and the record will "
                "show honestly what was believed and what was proved"
            )

    @staticmethod
    def guard_settleable(observation: InvestigationObservation) -> None:
        """Only a hypothesis is settled, and only once."""
        if observation.kind is not ObservationKind.HYPOTHESIS:
            raise InvestigationRefused(
                f"observation {observation.id!r} is an {observation.kind.value}; only a hypothesis "
                "is supported or rejected. An observed fact is superseded by a later observation, "
                "never overwritten"
            )
        if observation.is_settled:
            raise InvestigationRefused(
                f"hypothesis {observation.id!r} is already {observation.hypothesis_status.value}; "
                "re-settling it would rewrite what the investigation concluded and when"
            )

    @staticmethod
    def settle(
        observation: InvestigationObservation,
        *,
        status: HypothesisStatus,
        at: float,
        actor_id: str,
        evidence_ids: tuple[str, ...],
        reason: str,
    ) -> InvestigationObservation:
        return replace(
            observation,
            hypothesis_status=status,
            settled_at=at,
            settled_by=actor_id,
            settling_evidence_ids=evidence_ids,
            settled_reason=reason,
            version=observation.version + 1,
        )


class ConclusionGuardService:
    """**Where an unsupported statement is kept from being marked confirmed.**"""

    @staticmethod
    def guard_supported(
        classification: ConclusionClassification, supporting: tuple[EvidenceCapture, ...]
    ) -> None:
        """`CONFIRMED` and `CONTRADICTS_...` require evidence; `INCONCLUSIVE` requires none.

        That asymmetry is the whole point. Requiring evidence to say "we could not determine this"
        would push an investigator towards the one classification that *does* pass — which is how a
        case ends up asserting a root cause nobody established."""
        if classification is ConclusionClassification.INCONCLUSIVE:
            return
        if not any(capture.supports_conclusion for capture in supporting):
            raise InvestigationRefused(
                f"a {classification.value.upper()} conclusion must cite at least one available "
                "evidence snapshot. If the evidence does not support a statement, INCONCLUSIVE is "
                "the honest classification and it is always available — nobody is required to name "
                "a root cause in order to close a case"
            )

    @staticmethod
    def guard_contradiction_is_specific(
        classification: ConclusionClassification, evidenced: RootCause | None
    ) -> None:
        """A contradiction must say what the evidence indicates instead.

        A conclusion that says only "M9.20 is wrong" is not addressable by the owning workflow. And
        one that names the *same* cause the incident already carries is not a contradiction at
        all — recording it as one would raise a discrepancy nobody can act on."""
        if classification is not ConclusionClassification.CONTRADICTS_INCIDENT_CLASSIFICATION:
            if evidenced is not None:
                raise InvestigationRefused(
                    "an evidenced root cause is recorded only on a contradiction. M9.20 owns "
                    "root-cause classification; restating it here would create a second, divergent "
                    "copy of a fact another domain decides"
                )
            return
        if evidenced is None:
            raise InvestigationRefused(
                "a conclusion that contradicts the incident's classification must name the root "
                "cause the evidence indicates; a contradiction with no alternative is not "
                "something the owning M9.20 workflow can act on"
            )

    @staticmethod
    def guard_not_self_contradicting(incident: RootCause, evidenced: RootCause | None) -> None:
        if evidenced is not None and evidenced is incident:
            raise InvestigationRefused(
                f"the incident is already classified {incident.value!r}; recording that as a "
                "contradiction would raise a discrepancy against a classification that agrees"
            )


class ClosureService:
    """**Closure criteria, computed rather than asserted.**

    Returned as an assessment by a query as well as consulted by the command, so an operator sees
    exactly what is outstanding before pressing anything. Every reason is a sentence naming the
    thing to do.

    Closure means *the investigation is finished*. It does not mean the financial problem is fixed —
    M9.20 owns incident resolution, and an investigation can legitimately close
    `INCONCLUSIVE` on an incident that is still open."""

    def assess(
        self,
        investigation: Investigation,
        *,
        questions: tuple[InvestigationQuestion, ...],
        observations: tuple[InvestigationObservation, ...],
        captures: tuple[EvidenceCapture, ...],
        conclusion: InvestigationConclusion | None,
        custody: CustodyService | None = None,
    ) -> ClosureAssessment:
        custody = custody or CustodyService()
        reasons: list[str] = []

        unresolved = tuple(
            sorted(
                {q.kind for q in questions if q.required and not q.is_resolved},
                key=lambda k: k.value,
            )
        )
        for kind in unresolved:
            reasons.append(
                f"required question {kind.value!r} has no outcome; answer it, or record it "
                "UNANSWERABLE with the reason evidence could not settle it"
            )
        # A required question that was never asked is as unclosable as one left open. Otherwise an
        # investigation could close by simply never raising the question an examiner will ask.
        asked = {q.kind for q in questions}
        missing = tuple(sorted(set(REQUIRED_QUESTIONS) - asked, key=lambda k: k.value))
        for kind in missing:
            reasons.append(
                f"required question {kind.value!r} was never asked; an investigation does not "
                "close by leaving the question an examiner will ask unrecorded"
            )

        open_hypotheses = tuple(
            o.id
            for o in observations
            if o.kind is ObservationKind.HYPOTHESIS and not o.is_settled
        )
        for hypothesis_id in open_hypotheses:
            reasons.append(
                f"hypothesis {hypothesis_id!r} is unsettled; support it, reject it, or the record "
                "leaves a guess standing beside the conclusion with nothing distinguishing them"
            )

        gaps = custody.gaps(captures)
        blocking = tuple(g for g in gaps if g.blocks_conclusion)
        for gap in blocking:
            reasons.append(
                f"evidence {gap.locator!r} is {gap.availability.value} and blocks a conclusion; "
                "record it UNANSWERABLE against the question it was to answer, or conclude "
                "INCONCLUSIVE — it must not simply be omitted"
            )

        if conclusion is None:
            reasons.append(
                "no conclusion is on record; an investigation closes through CONCLUDED, and "
                "INCONCLUSIVE is always available so no root cause need be invented"
            )

        return ClosureAssessment(
            investigation_id=investigation.id,
            may_close=not reasons,
            unresolved_required_questions=unresolved + missing,
            open_hypotheses=open_hypotheses,
            blocking_gaps=blocking,
            conclusion_id=conclusion.id if conclusion else "",
            conclusion_classification=conclusion.classification if conclusion else None,
            reasons=tuple(reasons),
        )

    def guard_closable(self, assessment: ClosureAssessment) -> None:
        if assessment.may_close:
            return
        raise InvestigationRefused(
            f"investigation {assessment.investigation_id!r} cannot close: "
            + "; ".join(assessment.reasons)
        )


# ── the audit package ───────────────────────────────────────────────────────────


class ManifestBuilder:
    """Builds the canonical manifest an audit package is addressed by.

    Two properties matter and both come from `canonical_form` alone:

    * **deterministic** — sorted keys, tagged scalars, integer instants, no float anywhere. The same
      investigation manifests identically on any machine, so an examiner can recompute the package
      digest from the exported rows and see that nothing was edited after export;
    * **references, not figures** — every entry is an identity, a digest, a status or a count.
      Nothing is summed, priced or compared numerically. A manifest that carried a total would be
      this milestone quietly becoming a second opinion about money.
    """

    VERSION = "m9.21.1"

    #: Timeline kinds a manifest **excludes**, and the one reason it may exclude anything.
    #:
    #: Exporting appends `AUDIT_PACKAGE_CREATED` to the trail. If the manifest contained that
    #: entry, every export would change the thing it was describing: the second export of an
    #: otherwise-untouched investigation would see a longer timeline, compute a different digest,
    #: and mint a second package. The mandate requires the opposite — *exporting the same unchanged
    #: investigation twice must produce the same package identity* — so a package cannot contain
    #: the record of its own creation.
    #:
    #: Nothing is hidden by this. Every export remains visible on `/timeline`, and each package
    #: carries its own `created_at` and `created_by`. What is excluded is only the self-reference
    #: that would make determinism impossible to state.
    EXCLUDED_FROM_MANIFEST: frozenset[InvestigationEventKind] = frozenset(
        {InvestigationEventKind.AUDIT_PACKAGE_CREATED}
    )

    @classmethod
    def manifest_timeline(
        cls, timeline: tuple[InvestigationEvent, ...]
    ) -> tuple[InvestigationEvent, ...]:
        """The trail as a manifest sees it: everything except the export self-reference."""
        return tuple(e for e in timeline if e.kind not in cls.EXCLUDED_FROM_MANIFEST)

    def build(
        self,
        investigation: Investigation,
        *,
        incident: IntegrityIncident | None,
        captures: tuple[EvidenceCapture, ...],
        questions: tuple[InvestigationQuestion, ...],
        observations: tuple[InvestigationObservation, ...],
        conclusions: tuple[InvestigationConclusion, ...],
        timeline: tuple[InvestigationEvent, ...],
        custody: CustodyService | None = None,
    ) -> dict[str, object]:
        custody = custody or CustodyService()
        return {
            "manifest_version": self.VERSION,
            "org_id": investigation.org_id,
            "investigation": self._investigation(investigation),
            "incident": self._incident(incident, investigation.incident_id),
            "evidence": [
                self._capture(c) for c in sorted(captures, key=lambda c: (c.sequence, c.id))
            ],
            "custody_chains": [
                {
                    "locator": chain.locator,
                    "changed": chain.changed,
                    "links": [
                        {
                            "evidence_id": link.evidence_id,
                            "mode": link.mode.value,
                            "availability": link.availability.value,
                            "content_digest": link.content_digest,
                            "captured_at": canonical_instant(link.captured_at),
                            "captured_by": link.captured_by,
                            "previous_evidence_id": link.previous_evidence_id,
                            "reason": link.reason,
                        }
                        for link in chain.links
                    ],
                }
                for chain in custody.chains(captures)
            ],
            "questions": [self._question(q) for q in sorted(questions, key=lambda q: q.id)],
            "observations": [
                self._observation(o) for o in sorted(observations, key=lambda o: (o.sequence, o.id))
            ],
            "conclusions": [
                self._conclusion(c) for c in sorted(conclusions, key=lambda c: (c.version, c.id))
            ],
            "timeline": [
                self._event(e)
                for e in sorted(self.manifest_timeline(timeline), key=lambda e: e.sequence)
            ],
            "gaps": [self._gap(g) for g in custody.gaps(captures)],
        }

    @staticmethod
    def _investigation(i: Investigation) -> dict[str, object]:
        return {
            "id": i.id,
            "org_id": i.org_id,
            "incident_id": i.incident_id,
            "status": i.status.value,
            "priority": i.priority.value,
            "incident_severity": i.incident_severity.value,
            "summary": i.summary,
            "opened_at": canonical_instant(i.opened_at),
            "opened_by": i.opened_by,
            "closed_at": None if i.closed_at is None else canonical_instant(i.closed_at),
            "closed_by": i.closed_by,
            "closed_reason": i.closed_reason,
            "conclusion_id": i.conclusion_id,
            "evidence_count": i.evidence_count,
            "version": i.version,
        }

    @staticmethod
    def _incident(incident: IntegrityIncident | None, incident_id: str) -> dict[str, object]:
        """The incident **as referenced**, and explicitly marked unreadable when it could not be.

        `None` here does not mean "no incident" — it means the read failed, and a manifest that
        rendered that as an absent key would let an unreadable incident look like an investigation
        that never had one."""
        if incident is None:
            return {"id": incident_id, "available": False}
        return {
            "id": incident.id,
            "available": True,
            "correlation_key": incident.correlation_key,
            "anchor_kind": incident.anchor_kind.value,
            "anchor_value": incident.anchor_value,
            "status": incident.status.value,
            "severity": incident.severity.value,
            "root_cause": incident.root_cause.value,
            "impact": incident.impact.value,
            "finding_count": incident.finding_count,
            "evidence_digest": incident.evidence_digest,
            "version": incident.version,
        }

    @staticmethod
    def _capture(c: EvidenceCapture) -> dict[str, object]:
        return {
            "id": c.id,
            "locator": c.locator,
            "domain": c.reference.domain.value,
            "source_type": c.reference.source_type.value,
            "source_id": c.reference.source_id,
            "revision": c.reference.revision,
            "mode": c.mode.value,
            "availability": c.availability.value,
            "content_digest": c.content_digest,
            "body": c.body,
            "captured_at": canonical_instant(c.captured_at),
            "captured_by": c.captured_by,
            "previous_evidence_id": c.previous_evidence_id,
            "unavailable_reason": c.unavailable_reason,
            "redaction_reason": c.redaction_reason,
            "sequence": c.sequence,
        }

    @staticmethod
    def _question(q: InvestigationQuestion) -> dict[str, object]:
        return {
            "id": q.id,
            "kind": q.kind.value,
            "text": q.text,
            "status": q.status.value,
            "required": q.required,
            "answer": q.answer,
            "evidence_ids": list(q.evidence_ids),
            "asked_at": canonical_instant(q.asked_at),
            "asked_by": q.asked_by,
            "answered_at": None if q.answered_at is None else canonical_instant(q.answered_at),
            "answered_by": q.answered_by,
        }

    @staticmethod
    def _observation(o: InvestigationObservation) -> dict[str, object]:
        return {
            "id": o.id,
            "kind": o.kind.value,
            "statement": o.statement,
            "statement_digest": o.statement_digest,
            "evidence_ids": list(o.evidence_ids),
            "hypothesis_status": o.hypothesis_status.value,
            "recorded_at": canonical_instant(o.recorded_at),
            "recorded_by": o.recorded_by,
            "settled_at": None if o.settled_at is None else canonical_instant(o.settled_at),
            "settled_by": o.settled_by,
            "settling_evidence_ids": list(o.settling_evidence_ids),
            "settled_reason": o.settled_reason,
            "sequence": o.sequence,
        }

    @staticmethod
    def _conclusion(c: InvestigationConclusion) -> dict[str, object]:
        return {
            "id": c.id,
            "classification": c.classification.value,
            "statement": c.statement,
            "statement_digest": c.statement_digest,
            "evidence_ids": list(c.evidence_ids),
            "incident_root_cause": c.incident_root_cause.value,
            "evidenced_root_cause": (
                None if c.evidenced_root_cause is None else c.evidenced_root_cause.value
            ),
            "supersedes": c.supersedes,
            "recorded_at": canonical_instant(c.recorded_at),
            "recorded_by": c.recorded_by,
            "version": c.version,
        }

    @staticmethod
    def _event(e: InvestigationEvent) -> dict[str, object]:
        return {
            "sequence": e.sequence,
            "kind": e.kind.value,
            "at": canonical_instant(e.at),
            "actor_id": e.actor_id,
            "subject": e.subject,
            "summary": e.summary,
            "evidence_ids": list(e.evidence_ids),
            "detail": dict(e.detail),
        }

    @staticmethod
    def _gap(g: EvidenceGap) -> dict[str, object]:
        return {
            "locator": g.locator,
            "reason": g.reason,
            "availability": g.availability.value,
            "last_known_available_at": canonical_instant(g.last_known_available_at),
            "blocks_conclusion": g.blocks_conclusion,
            "evidence_id": g.evidence_id,
        }


class CompletenessService:
    """Decides `COMPLETE` vs `INCOMPLETE`. **On availability, never on optimism.**"""

    @staticmethod
    def assess(
        *,
        gaps: tuple[EvidenceGap, ...],
        questions: tuple[InvestigationQuestion, ...],
        incident_available: bool,
    ) -> PackageCompleteness:
        if not incident_available:
            return PackageCompleteness.INCOMPLETE
        if gaps:
            return PackageCompleteness.INCOMPLETE
        if any(q.required and not q.is_resolved for q in questions):
            return PackageCompleteness.INCOMPLETE
        return PackageCompleteness.COMPLETE


@dataclass(frozen=True, slots=True)
class PackageGuardService:
    """A package is written once and never touched again."""

    @staticmethod
    def guard_immutable(existing: AuditPackage, proposed_digest: str) -> None:
        """Two exports agreeing on an id must agree on the manifest that produced it.

        If they ever did not, the content-addressing would be broken — and the failure mode is the
        worst one available: an examiner recomputing the digest of an exported package would find it
        matches, while the bytes had changed underneath. Loud is the only acceptable behaviour."""
        if existing.manifest_digest != proposed_digest:
            raise EvidenceRefused(
                f"audit package {existing.id!r} already exists with manifest digest "
                f"{existing.manifest_digest[:12]!r}; a package is content-addressed and immutable. "
                "A changed investigation produces a new package, never a rewritten one"
            )


# ── validation ──────────────────────────────────────────────────────────────────


class InvestigationValidator:
    """Structural validation. Returns diagnostics; the factory decides what is fatal (§A2.6)."""

    def validate_investigation(self, investigation: Investigation) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not investigation.org_id:
            out.append(self._error("org_id", "an investigation must be org-scoped"))
        if not investigation.incident_id:
            out.append(
                self._error(
                    "incident_id",
                    "an investigation must reference an integrity incident; an investigation of "
                    "nothing is a place for a narrative no observation supports",
                )
            )
        if investigation.evidence_count < 0 or investigation.open_question_count < 0:
            out.append(self._error("counts", "counts cannot be negative"))
        return tuple(out)

    def validate_capture(self, capture: EvidenceCapture) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not capture.org_id:
            out.append(self._error("org_id", "evidence must be org-scoped"))
        if not capture.reference.source_id:
            out.append(
                self._error(
                    "reference",
                    "every evidence reference must be machine-resolvable; there is no free-text "
                    "source in this milestone",
                )
            )
        if capture.mode is CaptureMode.EVIDENCE_SNAPSHOT and not capture.content_digest:
            out.append(self._error("content_digest", "a snapshot must carry a content digest"))
        return tuple(out)

    def validate_question(self, question: InvestigationQuestion) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not question.text.strip():
            out.append(self._error("text", "a question must say what is being asked"))
        if question.status is QuestionStatus.ANSWERED and not question.answer.strip():
            out.append(
                self._error("answer", "a question marked ANSWERED must carry the answer")
            )
        return tuple(out)

    def validate_observation(self, observation: InvestigationObservation) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not observation.statement.strip():
            out.append(self._error("statement", "an observation must state something"))
        if (
            observation.kind is ObservationKind.OBSERVED_FACT
            and observation.hypothesis_status is not HypothesisStatus.NOT_APPLICABLE
        ):
            out.append(
                self._error(
                    "hypothesis_status",
                    "an observed fact carries no hypothesis status; only a hypothesis is supported "
                    "or rejected",
                )
            )
        return tuple(out)

    def validate_conclusion(self, conclusion: InvestigationConclusion) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        if not conclusion.statement.strip():
            out.append(self._error("statement", "a conclusion must state what was concluded"))
        if not conclusion.incident_id:
            out.append(self._error("incident_id", "a conclusion names the incident it concerns"))
        return tuple(out)

    @staticmethod
    def _error(field_name: str, message: str) -> Diagnostic:
        return Diagnostic(
            code=BillingDiagnosticCode.SNAPSHOT_INTEGRITY,
            severity=Severity.ERROR,
            message=message,
            path=f"investigation.{field_name}",
        )
