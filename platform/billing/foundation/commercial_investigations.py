"""Financial Investigation & Audit Evidence domain (M9.21) — the pure kernel.

M9.19 observes that two financial facts disagree. M9.20 proves which observations share a cause and
tracks the incident. **Neither records what a human found out.** An auditor asking *what did Billing
know on the fourteenth, who looked at it, what did they conclude, and on what evidence* is today
answered from application logs — which rotate, which are sampled, and which nobody can prove were
not edited. This milestone makes that answer a durable financial record.

## What lives here, and what deliberately does not

An investigation is an **evidence and audit workflow**. It records observations, questions,
hypotheses and conclusions about an incident somebody else declared, and it packages them so an
external examiner can reconstruct the reasoning without trusting a log line.

It is not a second reconciliation engine, a second correlation engine, or any kind of ledger. There
is no balance on any type in this module, no total, no tax, no price, no recognized amount and no
arithmetic over money at all — `Money` is not imported, and a test asserts it. Every figure an
investigator sees belongs to the domain that decided it and arrives here **by reference**.

## The two ideas the whole design turns on

**One. A reference is not a snapshot.** `LIVE_REFERENCE` says *look at the current authoritative
object*; `EVIDENCE_SNAPSHOT` says *this exact representation existed at this moment*. Confusing them
is how an audit package comes to contain today's corrected figure while claiming to show what was
known before the correction. Here the two cannot be confused: a live reference structurally has no
body and no content digest, a snapshot structurally must have both, and there is no update path on
the evidence store through which either could become the other.

**Two. Evidence is content-addressed and append-only.** A snapshot's identity is derived from what
it contains, so capturing the same immutable source twice converges on one row and a changed
representation is a *different* piece of evidence rather than an overwrite of the old one. Nothing
here is ever replaced: an evidence source becoming unavailable, redacted or superseded is recorded
as a **new link in a chain**, so the trail shows what was captured first, what was captured later,
what changed, and what did not.

## Closure is earned, not clicked

`INVESTIGATION_LIFECYCLE` has **no edge from any working state directly to `CLOSED`**. The only way
in is through `CONCLUDED`, and a conclusion is either `CONFIRMED` — which requires available
evidence — or `INCONCLUSIVE`, which requires none and is always available. That pair of facts is the
structural form of two mandate rules at once: an investigation cannot be quietly closed with nothing
found, and nobody is ever forced to invent a root cause in order to shut a case.

`CLOSED` has no outgoing edge either. A recurrence gets its own investigation; a historical one is
never re-used as a container for a new problem.

## Ownership, restated at the altitude this module sits at

M9.20 remains authoritative for whether an incident exists, which findings belong to it, its
severity, its lifecycle and its root-cause classification. When an investigation finds evidence that
**contradicts** that classification, it records a discrepancy addressed to the owning workflow — it
does not reach over and change the incident. There is no import of an incident repository here, and
none of ledger, invoice, tax, payment, recognition, collections, rating, metering, entitlement,
subscription or account either.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .commercial_incidents import IncidentSeverity, RootCause
from .commercial_reconciliation import RelationshipKind

# ── canonicalization: exactly one algorithm ─────────────────────────────────────

#: The value kinds a canonical evidence body may contain. Deliberately narrow.
#:
#: **`float` is absent, and that is the point.** A digest that depends on how a float is formatted
#: is a digest
#: that changes when the interpreter, the locale or the platform changes, and evidence whose
#: identity is not reproducible is not evidence. Timestamps reach a body through
#: `canonical_instant`, which converts to integer microseconds before it ever becomes a string.
CANONICAL_SCALARS: tuple[type, ...] = (str, int, bool, type(None))

#: Type tags. Present so that the integer `1`, the string `"1"` and the boolean `True` cannot
#: canonicalize to the same bytes — without them `{"n": 1}` and `{"n": "1"}` would share a digest
#: and two different pieces of evidence would claim one identity.
_TAG_STR = "s"
_TAG_INT = "i"
_TAG_BOOL = "b"
_TAG_NULL = "z"

_FS = "\x1f"  # between fields
_RS = "\x1e"  # between records


class CanonicalizationError(ValueError):
    """A value was offered as evidence that cannot be canonicalized deterministically.

    Raised rather than coerced. Silently rendering a float would produce a digest that is stable on
    the machine that wrote it and different on the machine that audits it."""


def canonical_instant(at: float) -> str:
    """A timestamp, as evidence. Integer microseconds — never a formatted float.

    Billing's clock seam produces `float` epoch seconds, and a float inside a digest is a digest
    that depends on `repr()`. Microsecond truncation is lossless at the resolution any financial
    event is recorded with, and produces the same string on every platform."""
    return str(int(round(at * 1_000_000)))


def canonical_form(value: object) -> str:
    """The one canonical serialization in this milestone. Deterministic by construction.

    * mappings are emitted in **sorted key order**, so two bodies built by different code paths
      canonicalize identically;
    * absent and null are **distinguishable**: a key that is not present emits nothing, a key whose
      value is `None` emits a null-tagged record. "The provider returned no reference" and "we never
      asked the provider" are different facts about an audit, and a canonicalizer that flattened
      them would let one be presented as the other;
    * integers are emitted exactly, never through a float;
    * strings are emitted exactly, with no case folding, trimming or locale-dependent collation;
    * enums are emitted as their `value`, so a rename of a Python member does not silently
      re-identify historical evidence;
    * sequences preserve order, because the order of a ledger's entries is part of what it says.

    There is deliberately no second algorithm anywhere in this milestone: the snapshot digest, the
    audit-package digest and the observation identity all reduce to this function."""
    return _canonical(value)


def _canonical(value: object) -> str:
    if isinstance(value, StrEnum):
        return f"{_TAG_STR}:{value.value}"
    if isinstance(value, bool):  # before int — bool is a subclass of int
        return f"{_TAG_BOOL}:{'1' if value else '0'}"
    if value is None:
        return f"{_TAG_NULL}:"
    if isinstance(value, str):
        return f"{_TAG_STR}:{value}"
    if isinstance(value, int):
        return f"{_TAG_INT}:{value:d}"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        inner = _RS.join(f"{_canonical(str(k))}{_FS}{_canonical(v)}" for k, v in items)
        return f"D({inner})"
    if isinstance(value, (tuple, list)):
        return "L(" + _RS.join(_canonical(v) for v in value) + ")"
    raise CanonicalizationError(
        f"{type(value).__name__} cannot appear in canonical evidence; permitted kinds are "
        "str, int, bool, None, mappings and sequences of those. A float in particular is refused: "
        "use canonical_instant() for a timestamp, and keep money in the domain that owns it"
    )


def canonical_digest(value: object) -> str:
    """A content digest over the canonical form. Same content in, same digest out — everywhere."""
    return hashlib.sha256(canonical_form(value).encode("utf-8")).hexdigest()


def _digest(*parts: str) -> str:
    return hashlib.sha256(_FS.join(parts).encode("utf-8")).hexdigest()


# ── identities ────────────────────────────────────────────────────────────────── # Every identity
# below is **derived** and **org-first**. Derived, so that two investigators, two workers or one
# worker restarted mid-command converge on one row instead of two. Org-first, so that two
# organizations holding an incident with the same id, a document with the same number or a snapshot
# with byte-identical content still cannot compute a colliding identity — cross-tenant isolation
# becomes a property of the key rather than of remembering a `WHERE` clause.


def derive_investigation_id(org_id: str, incident_id: str) -> str:
    """One investigation per incident, by construction.

    A second `OpenInvestigation` for the same incident lands on the same primary key and returns the
    existing case rather than opening a rival one with a divergent conclusion."""
    return "ivg_" + _digest(org_id, incident_id)[:32]


def derive_evidence_id(
    org_id: str,
    domain: str,
    source_type: str,
    source_id: str,
    revision: str,
    content_digest: str,
) -> str:
    """**Content-addressed.** Identity follows what the evidence contains.

    Capture the same immutable source twice and the id is identical, so the second capture writes
    nothing. Capture it after the representation changed and the id differs — the new capture is a
    *new* link in the chain, never an overwrite of what was true before."""
    return "evd_" + _digest(org_id, domain, source_type, source_id, revision, content_digest)[:32]


def derive_question_id(investigation_id: str, kind: str, subject: str) -> str:
    return "qst_" + _digest(investigation_id, kind, subject)[:32]


def derive_observation_id(investigation_id: str, kind: str, statement_digest: str) -> str:
    """Includes `kind`, and that is a safety property rather than a detail.

    A hypothesis and an observed fact with identical wording get **different** identities, so a
    hypothesis can never be promoted into a fact by re-submitting it. Doing so appends a second,
    separately-identified observation and the trail shows both."""
    return "obs_" + _digest(investigation_id, kind, statement_digest)[:32]


def derive_conclusion_id(investigation_id: str, classification: str, statement_digest: str) -> str:
    return "ccl_" + _digest(investigation_id, classification, statement_digest)[:32]


def derive_investigation_event_id(
    investigation_id: str, kind: str, subject: str, sequence: int
) -> str:
    return "ive_" + _digest(investigation_id, kind, subject, str(sequence))[:32]


def derive_audit_package_id(org_id: str, investigation_id: str, manifest_digest: str) -> str:
    """**The export determinism guarantee, as an identity.**

    Exporting an unchanged investigation twice produces the same manifest, hence the same digest,
    hence the same package id — and the second export writes nothing. Export it after anything in
    the investigation changed and the manifest differs, so the digest differs, so a *new* package
    exists beside the old one. No package is ever mutated."""
    return "apk_" + _digest(org_id, investigation_id, manifest_digest)[:32]


# ── evidence ────────────────────────────────────────────────────────────────────


class EvidenceDomain(StrEnum):
    """Which milestone owns the fact being referenced. A closed vocabulary, so an evidence
    reference can be machine-resolved rather than parsed out of prose."""

    LEDGER = "ledger"  # M9.13
    INVOICE = "invoice"  # M9.14
    TAX = "tax"  # M9.15
    PAYMENT = "payment"  # M9.16
    COLLECTIONS = "collections"  # M9.17
    REVENUE = "revenue"  # M9.18
    RECONCILIATION = "reconciliation"  # M9.19
    INTEGRITY = "integrity"  # M9.20
    #: M9.21's own artifacts, added by M9.22 so a regulatory report can *reference* the
    #: investigation that examined a period. Additive: no existing member changed, and M9.21 emits
    #: neither value itself — an investigation does not capture itself as evidence.
    INVESTIGATION = "investigation"


class EvidenceSourceType(StrEnum):
    """The specific immutable artifact referenced, within its domain."""

    LEDGER_ENTRY = "ledger_entry"
    JOURNAL_ENTRY = "journal_entry"
    INVOICE_DOCUMENT = "invoice_document"
    TAX_ASSESSMENT = "tax_assessment"
    PAYMENT_INTENT = "payment_intent"
    PAYMENT_ATTEMPT = "payment_attempt"
    COLLECTION_CASE = "collection_case"
    REVENUE_SCHEDULE = "revenue_schedule"
    REVENUE_ENTRY = "revenue_entry"
    RECONCILIATION_RUN = "reconciliation_run"
    RECONCILIATION_FINDING = "reconciliation_finding"
    REPAIR_REQUEST = "repair_request"
    INTEGRITY_INCIDENT = "integrity_incident"
    INCIDENT_EVENT = "incident_event"
    VERIFICATION_OUTCOME = "verification_outcome"
    #: Added by M9.22 (additive) so a report can pin the investigation and audit package that
    #: covered its period. M9.21 never captures either — it would be capturing itself.
    INVESTIGATION_CASE = "investigation_case"
    AUDIT_PACKAGE = "audit_package"


class CaptureMode(StrEnum):
    """**The central distinction of this milestone.**

    A `LIVE_REFERENCE` is a pointer: it says where the current authoritative object lives and
    asserts nothing about its content, now or ever. An `EVIDENCE_SNAPSHOT` is a claim: *this exact
    representation existed, and here is the digest that proves the copy was not edited afterwards*.

    Substituting one for the other is the failure this milestone exists to prevent, because a
    package that presented today's corrected figure as the historical evidence would make a
    correction look like something that never needed correcting. The two are kept apart
    structurally, not by convention: see `EvidenceReference` and `EvidenceGuardService`."""

    LIVE_REFERENCE = "live_reference"
    EVIDENCE_SNAPSHOT = "evidence_snapshot"


class EvidenceAvailability(StrEnum):
    """What can be said about retrieving this evidence *now*.

    `UNAVAILABLE` is a first-class outcome rather than a missing row. **"Could not retrieve" must
    never round to "verified"** — an audit package that silently omitted the evidence it failed to
    fetch would read as complete, which is worse than reading as incomplete."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    REDACTED = "redacted"
    SUPERSEDED = "superseded"


#: Availabilities that can support a confirmed conclusion. `REDACTED` is included because a
#: redaction replaces a representation with a narrower safe one — the fact remains attested. The two
#: absent members carry the whole rule: `UNAVAILABLE` never supports a conclusion, and `SUPERSEDED`
#: means a later capture is the evidence, so cite that one.
SUPPORTING_AVAILABILITY: frozenset[EvidenceAvailability] = frozenset(
    {EvidenceAvailability.AVAILABLE, EvidenceAvailability.REDACTED}
)


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """One machine-resolvable pointer at somebody else's immutable fact.

    There is no `source: str` free-text field anywhere in this milestone. A reference names its
    domain, its artifact type, its identifier and — where the owning domain publishes one — the
    revision it was read at, so an auditor can go and look rather than read a sentence about it."""

    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str
    revision: str = ""  # the owning domain's own version/revision, where it publishes one

    @property
    def locator(self) -> str:
        """A stable, human-quotable address. Not an identity — identity is content-addressed."""
        base = f"{self.domain.value}:{self.source_type.value}:{self.source_id}"
        return f"{base}@{self.revision}" if self.revision else base


@dataclass(frozen=True, slots=True)
class EvidenceCapture:
    """**One immutable link in the chain of custody.** Never updated, never deleted.

    A capture is what an auditor is actually shown: who took it, when, from where, in which mode,
    and — for a snapshot — the exact representation plus the digest that proves it is unedited.

    `previous_evidence_id` is what makes this a *chain* rather than a set. When a source later
    becomes unavailable, is redacted, or is superseded by a newer representation, a **new** capture
    is appended naming the one it follows. Nothing is overwritten, so the question *what did we hold
    first, and what changed* has an answer that does not depend on a backup."""

    id: str
    org_id: str
    investigation_id: str
    reference: EvidenceReference
    mode: CaptureMode
    availability: EvidenceAvailability = EvidenceAvailability.AVAILABLE
    #: The captured representation. **Present for a snapshot, absent for a live reference** — a
    #: structural distinction, guarded on construction and unchangeable afterwards because the
    #: evidence store has no update path.
    body: dict[str, object] | None = None
    content_digest: str = ""  # canonical_digest(body); empty for a live reference
    captured_at: float = 0.0
    captured_by: str = ""
    previous_evidence_id: str = ""  # the link this one follows in the custody chain
    unavailable_reason: str = ""
    redaction_reason: str = ""
    sequence: int = 0  # position within this investigation's capture order

    @property
    def is_snapshot(self) -> bool:
        return self.mode is CaptureMode.EVIDENCE_SNAPSHOT

    @property
    def supports_conclusion(self) -> bool:
        """Whether this capture can back a *confirmed* conclusion.

        A live reference cannot. It asserts nothing about content — pointing at an object that may
        have changed since is not evidence of what was true when the investigation ran."""
        return self.is_snapshot and self.availability in SUPPORTING_AVAILABILITY

    @property
    def locator(self) -> str:
        return self.reference.locator


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    """Evidence an audit package expected and does not have. **Named, never omitted.**

    Every field exists so the gap can be judged rather than merely noticed: which source, why it is
    missing, when it was last known to be retrievable, and — the field that matters most —
    whether its absence prevents a conclusion from standing."""

    locator: str
    reason: str
    availability: EvidenceAvailability
    last_known_available_at: float = 0.0
    blocks_conclusion: bool = False
    evidence_id: str = ""


# ── the investigation ───────────────────────────────────────────────────────────


class InvestigationStatus(StrEnum):
    """Six states, each earning its place by holding something no other state holds."""

    OPEN = "open"  # opened against an incident; nobody has triaged it
    TRIAGED = "triaged"  # priority assigned, not yet started
    INVESTIGATING = "investigating"  # actively worked
    AWAITING_EVIDENCE = "awaiting_evidence"  # blocked: evidence could not be retrieved
    CONCLUDED = "concluded"  # a conclusion is on record; not yet closed
    CLOSED = "closed"  # terminal


class InvestigationTrigger(StrEnum):
    TRIAGE = "triage"
    START = "start"
    AWAIT_EVIDENCE = "await_evidence"
    RESUME = "resume"
    CONCLUDE = "conclude"
    CLOSE = "close"


class InvestigationPriority(StrEnum):
    """Operational urgency, assigned at triage. **Not severity** — M9.20 owns severity, and an
    investigation never restates it. A critical incident may be low priority because the repair is
    already dispatched; a warning may be urgent because a quarter closes tomorrow."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


PRIORITY_ORDER: tuple[InvestigationPriority, ...] = (
    InvestigationPriority.URGENT,
    InvestigationPriority.HIGH,
    InvestigationPriority.NORMAL,
    InvestigationPriority.LOW,
)


#: **The lifecycle, and its two deliberate absences.** #: There is no edge from `OPEN`, `TRIAGED`,
#: `INVESTIGATING` or `AWAITING_EVIDENCE` to `CLOSED`. The only route to closure runs through
#: `CONCLUDED`, which makes the mandate's closure criteria structural instead of a checklist
#: somebody can skip: an investigation cannot be quietly shut with nothing on record. Because
#: `INCONCLUSIVE` is always an available classification, this forces a *statement* without ever
#: forcing a *root cause*. #: And `CLOSED` has no outgoing edge. A closed investigation is never
#: reopened and never re-used as the container for a later problem — a recurrence gets its own
#: incident from M9.20 and its own investigation from here.
INVESTIGATION_LIFECYCLE: StateMachineDefinition = define(
    [
        (InvestigationStatus.OPEN, InvestigationTrigger.TRIAGE, InvestigationStatus.TRIAGED),
        (
            InvestigationStatus.TRIAGED,
            InvestigationTrigger.START,
            InvestigationStatus.INVESTIGATING,
        ),
        (
            InvestigationStatus.INVESTIGATING,
            InvestigationTrigger.AWAIT_EVIDENCE,
            InvestigationStatus.AWAITING_EVIDENCE,
        ),
        (
            InvestigationStatus.AWAITING_EVIDENCE,
            InvestigationTrigger.RESUME,
            InvestigationStatus.INVESTIGATING,
        ),
        (
            InvestigationStatus.INVESTIGATING,
            InvestigationTrigger.CONCLUDE,
            InvestigationStatus.CONCLUDED,
        ),
        # Before closure a conclusion may be revised: new evidence arriving after somebody wrote one
        # is exactly when an audit most needs the case to move rather than to be closed anyway. The
        # earlier conclusion is not deleted — the new one supersedes it and both stay on record.
        (
            InvestigationStatus.CONCLUDED,
            InvestigationTrigger.RESUME,
            InvestigationStatus.INVESTIGATING,
        ),
        (InvestigationStatus.CONCLUDED, InvestigationTrigger.CLOSE, InvestigationStatus.CLOSED),
    ]
)

TERMINAL_INVESTIGATION_STATES: frozenset[InvestigationStatus] = frozenset(
    {InvestigationStatus.CLOSED}
)

LIVE_INVESTIGATION_STATES: frozenset[InvestigationStatus] = frozenset(
    set(InvestigationStatus) - TERMINAL_INVESTIGATION_STATES
)


@dataclass(frozen=True, slots=True)
class Investigation:
    """**One operational investigation of one already-declared integrity incident.**

    Holds no financial truth. No balance, no total, no amount, no currency, no rate — every figure
    the investigator reads belongs to the domain that decided it and is reached through an
    `EvidenceReference`.

    `incident_id` is required and validated against M9.20 at the Application boundary: an
    investigation cannot exist without an incident, because an investigation of nothing is a place
    for a narrative to accumulate that no observation supports.

    `evidence_digest` is a rolling canonical digest over the capture chain — it is what makes
    "did anything change since the last export?" answerable without diffing an audit package."""

    id: str
    org_id: str
    incident_id: str
    status: InvestigationStatus = InvestigationStatus.OPEN
    priority: InvestigationPriority = InvestigationPriority.NORMAL
    #: Copied from M9.20 **at open time**, for triage display only. Never recomputed here, and never
    #: written back: M9.20 owns severity, and a second mutable copy is how two systems come to
    #: disagree about how bad something was.
    incident_severity: IncidentSeverity = IncidentSeverity.INFO
    summary: str = ""
    opened_at: float = 0.0
    opened_by: str = ""
    last_activity_at: float = 0.0
    evidence_digest: str = ""
    evidence_count: int = 0  # a **count**, never money
    open_question_count: int = 0
    unavailable_evidence_count: int = 0
    conclusion_id: str = ""  # the effective conclusion, once one is recorded
    closed_at: float | None = None
    closed_by: str = ""
    closed_reason: str = ""
    version: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_INVESTIGATION_STATES

    @property
    def is_blocked(self) -> bool:
        return self.status is InvestigationStatus.AWAITING_EVIDENCE


# ── questions ───────────────────────────────────────────────────────────────────


class QuestionKind(StrEnum):
    """A closed vocabulary of the things an investigation is *for* answering.

    Closed rather than free text so that closure can be checked — "every required question has an
    outcome" is only enforceable if a question is a record with a status, not a sentence somebody
    typed. These are **records, not executable queries**: nothing in this milestone interprets the
    text, and there is no reasoning engine here."""

    WHY_DIVERGED = "why_diverged"
    WHICH_FACTS_AFFECTED = "which_facts_affected"
    FIRST_OBSERVED_BY = "first_observed_by"
    TRANSIENT_OR_PERSISTENT = "transient_or_persistent"
    REPAIR_REQUESTED = "repair_requested"
    REPAIR_EVIDENCE = "repair_evidence"
    KNOWN_AT_TIME = "known_at_time"
    OTHER = "other"


#: Questions every investigation must resolve before it can close. The rest are optional. #: These
#: three are the minimum an examiner asks: *why did the books disagree, which facts were affected,
#: and what proves it was fixed*. An investigation that closed without an outcome for any of them
#: would have recorded activity rather than an answer.
REQUIRED_QUESTIONS: tuple[QuestionKind, ...] = (
    QuestionKind.WHY_DIVERGED,
    QuestionKind.WHICH_FACTS_AFFECTED,
    QuestionKind.REPAIR_EVIDENCE,
)


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    #: Asked, pursued, and genuinely not answerable from available evidence. **A real outcome.**
    #: Without it, a required question with no retrievable evidence would leave the investigation
    #: unclosable forever or push somebody into writing an answer nothing supports.
    UNANSWERABLE = "unanswerable"


RESOLVED_QUESTION_STATES: frozenset[QuestionStatus] = frozenset(
    {QuestionStatus.ANSWERED, QuestionStatus.UNANSWERABLE}
)


@dataclass(frozen=True, slots=True)
class InvestigationQuestion:
    """One question put to an investigation, and what became of it."""

    id: str
    org_id: str
    investigation_id: str
    kind: QuestionKind
    text: str
    status: QuestionStatus = QuestionStatus.OPEN
    required: bool = False
    answer: str = ""
    evidence_ids: tuple[str, ...] = ()
    asked_at: float = 0.0
    asked_by: str = ""
    answered_at: float | None = None
    answered_by: str = ""
    version: int = 0

    @property
    def is_resolved(self) -> bool:
        return self.status in RESOLVED_QUESTION_STATES


# ── observations ────────────────────────────────────────────────────────────────


class ObservationKind(StrEnum):
    """**The distinction that stops a guess becoming a finding.**

    An `OBSERVED_FACT` states something the evidence shows and must cite evidence that supports it.
    A `HYPOTHESIS` states something somebody thinks might explain it and cites whatever prompted the
    thought. They are separate records with separate identities — an identity that includes the
    kind — so a hypothesis cannot be edited into a fact. Re-asserting the same wording as a fact
    appends a *second* observation, and the trail shows both, in order, with their authors."""

    OBSERVED_FACT = "observed_fact"
    HYPOTHESIS = "hypothesis"


class HypothesisStatus(StrEnum):
    """Only ever set on a `HYPOTHESIS`. `NOT_APPLICABLE` is what an observed fact carries."""

    NOT_APPLICABLE = "not_applicable"
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class InvestigationObservation:
    """One thing an investigator recorded. Append-only.

    A hypothesis being supported or rejected does not rewrite it — a `HypothesisVerdict` is
    appended naming the evidence that settled it, so "what did we believe, and what changed our
    mind" survives in the record instead of being replaced by the conclusion."""

    id: str
    org_id: str
    investigation_id: str
    kind: ObservationKind
    statement: str
    statement_digest: str = ""
    evidence_ids: tuple[str, ...] = ()
    hypothesis_status: HypothesisStatus = HypothesisStatus.NOT_APPLICABLE
    recorded_at: float = 0.0
    recorded_by: str = ""
    #: Set when a hypothesis was settled. The verdict is a *separate* appended row; these fields are
    #: the projection of it onto the observation so a list view need not join.
    settled_at: float | None = None
    settled_by: str = ""
    settling_evidence_ids: tuple[str, ...] = ()
    settled_reason: str = ""
    sequence: int = 0
    version: int = 0

    @property
    def is_fact(self) -> bool:
        return self.kind is ObservationKind.OBSERVED_FACT

    @property
    def is_settled(self) -> bool:
        return self.hypothesis_status in (HypothesisStatus.SUPPORTED, HypothesisStatus.REJECTED)


# ── conclusions ─────────────────────────────────────────────────────────────────


class ConclusionClassification(StrEnum):
    """What the investigation is willing to state.

    `INCONCLUSIVE` exists so that no investigator is ever forced to choose a root cause merely to
    close a case. It is a **first-class outcome**: an investigation that reached it and said so is a
    better financial record than one that guessed.

    `CONTRADICTS_INCIDENT_CLASSIFICATION` is the mandate's discrepancy. M9.20 owns root-cause
    classification; when the evidence disagrees with what the incident says, this milestone records
    the disagreement and addresses it to the owning workflow. **It does not change the incident** —
    there is no incident write path anywhere in M9.21."""

    CONFIRMED = "confirmed"
    INCONCLUSIVE = "inconclusive"
    CONTRADICTS_INCIDENT_CLASSIFICATION = "contradicts_incident_classification"


@dataclass(frozen=True, slots=True)
class InvestigationConclusion:
    """The investigation's statement, with the evidence behind it. Append-only.

    A conclusion is never edited. Revising one appends a new conclusion naming the one it
    `supersedes`, and both stay readable — which is the only way an auditor can see that the first
    answer was wrong and when it was corrected.

    `evidenced_root_cause` is filled only for a contradiction, and only ever *reported*. It sits
    beside `incident_root_cause` — what M9.20 said at the time — so the disagreement is legible
    without anyone reconciling the two by overwriting one."""

    id: str
    org_id: str
    investigation_id: str
    incident_id: str
    classification: ConclusionClassification
    statement: str
    statement_digest: str = ""
    evidence_ids: tuple[str, ...] = ()
    #: What M9.20 classified the incident as, read at the moment this conclusion was recorded. A
    #: historical copy for the record — never written back.
    incident_root_cause: RootCause = RootCause.UNKNOWN
    #: What this investigation's evidence indicates, when it differs. Reported to the owning
    #: workflow; never applied to the incident from here.
    evidenced_root_cause: RootCause | None = None
    supersedes: str = ""
    recorded_at: float = 0.0
    recorded_by: str = ""
    version: int = 0

    @property
    def is_confirmed(self) -> bool:
        return self.classification is ConclusionClassification.CONFIRMED

    @property
    def contradicts_incident(self) -> bool:
        return self.classification is ConclusionClassification.CONTRADICTS_INCIDENT_CLASSIFICATION


# ── the audit timeline ──────────────────────────────────────────────────────────


class InvestigationEventKind(StrEnum):
    """The audit vocabulary. **The record of the investigation, not a log of it.**

    An application log rotates, is sampled, and cannot be shown to be unedited. These rows are what
    an examiner reads to reconstruct who looked at a financial divergence, when, and on what basis.

    A rejected command produces none of these, for the reason every milestone since M9.19 has given:
    emitting an event for a refusal would make a history in which nothing happened
    indistinguishable from one in which something did."""

    INVESTIGATION_OPENED = "investigation_opened"
    INVESTIGATION_TRIAGED = "investigation_triaged"
    INVESTIGATION_STARTED = "investigation_started"
    QUESTION_RECORDED = "question_recorded"
    QUESTION_ANSWERED = "question_answered"
    QUESTION_UNANSWERABLE = "question_unanswerable"
    OBSERVATION_RECORDED = "observation_recorded"
    HYPOTHESIS_RECORDED = "hypothesis_recorded"
    HYPOTHESIS_SUPPORTED = "hypothesis_supported"
    HYPOTHESIS_REJECTED = "hypothesis_rejected"
    EVIDENCE_CAPTURED = "evidence_captured"
    EVIDENCE_MARKED_UNAVAILABLE = "evidence_marked_unavailable"
    EVIDENCE_REDACTED = "evidence_redacted"
    EVIDENCE_SUPERSEDED = "evidence_superseded"
    AWAITING_EVIDENCE = "awaiting_evidence"
    INVESTIGATION_RESUMED = "investigation_resumed"
    CONCLUSION_RECORDED = "conclusion_recorded"
    CLASSIFICATION_DISCREPANCY_RECORDED = "classification_discrepancy_recorded"
    AUDIT_PACKAGE_CREATED = "audit_package_created"
    INVESTIGATION_CLOSED = "investigation_closed"


@dataclass(frozen=True, slots=True)
class InvestigationEvent:
    """One immutable line of the investigation timeline, totally ordered per investigation."""

    id: str
    org_id: str
    investigation_id: str
    sequence: int
    kind: InvestigationEventKind
    at: float = 0.0
    actor_id: str = ""
    subject: str = ""  # the evidence / question / observation / conclusion id it concerns
    summary: str = ""
    evidence_ids: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)


# ── the audit package ───────────────────────────────────────────────────────────


class PackageCompleteness(StrEnum):
    """**Based on evidence availability, never on optimism.**

    A package is `COMPLETE` only when every referenced source was retrievable and every required
    question has an outcome. Anything else is `INCOMPLETE` and names its gaps — because a package
    that quietly dropped what it could not fetch would present as thorough exactly when it is least
    trustworthy."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class AuditPackage:
    """**An immutable, content-addressed export of one investigation at one moment.**

    Identity is derived from the canonical manifest, which gives the two properties the mandate
    asks for at once: exporting an unchanged investigation twice yields the same id and writes
    nothing, and any change to the investigation yields a different id — a *new* package beside the
    old one rather than a mutation of it. There is no update path on the package store.

    The manifest holds **references and digests**, not recomputed figures. No total is summed here,
    no impact is priced, and no amount is compared. `canonical_form` is the only serializer, so the
    digest is reproducible on any machine that reads the same rows."""

    id: str
    org_id: str
    investigation_id: str
    incident_id: str
    manifest_digest: str
    completeness: PackageCompleteness
    manifest: dict[str, object] = field(default_factory=dict)
    gaps: tuple[EvidenceGap, ...] = ()
    evidence_count: int = 0  # counts, never money
    observation_count: int = 0
    question_count: int = 0
    conclusion_count: int = 0
    timeline_length: int = 0
    created_at: float = 0.0
    created_by: str = ""

    @property
    def is_complete(self) -> bool:
        return self.completeness is PackageCompleteness.COMPLETE

    @property
    def blocking_gaps(self) -> tuple[EvidenceGap, ...]:
        return tuple(g for g in self.gaps if g.blocks_conclusion)


# ── read models ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CustodyLink:
    """One step of a chain of custody, as an auditor reads it."""

    evidence_id: str
    locator: str
    mode: CaptureMode
    availability: EvidenceAvailability
    content_digest: str
    captured_at: float
    captured_by: str
    previous_evidence_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CustodyChain:
    """Every capture of one source, oldest first. **The answer to "what changed, and when".**"""

    locator: str
    links: tuple[CustodyLink, ...] = ()

    @property
    def current(self) -> CustodyLink | None:
        return self.links[-1] if self.links else None

    @property
    def changed(self) -> bool:
        """Whether the representation ever differed between captures."""
        digests = {link.content_digest for link in self.links if link.content_digest}
        return len(digests) > 1


@dataclass(frozen=True, slots=True)
class EvidenceChainNode:
    """One node of the incident→evidence chain the investigation reconstructs."""

    kind: str  # 'incident' | 'finding' | 'evidence' | 'repair' | 'verification'
    identity: str
    label: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class EvidenceChainEdge:
    source: str
    target: str
    relation: str


@dataclass(frozen=True, slots=True)
class InvestigationEvidenceChain:
    """The chain from incident to verification, **assembled from references, never invented**.

    Every node names a fact some other milestone owns; every edge names why the two are connected.
    Nodes whose source could not be read are present and marked unavailable rather than dropped."""

    investigation_id: str
    incident_id: str
    nodes: tuple[EvidenceChainNode, ...] = ()
    edges: tuple[EvidenceChainEdge, ...] = ()
    relationships: tuple[RelationshipKind, ...] = ()

    @property
    def unavailable(self) -> tuple[EvidenceChainNode, ...]:
        return tuple(n for n in self.nodes if not n.available)


@dataclass(frozen=True, slots=True)
class ClosureAssessment:
    """Whether this investigation may close, and precisely what is outstanding if not.

    Returned by a query as well as consulted by the command, so an operator sees the blockers
    *before* pressing anything rather than receiving a refusal."""

    investigation_id: str
    may_close: bool
    unresolved_required_questions: tuple[QuestionKind, ...] = ()
    open_hypotheses: tuple[str, ...] = ()
    blocking_gaps: tuple[EvidenceGap, ...] = ()
    conclusion_id: str = ""
    conclusion_classification: ConclusionClassification | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InvestigationPosture:
    """Fleet-wide counts for an operator queue. **Counts only** — never a figure."""

    org_id: str
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)
    blocked: int = 0
    concluded_awaiting_closure: int = 0
    unavailable_evidence: int = 0
    packages_exported: int = 0
    incomplete_packages: int = 0


@dataclass(frozen=True, slots=True)
class InvestigationSearchQuery:
    """Server-side filters. Every field narrows a SQL predicate — nothing here is filtered in
    Python after loading, because at organizations × incidents × investigations an in-memory scan
    is an outage rather than a performance note."""

    org_id: str
    incident_id: str = ""
    status: InvestigationStatus | None = None
    priority: InvestigationPriority | None = None
    opened_by: str = ""
    opened_from: float | None = None
    opened_to: float | None = None
    blocked_only: bool = False
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class InvestigationSearchResult:
    investigations: tuple[Investigation, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = 50

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.investigations) < self.total
