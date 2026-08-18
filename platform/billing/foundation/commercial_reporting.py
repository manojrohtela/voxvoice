"""Billing Compliance & Regulatory Reporting domain (M9.22) — the pure kernel.

Nine milestones own financial facts. **None of them can tell a regulator what happened**, because a
regulatory report is not any one domain's view: it spans the Ledger, the Invoice, Tax, Payments,
Revenue Recognition, and the integrity record over all of them, for a period somebody outside
Billing defined, under a rule that changes on its own schedule.

> **What financial and operational facts must be reported, for which reporting period, under which
> reporting definition, from which authoritative sources, and what evidence supports the report?**

Under one constraint that shapes everything else:

> **A report is derived evidence. It is not financial truth.**

This domain may produce a `RegulatoryReport`. It may never produce a `LedgerEntry`, a
`TaxDetermination`, a `CommercialInvoice`, a `Payment` or a `RecognitionEntry` — and it does not
import a single repository that could write one.

## The four ideas the whole design turns on

**One. Every field names its authoritative source.** There is no `source = "computed"` escape.
`ReportFieldSpec` binds each field to a domain, a source type and a declared transformation drawn
from a closed set, so *where did this number come from* is answerable from the definition alone
rather than by reading service code.

**Two. Definitions are versioned, and a finalized report is bound to the version that produced
it.** A reporting rule that changes gets a new version; it never reinterprets a report somebody
already filed. `ReportDefinition` is immutable, and `definition_version` is inside the report's
identity.

**Three. Inputs are pinned, and identity follows them.** The input manifest records every source
this run read *at the revision it read it*, and the report id is derived from the manifest digest.
Same immutable inputs, same report. A source moving afterwards produces a **different** report
beside the old one — never a silent rewrite of what was filed.

**Four. Missing evidence can never become zero.** A source that could not be read is a
`SourceStatus.UNAVAILABLE` row and a `ReportException`, not an absent line that a sum treats as
nought. `payments = 0` and *we could not reach Payments* are different statements about a
regulated business, and only one of them is honest.

## Where financial calculation would have gone

Almost nowhere. `ReportTransformation` is a closed set of seven, exactly one of which touches
money — `SUM_SAME_CURRENCY`, which refuses a mixed-currency input rather than converting it. There
is no rate, no FX table, no `Money.times`, no `Money.allocate`, no balance folding, no tax
arithmetic and no allocation. `Money` *is* imported here, unlike M9.20 and M9.21, because a report
legitimately carries an authoritative figure somebody else decided — but it is carried, never
computed.

## Currency

Never combined implicitly. A definition declares a `CurrencyBasis`; sources that do not satisfy it
raise `UNSUPPORTED_CURRENCY` and the report says `MULTI_CURRENCY_UNSUPPORTED`. **There is no
conversion anywhere in this milestone** — not from a provider, not from a table, not from a
constant. Currency conversion is a rule nobody has approved here, and inventing one would put a
number in front of a regulator that no domain owns.

## What is reused rather than re-declared

`BillingPeriod` (M9.2), `Money` and `Currency` (M9.2), `EvidenceDomain` and `EvidenceSourceType`
(M9.21), and — most importantly — **M9.21's single canonicalizer**. `canonical_form` is the one
serialization in this repository; a second one here would mean the same evidence had two
identities, which is the failure M9.21 wrote its own rule against.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .commercial_investigations import (
    EvidenceDomain,
    EvidenceSourceType,
    canonical_digest,
    canonical_instant,
)
from .domain import BillingPeriod
from .money import Currency, Money

_FS = "\x1f"


def _digest(*parts: str) -> str:
    return hashlib.sha256(_FS.join(parts).encode("utf-8")).hexdigest()


# ── identities ──────────────────────────────────────────────────────────────────
#
# Every identity below is derived and **org-first**. Derived, so a retry, a restart or a second
# worker converges on one row rather than filing a duplicate. Org-first, so two organizations
# running the same report definition over the same period with byte-identical sources still cannot
# compute a colliding identity — cross-tenant isolation becomes a property of the key.


def derive_definition_id(org_id: str, report_code: str, version: int) -> str:
    """A definition is `(code, version)`. **Publishing the same version twice is the same row**,
    which is what stops a reporting rule being edited under a report that already used it."""
    return "rdf_" + _digest(org_id, report_code, str(version))[:32]


def derive_period_id(org_id: str, basis: str, period_key: str) -> str:
    """`basis` is inside the identity because a calendar month is **not** an accounting period is
    not a tax period is not a recognition period. Two definitions consuming "2026-03" under
    different bases must not collide onto one row."""
    return "rpd_" + _digest(org_id, basis, period_key)[:32]


def derive_manifest_id(
    org_id: str, definition_id: str, period_id: str, scope_key: str, source_digest: str
) -> str:
    return "rmn_" + _digest(org_id, definition_id, period_id, scope_key, source_digest)[:32]


def derive_report_id(
    org_id: str,
    definition_id: str,
    period_id: str,
    scope_key: str,
    manifest_digest: str,
    corrects: str = "",
) -> str:
    """**The pinning guarantee, as an identity.**

    Same definition, same period, same inputs → the same report, so a retry files nothing twice.
    Any input moving produces a different id, so the corrected figures land *beside* what was
    filed rather than on top of it.

    `scope_key` is the account a filing covers, or `ORG_SCOPE` for one that covers the whole
    organization. It is part of the identity because two accounts filing the same report for the
    same period are two filings, not one.

    `corrects` distinguishes a correction from the original it supersedes. A correction whose
    manifest is unchanged computes the id of a report that already exists — which is why
    `ReportGuardService` refuses it rather than letting a no-op look like a new filing."""
    return (
        "rpt_"
        + _digest(org_id, definition_id, period_id, scope_key, manifest_digest, corrects)[:32]
    )


def derive_report_snapshot_id(report_id: str, report_digest: str) -> str:
    return "rsn_" + _digest(report_id, report_digest)[:32]


def derive_exception_id(report_id: str, kind: str, subject: str) -> str:
    """Derived from `(report, kind, subject)`, so the same problem observed twice is recorded
    once. An exception list that double-counted would misstate how bad a filing is."""
    return "rxc_" + _digest(report_id, kind, subject)[:32]


def derive_submission_id(org_id: str, report_id: str, report_digest: str) -> str:
    return "sbp_" + _digest(org_id, report_id, report_digest)[:32]


def derive_report_event_id(report_id: str, kind: str, subject: str, sequence: int) -> str:
    return "rev_" + _digest(report_id, kind, subject, str(sequence))[:32]


#: The scope key for a filing that covers the whole organization rather than one account.
#:
#: **Not cosmetic.** Every period-scoped read Billing offers — the Ledger's journal entries, the
#: Invoice's documents — is keyed by `(org, account, period)`, because an accounting period is a
#: property of an *account*. A filing that names an account can therefore be assembled from
#: authoritative period reads; one that does not must either aggregate across accounts (which no
#: domain here is willing to do on a regulator's behalf) or report the gap. This milestone reports
#: the gap. See `ReportScope` and the limitation recorded in the architecture document.
ORG_SCOPE = "__org__"


class ReportScope(StrEnum):
    """Whether a filing covers one account or the whole organization."""

    ACCOUNT = "account"
    ORGANIZATION = "organization"


# ── reporting periods ───────────────────────────────────────────────────────────


class PeriodBasis(StrEnum):
    """**Which existing period concept a definition consumes.**

    Declared rather than assumed, because Billing already owns four of these and they are not the
    same thing. An accounting period closes when somebody closes it; a calendar month closes on the
    calendar; a recognition period follows a policy's granularity; a tax period follows a
    jurisdiction's rules. A report that silently used one where the regulator meant another would
    be wrong in a way nobody notices until an audit.

    `CALENDAR_MONTH` and `CALENDAR_QUARTER` are M9.22's own — a window over wall-clock time with no
    Billing state behind it. The other three **reference** the owning domain's concept, and a
    definition consuming one of them must name the source it is aligned to."""

    CALENDAR_MONTH = "calendar_month"
    CALENDAR_QUARTER = "calendar_quarter"
    CALENDAR_YEAR = "calendar_year"
    ACCOUNTING_PERIOD = "accounting_period"  # M9.13's close, per account
    RECOGNITION_PERIOD = "recognition_period"  # M9.18's granularity
    TAX_PERIOD = "tax_period"  # M9.15's period key


class ReportingPeriodState(StrEnum):
    """A reporting period's own state. **Not the accounting period's** — a reporting period may be
    closed for filing while M9.13's ledger period is still open, and the reverse."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ReportingPeriod:
    """One window a report is filed for, under one basis.

    `window` is M9.2's `BillingPeriod`, reused unchanged (§A2.5) rather than a fifth declaration of
    "a half-open range of seconds". `period_key` is the human-quotable label a regulator uses;
    `basis` says what kind of period it is, and the two together are the identity."""

    id: str
    org_id: str
    basis: PeriodBasis
    period_key: str  # e.g. "2026-03", "2026-Q1", "FY2026"
    window: BillingPeriod
    state: ReportingPeriodState = ReportingPeriodState.OPEN
    #: For a basis that references another domain's period, the identity of that period. Empty for
    #: a calendar basis, which has no Billing state behind it.
    aligned_source_id: str = ""
    opened_at: float = 0.0
    closed_at: float | None = None
    closed_by: str = ""
    version: int = 0

    @property
    def is_closed(self) -> bool:
        return self.state is ReportingPeriodState.CLOSED


# ── report definitions ──────────────────────────────────────────────────────────


class ReportTransformation(StrEnum):
    """**The closed set of things a report field may do to its sources.**

    Closed, and small, because §5 forbids regulatory formulas scattered through service code and
    §13 forbids this milestone becoming a second Rating, Tax, Ledger or Recognition engine. A
    transformation that is not in this list cannot be expressed, which means a new reporting rule
    has to be *added here and versioned* rather than written inline somewhere.

    Exactly one member touches money. `SUM_SAME_CURRENCY` adds integer minor units of facts that
    already share a currency and **refuses a mixed-currency input** rather than converting it.
    There is no `SUM_CONVERTED`, no `AVERAGE`, no `ALLOCATE`, no `APPLY_RATE` and no `NET_OF` —
    each of those would be a financial rule this domain does not own."""

    COPY = "copy"  # the source's own value, verbatim
    COUNT = "count"  # how many source facts matched
    DISTINCT_COUNT = "distinct_count"
    SUM_SAME_CURRENCY = "sum_same_currency"  # the only member that touches money
    EARLIEST = "earliest"  # earliest timestamp among the sources
    LATEST = "latest"  # latest timestamp among the sources
    CLASSIFY = "classify"  # map a source state onto a declared report classification


#: The transformations that produce a monetary value. Named as a set so "which of these can touch
#: money?" is answerable by membership rather than by reading seven branches.
MONETARY_TRANSFORMATIONS: frozenset[ReportTransformation] = frozenset(
    {ReportTransformation.SUM_SAME_CURRENCY}
)


class CurrencyBasis(StrEnum):
    """How a definition handles currency. **There is no conversion member, deliberately.**

    `SINGLE_DECLARED` means the definition names one currency and any source in another raises
    `UNSUPPORTED_CURRENCY`. `SOURCE_NATIVE` means figures are reported in the currency the source
    decided, never combined across currencies. `NOT_APPLICABLE` is for definitions with no
    monetary field at all.

    A `CONVERTED` member would require an FX rate, and every source of one — a provider, a table, a
    constant — would put a number in front of a regulator that no Billing domain owns."""

    NOT_APPLICABLE = "not_applicable"
    SINGLE_DECLARED = "single_declared"
    SOURCE_NATIVE = "source_native"


class ReportFrequency(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class ReportFieldSpec:
    """One field of a report, **bound to the source that answers it**.

    There is no `source = "computed"` here and no free-text source. `domain` and `source_type` name
    an owning milestone's own artifact, `transformation` is drawn from the closed set above, and
    `mandatory` decides whether the field's absence blocks finalization.

    `classification_map` applies only to `CLASSIFY` and is part of the *definition*, so mapping a
    payment state onto a regulator's vocabulary is versioned evidence rather than a branch in a
    service."""

    key: str
    label: str
    domain: EvidenceDomain
    source_type: EvidenceSourceType
    transformation: ReportTransformation = ReportTransformation.COPY
    mandatory: bool = True
    #: The attribute read from each source fact. Named in the definition so a field's provenance is
    #: legible without reading the reader that fetches it.
    source_attribute: str = ""
    classification_map: tuple[tuple[str, str], ...] = ()
    note: str = ""

    @property
    def is_monetary(self) -> bool:
        return self.transformation in MONETARY_TRANSFORMATIONS


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    """**What a report means, at one version.** Immutable once published.

    A reporting rule that changes produces a new version; the old one keeps producing exactly what
    it always produced, because a finalized report names the version that made it and no query
    reaches for `latest()`. That is the difference between a filing you can defend and one that
    quietly changed meaning after submission.

    `required_sources` is the evidence contract: a domain named here must be readable, or the
    report cannot reach `FINALIZED`."""

    id: str
    org_id: str
    report_code: str
    version: int
    title: str
    jurisdiction: str  # free-form scope label, e.g. "IN", "IN-KA", "global"
    frequency: ReportFrequency
    period_basis: PeriodBasis
    currency_basis: CurrencyBasis = CurrencyBasis.NOT_APPLICABLE
    declared_currency: Currency | None = None  # set iff SINGLE_DECLARED
    fields: tuple[ReportFieldSpec, ...] = ()
    required_sources: tuple[EvidenceDomain, ...] = ()
    #: Whether an open M9.19 finding or M9.20 incident touching this period blocks finalization.
    #: A definition decides this: some filings must state that integrity is disputed, others must
    #: not be filed at all while it is.
    integrity_blocks_finalization: bool = False
    published_at: float = 0.0
    published_by: str = ""
    definition_digest: str = ""

    @property
    def monetary_fields(self) -> tuple[ReportFieldSpec, ...]:
        return tuple(f for f in self.fields if f.is_monetary)

    @property
    def mandatory_fields(self) -> tuple[ReportFieldSpec, ...]:
        return tuple(f for f in self.fields if f.mandatory)


# ── the input manifest ──────────────────────────────────────────────────────────


class SourceStatus(StrEnum):
    """What could be said about a source **at the moment the report read it**.

    `UNAVAILABLE` is a first-class outcome and never rounds to an empty result set. The difference
    between *Payments returned no rows for this period* and *we could not reach Payments* is the
    difference between reporting zero and reporting nothing, and only the first is a fact."""

    RESOLVED = "resolved"  # read, and it returned facts
    EMPTY = "empty"  # read, and it genuinely had nothing for this period
    UNAVAILABLE = "unavailable"  # could not be read — never treated as zero
    NOT_REQUIRED = "not_required"  # this definition does not consume the domain


#: Statuses a mandatory source may hold and still permit finalization. `EMPTY` counts: a period in
#: which nothing happened is a legitimate filing. `UNAVAILABLE` never does.
SATISFYING_SOURCE_STATUS: frozenset[SourceStatus] = frozenset(
    {SourceStatus.RESOLVED, SourceStatus.EMPTY}
)


@dataclass(frozen=True, slots=True)
class ReportSourceRef:
    """**One pinned source, at the revision this run read it.**

    `revision` and `digest` are what make a report reproducible: an examiner re-reading the source
    can tell whether it has moved since, and the report still says what it said. Nothing here is
    a copy of the source's content — this is a reference plus an attestation.

    A `Money` value never appears on this shape. Amounts belong to the report's *fields*, where a
    declared transformation put them; a source reference is provenance."""

    domain: EvidenceDomain
    source_type: EvidenceSourceType
    source_id: str
    status: SourceStatus = SourceStatus.RESOLVED
    revision: str = ""
    digest: str = ""
    resolved_at: float = 0.0
    fact_count: int = 0  # a **count**, never money
    unavailable_reason: str = ""

    @property
    def locator(self) -> str:
        base = f"{self.domain.value}:{self.source_type.value}:{self.source_id}"
        return f"{base}@{self.revision}" if self.revision else base

    @property
    def satisfies(self) -> bool:
        return self.status in SATISFYING_SOURCE_STATUS


@dataclass(frozen=True, slots=True)
class ReportInputManifest:
    """**Immutable. The pinned inputs one report run read, and the identity of the report.**

    `source_digest` is a canonical digest over every reference below, so the report id derived from
    it changes the instant any input does. That is what makes "never overwrite a previous report
    because a source changed" structural rather than a rule somebody has to remember."""

    id: str
    org_id: str
    definition_id: str
    definition_version: int
    period_id: str
    account_id: str = ""  # empty ⇒ organization-wide
    sources: tuple[ReportSourceRef, ...] = ()
    source_digest: str = ""
    query_scope: dict[str, str] = field(default_factory=dict)
    generated_at: float = 0.0
    generator_version: str = ""

    @property
    def unavailable(self) -> tuple[ReportSourceRef, ...]:
        return tuple(s for s in self.sources if s.status is SourceStatus.UNAVAILABLE)


# ── report values ───────────────────────────────────────────────────────────────


class ValueStatus(StrEnum):
    """Whether a field actually has a value, and if not, why. **Never silently absent.**"""

    PRESENT = "present"
    SOURCE_UNAVAILABLE = "source_unavailable"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"  # e.g. multi-currency with no approved conversion rule


@dataclass(frozen=True, slots=True)
class ReportFieldValue:
    """One computed field, carrying **which sources produced it**.

    `amount` is present only for a monetary transformation and is the authoritative figure the
    owning domain wrote — summed, at most, across facts that already share a currency. `text` and
    `number` cover everything else.

    A field whose source was unreadable is `SOURCE_UNAVAILABLE` with **no value at all** — not
    zero, not an empty string. Rendering it as zero is the single most damaging thing this
    milestone could do."""

    key: str
    status: ValueStatus
    transformation: ReportTransformation
    amount: Money | None = None
    number: int | None = None
    text: str = ""
    currency: Currency | None = None
    source_locators: tuple[str, ...] = ()
    detail: str = ""

    @property
    def is_present(self) -> bool:
        return self.status is ValueStatus.PRESENT


# ── completeness ────────────────────────────────────────────────────────────────


class ReportCompleteness(StrEnum):
    """**Decided on source availability, never on optimism.**

    `BLOCKED` is distinct from `INCOMPLETE` on purpose: incomplete means something optional is
    missing and the filing can proceed if the definition allows it; blocked means a *mandatory*
    source could not be read, and no amount of operator confidence changes that."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class EvidenceCompleteness(StrEnum):
    """Whether every source the definition requires was actually resolved.

    Separate from `ReportCompleteness` because a report can be complete in its *fields* while
    missing evidence for one of them — and a regulator asks about both."""

    EVIDENCE_COMPLETE = "evidence_complete"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"


class ReportedIntegrity(StrEnum):
    """What the integrity record says about this period. **`INDETERMINATE` is a real answer.**

    The mandate's §19 refusal, as a type: *do not infer that no report data means no integrity
    issue*. If M9.19 or M9.20 could not be read, the report says so rather than reporting a clean
    bill of health it has no basis for."""

    NO_FINDING = "no_finding"
    OPEN_FINDING = "open_finding"
    OPEN_INCIDENT = "open_incident"
    RESOLVED_INCIDENT = "resolved_incident"
    CLOSED_INCIDENT = "closed_incident"
    INDETERMINATE = "indeterminate"


#: Postures describing an unsettled integrity problem. A definition with
#: `integrity_blocks_finalization` refuses to file while the report holds one of these.
UNSETTLED_INTEGRITY: frozenset[ReportedIntegrity] = frozenset(
    {
        ReportedIntegrity.OPEN_FINDING,
        ReportedIntegrity.OPEN_INCIDENT,
        ReportedIntegrity.INDETERMINATE,
    }
)


# ── exceptions ──────────────────────────────────────────────────────────────────


class ReportExceptionKind(StrEnum):
    """**A named diagnosis, never a generic "report failed".**

    Each member says what an operator must go and do. A single `FAILED` would leave a compliance
    officer with a red badge and no next step, which is how a filing deadline is missed."""

    MISSING_SOURCE = "missing_source"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    SOURCE_PERIOD_MISMATCH = "source_period_mismatch"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    MULTI_CURRENCY_UNSUPPORTED = "multi_currency_unsupported"
    MISSING_TAX_EVIDENCE = "missing_tax_evidence"
    MISSING_RECOGNITION_EVIDENCE = "missing_recognition_evidence"
    MISSING_PAYMENT_EVIDENCE = "missing_payment_evidence"
    AMBIGUOUS_PAYMENT_STATE = "ambiguous_payment_state"
    OPEN_INTEGRITY_INCIDENT = "open_integrity_incident"
    INDETERMINATE_INTEGRITY = "indeterminate_integrity"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    MANDATORY_FIELD_MISSING = "mandatory_field_missing"
    UNSUPPORTED_REPORT_DEFINITION = "unsupported_report_definition"
    PERIOD_NOT_CLOSED = "period_not_closed"


class ExceptionSeverity(StrEnum):
    """`BLOCKING` prevents finalization. `ADVISORY` is recorded and filed alongside."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


#: Which severity each exception kind carries. A table rather than a branch, so "what stops a
#: filing?" is answerable by reading one mapping instead of tracing the validator.
SEVERITY_BY_EXCEPTION: dict[ReportExceptionKind, ExceptionSeverity] = {
    ReportExceptionKind.MISSING_SOURCE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.SOURCE_UNAVAILABLE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.SOURCE_VERSION_MISMATCH: ExceptionSeverity.ADVISORY,
    ReportExceptionKind.SOURCE_PERIOD_MISMATCH: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.UNSUPPORTED_CURRENCY: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.MULTI_CURRENCY_UNSUPPORTED: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.MISSING_TAX_EVIDENCE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.MISSING_RECOGNITION_EVIDENCE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.MISSING_PAYMENT_EVIDENCE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.AMBIGUOUS_PAYMENT_STATE: ExceptionSeverity.ADVISORY,
    ReportExceptionKind.OPEN_INTEGRITY_INCIDENT: ExceptionSeverity.ADVISORY,
    ReportExceptionKind.INDETERMINATE_INTEGRITY: ExceptionSeverity.ADVISORY,
    ReportExceptionKind.INCOMPLETE_EVIDENCE: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.MANDATORY_FIELD_MISSING: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.UNSUPPORTED_REPORT_DEFINITION: ExceptionSeverity.BLOCKING,
    ReportExceptionKind.PERIOD_NOT_CLOSED: ExceptionSeverity.BLOCKING,
}


class ExceptionState(StrEnum):
    """An exception is **never silently cleared**. It is acknowledged, or it is superseded by a
    later run that no longer observes it — and both leave a record."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ReportException:
    """One named problem with one report. Immutable except for its acknowledgement."""

    id: str
    org_id: str
    report_id: str
    kind: ReportExceptionKind
    severity: ExceptionSeverity
    subject: str  # the field key or source locator it concerns
    diagnostic: str
    state: ExceptionState = ExceptionState.OPEN
    field_key: str = ""
    source_locator: str = ""
    created_at: float = 0.0
    acknowledged_at: float | None = None
    acknowledged_by: str = ""
    acknowledgement_reason: str = ""
    version: int = 0

    @property
    def blocks_finalization(self) -> bool:
        """**An acknowledgement does not unblock a filing.**

        It records that somebody has seen the problem, which is a different fact from the problem
        being gone. Only a re-prepared run that no longer observes it clears the path."""
        return self.severity is ExceptionSeverity.BLOCKING


# ── validation ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One check the definition demanded, and what it found."""

    name: str
    passed: bool
    detail: str = ""
    subject: str = ""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """**Validation is not reconciliation.**

    Every check here is about the *report*: are mandatory fields populated, did required sources
    resolve, do source periods match the reporting period, is the currency basis satisfied. None of
    them compares two financial domains against each other — that is M9.19's question, and this
    milestone *references* its answer rather than computing a second one."""

    report_id: str
    org_id: str
    passed: bool
    checks: tuple[ValidationCheck, ...] = ()
    validated_at: float = 0.0
    validated_by: str = ""
    definition_version: int = 0

    @property
    def failures(self) -> tuple[ValidationCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


# ── the report ──────────────────────────────────────────────────────────────────


class ReportStatus(StrEnum):
    """Six states. `FINALIZED` is terminal; a correction is a **new report**, never an edit."""

    DRAFT = "draft"
    PREPARING = "preparing"
    VALIDATING = "validating"
    EXCEPTIONS_RAISED = "exceptions_raised"
    READY = "ready"
    FINALIZED = "finalized"


class ReportTrigger(StrEnum):
    PREPARE = "prepare"
    COMPLETE_PREPARATION = "complete_preparation"
    PASS_VALIDATION = "pass_validation"
    FAIL_VALIDATION = "fail_validation"
    FINALIZE = "finalize"


#: **The lifecycle, and its two deliberate absences.**
#:
#: There is no edge from `DRAFT`, `PREPARING`, `VALIDATING` or `EXCEPTIONS_RAISED` to `FINALIZED`.
#: The only route runs through `READY`, which is reachable only from a validation that passed —
#: which is the structural form of §22: *a report that cannot satisfy mandatory evidence
#: requirements must not reach FINALIZED*. No operator confidence, no override flag and no retry
#: reaches the terminal state around validation.
#:
#: And `FINALIZED` has no outgoing edge. A filed report is never edited, never re-validated and
#: never re-prepared; a correction opens a new report with its own pinned inputs, and both stay
#: readable so an examiner can see what changed between versions.
REPORT_LIFECYCLE: StateMachineDefinition = define(
    [
        (ReportStatus.DRAFT, ReportTrigger.PREPARE, ReportStatus.PREPARING),
        (
            ReportStatus.PREPARING,
            ReportTrigger.COMPLETE_PREPARATION,
            ReportStatus.VALIDATING,
        ),
        (ReportStatus.VALIDATING, ReportTrigger.PASS_VALIDATION, ReportStatus.READY),
        (
            ReportStatus.VALIDATING,
            ReportTrigger.FAIL_VALIDATION,
            ReportStatus.EXCEPTIONS_RAISED,
        ),
        # Re-preparation after the sources were fixed. Going back is honest; staying in
        # EXCEPTIONS_RAISED with a green badge would not be.
        (ReportStatus.EXCEPTIONS_RAISED, ReportTrigger.PREPARE, ReportStatus.PREPARING),
        # A ready report whose inputs moved must be re-prepared rather than filed stale.
        (ReportStatus.READY, ReportTrigger.PREPARE, ReportStatus.PREPARING),
        (ReportStatus.READY, ReportTrigger.FINALIZE, ReportStatus.FINALIZED),
    ]
)

TERMINAL_REPORT_STATES: frozenset[ReportStatus] = frozenset({ReportStatus.FINALIZED})

LIVE_REPORT_STATES: frozenset[ReportStatus] = frozenset(
    set(ReportStatus) - TERMINAL_REPORT_STATES
)


@dataclass(frozen=True, slots=True)
class RegulatoryReport:
    """**One report run: one definition version, one period, one set of pinned inputs.**

    Carries no financial authority. Every figure on it arrived from the domain that decided it,
    through a declared transformation, and is reproducible from `manifest_id` — which pins each
    source at the revision this run read.

    `corrects` names the report this one supersedes, and both survive. A correction is a new
    filing beside the old one, because rewriting what was submitted is not a correction, it is a
    disappearance."""

    id: str
    org_id: str
    definition_id: str
    definition_version: int
    report_code: str
    period_id: str
    manifest_id: str
    manifest_digest: str
    #: The account this filing covers. Empty means organization-wide — see `ORG_SCOPE`.
    account_id: str = ""
    status: ReportStatus = ReportStatus.DRAFT
    completeness: ReportCompleteness = ReportCompleteness.INCOMPLETE
    evidence_completeness: EvidenceCompleteness = EvidenceCompleteness.EVIDENCE_INCOMPLETE
    integrity_posture: ReportedIntegrity = ReportedIntegrity.INDETERMINATE
    values: tuple[ReportFieldValue, ...] = ()
    report_digest: str = ""
    snapshot_id: str = ""
    exception_count: int = 0  # a **count**, never money
    blocking_exception_count: int = 0
    source_count: int = 0
    corrects: str = ""  # the report this one supersedes
    corrected_by: str = ""  # set when a later correction supersedes this one
    created_at: float = 0.0
    created_by: str = ""
    prepared_at: float | None = None
    validated_at: float | None = None
    finalized_at: float | None = None
    finalized_by: str = ""
    version: int = 0

    @property
    def scope_key(self) -> str:
        return self.account_id or ORG_SCOPE

    @property
    def scope(self) -> ReportScope:
        return ReportScope.ACCOUNT if self.account_id else ReportScope.ORGANIZATION

    @property
    def is_finalized(self) -> bool:
        return self.status is ReportStatus.FINALIZED

    @property
    def is_correction(self) -> bool:
        return bool(self.corrects)

    @property
    def may_finalize(self) -> bool:
        """A projection for display. **Not the authority** — `ReportGuardService` decides, on
        freshly-read rows, inside the transaction."""
        return (
            self.status is ReportStatus.READY
            and self.blocking_exception_count == 0
            and self.completeness is not ReportCompleteness.BLOCKED
        )


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """**The finalized report, content-addressed and immutable.**

    Taken at finalization and never touched again. `report_digest` is a canonical digest over the
    representation below, so an examiner holding the filing can recompute it and see that the bytes
    were not edited afterwards — which is what makes a submission defensible rather than merely
    archived.

    Note what the snapshot pins: the definition **version**, the manifest **id and digest**, and
    the values as computed. Nothing here reaches for current state, so a source moving next week
    changes nothing about what this says."""

    id: str
    org_id: str
    report_id: str
    definition_id: str
    definition_version: int
    manifest_id: str
    manifest_digest: str
    report_digest: str
    representation: dict[str, object] = field(default_factory=dict)
    completeness: ReportCompleteness = ReportCompleteness.COMPLETE
    evidence_completeness: EvidenceCompleteness = (
        EvidenceCompleteness.EVIDENCE_COMPLETE
    )
    validation_passed: bool = True
    finalized_at: float = 0.0
    finalized_by: str = ""


# ── submission ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubmissionPackage:
    """An immutable bundle prepared **for** external submission. Nothing here submits anything.

    There is no network client, no regulator API, no endpoint and no credential in this milestone —
    §24 defers submission execution to a future adapter, and a package that could send itself would
    make that boundary a comment rather than a fact.

    Content-addressed on the report digest, so preparing the same finalized report twice yields one
    package."""

    id: str
    org_id: str
    report_id: str
    snapshot_id: str
    definition_id: str
    definition_version: int
    report_digest: str
    manifest_digest: str
    package_digest: str
    completeness: ReportCompleteness = ReportCompleteness.COMPLETE
    evidence_completeness: EvidenceCompleteness = EvidenceCompleteness.EVIDENCE_COMPLETE
    validation_passed: bool = True
    artifact: dict[str, object] = field(default_factory=dict)
    prepared_at: float = 0.0
    prepared_by: str = ""
    #: Where a future adapter would record a transmission. Empty here, always — and a test asserts
    #: that nothing in this milestone can write it.
    submitted_at: float | None = None
    submission_reference: str = ""


# ── the audit trail ─────────────────────────────────────────────────────────────


class ReportEventKind(StrEnum):
    """The reporting audit vocabulary. **The record, not a log of it.**

    A rejected command produces none of these, for the reason every milestone since M9.19 has
    given: a history in which nothing happened must not be indistinguishable from one in which
    something did."""

    DEFINITION_PUBLISHED = "definition_published"
    PERIOD_OPENED = "period_opened"
    PERIOD_CLOSED = "period_closed"
    REPORT_CREATED = "report_created"
    PREPARATION_STARTED = "preparation_started"
    SOURCES_RESOLVED = "sources_resolved"
    SOURCE_UNAVAILABLE = "source_unavailable"
    VALUES_COMPUTED = "values_computed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    EXCEPTION_RAISED = "exception_raised"
    EXCEPTION_ACKNOWLEDGED = "exception_acknowledged"
    REPORT_FINALIZED = "report_finalized"
    CORRECTION_OPENED = "correction_opened"
    SUBMISSION_PACKAGE_CREATED = "submission_package_created"


@dataclass(frozen=True, slots=True)
class ReportEvent:
    """One immutable line of a report's trail, totally ordered per report."""

    id: str
    org_id: str
    report_id: str
    sequence: int
    kind: ReportEventKind
    at: float = 0.0
    actor_id: str = ""
    subject: str = ""
    summary: str = ""
    detail: dict[str, str] = field(default_factory=dict)


# ── read models ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceTrace:
    """Where one report field came from, as an examiner reads it."""

    field_key: str
    label: str
    domain: str
    source_type: str
    transformation: str
    status: str
    locators: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReportProvenance:
    """**The §25 answer, assembled from stored rows alone.**

    Which definition, which version, which period, which sources at which revisions, what was
    available, what was not, what validation ran, who finalized it and when. Nothing here reads
    current source state — every field comes from the pinned manifest and the snapshot."""

    report_id: str
    report_code: str
    definition_id: str
    definition_version: int
    period_key: str
    period_basis: str
    manifest_id: str
    manifest_digest: str
    report_digest: str
    traces: tuple[SourceTrace, ...] = ()
    unavailable_sources: tuple[str, ...] = ()
    validation_passed: bool = False
    exception_kinds: tuple[str, ...] = ()
    finalized_at: float | None = None
    finalized_by: str = ""
    corrects: str = ""
    corrected_by: str = ""


@dataclass(frozen=True, slots=True)
class ReportDiff:
    """What changed between two versions of one filing. **The §25 question nobody else answers.**"""

    earlier_report_id: str
    later_report_id: str
    changed_fields: tuple[str, ...] = ()
    added_fields: tuple[str, ...] = ()
    removed_fields: tuple[str, ...] = ()
    changed_sources: tuple[str, ...] = ()
    definition_version_changed: bool = False


@dataclass(frozen=True, slots=True)
class ReportSearchQuery:
    """Server-side filters. Every field narrows a SQL predicate; nothing is filtered in Python."""

    org_id: str
    report_code: str = ""
    definition_version: int | None = None
    period_id: str = ""
    status: ReportStatus | None = None
    completeness: ReportCompleteness | None = None
    finalized_only: bool = False
    corrections_only: bool = False
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ReportSearchResult:
    reports: tuple[RegulatoryReport, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = 50

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.reports) < self.total


@dataclass(frozen=True, slots=True)
class ReportingPosture:
    """Fleet-wide counts for a compliance queue. **Counts only** — never a figure."""

    org_id: str
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_completeness: dict[str, int] = field(default_factory=dict)
    finalized: int = 0
    blocked: int = 0
    open_exceptions: int = 0
    blocking_exceptions: int = 0
    submission_packages: int = 0


def report_representation(
    report: RegulatoryReport,
    definition: ReportDefinition,
    period: ReportingPeriod,
    manifest: ReportInputManifest,
) -> dict[str, object]:
    """The canonical representation a snapshot is addressed by.

    Built from **pinned** state only: the definition version this run used, the period, the
    manifest's own digest, and the values as computed. It reaches for nothing current, which is why
    a snapshot taken today still says the same thing after every source has moved.

    Timestamps become integer microseconds via M9.21's `canonical_instant`, and no float reaches
    the digest — a digest that depended on `repr()` would be stable on the machine that filed and
    different on the machine that audits."""
    return {
        "report_code": definition.report_code,
        "definition_id": definition.id,
        "definition_version": definition.version,
        "definition_digest": definition.definition_digest,
        "jurisdiction": definition.jurisdiction,
        "currency_basis": definition.currency_basis.value,
        "declared_currency": (
            None if definition.declared_currency is None else str(definition.declared_currency)
        ),
        "period": {
            "id": period.id,
            "basis": period.basis.value,
            "key": period.period_key,
            "start": canonical_instant(period.window.start),
            "end": canonical_instant(period.window.end),
            "state": period.state.value,
        },
        "manifest": {
            "id": manifest.id,
            "source_digest": manifest.source_digest,
            "generator_version": manifest.generator_version,
            "generated_at": canonical_instant(manifest.generated_at),
            "sources": [
                {
                    "locator": s.locator,
                    "domain": s.domain.value,
                    "source_type": s.source_type.value,
                    "source_id": s.source_id,
                    "revision": s.revision,
                    "digest": s.digest,
                    "status": s.status.value,
                    "fact_count": s.fact_count,
                    "unavailable_reason": s.unavailable_reason,
                }
                for s in sorted(manifest.sources, key=lambda s: s.locator)
            ],
        },
        "scope": report.scope.value,
        "account_id": report.account_id,
        "completeness": report.completeness.value,
        "evidence_completeness": report.evidence_completeness.value,
        "integrity_posture": report.integrity_posture.value,
        "corrects": report.corrects,
        "values": [
            {
                "key": v.key,
                "status": v.status.value,
                "transformation": v.transformation.value,
                # `None`, not `0`. A field whose source was unreadable has **no value**, and the
                # canonicalizer distinguishes null from absent so the two stay different facts.
                "amount_minor": None if v.amount is None else v.amount.minor,
                "currency": None if v.currency is None else str(v.currency),
                "number": v.number,
                "text": v.text,
                "source_locators": list(v.source_locators),
                "detail": v.detail,
            }
            for v in sorted(report.values, key=lambda v: v.key)
        ],
    }


def source_manifest_digest(sources: tuple[ReportSourceRef, ...]) -> str:
    """A canonical digest over the pinned sources. **The report's identity depends on this**, so a
    source moving to a new revision produces a different report rather than mutating one."""
    return canonical_digest(
        [
            {
                "locator": s.locator,
                "revision": s.revision,
                "digest": s.digest,
                "status": s.status.value,
                "fact_count": s.fact_count,
            }
            for s in sorted(sources, key=lambda s: s.locator)
        ]
    )


def definition_digest(definition: ReportDefinition) -> str:
    """A canonical digest over what a definition *means*, so two publications of one version can be
    proven identical rather than assumed to be."""
    return canonical_digest(
        {
            "report_code": definition.report_code,
            "version": definition.version,
            "jurisdiction": definition.jurisdiction,
            "frequency": definition.frequency.value,
            "period_basis": definition.period_basis.value,
            "currency_basis": definition.currency_basis.value,
            "declared_currency": (
                None if definition.declared_currency is None else str(definition.declared_currency)
            ),
            "integrity_blocks_finalization": definition.integrity_blocks_finalization,
            "required_sources": [d.value for d in definition.required_sources],
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "domain": f.domain.value,
                    "source_type": f.source_type.value,
                    "transformation": f.transformation.value,
                    "mandatory": f.mandatory,
                    "source_attribute": f.source_attribute,
                    "classification_map": [list(pair) for pair in f.classification_map],
                }
                for f in sorted(definition.fields, key=lambda f: f.key)
            ],
        }
    )
