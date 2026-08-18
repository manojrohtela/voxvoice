"""Entitlement Engine domain (M9.10) — Billing decides; the owning platform enforces.

Four milestones built the vocabulary this one consumes. M9.6 said **what is sold**, M9.7 said
**what it costs**, M9.8 said **who we sell to**, M9.9 said **what was agreed**. This one answers
the question every other plane actually asks at runtime: *what is this billing account entitled
to, right now, and what should I do if I cannot see fresh data?*

The load-bearing Epic 9 invariant, restated as code shape rather than prose:

* **Billing decides.** Precedence resolution, grant windows, bundle expansion, and snapshot
  composition all live here.
* **Billing publishes immutable snapshots.** A published `EntitlementVersion` is frozen; a new
  decision produces a new version and never edits an old one.
* **The owning platform enforces locally.** Every gate carries a fail-open / fail-closed /
  degrade classification, so an enforcing plane knows what to do without asking Billing.
* **Billing never performs authorization.** Not one type here takes a principal, a role, a
  scope, a session, or a request. Entitlement is *commercial capability*; §A11.2 RBAC keeps
  authorization and gains no rival (ADR-0023 §11.3). A test greps for the words.

Three structural properties, each a decision rather than a convention:

* **M9.2's vocabulary is reused, not duplicated.** `GateClassification`, `GrantSource`, and
  `GRANT_PRECEDENCE` are imported from `entitlement.py` rather than re-declared. Two answers to
  "which source wins?" is exactly the drift §A2.5 exists to prevent — the lesson M9.9 learned
  the expensive way with `INTERVAL_SECONDS`.
* **`EntitlementSet.to_snapshot()` projects onto M9.2's frozen `EntitlementSnapshot`.** The rich
  resolution this milestone performs is published in the *same shape* the M9.5 enforcement
  contract already exposes, so an enforcing plane written against M9.2 reads an M9.10 snapshot
  unchanged.
* **A grant is never deleted.** The lifecycle below has **no removal state at all** — the same
  call M9.9 made, for the same reason: what a customer was given, and when it was taken away,
  is evidence. `archived` is as far out of the working set as one can go.

Carries no `Money`, computes no charge, and consumes no usage: an allowance says *how much is
included*, never what anything costs or how much has been used. A test asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .catalog import FeatureKind
from .entitlement import (
    GRANT_PRECEDENCE,
    Entitlement,
    EntitlementGate,
    EntitlementSnapshot,
    GateClassification,
    GrantSource,
)

#: How deep entitlement bundles may nest before the structure stops being explicable to the
#: customer whose capabilities it decides.
MAX_BUNDLE_DEPTH = 5

#: How many pending entitlement changes one profile may carry, for the same reason M9.9 bounds
#: scheduled subscription changes: beyond this nobody can say what the account will be entitled to.
MAX_PENDING_ENTITLEMENT_CHANGES = 10

#: The default staleness budget an enforcing plane is given. One hour is deliberately generous:
#: ADR-0023 E4 prefers bounded, measured revenue leakage to a customer-visible interruption.
DEFAULT_MAX_STALENESS_SECONDS = 3600.0


# ── lifecycle ───────────────────────────────────────────────────────────────────


class GrantState(StrEnum):
    """The lifecycle of one durable grant.

    Named `GrantState` rather than reusing anything in M9.2, which declares grants as plain
    records with a `revoked_at` and no state machine. Both exist: M9.2's `EntitlementGrant` is
    the minimal record the revenue spine composes from, this is the governed one."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class GrantTrigger(StrEnum):
    ACTIVATE = "activate"
    SUSPEND = "suspend"
    RESUME = "resume"
    REVOKE = "revoke"
    EXPIRE = "expire"
    ARCHIVE = "archive"


# The grant lifecycle. Four absences, each a decision:
#
#   * **no edge reaches a deletion state, because there is no deletion state.** What a customer
#     was given, and when it was taken away, is evidence — of a support promise, of a contract
#     term, of a partner arrangement. ADR-0023 RS4 makes the record permanent;
#   * there is no REVOKED → ACTIVE edge. Re-granting a capability is a *new* grant, because
#     collapsing the two makes "was this ever revoked?" unanswerable — and that is precisely the
#     question a disputed entitlement turns on;
#   * there is no DRAFT → SUSPEND edge: suspending something that was never active is a
#     distinction without a difference, and offering it would produce two ways to say "not yet";
#   * there is no edge out of ARCHIVED, which is what makes it terminal.
#
# EXPIRED is reached by an explicit administrative move, **not** by the clock. Effective dating
# (`EntitlementWindow`) is a *resolution-time* property: a grant whose window has closed simply
# stops being composed into snapshots, without anything mutating. The state records the
# administrative fact; the window decides the answer. Keeping them separate is what lets this
# domain contain no scheduler at all.
GRANT_LIFECYCLE: StateMachineDefinition = define(
    [
        (GrantState.DRAFT, GrantTrigger.ACTIVATE, GrantState.ACTIVE),
        (GrantState.DRAFT, GrantTrigger.REVOKE, GrantState.REVOKED),
        (GrantState.ACTIVE, GrantTrigger.SUSPEND, GrantState.SUSPENDED),
        (GrantState.ACTIVE, GrantTrigger.REVOKE, GrantState.REVOKED),
        (GrantState.ACTIVE, GrantTrigger.EXPIRE, GrantState.EXPIRED),
        (GrantState.SUSPENDED, GrantTrigger.RESUME, GrantState.ACTIVE),
        (GrantState.SUSPENDED, GrantTrigger.REVOKE, GrantState.REVOKED),
        (GrantState.SUSPENDED, GrantTrigger.EXPIRE, GrantState.EXPIRED),
        (GrantState.REVOKED, GrantTrigger.ARCHIVE, GrantState.ARCHIVED),
        (GrantState.EXPIRED, GrantTrigger.ARCHIVE, GrantState.ARCHIVED),
    ]
)

#: The one state in which a grant can contribute capability. Everything else is history or
#: intent — and a grant that is merely *suspended* contributes nothing, reversibly.
LIVE_GRANT_STATES: frozenset[GrantState] = frozenset({GrantState.ACTIVE})
#: States after which this grant never confers anything again.
CLOSED_GRANT_STATES: frozenset[GrantState] = frozenset(
    {GrantState.REVOKED, GrantState.EXPIRED, GrantState.ARCHIVED}
)


# ── effective dating ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementWindow:
    """When a grant is in force. **Half-open `[starts_at, ends_at)`**, the same convention M9.7
    uses for effective-dated prices.

    Half-open is not a stylistic choice: with closed intervals, one grant's end and the next
    grant's start are both in force for one instant, and a snapshot composed at exactly that
    instant would be ambiguous. `None` on either side means unbounded."""

    starts_at: float | None = None
    ends_at: float | None = None

    @property
    def is_open_ended(self) -> bool:
        return self.ends_at is None

    @property
    def is_bounded(self) -> bool:
        return self.starts_at is not None and self.ends_at is not None

    @property
    def is_valid(self) -> bool:
        return not self.is_bounded or self.ends_at > self.starts_at  # type: ignore[operator]

    def covers(self, at: float) -> bool:
        if self.starts_at is not None and at < self.starts_at:
            return False
        return self.ends_at is None or at < self.ends_at

    def overlaps(self, other: EntitlementWindow) -> bool:
        """Whether two windows are ever simultaneously in force. Used to warn about a
        double-grant of the same code from the same source, which is a data-entry mistake far
        more often than an intention."""
        start = max(self.starts_at or float("-inf"), other.starts_at or float("-inf"))
        end = min(self.ends_at or float("inf"), other.ends_at or float("inf"))
        return start < end


# ── values ──────────────────────────────────────────────────────────────────────


#: The generosity sentinel for an unlimited allowance. A plain integer keeps precedence
#: comparison an ordinary ordering rather than a special case at every call site.
UNLIMITED_GENEROSITY = 1 << 62


@dataclass(frozen=True, slots=True)
class EntitlementValue:
    """*What* a grant confers: on/off, how much, and whether exceeding it is billable.

    `allowance=None` means unlimited; `0` means explicitly none. The distinction matters —
    "unlimited" and "none" are opposite answers, and a nullable integer that collapsed them
    would make an enterprise contract indistinguishable from a suspension."""

    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False

    @property
    def is_unlimited(self) -> bool:
        return self.enabled and self.allowance is None

    @property
    def is_denial(self) -> bool:
        """An explicit *withholding* — enabled=False, or a hard zero. Distinct from the absence
        of a grant, which simply says nothing."""
        return not self.enabled or self.allowance == 0

    @property
    def generosity(self) -> int:
        """How much this value confers, as one comparable integer. Only ever a tie-break:
        declared source precedence is decided first."""
        if not self.enabled:
            return -1
        return UNLIMITED_GENEROSITY if self.allowance is None else self.allowance


# ── definitions (the gate registry) ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementDefinition:
    """The declaration of one enforceable capability: what it is, what it defaults to, and
    **how an enforcing plane must behave when it cannot see fresh data**.

    Richer than M9.2's `EntitlementGate` but deliberately projectable onto it (`as_gate`), so
    the published contract stays the one enforcing planes already read. Classification is a
    design-time property recorded once here, never a call-site decision — ADR-0023 E3 makes an
    unclassified gate an incomplete design, and validation reports it as an error."""

    id: str
    org_id: str
    code: str
    display_name: str = ""
    kind: FeatureKind = FeatureKind.FLAG
    classification: GateClassification = GateClassification.FAIL_OPEN
    default: EntitlementValue = field(default_factory=lambda: EntitlementValue(enabled=False))
    meter_code: str = ""  # required when kind is QUOTA
    max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS
    description: str = ""
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    retired_at: float | None = None

    @property
    def is_retired(self) -> bool:
        return self.retired_at is not None

    @property
    def is_quota(self) -> bool:
        return self.kind is FeatureKind.QUOTA

    @property
    def requires_meter(self) -> bool:
        """A quota is an allowance *of something measured*. Without a meter nobody can say
        whether it has been consumed, which makes the gate undecidable rather than generous."""
        return self.is_quota

    @property
    def fails_closed(self) -> bool:
        return self.classification is GateClassification.FAIL_CLOSED

    def as_gate(self) -> EntitlementGate:
        """Project onto M9.2's frozen gate type — the shape enforcing planes already read."""
        return EntitlementGate(
            code=self.code,
            kind=self.kind,
            classification=self.classification,
            description=self.description,
            meter_code=self.meter_code,
        )


# ── bundles ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BundleEntry:
    """One capability a bundle confers, at a stated value."""

    code: str
    value: EntitlementValue = field(default_factory=EntitlementValue)


@dataclass(frozen=True, slots=True)
class EntitlementBundle:
    """A named set of capabilities granted together — "Enterprise Security", "Partner Tier 2".

    Bundles may contain bundles, because that is how real commercial packaging is expressed:
    a partner tier includes the enterprise pack plus two extras. Nesting means cycles are
    possible, so `EntitlementBundleExpander` detects them and reports the path rather than
    recursing until the stack ends."""

    id: str
    org_id: str
    code: str
    display_name: str = ""
    entries: tuple[BundleEntry, ...] = ()
    member_bundles: tuple[str, ...] = ()
    description: str = ""
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({e.code for e in self.entries}))

    @property
    def is_composite(self) -> bool:
        return bool(self.member_bundles)

    def entry(self, code: str) -> BundleEntry | None:
        return next((e for e in self.entries if e.code == code), None)


# ── grants ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialGrant:
    """A durable, governed grant — the audit-bearing record of *why* an account has a capability.

    Snapshots are computed from grants; grants are never computed from snapshots. That direction
    is what makes an entitlement explicable: every resolved capability can name the grant that
    produced it, the source that outranked the others, and the window that admitted it.

    `precedence_override` exists for the one case declared precedence cannot express — a
    negotiated term that must outrank a partner ceiling. It is recorded, not inferred, and
    validation insists it stay inside the declared band so an override can never silently
    invert the whole ordering."""

    id: str
    org_id: str
    account_id: str
    entitlement_code: str
    value: EntitlementValue = field(default_factory=EntitlementValue)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""  # subscription id, contract id, order id, partner id
    bundle_code: str = ""  # set when this grant came from expanding a bundle
    window: EntitlementWindow = field(default_factory=EntitlementWindow)
    state: GrantState = GrantState.DRAFT
    precedence_override: int | None = None
    reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float | None = None
    suspended_at: float | None = None
    revoked_at: float | None = None
    revoked_reason: str = ""
    expired_at: float | None = None
    archived_at: float | None = None

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_GRANT_STATES

    @property
    def is_closed(self) -> bool:
        return self.state in CLOSED_GRANT_STATES

    @property
    def is_editable(self) -> bool:
        """A closed grant is a record of what was conferred and withdrawn. Editing it after the
        fact would change the record rather than the entitlement."""
        return not self.is_closed

    @property
    def is_bundled(self) -> bool:
        return bool(self.bundle_code)

    @property
    def precedence(self) -> int:
        """Declared, never inferred from insertion order — which is what makes resolution
        deterministic across two machines, two processes, and two years."""
        return (
            self.precedence_override
            if self.precedence_override is not None
            else GRANT_PRECEDENCE[self.source]
        )

    def is_effective_at(self, at: float) -> bool:
        """Whether this grant contributes capability at an instant. Both conditions are
        required, and they mean different things: the state is administrative, the window is
        commercial."""
        return self.is_live and self.window.covers(at)


# ── scheduled changes ───────────────────────────────────────────────────────────


class EntitlementChangeKind(StrEnum):
    GRANT = "grant"
    REVOKE = "revoke"
    ADJUST = "adjust"
    BUNDLE_GRANT = "bundle_grant"


class EntitlementChangeStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScheduledEntitlementChange:
    """An accepted intention to change entitlement at a future instant.

    **Intent only.** There is no scheduler in this domain, no timer, and no background worker.
    A change records what was agreed and when it should take effect; the surface that later
    applies it dispatches an ordinary command, validated then exactly as if a human had asked.
    That separation is what keeps "what we decided" auditable independently of "what the system
    managed to run" — and it is why a snapshot composed a year from now still reproduces."""

    id: str
    kind: EntitlementChangeKind
    effective_at: float
    status: EntitlementChangeStatus = EntitlementChangeStatus.PENDING
    entitlement_code: str = ""
    bundle_code: str = ""
    grant_id: str = ""  # set for REVOKE / ADJUST
    value: EntitlementValue = field(default_factory=EntitlementValue)
    source: GrantSource = GrantSource.PLAN
    source_ref: str = ""
    reason: str = ""
    requested_by: str = ""
    requested_at: float = 0.0
    applied_at: float | None = None

    @property
    def is_pending(self) -> bool:
        return self.status is EntitlementChangeStatus.PENDING

    @property
    def targets_a_grant(self) -> bool:
        return self.kind in (EntitlementChangeKind.REVOKE, EntitlementChangeKind.ADJUST)

    def is_due(self, at: float) -> bool:
        return self.is_pending and at >= self.effective_at


# ── the profile (the aggregate) ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementProfile:
    """One billing account's entitlement position: every grant it holds, every intention it
    carries, and the staleness budget its snapshots publish.

    The aggregate has no lifecycle of its own, deliberately. A profile is not a thing that can
    be active or cancelled — the *grants* have states, and asking whether a profile is "active"
    would invite a second, weaker answer to a question the grants already answer precisely."""

    id: str
    org_id: str
    account_id: str
    subscription_id: str = ""  # the agreement most grants derive from, when there is one
    grants: tuple[CommercialGrant, ...] = ()
    scheduled_changes: tuple[ScheduledEntitlementChange, ...] = ()
    version: int = 0  # 0 ⇒ nothing published yet; publishing mints 1
    max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS
    created_at: float = 0.0
    updated_at: float = 0.0

    # ── grants ──────────────────────────────────────────────────────────────────

    def grant(self, grant_id: str) -> CommercialGrant | None:
        return next((g for g in self.grants if g.id == grant_id), None)

    def grants_for(self, code: str) -> tuple[CommercialGrant, ...]:
        return tuple(g for g in self.grants if g.entitlement_code == code)

    @property
    def live_grants(self) -> tuple[CommercialGrant, ...]:
        return tuple(g for g in self.grants if g.is_live)

    def effective_grants(self, at: float) -> tuple[CommercialGrant, ...]:
        """The grants that contribute capability at an instant. A read — nothing here mutates,
        so composing a snapshot for a past instant is free of side effects."""
        return tuple(g for g in self.grants if g.is_effective_at(at))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({g.entitlement_code for g in self.grants}))

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({g.source.value for g in self.grants}))

    @property
    def bundle_codes(self) -> tuple[str, ...]:
        return tuple(sorted({g.bundle_code for g in self.grants if g.bundle_code}))

    @property
    def has_published_snapshot(self) -> bool:
        return self.version > 0

    # ── scheduling ──────────────────────────────────────────────────────────────

    @property
    def pending_changes(self) -> tuple[ScheduledEntitlementChange, ...]:
        return tuple(
            sorted(
                (c for c in self.scheduled_changes if c.is_pending),
                key=lambda c: (c.effective_at, c.id),
            )
        )

    def due_changes(self, at: float) -> tuple[ScheduledEntitlementChange, ...]:
        """What *would* be applied at this instant. Calling it applies nothing."""
        return tuple(c for c in self.pending_changes if c.is_due(at))

    def scheduled_change(self, change_id: str) -> ScheduledEntitlementChange | None:
        return next((c for c in self.scheduled_changes if c.id == change_id), None)


# ── resolution ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class ResolvedEntitlement:
    """One composed capability, with its provenance attached.

    `resolution_path` is the same idea M9.7 gave `ResolvedPrice`: the ordered sentences that
    explain how this answer was reached. An entitlement a customer disputes is worth nothing if
    nobody can say *why* the system decided it, and reconstructing that from grants after the
    fact is exactly the archaeology this field prevents."""

    code: str
    kind: FeatureKind = FeatureKind.FLAG
    enabled: bool = True
    allowance: int | None = None
    allows_overage: bool = False
    source: GrantSource = GrantSource.PLAN
    classification: GateClassification = GateClassification.FAIL_OPEN
    grant_id: str = field(default="", compare=False)
    precedence: int = field(default=0, compare=False)
    from_bundle: str = field(default="", compare=False)
    resolution_path: tuple[str, ...] = field(default=(), compare=False)

    @property
    def is_unlimited(self) -> bool:
        return self.enabled and self.allowance is None

    def as_entitlement(self) -> Entitlement:
        """Project onto M9.2's frozen `Entitlement` — the shape the published snapshot carries."""
        return Entitlement(
            code=self.code,
            kind=self.kind,
            enabled=self.enabled,
            allowance=self.allowance,
            allows_overage=self.allows_overage,
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class GrantConsideration:
    """One grant that was weighed during resolution, and what became of it.

    Losers are kept, not discarded. "Why does this account *not* have X?" is asked at least as
    often as the opposite, and it is unanswerable from the winner alone."""

    grant_id: str
    source: GrantSource
    precedence: int
    value: EntitlementValue
    effective: bool
    won: bool = False
    excluded_reason: str = ""


@dataclass(frozen=True, slots=True)
class EntitlementExplanation:
    """The inspection answer for one code: what was decided, and every grant that was weighed."""

    code: str
    at: float = 0.0
    winner: ResolvedEntitlement | None = None
    considered: tuple[GrantConsideration, ...] = ()
    definition_known: bool = True

    @property
    def is_granted(self) -> bool:
        return self.winner is not None and self.winner.enabled

    @property
    def contested(self) -> bool:
        """More than one grant was in force for this code — the case where precedence actually
        did something, and the case a support conversation is usually about."""
        return sum(1 for c in self.considered if c.effective) > 1


# ── the snapshot ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementSet:
    """The composed, immutable statement of what an account is entitled to.

    This object is the *entire* interface between Billing and enforcement. It contains no
    principal, no role, and no request context — only what the account is entitled to, plus the
    metadata an enforcing plane needs to reason about staleness. Reading it creates no
    dependency on the Billing Platform (ADR-0031 D3): it is data, and a plane that has cached it
    keeps working through a total Billing outage.

    `to_snapshot()` projects onto M9.2's frozen `EntitlementSnapshot`, so a plane written against
    the M9.5 contract consumes an M9.10 decision without changing a line."""

    id: str
    org_id: str
    account_id: str
    version: int
    issued_at: float
    entitlements: tuple[ResolvedEntitlement, ...] = ()
    gates: tuple[EntitlementGate, ...] = ()
    subscription_id: str = ""
    max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS
    grant_count: int = 0
    resolved_from: tuple[str, ...] = ()  # the grant ids that produced this set

    # ── read-only queries (what, never who) ─────────────────────────────────────

    def entitlement(self, code: str) -> ResolvedEntitlement | None:
        return next((e for e in self.entitlements if e.code == code), None)

    def gate(self, code: str) -> EntitlementGate | None:
        return next((g for g in self.gates if g.code == code), None)

    def is_enabled(self, code: str) -> bool:
        """Whether a capability is present. **Not** a permission check — the caller owns the
        write and decides what to do with the answer (ADR-0023 §11.1)."""
        e = self.entitlement(code)
        return bool(e and e.enabled)

    def allowance(self, code: str) -> int | None:
        e = self.entitlement(code)
        return None if e is None else e.allowance

    def classification(self, code: str) -> GateClassification | None:
        g = self.gate(code)
        return None if g is None else g.classification

    def is_stale_at(self, ts: float) -> bool:
        return (ts - self.issued_at) > self.max_staleness_seconds

    def age_at(self, ts: float) -> float:
        return max(ts - self.issued_at, 0.0)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(e.code for e in self.entitlements))

    @property
    def unclassified_gates(self) -> tuple[str, ...]:
        """Entitlement codes with no declared gate. Non-empty ⇒ an incomplete design
        (ADR-0023 E3); validation reports it as an error, not a warning."""
        declared = {g.code for g in self.gates}
        return tuple(sorted(e.code for e in self.entitlements if e.code not in declared))

    @property
    def fail_closed_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(g.code for g in self.gates if g.classification is GateClassification.FAIL_CLOSED)
        )

    @property
    def fingerprint(self) -> tuple:
        """A deterministic identity for *what this set says*, excluding when it was said.

        Two compositions of unchanged grants produce equal fingerprints, which is what makes
        "did anything actually change?" a comparison rather than an investigation — and what
        lets a publisher decline to mint a version that would say the same thing twice."""
        return tuple(
            (e.code, e.kind.value, e.enabled, e.allowance, e.allows_overage, e.source.value)
            for e in sorted(self.entitlements)
        )

    def to_snapshot(self) -> EntitlementSnapshot:
        """Project onto M9.2's frozen snapshot type — the published enforcement contract."""
        return EntitlementSnapshot(
            id=self.id,
            org_id=self.org_id,
            account_id=self.account_id,
            version=self.version,
            issued_at=self.issued_at,
            entitlements=tuple(e.as_entitlement() for e in self.entitlements),
            gates=self.gates,
            subscription_id=self.subscription_id,
            max_staleness_seconds=self.max_staleness_seconds,
        )


# ── staleness ───────────────────────────────────────────────────────────────────


class StaleDirective(StrEnum):
    """What an enforcing plane should do with a gate whose data it cannot refresh.

    Derived from the gate's declared classification — never decided here, and never decided at
    a call site. This enum exists so the answer travels *with* the snapshot instead of living in
    each consuming plane's head."""

    ALLOW = "allow"
    DENY = "deny"
    DEGRADE = "degrade"


#: Classification → what to do when the snapshot is stale (ADR-0023 E4). Declared as a table so
#: the mapping is one readable fact rather than a conditional in every enforcing plane.
STALE_DIRECTIVES: dict[GateClassification, StaleDirective] = {
    GateClassification.FAIL_OPEN: StaleDirective.ALLOW,
    GateClassification.FAIL_CLOSED: StaleDirective.DENY,
    GateClassification.DEGRADE: StaleDirective.DEGRADE,
}


@dataclass(frozen=True, slots=True)
class GateDirective:
    """One gate's staleness instruction, resolved for a given instant."""

    code: str
    classification: GateClassification
    directive: StaleDirective
    stale: bool


@dataclass(frozen=True, slots=True)
class StalenessReport:
    """What an enforcing plane should do *right now*, gate by gate.

    Answers the operational question directly rather than handing over a timestamp and a budget
    and hoping every consuming plane does the subtraction the same way."""

    account_id: str
    version: int
    issued_at: float
    at: float
    age: float
    max_staleness_seconds: float
    stale: bool
    directives: tuple[GateDirective, ...] = ()

    @property
    def denied_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                d.code for d in self.directives if d.stale and d.directive is StaleDirective.DENY
            )
        )

    @property
    def degraded_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                d.code for d in self.directives if d.stale and d.directive is StaleDirective.DEGRADE
            )
        )


# ── versions, history ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementVersion:
    """An immutable published entitlement decision.

    Self-contained, like a catalog, pricing, or subscription version: it embeds both the composed
    set *and* the profile that produced it, so reading version 3 never depends on what version 7
    did. Embedding the profile as well as the answer is what makes an old decision auditable
    rather than merely readable — the grants are right there, and the composition can be redone
    and compared.

    `rolled_back_from` records provenance when this version restored an earlier one. History is
    appended to; nothing is rewritten (§A5)."""

    id: str
    org_id: str
    account_id: str
    version: int
    published_at: float = 0.0
    published_by: str = ""
    reason: str = ""
    rolled_back_from: int | None = None
    entitlements: EntitlementSet | None = None
    profile: EntitlementProfile | None = None

    @property
    def is_rollback(self) -> bool:
        return self.rolled_back_from is not None

    @property
    def entitlement_count(self) -> int:
        return len(self.entitlements.entitlements) if self.entitlements else 0

    @property
    def grant_count(self) -> int:
        return len(self.profile.grants) if self.profile else 0

    @property
    def codes(self) -> tuple[str, ...]:
        return self.entitlements.codes if self.entitlements else ()


class EntitlementAction(StrEnum):
    """What happened to an account's entitlements. The activity log's closed vocabulary."""

    PROFILE_OPENED = "entitlement.profile_opened"
    DEFINITION_CREATED = "entitlement.definition_created"
    DEFINITION_UPDATED = "entitlement.definition_updated"
    DEFINITION_RETIRED = "entitlement.definition_retired"
    BUNDLE_CREATED = "entitlement.bundle_created"
    BUNDLE_UPDATED = "entitlement.bundle_updated"
    BUNDLE_GRANTED = "entitlement.bundle_granted"
    GRANTED = "entitlement.granted"
    ACTIVATED = "entitlement.activated"
    ADJUSTED = "entitlement.adjusted"
    SUSPENDED = "entitlement.suspended"
    RESUMED = "entitlement.resumed"
    REVOKED = "entitlement.revoked"
    EXPIRED = "entitlement.expired"
    ARCHIVED = "entitlement.archived"
    CHANGE_SCHEDULED = "entitlement.change_scheduled"
    CHANGE_CANCELLED = "entitlement.change_cancelled"
    VERSION_PUBLISHED = "entitlement.version_published"
    ROLLED_BACK = "entitlement.rolled_back"


@dataclass(frozen=True, slots=True)
class EntitlementActivity:
    """One append-only entry in an account's entitlement history: who changed what, when.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log; this is the account's ordered, queryable timeline, and an entitlement
    screen should not have to filter the whole revenue stream to render it."""

    id: str
    org_id: str
    account_id: str
    action: EntitlementAction
    actor_id: str = ""
    at: float = 0.0
    entitlement_code: str = ""
    grant_id: str = ""
    from_state: str = ""
    to_state: str = ""
    version: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EntitlementHistory:
    """The whole story of one account's entitlements: ordered activity plus the version line.

    Two sequences rather than one because they answer different questions — the activity says
    *what happened*, the versions say *what was decided*. A support conversation needs the
    first; a disputed capability needs the second."""

    account_id: str
    activity: tuple[EntitlementActivity, ...] = ()
    versions: tuple[EntitlementVersion, ...] = ()

    @property
    def event_count(self) -> int:
        return len(self.activity)

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def current_version(self) -> int:
        return max((v.version for v in self.versions), default=0)

    def version_at(self, at: float) -> EntitlementVersion | None:
        """What was decided at an instant — the question a disputed capability actually asks."""
        covering = [v for v in self.versions if v.published_at <= at]
        return max(covering, key=lambda v: v.version) if covering else None


# ── search ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntitlementSearchQuery:
    """A deterministic filter over grants. No relevance scoring and no index — the same grants
    and the same query always come back in the same order, which is what a paged admin screen
    and a reproducible test both need."""

    text: str = ""  # case-insensitive substring over code, bundle code, source ref, reason
    codes: tuple[str, ...] = ()
    states: tuple[GrantState, ...] = ()
    sources: tuple[GrantSource, ...] = ()
    kinds: tuple[FeatureKind, ...] = ()
    account_id: str = ""
    bundle_code: str = ""
    source_ref: str = ""
    effective_at: float | None = None
    live_only: bool = False
    bundled_only: bool = False
    unlimited_only: bool = False
    denials_only: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class EntitlementSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    grants: tuple[CommercialGrant, ...] = ()
    total: int = 0
