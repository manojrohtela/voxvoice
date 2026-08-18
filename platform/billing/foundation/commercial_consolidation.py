"""Billing Period Scope & Consolidation (M9.23 domain) — scope, membership, and consolidation.

M9.22 shipped with a gap it named rather than hid: **every period-scoped read Billing publishes is
keyed by `(org, account, period)`**, because an accounting period is a property of an *account*. A
filing that covers a group of accounts therefore has no authoritative period read to consume, and
M9.22 refused to invent one.

This milestone is that gap's owner. It answers exactly one question:

> **Which accounts belong to a reporting scope, for which period, at which source revisions — and
> what could not be included, and why?**

Under one constraint that shapes every type below:

> **M9.23 owns scope. It does not own a single financial fact.**

A `ConsolidationRun` may aggregate integer minor units that M9.13 already decided. It may never
decide one. There is no invoice total recomputed here, no tax recalculated, no balance folded, no
recognition posted, no payment reinterpreted, no reconciliation performed, and no FX rate anywhere
at any altitude.

## What is consumed rather than redeclared

* **M9.8 owns the hierarchy.** `parent_account_id`, `payer_account_id`,
  `AccountHierarchyResolver` (ancestors, descendants, subtree, cycles, `MAX_HIERARCHY_DEPTH`)
  and `PayerResolver` already exist and are already correct. `ScopeResolutionService` *calls* them.
  There is no second walker in this milestone, and a test asserts it.
* **M9.22 owns period vocabulary.** `PeriodBasis` names which of Billing's period concepts a
  definition consumes, and it is imported unchanged. A sixth "kind of period" would be the exact
  duplication the mandate forbids.
* **M9.21 owns canonicalization.** `canonical_form` / `canonical_digest` are the single
  serialization in this repository. A second one here would mean the same consolidation had two
  digests.
* **M9.22 owns `SourceStatus` and `ReportTransformation`.** Reused, with
  `CONSOLIDATION_AGGREGATIONS` naming the subset a consolidation may use.

## The two identities, and why there are two

`ConsolidationRun.id` derives from the immutable **request**: organization, scope snapshot, period,
and the run this one corrects. Two workers asking for the same consolidation land on one row — a
duplicate consolidation is not merely rejected, it is *inexpressible*.

`ConsolidationSnapshot.id` derives from the finalized **artifact**: the run plus a digest over
every source revision that was pinned. Source revisions cannot be part of the run's identity
because they are not known until the run has read them — a run must exist in order to collect. So
the revisions bind where they can be known, and the rule the mandate is protecting still holds
end-to-end: a finalized consolidation is content-addressed on the exact revisions it consumed, and
a later consolidation over moved sources is a **correction with a different run id**, never an
overwrite. This is stated plainly rather than papered over; see the milestone report §10.
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
)
from .commercial_reporting import PeriodBasis, ReportTransformation, SourceStatus
from .domain import BillingPeriod
from .money import Currency, Money

# ── identity ────────────────────────────────────────────────────────────────────
#
# Every id below is derived from immutable inputs. None derives from wall-clock time, a random
# UUID, database row order, a `latest()` lookup, or a mutable status — the five sources the
# mandate names, each of which makes a "deterministic" identity a lie the first time a run is
# retried.

PREFIX_SCOPE_DEFINITION = "csd_"
PREFIX_SCOPE_SNAPSHOT = "csp_"
PREFIX_CONSOLIDATION = "con_"
PREFIX_CONSOLIDATION_PERIOD = "cpd_"
PREFIX_SOURCE_BINDING = "csb_"
PREFIX_CONSOLIDATION_EXCEPTION = "cex_"
PREFIX_CONSOLIDATION_SNAPSHOT = "cns_"
PREFIX_CONSOLIDATION_EVENT = "cev_"

_FS = "\x1f"


def _digest(*parts: str) -> str:
    return hashlib.sha256(_FS.join(parts).encode("utf-8")).hexdigest()


def derive_scope_definition_id(org_id: str, scope_code: str, version: int) -> str:
    """`(org, code, version)`. Publishing the same version twice lands on the same row, which is
    what stops a scope being edited under a consolidation that already used it."""
    return PREFIX_SCOPE_DEFINITION + _digest(org_id, scope_code, str(version))[:32]


def derive_scope_snapshot_id(
    org_id: str, scope_definition_id: str, scope_version: int, membership_digest: str
) -> str:
    """`(org, definition, version, membership digest)`.

    **Not the effective timestamp.** Resolving the same scope twice against an unchanged hierarchy
    produces one snapshot, not two — so a retry does not mint a second view of January."""
    return PREFIX_SCOPE_SNAPSHOT + _digest(
        org_id, scope_definition_id, str(scope_version), membership_digest
    )[:32]


def derive_consolidation_period_id(org_id: str, basis: str, period_key: str) -> str:
    """`(org, basis, period key)`. **The basis is inside the key**, so a tax period and an
    accounting period with the same label are different objects rather than one shared row."""
    return PREFIX_CONSOLIDATION_PERIOD + _digest(org_id, basis, period_key)[:32]


def derive_consolidation_id(
    org_id: str, scope_snapshot_id: str, period_id: str, corrects: str = ""
) -> str:
    """`(org, scope snapshot, period, corrects)` — the immutable request.

    The scope snapshot already pins *which accounts*, at *which revisions*, under *which
    hierarchy*. Two workers asking for the same consolidation therefore collide on the primary key
    rather than producing two runs over one period."""
    return PREFIX_CONSOLIDATION + _digest(org_id, scope_snapshot_id, period_id, corrects)[:32]


def derive_source_binding_id(
    consolidation_id: str, account_id: str, domain: str, artifact: str
) -> str:
    """One binding per `(run, account, domain, artifact)`. Collecting the same source twice —
    which two workers racing on one batch will do — writes one row."""
    return PREFIX_SOURCE_BINDING + _digest(consolidation_id, account_id, domain, artifact)[:32]


def derive_consolidation_exception_id(consolidation_id: str, kind: str, subject: str) -> str:
    """One exception per `(run, kind, subject)`. Re-collecting a still-unavailable source does not
    stack a second copy of the same problem."""
    return PREFIX_CONSOLIDATION_EXCEPTION + _digest(consolidation_id, kind, subject)[:32]


def derive_consolidation_snapshot_id(consolidation_id: str, consolidation_digest: str) -> str:
    """**Where the source revisions bind.** `consolidation_digest` covers every pinned revision,
    so the finalized artifact is content-addressed on exactly what it consumed."""
    return PREFIX_CONSOLIDATION_SNAPSHOT + _digest(consolidation_id, consolidation_digest)[:32]


def derive_consolidation_event_id(
    consolidation_id: str, kind: str, subject: str, sequence: int
) -> str:
    return PREFIX_CONSOLIDATION_EVENT + _digest(
        consolidation_id, kind, subject, str(sequence)
    )[:32]


# ── scope definition ────────────────────────────────────────────────────────────


class ScopeMode(StrEnum):
    """**How accounts enter a scope.** Five modes, each with proven semantics.

    The mandate forbids exposing a mode whose semantics cannot be proven, and forbids hidden
    fallback between modes. Both are structural here: every mode's inclusion rule, exclusion rule,
    attribution requirement, hierarchy requirement and currency requirement are declared in
    `SCOPE_RULES` below, and `ScopeResolutionService` dispatches on an exhaustive match with no
    default branch — an unhandled mode raises rather than silently resolving as `SINGLE_ACCOUNT`.

    `PAYER_GROUP` and `PARENT_GROUP` are **different modes on purpose**. M9.8 keeps the payer graph
    and the hierarchy graph separate precisely because a subsidiary can pay for its parent without
    being owned by it, and collapsing them here would re-introduce the bug M9.8 designed around."""

    SINGLE_ACCOUNT = "single_account"
    ACCOUNT_SET = "account_set"  # an explicit, enumerated list
    PAYER_GROUP = "payer_group"  # M9.8's payer graph, anchored at an ultimate payer
    PARENT_GROUP = "parent_group"  # M9.8's hierarchy graph, anchored at a parent
    ORGANIZATION = "organization"


class HierarchyRelation(StrEnum):
    """Which M9.8 graph a mode traverses. `NONE` means the mode reads no relationship at all —
    and a mode declaring `NONE` that nonetheless walked a graph would be inventing authority."""

    NONE = "none"
    PARENT_SUBTREE = "parent_subtree"  # AccountHierarchyResolver
    PAYER_CHAIN = "payer_chain"  # PayerResolver
    ORGANIZATION_MEMBERSHIP = "organization_membership"


class ScopeCurrencyMode(StrEnum):
    """How a scope handles accounts that bill in different currencies.

    **There is no conversion member.** `CURRENCY_SEPARATED` keeps one aggregate per currency and
    never produces a combined figure; `SINGLE_DECLARED` names one currency and excludes any account
    billing in another, with a reason. A third member would need an FX rate, and every source of
    one — a provider, a table, a constant, an operator's assumption — would put a number in a
    consolidated financial artifact that no Billing domain owns."""

    CURRENCY_SEPARATED = "currency_separated"
    SINGLE_DECLARED = "single_declared"


@dataclass(frozen=True, slots=True)
class ScopeRule:
    """The proven semantics of one `ScopeMode`, as data rather than as branches.

    Declared as a table so "what does PAYER_GROUP require?" is answered by a lookup an auditor can
    read, not by tracing conditionals through a resolver."""

    mode: ScopeMode
    inclusion: str
    exclusion: str
    requires_anchor: bool
    requires_explicit_set: bool
    hierarchy: HierarchyRelation
    #: Whether a source fact must be attributable to exactly one member before it may contribute.
    #: True for every mode: an unattributable fact is never consolidated under any scope.
    requires_attribution: bool = True
    permits_mixed_currency: bool = True


SCOPE_RULES: dict[ScopeMode, ScopeRule] = {
    ScopeMode.SINGLE_ACCOUNT: ScopeRule(
        mode=ScopeMode.SINGLE_ACCOUNT,
        inclusion="exactly the anchor account",
        exclusion="nothing; an explicit exclusion of the anchor empties the scope",
        requires_anchor=True,
        requires_explicit_set=False,
        hierarchy=HierarchyRelation.NONE,
    ),
    ScopeMode.ACCOUNT_SET: ScopeRule(
        mode=ScopeMode.ACCOUNT_SET,
        inclusion="exactly the enumerated accounts that exist in this organization",
        exclusion="an enumerated account that is not found, or is explicitly excluded",
        requires_anchor=False,
        requires_explicit_set=True,
        hierarchy=HierarchyRelation.NONE,
    ),
    ScopeMode.PAYER_GROUP: ScopeRule(
        mode=ScopeMode.PAYER_GROUP,
        inclusion="the anchor plus every account whose payer chain reaches it (M9.8's payer graph)",
        exclusion="explicit exclusion, or an account whose payer chain loops",
        requires_anchor=True,
        requires_explicit_set=False,
        hierarchy=HierarchyRelation.PAYER_CHAIN,
    ),
    ScopeMode.PARENT_GROUP: ScopeRule(
        mode=ScopeMode.PARENT_GROUP,
        inclusion="the anchor and its subtree (M9.8's hierarchy graph)",
        exclusion="explicit exclusion, a cycle, or depth beyond M9.8's MAX_HIERARCHY_DEPTH",
        requires_anchor=True,
        requires_explicit_set=False,
        hierarchy=HierarchyRelation.PARENT_SUBTREE,
    ),
    ScopeMode.ORGANIZATION: ScopeRule(
        mode=ScopeMode.ORGANIZATION,
        inclusion="every billing account in the organization",
        exclusion="explicit exclusion only",
        requires_anchor=False,
        requires_explicit_set=False,
        hierarchy=HierarchyRelation.ORGANIZATION_MEMBERSHIP,
    ),
}

#: How many accounts one scope snapshot may hold. Beyond this the resolution raises
#: `SCOPE_TOO_LARGE` rather than materializing an unbounded membership — see the limitation
#: recorded against M9.8 in the milestone report, which owns the paginated account read this
#: ceiling exists in place of.
MAX_SCOPE_MEMBERS = 10_000


@dataclass(frozen=True, slots=True)
class ScopeDefinition:
    """**How accounts enter a reporting scope**, at one version, immutable once published.

    Versioned for the same reason a reporting definition is: a consolidation stores the version it
    resolved under, and changing what "the EMEA group" means must never retroactively change what
    January's consolidation covered."""

    id: str
    org_id: str
    scope_code: str
    version: int
    mode: ScopeMode
    title: str = ""
    anchor_account_id: str = ""
    member_account_ids: tuple[str, ...] = ()  # ACCOUNT_SET only
    excluded_account_ids: tuple[str, ...] = ()
    currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED
    declared_currency: Currency | None = None
    period_basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD
    required_domains: tuple[EvidenceDomain, ...] = ()
    integrity_blocks_finalization: bool = False
    definition_digest: str = ""
    published_at: float = 0.0
    published_by: str = ""

    @property
    def rule(self) -> ScopeRule:
        return SCOPE_RULES[self.mode]


# ── scope membership ────────────────────────────────────────────────────────────


class InclusionReason(StrEnum):
    """**Why an account is in the scope.** Never blank: an account with no stated reason for being
    consolidated is an account somebody guessed at."""

    ANCHOR = "anchor"
    ENUMERATED = "enumerated"
    HIERARCHY_DESCENDANT = "hierarchy_descendant"
    PAYER_DEPENDENT = "payer_dependent"
    ORGANIZATION_MEMBER = "organization_member"


class ExclusionReason(StrEnum):
    """**Why an account is not in the scope.** Also never blank — the mandate requires every
    excluded item to carry a reason, because a silently dropped account and a deliberately excluded
    one look identical in a total."""

    EXPLICITLY_EXCLUDED = "explicitly_excluded"
    ACCOUNT_NOT_FOUND = "account_not_found"
    CURRENCY_NOT_PERMITTED = "currency_not_permitted"
    HIERARCHY_CYCLE = "hierarchy_cycle"
    DEPTH_EXCEEDED = "depth_exceeded"
    DUPLICATE_PATH = "duplicate_path"
    HIERARCHY_UNAVAILABLE = "hierarchy_unavailable"


@dataclass(frozen=True, slots=True)
class ScopeMember:
    """One account in a resolved scope, with the path that put it there.

    `account_revision` is the account's own state at resolution time. It is what makes the snapshot
    a snapshot: if the account later moves parent, this row still records the parent it had, and
    `hierarchy_path` still records the route the January consolidation walked."""

    account_id: str
    inclusion: InclusionReason
    account_revision: str  # M9.8's account digest/version at resolution
    currency: Currency | None = None
    depth: int = 0
    hierarchy_path: tuple[str, ...] = ()  # anchor → … → this account

    @property
    def is_anchor(self) -> bool:
        return self.inclusion is InclusionReason.ANCHOR


@dataclass(frozen=True, slots=True)
class ExcludedAccount:
    """An account the scope considered and did not include. A first-class record, not an absence."""

    account_id: str
    reason: ExclusionReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    """**An immutable resolved scope.** The centre of this milestone.

    A reporting scope must not depend on today's hierarchy. Account A belonged to parent P in
    January and moved to parent Q in March; January's consolidation must continue to represent
    January's scope forever. That is not achieved by a comment — it is achieved by the finalized
    consolidation referencing *this* object, which has no update path anywhere in the persistence
    layer and whose identity is a digest over its own membership.

    `membership_digest` covers the members and the exclusions. `snapshot_digest` covers that plus
    the definition version and the hierarchy relation walked, so two snapshots that resolved
    identically under different scope versions are still distinguishable."""

    id: str
    org_id: str
    scope_definition_id: str
    scope_version: int
    mode: ScopeMode
    hierarchy: HierarchyRelation
    members: tuple[ScopeMember, ...] = ()
    excluded: tuple[ExcludedAccount, ...] = ()
    currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED
    declared_currency: Currency | None = None
    membership_digest: str = ""
    snapshot_digest: str = ""
    effective_at: float = 0.0
    resolved_by: str = ""

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(m.account_id for m in self.members)

    @property
    def member_count(self) -> int:
        return len(self.members)

    def contains(self, account_id: str) -> bool:
        return any(m.account_id == account_id for m in self.members)

    @property
    def currencies(self) -> tuple[Currency, ...]:
        seen: list[Currency] = []
        for m in self.members:
            if m.currency is not None and m.currency not in seen:
                seen.append(m.currency)
        return tuple(sorted(seen, key=str))


# ── period compatibility ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConsolidationPeriod:
    """The window a consolidation covers, under a **declared** basis.

    `basis` is M9.22's `PeriodBasis`, imported unchanged. `window` is M9.2's `BillingPeriod`,
    reused unchanged. Nothing here is a sixth declaration of "a range of seconds" or a sixth kind
    of period — the mandate's §7 is about *never assuming two period concepts coincide*, and the
    defence against that is `SourceCapability` below, not another type."""

    id: str
    org_id: str
    basis: PeriodBasis
    period_key: str
    window: BillingPeriod
    aligned_source_id: str = ""


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """**The explicit adapter contract of §7/§8.** What one source domain can actually answer.

    This table is why a period mismatch cannot be silently approximated. A consolidation declares
    `PeriodBasis.TAX_PERIOD`; the Ledger's period read speaks `AccountingPeriod`; the two are not
    the same window and nobody may assume they are. `ScopePeriodService` consults this table and
    emits `PERIOD_NOT_SUPPORTED` rather than passing a tax period key to a ledger read and hoping.

    `account_keyed` records the finding this milestone exists because of: every period read below
    requires an account, which is exactly why a group filing needs a resolved scope first."""

    domain: EvidenceDomain
    artifact: EvidenceSourceType
    read_name: str  # the repository method, named so the contract is checkable
    supported_bases: frozenset[PeriodBasis]
    account_keyed: bool
    period_keyed: bool
    #: Whether this domain is *supposed* to answer for a period at all.
    #:
    #: The distinction that separates a gap from a category. The Ledger, the Invoice, Tax, Payment
    #: and Revenue all describe things that happen *in* a period, so a domain that cannot answer for
    #: one is a genuine hole in the consolidation. Reconciliation and Integrity are **operational
    #: queue reads**: "which divergences are open right now" has no period, and never had one.
    #: Treating their absence of a period read as the same failure would make §16's integrity
    #: boundary permanently unusable — a scope that surfaces the integrity record could never
    #: finalize, which is not the same as saying the record is unsettled.
    period_expected: bool = True
    aggregation: ReportTransformation = ReportTransformation.COUNT
    #: An empty string means the domain publishes the read. Otherwise: why it cannot, and which
    #: milestone owns the capability that is missing.
    unavailable_reason: str = ""

    @property
    def is_available(self) -> bool:
        return not self.unavailable_reason


#: **The audited seam.** Every entry was verified against the owning milestone's repository
#: contract, not assumed — including the three that cannot answer, which are recorded here rather
#: than discovered at collection time.
SOURCE_CAPABILITIES: dict[EvidenceDomain, SourceCapability] = {
    EvidenceDomain.LEDGER: SourceCapability(
        domain=EvidenceDomain.LEDGER,
        artifact=EvidenceSourceType.JOURNAL_ENTRY,
        read_name="entries.for_period",
        supported_bases=frozenset({PeriodBasis.ACCOUNTING_PERIOD}),
        account_keyed=True,
        period_keyed=True,
        aggregation=ReportTransformation.SUM_SAME_CURRENCY,
    ),
    EvidenceDomain.INVOICE: SourceCapability(
        domain=EvidenceDomain.INVOICE,
        artifact=EvidenceSourceType.INVOICE_DOCUMENT,
        read_name="documents.for_period",
        supported_bases=frozenset({PeriodBasis.ACCOUNTING_PERIOD}),
        account_keyed=True,
        period_keyed=True,
        aggregation=ReportTransformation.SUM_SAME_CURRENCY,
    ),
    EvidenceDomain.TAX: SourceCapability(
        domain=EvidenceDomain.TAX,
        artifact=EvidenceSourceType.TAX_ASSESSMENT,
        read_name="assessments.for_account",
        supported_bases=frozenset(),
        account_keyed=True,
        period_keyed=False,
        aggregation=ReportTransformation.COUNT,
        unavailable_reason=(
            "M9.15 publishes no period-scoped assessment read; a tax period cannot be "
            "requested from the authoritative source"
        ),
    ),
    EvidenceDomain.PAYMENT: SourceCapability(
        domain=EvidenceDomain.PAYMENT,
        artifact=EvidenceSourceType.PAYMENT_INTENT,
        read_name="intents.for_account",
        supported_bases=frozenset(),
        account_keyed=True,
        period_keyed=False,
        aggregation=ReportTransformation.COUNT,
        unavailable_reason=(
            "M9.16 publishes no period-scoped intent read; a settlement period cannot be "
            "requested from the authoritative source"
        ),
    ),
    EvidenceDomain.REVENUE: SourceCapability(
        domain=EvidenceDomain.REVENUE,
        artifact=EvidenceSourceType.REVENUE_SCHEDULE,
        read_name="schedules.for_account",
        supported_bases=frozenset(),
        account_keyed=True,
        period_keyed=False,
        aggregation=ReportTransformation.COUNT,
        unavailable_reason=(
            "M9.18 publishes no period-scoped recognition read; a recognition period cannot "
            "be requested from the authoritative source"
        ),
    ),
    EvidenceDomain.RECONCILIATION: SourceCapability(
        domain=EvidenceDomain.RECONCILIATION,
        artifact=EvidenceSourceType.RECONCILIATION_FINDING,
        read_name="findings.for_account",
        supported_bases=frozenset(),
        account_keyed=True,
        period_keyed=False,
        period_expected=False,
        aggregation=ReportTransformation.COUNT,
    ),
    EvidenceDomain.INTEGRITY: SourceCapability(
        domain=EvidenceDomain.INTEGRITY,
        artifact=EvidenceSourceType.INTEGRITY_INCIDENT,
        read_name="incidents.live",
        supported_bases=frozenset(),
        account_keyed=False,
        period_keyed=False,
        period_expected=False,
        aggregation=ReportTransformation.COUNT,
    ),
}

#: The aggregations a consolidation may apply. A strict subset of M9.22's closed set: exactly one
#: touches money, and it refuses a mixed-currency input rather than converting it.
CONSOLIDATION_AGGREGATIONS: frozenset[ReportTransformation] = frozenset(
    {
        ReportTransformation.COUNT,
        ReportTransformation.DISTINCT_COUNT,
        ReportTransformation.SUM_SAME_CURRENCY,
    }
)


# ── source bindings ─────────────────────────────────────────────────────────────


class SourceCompleteness(StrEnum):
    """Whether the source answered for the whole scope, or part of it."""

    SOURCE_COMPLETE = "source_complete"
    SOURCE_PARTIAL = "source_partial"
    SOURCE_ABSENT = "source_absent"


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """**One source, pinned at the revision it was read.** One row per `(run, account, domain)`.

    `status` is M9.22's `SourceStatus`, reused: `RESOLVED` (the domain answered and had facts),
    `EMPTY` (the domain answered and genuinely had nothing — a legitimate zero), `UNAVAILABLE` (we
    could not look — never a zero), `NOT_REQUIRED`.

    `amount_minor` is `None` unless `status is RESOLVED`. That is the rule this milestone exists to
    protect, and it is a type-level property here rather than a convention: an unavailable source
    carries no number at all, so no downstream sum can accidentally treat it as nought."""

    id: str
    org_id: str
    consolidation_id: str
    account_id: str
    domain: EvidenceDomain
    artifact: EvidenceSourceType
    read_name: str
    status: SourceStatus
    completeness: SourceCompleteness = SourceCompleteness.SOURCE_COMPLETE
    period_key: str = ""
    period_applied: bool = False  # False when the read is account-keyed only
    source_revision: str = ""
    source_digest: str = ""
    record_count: int = 0
    amount_minor: int | None = None
    currency: Currency | None = None
    detail: str = ""
    bound_at: float = 0.0

    @property
    def contributes(self) -> bool:
        return self.status is SourceStatus.RESOLVED

    @property
    def is_gap(self) -> bool:
        return self.status is SourceStatus.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ConsolidatedAmount:
    """One aggregate: one domain, one currency, integer minor units.

    **Never a combined figure across currencies**, and never a converted one. The two
    contribution counts
    and `contributing_records` are what make the number traceable without copying a million source
    ids into a JSON blob — the count plus `source_digest` plus the paginated binding table answers
    *which accounts, which records, which revision* without materializing any of them."""

    domain: EvidenceDomain
    currency: Currency
    minor: int
    contributing_accounts: int
    contributing_records: int
    source_digest: str = ""

    @property
    def amount(self) -> Money:
        return Money(minor=self.minor, currency=self.currency)


# ── completeness, exceptions ────────────────────────────────────────────────────


class ConsolidationCompleteness(StrEnum):
    """Four values, and the distinctions between them are the point.

    `PARTIAL` means some members contributed and some did not. `UNAVAILABLE` means no member's
    sources could be read at all. `BLOCKED` means a blocking exception stands. **None of them is
    ever rendered as a zero, an empty result, or a green badge.**"""

    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


SATISFYING_COMPLETENESS: frozenset[ConsolidationCompleteness] = frozenset(
    {ConsolidationCompleteness.COMPLETE}
)


class ConsolidationExceptionKind(StrEnum):
    """**Named problems.** There is deliberately no `FAILED`, no `ERROR` and no `OTHER`.

    A generic classification is how a specific, fixable problem becomes an unactionable badge. A
    test asserts no member of this enum is called any of those three."""

    UNATTRIBUTABLE_SOURCE = "unattributable_source"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INCOMPLETE = "source_incomplete"
    SOURCE_READ_FAILED = "source_read_failed"
    SOURCE_REVISION_CHANGED = "source_revision_changed"
    PERIOD_NOT_SUPPORTED = "period_not_supported"
    PERIOD_MISMATCH = "period_mismatch"
    SCOPE_EMPTY = "scope_empty"
    SCOPE_CHANGED = "scope_changed"
    SCOPE_TOO_LARGE = "scope_too_large"
    ACCOUNT_NOT_IN_SCOPE = "account_not_in_scope"
    HIERARCHY_UNAVAILABLE = "hierarchy_unavailable"
    CURRENCY_MISMATCH = "currency_mismatch"
    CURRENCY_UNSUPPORTED = "currency_unsupported"
    INTEGRITY_FINDING_PRESENT = "integrity_finding_present"


class ConsolidationSeverity(StrEnum):
    BLOCKING = "blocking"  # finalization is refused while this stands
    ADVISORY = "advisory"


class ConsolidationExceptionState(StrEnum):
    """An exception is **never removed**. It is acknowledged, or it is resolved by a new
    consolidation over fixed sources — and acknowledgement does not unblock anything."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True, slots=True)
class ConsolidationException:
    """An immutable named problem. Facts, not failures."""

    id: str
    org_id: str
    consolidation_id: str
    kind: ConsolidationExceptionKind
    severity: ConsolidationSeverity
    subject: str  # the account, domain, or period the problem is about
    diagnostic: str
    state: ConsolidationExceptionState = ConsolidationExceptionState.OPEN
    account_id: str = ""
    domain: EvidenceDomain | None = None
    period_key: str = ""
    created_at: float = 0.0
    acknowledged_at: float | None = None
    acknowledged_by: str = ""
    acknowledgement_note: str = ""

    @property
    def is_blocking(self) -> bool:
        """**Acknowledgement does not clear this.** Somebody having seen a problem is a different
        fact from the problem being gone, and only the second one may unblock a filing."""
        return self.severity is ConsolidationSeverity.BLOCKING


# ── validation ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConsolidationCheck:
    """One validation question and its answer. Every check asks about the *consolidation* — never
    whether two financial domains agree, which is M9.19's question and stays M9.19's."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ConsolidationValidation:
    """The current verdict on a run. Pinned into the snapshot at finalization."""

    consolidation_id: str
    org_id: str
    checks: tuple[ConsolidationCheck, ...] = ()
    validated_at: float = 0.0
    validated_by: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failures(self) -> tuple[ConsolidationCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class ConsolidationStatus(StrEnum):
    DRAFT = "draft"
    SCOPING = "scoping"
    COLLECTING = "collecting"
    VALIDATING = "validating"
    READY = "ready"
    FINALIZED = "finalized"
    BLOCKED = "blocked"


class ConsolidationTrigger(StrEnum):
    RESOLVE_SCOPE = "resolve_scope"
    BEGIN_COLLECTION = "begin_collection"
    COMPLETE_COLLECTION = "complete_collection"
    PASS_VALIDATION = "pass_validation"
    FAIL_VALIDATION = "fail_validation"
    RECOLLECT = "recollect"
    FINALIZE = "finalize"


CONSOLIDATION_LIFECYCLE: StateMachineDefinition = define(
    [
        (
            ConsolidationStatus.DRAFT,
            ConsolidationTrigger.RESOLVE_SCOPE,
            ConsolidationStatus.SCOPING,
        ),
        (
            ConsolidationStatus.SCOPING,
            ConsolidationTrigger.BEGIN_COLLECTION,
            ConsolidationStatus.COLLECTING,
        ),
        (
            ConsolidationStatus.COLLECTING,
            ConsolidationTrigger.COMPLETE_COLLECTION,
            ConsolidationStatus.VALIDATING,
        ),
        (
            ConsolidationStatus.VALIDATING,
            ConsolidationTrigger.PASS_VALIDATION,
            ConsolidationStatus.READY,
        ),
        (
            ConsolidationStatus.VALIDATING,
            ConsolidationTrigger.FAIL_VALIDATION,
            ConsolidationStatus.BLOCKED,
        ),
        # Going back to collection after the sources were fixed is honest. Staying in BLOCKED with
        # a green badge would not be, and neither would an edge from BLOCKED straight to READY.
        (
            ConsolidationStatus.BLOCKED,
            ConsolidationTrigger.RECOLLECT,
            ConsolidationStatus.COLLECTING,
        ),
        # A ready run whose scope or sources moved must re-collect rather than finalize stale.
        (ConsolidationStatus.READY, ConsolidationTrigger.RECOLLECT, ConsolidationStatus.COLLECTING),
        (ConsolidationStatus.READY, ConsolidationTrigger.FINALIZE, ConsolidationStatus.FINALIZED),
    ]
)

#: `FINALIZED` has no outgoing edge — the invariant is the *absence* of a transition, which is why
#: no override flag, no operator confidence and no retry path can reach around it. A reachability
#: test walks the machine to prove nothing leaves.
TERMINAL_CONSOLIDATION_STATES: frozenset[ConsolidationStatus] = frozenset(
    {ConsolidationStatus.FINALIZED}
)

LIVE_CONSOLIDATION_STATES: frozenset[ConsolidationStatus] = frozenset(
    set(ConsolidationStatus) - TERMINAL_CONSOLIDATION_STATES
)


@dataclass(frozen=True, slots=True)
class ConsolidationRun:
    """**One deterministic consolidation:** one scope snapshot, one period, one set of pinned
    sources.

    Carries no financial authority. Every minor unit in `amounts` arrived from the domain that
    decided it, through an aggregation drawn from a closed set of three, and is reproducible from
    the binding rows this run wrote.

    `corrects` names the run this one supersedes, and **both survive**. A correction is a new
    consolidation beside the old one, because rewriting what was consolidated is not a correction,
    it is a disappearance."""

    id: str
    org_id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_id: str
    period_basis: PeriodBasis
    period_key: str
    status: ConsolidationStatus = ConsolidationStatus.DRAFT
    completeness: ConsolidationCompleteness = ConsolidationCompleteness.UNAVAILABLE
    currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED
    declared_currency: Currency | None = None
    amounts: tuple[ConsolidatedAmount, ...] = ()
    member_count: int = 0
    collected_count: int = 0
    #: The cursor batched collection advances. Deterministic: members are collected in account-id
    #: order, so a restart mid-collection resumes at the same place rather than re-reading or
    #: skipping. §25's answer to "1M facts" is this field, not a bigger `limit`.
    collection_cursor: str = ""
    source_count: int = 0
    unavailable_source_count: int = 0
    exception_count: int = 0
    blocking_exception_count: int = 0
    consolidation_digest: str = ""
    corrects: str = ""
    superseded_by: str = ""
    correction_reason: str = ""
    opened_at: float = 0.0
    opened_by: str = ""
    finalized_at: float | None = None
    finalized_by: str = ""
    version: int = 0

    @property
    def is_finalized(self) -> bool:
        return self.status is ConsolidationStatus.FINALIZED

    @property
    def is_correction(self) -> bool:
        return bool(self.corrects)

    @property
    def collection_complete(self) -> bool:
        return self.member_count > 0 and self.collected_count >= self.member_count


@dataclass(frozen=True, slots=True)
class ConsolidationSnapshot:
    """The finalized artifact, content-addressed on **pinned state only**.

    `representation` is built from the scope snapshot digest, the period, the pinned source digest
    and the aggregates as computed — never from a live read. A test demonstrates that a finalized
    consolidation's digest and representation are byte-identical after every source moves."""

    id: str
    org_id: str
    consolidation_id: str
    consolidation_digest: str
    scope_snapshot_id: str
    scope_snapshot_digest: str
    period_key: str
    representation: str = ""
    source_digest: str = ""
    completeness: ConsolidationCompleteness = ConsolidationCompleteness.UNAVAILABLE
    finalized_at: float = 0.0
    finalized_by: str = ""


# ── provenance ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AccountContribution:
    """What one account contributed, and what it did not. The row an auditor asking *which accounts
    made up this number?* reads."""

    account_id: str
    inclusion: InclusionReason
    resolved_sources: int = 0
    empty_sources: int = 0
    unavailable_sources: int = 0
    record_count: int = 0
    currency: Currency | None = None

    @property
    def contributed(self) -> bool:
        return self.resolved_sources > 0


@dataclass(frozen=True, slots=True)
class ConsolidationProvenance:
    """The full traceability answer, assembled from stored rows rather than recomputed."""

    consolidation_id: str
    org_id: str
    scope_snapshot_id: str
    scope_definition_id: str
    scope_version: int
    period_basis: PeriodBasis
    period_key: str
    hierarchy: HierarchyRelation
    contributions: tuple[AccountContribution, ...] = ()
    excluded: tuple[ExcludedAccount, ...] = ()
    domains: tuple[EvidenceDomain, ...] = ()
    source_digest: str = ""
    consolidation_digest: str = ""
    corrects: str = ""
    superseded_by: str = ""


# ── trail ───────────────────────────────────────────────────────────────────────


class ConsolidationEventKind(StrEnum):
    SCOPE_DEFINITION_PUBLISHED = "scope_definition_published"
    SCOPE_RESOLVED = "scope_resolved"
    CONSOLIDATION_OPENED = "consolidation_opened"
    COLLECTION_STARTED = "collection_started"
    SOURCE_BOUND = "source_bound"
    SOURCE_UNAVAILABLE = "source_unavailable"
    COLLECTION_COMPLETED = "collection_completed"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    EXCEPTION_RAISED = "exception_raised"
    EXCEPTION_ACKNOWLEDGED = "exception_acknowledged"
    RECOLLECTION_STARTED = "recollection_started"
    CONSOLIDATION_FINALIZED = "consolidation_finalized"
    CORRECTION_OPENED = "correction_opened"


@dataclass(frozen=True, slots=True)
class ConsolidationEvent:
    """Append-only. The authority on what happened; `status` is a projection over this."""

    id: str
    org_id: str
    consolidation_id: str
    kind: ConsolidationEventKind
    sequence: int
    subject: str = ""
    detail: str = ""
    actor: str = ""
    occurred_at: float = 0.0
    data: dict = field(default_factory=dict)


# ── search & posture ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConsolidationSearchQuery:
    org_id: str
    scope_definition_id: str = ""
    period_key: str = ""
    statuses: tuple[ConsolidationStatus, ...] = ()
    completeness: tuple[ConsolidationCompleteness, ...] = ()
    corrections_only: bool = False
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True, slots=True)
class ConsolidationSearchResult:
    runs: tuple[ConsolidationRun, ...]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // max(1, self.page_size)))


@dataclass(frozen=True, slots=True)
class ConsolidationPosture:
    """The operational summary a dashboard reads. Counts and labels — no money."""

    org_id: str
    total_runs: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_completeness: dict[str, int] = field(default_factory=dict)
    open_exceptions: int = 0
    blocking_exceptions: int = 0
    scope_definitions: int = 0


# ── digests ─────────────────────────────────────────────────────────────────────


def membership_digest(
    members: tuple[ScopeMember, ...], excluded: tuple[ExcludedAccount, ...]
) -> str:
    """A canonical digest over a resolved membership, using M9.21's single canonicalizer.

    Order-independent by construction: members and exclusions are sorted by account id before
    serialization, so two resolutions that found the same accounts in different orders produce the
    same digest — and therefore the same snapshot id, which is what stops a retry minting a second
    view of one scope."""
    return canonical_digest(
        {
            "members": [
                {
                    "account_id": m.account_id,
                    "inclusion": m.inclusion.value,
                    "revision": m.account_revision,
                    "currency": str(m.currency) if m.currency else None,
                    "depth": m.depth,
                    "path": list(m.hierarchy_path),
                }
                for m in sorted(members, key=lambda m: m.account_id)
            ],
            "excluded": [
                {"account_id": e.account_id, "reason": e.reason.value, "detail": e.detail}
                for e in sorted(excluded, key=lambda e: e.account_id)
            ],
        }
    )


def scope_snapshot_digest(
    scope_definition_id: str,
    scope_version: int,
    mode: ScopeMode,
    hierarchy: HierarchyRelation,
    membership: str,
) -> str:
    return canonical_digest(
        {
            "definition": scope_definition_id,
            "version": scope_version,
            "mode": mode.value,
            "hierarchy": hierarchy.value,
            "membership": membership,
        }
    )


def consolidation_source_digest(bindings: tuple[SourceBinding, ...]) -> str:
    """A canonical digest over every pinned source revision.

    Covers `status` as well as `source_revision`: a source that went from `RESOLVED` to
    `UNAVAILABLE` changed the consolidation even though no revision string moved, and a digest that
    missed that would let a gap open silently under a finalized artifact."""
    return canonical_digest(
        [
            {
                "account_id": b.account_id,
                "domain": b.domain.value,
                "artifact": b.artifact.value,
                "status": b.status.value,
                "revision": b.source_revision,
                "digest": b.source_digest,
                "records": b.record_count,
                "period_applied": b.period_applied,
            }
            for b in sorted(
                bindings, key=lambda b: (b.account_id, b.domain.value, b.artifact.value)
            )
        ]
    )


def consolidation_digest(
    scope_snapshot_digest_value: str,
    period_basis: PeriodBasis,
    period_key: str,
    source_digest: str,
    amounts: tuple[ConsolidatedAmount, ...],
    completeness: ConsolidationCompleteness,
) -> str:
    """The content digest of a finalized consolidation. **Pinned state only** — no live read, no
    clock, no `latest()`. Same inputs in, same digest out, on any machine, forever."""
    return canonical_digest(
        {
            "scope": scope_snapshot_digest_value,
            "basis": period_basis.value,
            "period": period_key,
            "sources": source_digest,
            "completeness": completeness.value,
            "amounts": [
                {
                    "domain": a.domain.value,
                    "currency": str(a.currency),
                    "minor": a.minor,
                    "accounts": a.contributing_accounts,
                    "records": a.contributing_records,
                }
                for a in sorted(amounts, key=lambda a: (a.domain.value, str(a.currency)))
            ],
        }
    )


def scope_definition_digest(definition: ScopeDefinition) -> str:
    """Over the *content* of a scope rule, so a re-publication can be proven identical rather than
    assumed. `published_at` and `published_by` are excluded deliberately: who pressed the button
    and when is not part of what the rule means."""
    return canonical_digest(
        {
            "code": definition.scope_code,
            "version": definition.version,
            "mode": definition.mode.value,
            "anchor": definition.anchor_account_id,
            "members": sorted(definition.member_account_ids),
            "excluded": sorted(definition.excluded_account_ids),
            "currency_mode": definition.currency_mode.value,
            "declared_currency": (
                str(definition.declared_currency) if definition.declared_currency else None
            ),
            "period_basis": definition.period_basis.value,
            "required_domains": sorted(d.value for d in definition.required_domains),
            "integrity_blocks": definition.integrity_blocks_finalization,
        }
    )


__all__ = [
    "CONSOLIDATION_AGGREGATIONS",
    "CONSOLIDATION_LIFECYCLE",
    "LIVE_CONSOLIDATION_STATES",
    "MAX_SCOPE_MEMBERS",
    "PREFIX_CONSOLIDATION",
    "PREFIX_CONSOLIDATION_EVENT",
    "PREFIX_CONSOLIDATION_PERIOD",
    "PREFIX_CONSOLIDATION_EXCEPTION",
    "PREFIX_CONSOLIDATION_SNAPSHOT",
    "PREFIX_SCOPE_DEFINITION",
    "PREFIX_SCOPE_SNAPSHOT",
    "PREFIX_SOURCE_BINDING",
    "SATISFYING_COMPLETENESS",
    "SCOPE_RULES",
    "SOURCE_CAPABILITIES",
    "TERMINAL_CONSOLIDATION_STATES",
    "AccountContribution",
    "ConsolidatedAmount",
    "ConsolidationCheck",
    "ConsolidationCompleteness",
    "ConsolidationEvent",
    "ConsolidationEventKind",
    "ConsolidationException",
    "ConsolidationExceptionKind",
    "ConsolidationExceptionState",
    "ConsolidationPeriod",
    "ConsolidationPosture",
    "ConsolidationProvenance",
    "ConsolidationRun",
    "ConsolidationSearchQuery",
    "ConsolidationSearchResult",
    "ConsolidationSeverity",
    "ConsolidationSnapshot",
    "ConsolidationStatus",
    "ConsolidationTrigger",
    "ConsolidationValidation",
    "ExcludedAccount",
    "ExclusionReason",
    "HierarchyRelation",
    "InclusionReason",
    "ScopeCurrencyMode",
    "ScopeDefinition",
    "ScopeMember",
    "ScopeMode",
    "ScopeRule",
    "ScopeSnapshot",
    "SourceBinding",
    "SourceCapability",
    "SourceCompleteness",
    "consolidation_digest",
    "derive_consolidation_event_id",
    "derive_consolidation_exception_id",
    "derive_consolidation_id",
    "derive_consolidation_period_id",
    "derive_consolidation_snapshot_id",
    "derive_scope_definition_id",
    "derive_scope_snapshot_id",
    "derive_source_binding_id",
    "membership_digest",
    "scope_definition_digest",
    "scope_snapshot_digest",
    "consolidation_source_digest",
]
