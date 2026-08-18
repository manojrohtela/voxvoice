"""Entitlement rules (M9.10) — validation, lifecycle, bundles, precedence, snapshots, staleness.

Every entitlement *decision* lives here: what makes a definition, a bundle, a grant, or a
schedule valid; which grant-lifecycle moves are legal; how bundles expand; how competing grants
are resolved; how a snapshot is composed and frozen; and what an enforcing plane should do when
its data is stale. Layers above coordinate these; they never re-decide.

Validation follows the Foundation discipline exactly — it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically.

Four disciplines are worth stating before the code:

* **Nothing here evaluates a permission.** No function takes a principal, a role, a scope, or a
  request. Resolution answers *what an account is entitled to*, never *who may do it*; §A11.2
  RBAC keeps authorization (ADR-0023 §11.3). A test greps this module for the vocabulary.
* **Nothing here executes a schedule.** `EntitlementScheduleService` validates and *reads*
  intent; there is no timer, no worker, and no auto-apply. A due change is a question the caller
  asks, not an action this module takes.
* **Nothing here reads a clock.** Every temporal decision takes `at` as an argument, which is
  what makes a snapshot composed for a past instant reproducible in a test and in an audit alike
  (§A2.4).
* **Nothing here computes an amount or consumes usage.** An allowance says how much is included;
  what that costs is pricing's and what has been used is metering's.

Pure: no I/O, no clock read, no persistence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from core.fsm import StateMachineDefinition

from .catalog import FeatureKind
from .commercial_entitlements import (
    GRANT_LIFECYCLE,
    MAX_BUNDLE_DEPTH,
    MAX_PENDING_ENTITLEMENT_CHANGES,
    STALE_DIRECTIVES,
    BundleEntry,
    CommercialGrant,
    EntitlementAction,
    EntitlementActivity,
    EntitlementBundle,
    EntitlementChangeKind,
    EntitlementChangeStatus,
    EntitlementDefinition,
    EntitlementExplanation,
    EntitlementHistory,
    EntitlementProfile,
    EntitlementSearchQuery,
    EntitlementSearchResult,
    EntitlementSet,
    EntitlementValue,
    EntitlementVersion,
    EntitlementWindow,
    GateDirective,
    GrantConsideration,
    GrantState,
    GrantTrigger,
    ResolvedEntitlement,
    ScheduledEntitlementChange,
    StalenessReport,
)
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .entitlement import GRANT_PRECEDENCE, GateClassification, GrantSource
from .errors import BillingConflict, InvalidBillingTransition

#: The band a `precedence_override` must stay inside. Overrides exist for the negotiated term
#: declared precedence cannot express; letting one sit outside the declared range would let a
#: single grant silently invert the whole ordering, which is the opposite of a declared policy.
MIN_PRECEDENCE = min(GRANT_PRECEDENCE.values())
MAX_PRECEDENCE = max(GRANT_PRECEDENCE.values()) + 10


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


# ── validation ──────────────────────────────────────────────────────────────────


class EntitlementValidator:
    """Structural, referential, and temporal validation for the entitlement domain.

    Never raises. Factories decide whether to fail fast on error-severity findings (§A2.6)."""

    # ── definitions ─────────────────────────────────────────────────────────────

    def validate_definition(self, definition: EntitlementDefinition) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not definition.org_id:
            out.append(_err("definition.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not definition.code:
            out.append(_err("definition.code", C.EMPTY_IDENTITY, "an entitlement must have a code"))
        if definition.version < 1:
            out.append(
                _err("definition.version", C.INVALID_CODE, "versions start at 1 and increase")
            )
        if definition.requires_meter and not definition.meter_code:
            out.append(
                _err(
                    "definition.meter_code",
                    C.MISSING_QUOTA_METER,
                    f"quota {definition.code!r} names no meter",
                    "a quota is an allowance of something measured; without a meter nobody can "
                    "say whether it has been consumed",
                )
            )
        if definition.kind is FeatureKind.FLAG and definition.default.allowance is not None:
            out.append(
                _warn(
                    "definition.default.allowance",
                    C.INVALID_ENTITLEMENT_VALUE,
                    "a flag carries an allowance, which nothing will read",
                )
            )
        if definition.max_staleness_seconds <= 0:
            out.append(
                _err(
                    "definition.max_staleness_seconds",
                    C.INVALID_STALENESS_BUDGET,
                    "a staleness budget must be positive",
                    "zero would make every snapshot stale on arrival",
                )
            )
        out.extend(self.validate_value(definition.default, path="definition.default").findings)
        return BillingDiagnostics.of(out)

    def validate_definitions(
        self, definitions: Sequence[EntitlementDefinition]
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        for dupe in _duplicates(d.code for d in definitions):
            out.append(
                _err(
                    "definitions",
                    C.DUPLICATE_ENTITLEMENT_CODE,
                    f"entitlement code {dupe!r} is defined more than once",
                    "one definition per code; changing one publishes a new version of it",
                )
            )
        for definition in definitions:
            out.extend(self.validate_definition(definition).findings)
        return BillingDiagnostics.of(out)

    # ── values ──────────────────────────────────────────────────────────────────

    def validate_value(self, value: EntitlementValue, *, path: str = "value") -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if value.allowance is not None and value.allowance < 0:
            out.append(
                _err(
                    f"{path}.allowance",
                    C.INVALID_ALLOWANCE,
                    "an allowance cannot be negative",
                    "use 0 for 'explicitly none' and null for 'unlimited'",
                )
            )
        if value.allows_overage and not value.enabled:
            out.append(
                _err(
                    f"{path}.allows_overage",
                    C.INVALID_ENTITLEMENT_VALUE,
                    "a disabled capability cannot bill overage",
                    "overage is consumption beyond an allowance; there is nothing to exceed",
                )
            )
        if value.allows_overage and value.allowance is None:
            out.append(
                _warn(
                    f"{path}.allows_overage",
                    C.INVALID_ENTITLEMENT_VALUE,
                    "an unlimited capability declares overage, which can never occur",
                )
            )
        return BillingDiagnostics.of(out)

    # ── bundles ─────────────────────────────────────────────────────────────────

    def validate_bundle(
        self,
        bundle: EntitlementBundle,
        *,
        known_codes: frozenset[str] | None = None,
        known_bundles: frozenset[str] | None = None,
    ) -> BillingDiagnostics:
        """`known_*=None` means "not checked here"; an **empty set** means "nothing is defined",
        which must reject the reference rather than skip the check — the distinction an
        empty-collection falsiness test silently loses, and the defect M9.6 caught the hard way."""
        out: list[Diagnostic] = []
        if not bundle.org_id:
            out.append(_err("bundle.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not bundle.code:
            out.append(_err("bundle.code", C.EMPTY_IDENTITY, "a bundle must have a code"))
        if not bundle.entries and not bundle.member_bundles:
            out.append(
                _err(
                    "bundle.entries",
                    C.EMPTY_ENTITLEMENT_BUNDLE,
                    "a bundle that confers nothing is not a bundle",
                )
            )
        for dupe in _duplicates(e.code for e in bundle.entries):
            out.append(
                _err(
                    "bundle.entries",
                    C.DUPLICATE_ENTITLEMENT_CODE,
                    f"{dupe!r} appears twice in this bundle",
                )
            )
        for dupe in _duplicates(bundle.member_bundles):
            out.append(
                _err(
                    "bundle.member_bundles",
                    C.DUPLICATE_ENTITLEMENT_BUNDLE_CODE,
                    f"member bundle {dupe!r} is included twice",
                )
            )
        if bundle.code and bundle.code in bundle.member_bundles:
            out.append(
                _err(
                    "bundle.member_bundles",
                    C.ENTITLEMENT_BUNDLE_CYCLE,
                    f"bundle {bundle.code!r} includes itself",
                )
            )
        for i, entry in enumerate(bundle.entries):
            path = f"bundle.entries[{i}]"
            if not entry.code:
                out.append(_err(f"{path}.code", C.EMPTY_IDENTITY, "a bundle entry needs a code"))
            elif known_codes is not None and entry.code not in known_codes:
                out.append(
                    _err(
                        f"{path}.code",
                        C.UNKNOWN_ENTITLEMENT_DEFINITION,
                        f"no entitlement {entry.code!r} is defined",
                        "a bundle can only confer capabilities that have been declared",
                    )
                )
            out.extend(self.validate_value(entry.value, path=path).findings)
        if known_bundles is not None:
            for member in bundle.member_bundles:
                if member not in known_bundles:
                    out.append(
                        _err(
                            "bundle.member_bundles",
                            C.UNKNOWN_ENTITLEMENT_BUNDLE,
                            f"no bundle {member!r} exists",
                        )
                    )
        return BillingDiagnostics.of(out)

    # ── grants ──────────────────────────────────────────────────────────────────

    def validate_grant(
        self,
        grant: CommercialGrant,
        *,
        definition: EntitlementDefinition | None = None,
        known_codes: frozenset[str] | None = None,
        subscription_state: str | None = None,
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not grant.org_id:
            out.append(_err("grant.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not grant.account_id:
            out.append(
                _err("grant.account_id", C.EMPTY_IDENTITY, "a grant belongs to a billing account")
            )
        if not grant.entitlement_code:
            out.append(
                _err("grant.entitlement_code", C.EMPTY_IDENTITY, "a grant must name a capability")
            )
        elif known_codes is not None and grant.entitlement_code not in known_codes:
            out.append(
                _err(
                    "grant.entitlement_code",
                    C.UNKNOWN_ENTITLEMENT_DEFINITION,
                    f"no entitlement {grant.entitlement_code!r} is defined",
                    "an undeclared capability has no gate, and a gate is what an enforcing "
                    "plane needs to behave correctly when it cannot reach Billing",
                )
            )
        if definition is not None and definition.is_retired:
            out.append(
                _warn(
                    "grant.entitlement_code",
                    C.RETIRED_ENTITLEMENT_DEFINITION,
                    f"entitlement {definition.code!r} is retired",
                    "existing grants keep working; new ones probably want the replacement",
                )
            )
        out.extend(self.validate_window(grant.window).findings)
        out.extend(self.validate_value(grant.value, path="grant.value").findings)
        out.extend(self._precedence(grant))
        out.extend(self._source_reference(grant, subscription_state))
        return BillingDiagnostics.of(out)

    def validate_window(self, window: EntitlementWindow) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not window.is_valid:
            out.append(
                _err(
                    "grant.window",
                    C.INVALID_GRANT_WINDOW,
                    "a grant window must end after it starts",
                    "windows are half-open [starts_at, ends_at)",
                )
            )
        return BillingDiagnostics.of(out)

    @staticmethod
    def _precedence(grant: CommercialGrant) -> list[Diagnostic]:
        override = grant.precedence_override
        if override is None:
            return []
        if not MIN_PRECEDENCE <= override <= MAX_PRECEDENCE:
            return [
                _err(
                    "grant.precedence_override",
                    C.INVALID_PRECEDENCE_OVERRIDE,
                    f"{override} is outside the declared band {MIN_PRECEDENCE}..{MAX_PRECEDENCE}",
                    "an override outside the band would silently invert the whole ordering",
                )
            ]
        return []

    @staticmethod
    def _source_reference(
        grant: CommercialGrant, subscription_state: str | None
    ) -> list[Diagnostic]:
        """`subscription_state` arrives as a *string* deliberately: this domain refers to an
        agreement, and refusing to import its enum keeps the dependency one-way and shallow —
        the same call M9.9 made about accounts."""
        out: list[Diagnostic] = []
        if grant.source in (GrantSource.CONTRACT, GrantSource.PARTNER) and not grant.source_ref:
            out.append(
                _warn(
                    "grant.source_ref",
                    C.CONFLICTING_GRANT,
                    f"a {grant.source.value} grant names no source document",
                    "the reference is what makes the grant explicable a year from now",
                )
            )
        if subscription_state is None:
            return out
        if subscription_state == "":
            out.append(
                _err(
                    "grant.source_ref",
                    C.UNKNOWN_SUBSCRIPTION_REFERENCE,
                    f"no subscription {grant.source_ref!r} in this organization",
                )
            )
        return out

    def validate_grants(self, grants: Sequence[CommercialGrant]) -> BillingDiagnostics:
        """Cross-grant coherence: duplicate ids, and same-source overlapping windows for one code.

        Overlap between *different* sources is not an error — that is exactly what precedence is
        for. Overlap within the same source is almost always a double entry, so it is reported
        as a warning rather than silently resolved by a tie-break nobody intended."""
        out: list[Diagnostic] = []
        for dupe in _duplicates(g.id for g in grants):
            out.append(
                _err("profile.grants", C.DUPLICATE_ID, f"grant id {dupe!r} appears more than once")
            )
        live = [g for g in grants if g.is_live]
        for i, first in enumerate(live):
            for second in live[i + 1 :]:
                if (
                    first.entitlement_code == second.entitlement_code
                    and first.source is second.source
                    and first.window.overlaps(second.window)
                ):
                    out.append(
                        _warn(
                            "profile.grants",
                            C.OVERLAPPING_GRANT_WINDOW,
                            f"two {first.source.value} grants of {first.entitlement_code!r} are "
                            "in force at the same time",
                            "precedence will pick the more generous, which is rarely what a "
                            "double entry intended",
                        )
                    )
        return BillingDiagnostics.of(out)

    # ── profile ─────────────────────────────────────────────────────────────────

    def validate_profile(
        self,
        profile: EntitlementProfile,
        *,
        known_codes: frozenset[str] | None = None,
        at: float | None = None,
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not profile.org_id:
            out.append(_err("profile.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not profile.account_id:
            out.append(
                _err(
                    "profile.account_id",
                    C.EMPTY_IDENTITY,
                    "an entitlement profile belongs to a billing account",
                )
            )
        if profile.version < 0:
            out.append(
                _err(
                    "profile.version",
                    C.INVALID_CODE,
                    "versions start at 1; 0 means nothing has been published yet",
                )
            )
        if profile.max_staleness_seconds <= 0:
            out.append(
                _err(
                    "profile.max_staleness_seconds",
                    C.INVALID_STALENESS_BUDGET,
                    "a staleness budget must be positive",
                )
            )
        out.extend(self.validate_grants(profile.grants).findings)
        for grant in profile.grants:
            out.extend(self.validate_grant(grant, known_codes=known_codes).findings)
        out.extend(self.validate_schedule(profile.scheduled_changes, at=at).findings)
        return BillingDiagnostics.of(out)

    # ── schedule ────────────────────────────────────────────────────────────────

    def validate_schedule(
        self, changes: Sequence[ScheduledEntitlementChange], *, at: float | None = None
    ) -> BillingDiagnostics:
        """Scheduled changes are *intent*. Validation checks that the intent is coherent and
        unambiguous, never that it is executable — nothing executes it here."""
        out: list[Diagnostic] = []
        pending = [c for c in changes if c.is_pending]

        for dupe in _duplicates(c.id for c in changes):
            out.append(
                _err(
                    "profile.scheduled_changes",
                    C.INVALID_SCHEDULED_ENTITLEMENT_CHANGE,
                    f"change id {dupe!r} appears more than once",
                )
            )
        if len(pending) > MAX_PENDING_ENTITLEMENT_CHANGES:
            out.append(
                _err(
                    "profile.scheduled_changes",
                    C.CONFLICTING_SCHEDULED_ENTITLEMENT_CHANGE,
                    f"{len(pending)} pending changes; the maximum is "
                    f"{MAX_PENDING_ENTITLEMENT_CHANGES}",
                    "beyond this nobody can say what the account will be entitled to",
                )
            )

        for i, change in enumerate(changes):
            path = f"profile.scheduled_changes[{i}]"
            if change.effective_at <= 0:
                out.append(
                    _err(
                        f"{path}.effective_at",
                        C.INVALID_SCHEDULED_ENTITLEMENT_CHANGE,
                        "a scheduled change must say when it takes effect",
                    )
                )
            elif at is not None and change.is_pending and change.effective_at < at:
                out.append(
                    _warn(
                        f"{path}.effective_at",
                        C.SCHEDULED_CHANGE_IN_PAST,
                        "this change was due in the past and is still pending",
                        "nothing in this domain applies it; a caller must dispatch the command",
                    )
                )
            if change.kind is EntitlementChangeKind.BUNDLE_GRANT and not change.bundle_code:
                out.append(
                    _err(
                        f"{path}.bundle_code",
                        C.INVALID_SCHEDULED_ENTITLEMENT_CHANGE,
                        "a scheduled bundle grant must name the bundle",
                    )
                )
            if change.kind is EntitlementChangeKind.GRANT and not change.entitlement_code:
                out.append(
                    _err(
                        f"{path}.entitlement_code",
                        C.INVALID_SCHEDULED_ENTITLEMENT_CHANGE,
                        "a scheduled grant must name the capability",
                    )
                )
            if change.targets_a_grant and not change.grant_id:
                out.append(
                    _err(
                        f"{path}.grant_id",
                        C.INVALID_SCHEDULED_ENTITLEMENT_CHANGE,
                        f"a scheduled {change.kind.value} must name the grant it acts on",
                    )
                )
            out.extend(self.validate_value(change.value, path=f"{path}.value").findings)

        # Two pending changes to the same grant, or a grant scheduled to be revoked and then
        # adjusted, is an ambiguous future rather than a plan.
        by_grant: dict[str, int] = {}
        for change in pending:
            if change.grant_id:
                by_grant[change.grant_id] = by_grant.get(change.grant_id, 0) + 1
        for grant_id, count in sorted(by_grant.items()):
            if count > 1:
                out.append(
                    _err(
                        "profile.scheduled_changes",
                        C.CONFLICTING_SCHEDULED_ENTITLEMENT_CHANGE,
                        f"{count} pending changes target grant {grant_id!r}",
                        "the order they would apply in is not stated anywhere",
                    )
                )
        return BillingDiagnostics.of(out)

    # ── snapshots ───────────────────────────────────────────────────────────────

    def validate_set(self, entitlements: EntitlementSet) -> BillingDiagnostics:
        """ADR-0023 E3: **every entitlement must carry a gate classification.** An entitlement
        with no gate is an incomplete design — an enforcing plane that cannot reach Billing has
        nothing to fall back on — so this is an error, not a warning."""
        out: list[Diagnostic] = []
        for code in entitlements.unclassified_gates:
            out.append(
                _err(
                    f"snapshot.entitlements[{code}]",
                    C.UNCLASSIFIED_GATE,
                    f"entitlement {code!r} has no gate classification",
                    "declare fail_open, fail_closed, or degrade on its definition",
                )
            )
        for dupe in _duplicates(e.code for e in entitlements.entitlements):
            out.append(
                _err(
                    f"snapshot.entitlements[{dupe}]",
                    C.DUPLICATE_ENTITLEMENT,
                    "resolved twice; precedence should have produced one answer",
                )
            )
        for e in entitlements.entitlements:
            if e.allowance is not None and e.allowance < 0:
                out.append(
                    _err(
                        f"snapshot.entitlements[{e.code}].allowance",
                        C.INVALID_ALLOWANCE,
                        "a resolved allowance cannot be negative",
                    )
                )
        if entitlements.max_staleness_seconds <= 0:
            out.append(
                _err(
                    "snapshot.max_staleness_seconds",
                    C.INVALID_STALENESS_BUDGET,
                    "a staleness budget must be positive",
                )
            )
        if entitlements.version < 1:
            out.append(
                _err(
                    "snapshot.version",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    "published versions start at 1",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_version(self, version: EntitlementVersion) -> BillingDiagnostics:
        """Version integrity — a published version must be internally complete, because nothing
        may ever be added to it."""
        out: list[Diagnostic] = []
        if version.version < 1:
            out.append(
                _err(
                    "entitlement_version.version",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    "versions start at 1 and increase",
                )
            )
        if not version.org_id:
            out.append(_err("entitlement_version.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not version.account_id:
            out.append(
                _err(
                    "entitlement_version.account_id",
                    C.EMPTY_IDENTITY,
                    "a version belongs to an account",
                )
            )
        if version.entitlements is None:
            out.append(
                _err(
                    "entitlement_version.entitlements",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    "a version must embed the decision it froze",
                    "a version that pointed at live grants would not be a snapshot",
                )
            )
        elif version.entitlements.version != version.version:
            out.append(
                _err(
                    "entitlement_version.entitlements",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    f"version {version.version} embeds a set at v{version.entitlements.version}",
                )
            )
        else:
            out.extend(self.validate_set(version.entitlements).findings)
        if version.profile is None:
            out.append(
                _warn(
                    "entitlement_version.profile",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    "a version embeds no profile, so the decision cannot be recomputed",
                    "embedding the grants is what makes an old decision auditable, not merely "
                    "readable",
                )
            )
        if version.rolled_back_from is not None and version.rolled_back_from >= version.version:
            out.append(
                _err(
                    "entitlement_version.rolled_back_from",
                    C.ENTITLEMENT_SNAPSHOT_INTEGRITY,
                    "a rollback source must be an earlier version",
                    "rollback moves forward by copying backward — history is never rewritten",
                )
            )
        return BillingDiagnostics.of(out)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class GrantLifecycleService:
    """Applies grant lifecycle triggers through the shared FSM. Illegal edges are rejected with
    a typed error, never coerced into an adjacent state."""

    def __init__(self, definition: StateMachineDefinition = GRANT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: GrantState, trigger: GrantTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: GrantState, trigger: GrantTrigger) -> GrantState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"grant cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: GrantState) -> tuple[GrantTrigger, ...]:
        """Which moves are legal from here — what a UI needs to render buttons that cannot
        produce a 409."""
        return tuple(t for t in GrantTrigger if self.can(current, t))

    def apply(
        self,
        grant: CommercialGrant,
        trigger: GrantTrigger,
        *,
        at: float,
        reason: str = "",
    ) -> CommercialGrant:
        state = self.next_state(grant.state, trigger)
        changes: dict = {"state": state, "updated_at": at}
        if trigger is GrantTrigger.ACTIVATE:
            changes["activated_at"] = grant.activated_at or at
            # An activation with no explicit start means "from now": leaving it unbounded would
            # make the grant retroactive, and a capability that quietly applied to last month is
            # a different promise from the one that was made.
            if grant.window.starts_at is None:
                changes["window"] = replace(grant.window, starts_at=at)
        if trigger is GrantTrigger.SUSPEND:
            changes["suspended_at"] = at
        if trigger is GrantTrigger.RESUME:
            changes["suspended_at"] = None
        if trigger is GrantTrigger.REVOKE:
            changes["revoked_at"] = at
            changes["revoked_reason"] = reason or grant.revoked_reason
            # Close the window as well as the state, so a snapshot composed for an instant after
            # the revocation cannot re-admit the grant through effective dating.
            if grant.window.ends_at is None or grant.window.ends_at > at:
                changes["window"] = replace(grant.window, ends_at=at)
        if trigger is GrantTrigger.EXPIRE:
            changes["expired_at"] = at
        if trigger is GrantTrigger.ARCHIVE:
            changes["archived_at"] = at
        return replace(grant, **changes)

    def guard_editable(self, grant: CommercialGrant) -> None:
        """A closed grant is a record of what was conferred and withdrawn. Editing it after the
        fact would change the record rather than the entitlement."""
        if not grant.is_editable:
            raise BillingConflict(
                f"grant {grant.id!r} is {grant.state.value}; it is a retained record and is no "
                "longer editable"
            )


# ── bundles ─────────────────────────────────────────────────────────────────────


class EntitlementBundleExpander:
    """Expands bundles — including nested ones — into a flat, deterministic set of entries.

    Cycles are **detected and reported with the path**, never allowed to recurse: a bundle graph
    is customer-facing packaging, and "Enterprise includes Partner includes Enterprise" is a
    mistake somebody made, not an infinite capability."""

    def find_cycle(self, bundles: Sequence[EntitlementBundle], start: str) -> tuple[str, ...]:
        """The cycle through `start`, as the path that closes it, or `()`. Returning the path
        rather than a boolean is deliberate: whoever has to fix it needs to see the loop."""
        index = {b.code: b for b in bundles}
        path: list[str] = []
        visiting: set[str] = set()

        def walk(code: str) -> tuple[str, ...]:
            if code in visiting:
                return tuple(path[path.index(code) :] + [code])
            bundle = index.get(code)
            if bundle is None:
                return ()
            visiting.add(code)
            path.append(code)
            for member in bundle.member_bundles:
                found = walk(member)
                if found:
                    return found
            path.pop()
            visiting.discard(code)
            return ()

        return walk(start)

    def depth(self, bundles: Sequence[EntitlementBundle], code: str) -> int:
        """How deep this bundle nests. Guarded by the cycle check, so it terminates."""
        index = {b.code: b for b in bundles}
        if self.find_cycle(bundles, code):
            return MAX_BUNDLE_DEPTH + 1
        bundle = index.get(code)
        if bundle is None or not bundle.member_bundles:
            return 1
        return 1 + max(self.depth(bundles, member) for member in bundle.member_bundles)

    def expand(self, bundles: Sequence[EntitlementBundle], code: str) -> tuple[BundleEntry, ...]:
        """The flat entry set this bundle confers.

        A nearer definition wins over a deeper one — an outer bundle that restates a capability
        is overriding the pack it includes, which is exactly why it restated it. Ordering is by
        code so two expansions of the same graph are byte-identical."""
        index = {b.code: b for b in bundles}
        if self.find_cycle(bundles, code):
            raise BillingConflict(
                f"entitlement bundle {code!r} is part of a cycle: "
                f"{' → '.join(self.find_cycle(bundles, code))}"
            )
        merged: dict[str, BundleEntry] = {}

        def walk(current: str, depth: int) -> None:
            if depth > MAX_BUNDLE_DEPTH:
                raise BillingConflict(
                    f"entitlement bundle {code!r} nests deeper than {MAX_BUNDLE_DEPTH}"
                )
            bundle = index.get(current)
            if bundle is None:
                raise BillingConflict(f"no entitlement bundle {current!r}")
            for member in bundle.member_bundles:  # deepest first, so nearer entries overwrite
                walk(member, depth + 1)
            for entry in bundle.entries:
                merged[entry.code] = entry

        walk(code, 1)
        return tuple(merged[k] for k in sorted(merged))

    def codes(self, bundles: Sequence[EntitlementBundle], code: str) -> tuple[str, ...]:
        return tuple(e.code for e in self.expand(bundles, code))


# ── precedence resolution ───────────────────────────────────────────────────────


class EntitlementPrecedenceResolver:
    """Composes grants into resolved entitlements. **Decides; never enforces.**

    Nothing in this class takes a principal, a role, or a request — entitlement is commercial
    capability, and authorization stays entirely with §A11.2 RBAC (ADR-0023 §11.3).

    Resolution is deterministic and fully ordered, with the tie-breaks declared rather than
    emergent:

    1. **declared source precedence** (`GRANT_PRECEDENCE`, or an in-band override) — highest wins;
    2. **generosity** — an explicit denial loses to any grant, and unlimited beats any number;
    3. **grant id**, lexicographically — an arbitrary but *stable* last resort, so two machines
       composing the same grants never disagree.

    Insertion order is never consulted. A resolution that depended on the order rows came back
    in would be reproducible only by accident."""

    def resolve(
        self,
        grants: Sequence[CommercialGrant],
        definitions: Sequence[EntitlementDefinition],
        *,
        at: float,
        include_defaults: bool = False,
    ) -> tuple[ResolvedEntitlement, ...]:
        """The composed set at an instant.

        `include_defaults=True` emits a definition's declared default for codes nobody granted.
        Off by default: a snapshot that silently asserted a default the customer was never given
        would make "why does this account have X?" unanswerable from the grants."""
        by_code = self._definitions_by_code(definitions)
        best: dict[str, tuple[int, int, str, ResolvedEntitlement]] = {}
        for grant in grants:
            if not grant.is_effective_at(at):
                continue
            definition = by_code.get(grant.entitlement_code)
            candidate = self._resolved(grant, definition)
            key = (grant.precedence, grant.value.generosity, grant.id)
            current = best.get(grant.entitlement_code)
            if (
                current is None
                or (key[0], key[1]) > (current[0], current[1])
                or ((key[0], key[1]) == (current[0], current[1]) and grant.id < current[2])
            ):
                best[grant.entitlement_code] = (key[0], key[1], grant.id, candidate)

        resolved = {code: value[3] for code, value in best.items()}
        if include_defaults:
            for definition in definitions:
                if definition.is_retired or definition.code in resolved:
                    continue
                resolved[definition.code] = ResolvedEntitlement(
                    code=definition.code,
                    kind=definition.kind,
                    enabled=definition.default.enabled,
                    allowance=definition.default.allowance,
                    allows_overage=definition.default.allows_overage,
                    source=GrantSource.PLAN,
                    classification=definition.classification,
                    resolution_path=(f"no grant; declared default of {definition.code!r}",),
                )
        return tuple(sorted(resolved.values()))

    def explain(
        self,
        grants: Sequence[CommercialGrant],
        definitions: Sequence[EntitlementDefinition],
        code: str,
        *,
        at: float,
    ) -> EntitlementExplanation:
        """Why this account does — or does not — have this capability.

        Losing grants are kept in the answer. "Why does this account *not* have X?" is asked at
        least as often as the opposite, and it is unanswerable from the winner alone."""
        by_code = self._definitions_by_code(definitions)
        candidates = [g for g in grants if g.entitlement_code == code]
        winner = next(iter(self.resolve(candidates, definitions, at=at)), None)
        considered: list[GrantConsideration] = []
        for grant in sorted(candidates, key=lambda g: (-g.precedence, g.id)):
            effective = grant.is_effective_at(at)
            considered.append(
                GrantConsideration(
                    grant_id=grant.id,
                    source=grant.source,
                    precedence=grant.precedence,
                    value=grant.value,
                    effective=effective,
                    won=bool(winner and winner.grant_id == grant.id),
                    excluded_reason=self._exclusion(grant, at),
                )
            )
        return EntitlementExplanation(
            code=code,
            at=at,
            winner=winner,
            considered=tuple(considered),
            definition_known=code in by_code,
        )

    @staticmethod
    def _exclusion(grant: CommercialGrant, at: float) -> str:
        """Why a grant took no part, as a sentence, or `""`."""
        if grant.is_live and grant.window.covers(at):
            return ""
        if not grant.is_live:
            return f"the grant is {grant.state.value}"
        if grant.window.starts_at is not None and at < grant.window.starts_at:
            return f"the grant does not start until {grant.window.starts_at}"
        return f"the grant ended at {grant.window.ends_at}"

    def _resolved(
        self, grant: CommercialGrant, definition: EntitlementDefinition | None
    ) -> ResolvedEntitlement:
        path = [
            f"grant {grant.id} from {grant.source.value} (precedence {grant.precedence})",
        ]
        if grant.precedence_override is not None:
            path.append(f"precedence overridden to {grant.precedence_override}")
        if grant.bundle_code:
            path.append(f"conferred by bundle {grant.bundle_code!r}")
        if grant.window.starts_at is not None or grant.window.ends_at is not None:
            path.append(f"in force [{grant.window.starts_at}, {grant.window.ends_at})")
        if definition is None:
            path.append("no definition found; gate classification unknown")
        return ResolvedEntitlement(
            code=grant.entitlement_code,
            kind=definition.kind if definition else FeatureKind.FLAG,
            enabled=grant.value.enabled,
            allowance=grant.value.allowance,
            allows_overage=grant.value.allows_overage,
            source=grant.source,
            classification=(
                definition.classification if definition else GateClassification.FAIL_CLOSED
            ),
            grant_id=grant.id,
            precedence=grant.precedence,
            from_bundle=grant.bundle_code,
            resolution_path=tuple(path),
        )

    @staticmethod
    def _definitions_by_code(
        definitions: Sequence[EntitlementDefinition],
    ) -> dict[str, EntitlementDefinition]:
        return {d.code: d for d in definitions}


# ── snapshot composition ────────────────────────────────────────────────────────


class EntitlementAssembler:
    """Composes entitlement sets, freezes them into immutable versions, and rolls forward.

    Pure and deterministic: the same profile, the same definitions, and the same instant always
    produce an identical set — which is what makes "did anything actually change between v3 and
    v4?" a comparison of fingerprints rather than an investigation."""

    def __init__(
        self,
        resolver: EntitlementPrecedenceResolver | None = None,
    ) -> None:
        self._resolver = resolver or EntitlementPrecedenceResolver()

    def compose(
        self,
        profile: EntitlementProfile,
        definitions: Sequence[EntitlementDefinition],
        *,
        snapshot_id: str,
        version: int,
        at: float,
        include_defaults: bool = False,
    ) -> EntitlementSet:
        """Resolve the profile's grants into the statement enforcing planes read.

        Gates are emitted for **every declared definition**, not only for codes that resolved.
        An enforcing plane asking about a capability nobody granted still needs to know how to
        behave when it cannot reach Billing, and a gate that appeared only once a grant existed
        would leave exactly that case undefined."""
        entitlements = self._resolver.resolve(
            profile.effective_grants(at), definitions, at=at, include_defaults=include_defaults
        )
        gates = tuple(
            sorted((d.as_gate() for d in definitions if not d.is_retired), key=lambda g: g.code)
        )
        return EntitlementSet(
            id=snapshot_id,
            org_id=profile.org_id,
            account_id=profile.account_id,
            version=version,
            issued_at=at,
            entitlements=entitlements,
            gates=gates,
            subscription_id=profile.subscription_id,
            max_staleness_seconds=profile.max_staleness_seconds,
            grant_count=len(profile.grants),
            resolved_from=tuple(sorted({e.grant_id for e in entitlements if e.grant_id})),
        )

    def freeze(
        self,
        profile: EntitlementProfile,
        entitlements: EntitlementSet,
        *,
        version_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        reason: str = "",
        rolled_back_from: int | None = None,
    ) -> EntitlementVersion:
        """Embed the decision *and* the grants that produced it. Both are needed: the decision is
        what was published, the grants are what make it auditable rather than merely readable."""
        frozen_profile = replace(
            profile,
            version=version,
            grants=tuple(sorted(profile.grants, key=lambda g: (g.entitlement_code, g.id))),
            scheduled_changes=tuple(
                sorted(profile.scheduled_changes, key=lambda c: (c.effective_at, c.id))
            ),
        )
        return EntitlementVersion(
            id=version_id,
            org_id=profile.org_id,
            account_id=profile.account_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            reason=reason,
            rolled_back_from=rolled_back_from,
            entitlements=replace(entitlements, version=version),
            profile=frozen_profile,
        )

    def rollback(
        self,
        source: EntitlementVersion,
        *,
        version_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        reason: str = "",
    ) -> EntitlementVersion:
        """A rollback is a **new version** whose decision is copied from an older one.

        Nothing is rewritten and nothing is deleted: the versions in between remain readable
        forever, and the new version records where its decision came from. An entitlement
        snapshot is what an enforcing plane acted on — rewriting one would erase the reason a
        past request was allowed or refused."""
        if version <= source.version:
            raise BillingConflict(
                f"rollback must publish a *new* version; {version} does not follow {source.version}"
            )
        if source.entitlements is None:
            raise BillingConflict("cannot roll back to a version that embeds no decision")
        return EntitlementVersion(
            id=version_id,
            org_id=source.org_id,
            account_id=source.account_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            reason=reason or f"rollback to v{source.version}",
            rolled_back_from=source.version,
            entitlements=replace(source.entitlements, version=version, issued_at=published_at),
            profile=(
                replace(source.profile, version=version, updated_at=published_at)
                if source.profile
                else None
            ),
        )

    def says_the_same_as(self, candidate: EntitlementSet, previous: EntitlementSet | None) -> bool:
        """Whether publishing would restate the previous decision verbatim.

        Offered as a *question*, not enforced: a caller may legitimately want a version boundary
        for an audit even when nothing changed. What it prevents is publishing a hundred
        identical versions by accident and losing the ones that meant something."""
        return previous is not None and candidate.fingerprint == previous.fingerprint

    def history(
        self,
        account_id: str,
        *,
        activity: Sequence[EntitlementActivity] = (),
        versions: Sequence[EntitlementVersion] = (),
    ) -> EntitlementHistory:
        return EntitlementHistory(
            account_id=account_id,
            activity=tuple(sorted(activity, key=lambda a: (a.at, a.id))),
            versions=tuple(sorted(versions, key=lambda v: v.version)),
        )


# ── staleness ───────────────────────────────────────────────────────────────────


class EntitlementStalenessService:
    """Turns "how old is this snapshot?" into "what should I do about each gate?".

    The directive comes from the gate's declared classification through one table
    (`STALE_DIRECTIVES`), so every enforcing plane gets the same answer. ADR-0023 E4 chose these
    defaults deliberately: anything that could interrupt a live conversation fails **open**,
    anything that could create unbounded new cost fails **closed**. Bounded, measured revenue
    leakage is preferred, explicitly, to a customer-visible interruption."""

    def report(self, entitlements: EntitlementSet, *, at: float) -> StalenessReport:
        stale = entitlements.is_stale_at(at)
        directives = tuple(
            GateDirective(
                code=gate.code,
                classification=gate.classification,
                directive=STALE_DIRECTIVES[gate.classification],
                stale=stale,
            )
            for gate in sorted(entitlements.gates, key=lambda g: g.code)
        )
        return StalenessReport(
            account_id=entitlements.account_id,
            version=entitlements.version,
            issued_at=entitlements.issued_at,
            at=at,
            age=entitlements.age_at(at),
            max_staleness_seconds=entitlements.max_staleness_seconds,
            stale=stale,
            directives=directives,
        )

    def directive_for(
        self, entitlements: EntitlementSet, code: str, *, at: float
    ) -> GateDirective | None:
        gate = entitlements.gate(code)
        if gate is None:
            return None
        return GateDirective(
            code=code,
            classification=gate.classification,
            directive=STALE_DIRECTIVES[gate.classification],
            stale=entitlements.is_stale_at(at),
        )


# ── scheduled changes ───────────────────────────────────────────────────────────


class EntitlementScheduleService:
    """Reads and reasons about scheduled entitlement changes. **Applies nothing.**

    There is no timer here, no worker, and no auto-apply. `due` answers a question; turning that
    answer into a change is an explicit command a caller dispatches, validated at that moment
    exactly as if a human had asked."""

    def due(
        self, profile: EntitlementProfile, *, at: float
    ) -> tuple[ScheduledEntitlementChange, ...]:
        return profile.due_changes(at)

    def next_change(self, profile: EntitlementProfile) -> ScheduledEntitlementChange | None:
        pending = profile.pending_changes
        return pending[0] if pending else None

    def conflicts_with(
        self, profile: EntitlementProfile, candidate: ScheduledEntitlementChange
    ) -> str:
        """Why this change cannot be added, as a sentence, or `""`."""
        pending = profile.pending_changes
        if len(pending) >= MAX_PENDING_ENTITLEMENT_CHANGES:
            return f"there are already {len(pending)} pending changes"
        if candidate.grant_id and any(c.grant_id == candidate.grant_id for c in pending):
            return f"a change is already scheduled for grant {candidate.grant_id!r}"
        if candidate.targets_a_grant and profile.grant(candidate.grant_id) is None:
            return f"grant {candidate.grant_id!r} is not on this profile"
        return ""

    def mark_applied(
        self, profile: EntitlementProfile, change_id: str, *, at: float
    ) -> EntitlementProfile:
        """Record that a change was applied. The *applying* is the caller's command; this only
        moves the intent out of the pending set, so the record and the reality agree."""
        return self._restatus(profile, change_id, EntitlementChangeStatus.APPLIED, at=at)

    def cancel_change(
        self, profile: EntitlementProfile, change_id: str, *, at: float
    ) -> EntitlementProfile:
        return self._restatus(profile, change_id, EntitlementChangeStatus.CANCELLED, at=at)

    @staticmethod
    def _restatus(
        profile: EntitlementProfile,
        change_id: str,
        status: EntitlementChangeStatus,
        *,
        at: float,
    ) -> EntitlementProfile:
        found = profile.scheduled_change(change_id)
        if found is None:
            raise BillingConflict(f"scheduled change {change_id!r} is not on this profile")
        if not found.is_pending:
            raise BillingConflict(f"scheduled change {change_id!r} is already {found.status.value}")
        moved = replace(
            found,
            status=status,
            applied_at=at if status is EntitlementChangeStatus.APPLIED else found.applied_at,
        )
        return replace(
            profile,
            scheduled_changes=tuple(
                moved if c.id == change_id else c for c in profile.scheduled_changes
            ),
            updated_at=at,
        )


# ── search ──────────────────────────────────────────────────────────────────────


class EntitlementSearchService:
    """Deterministic grant filtering. No scoring, no ranking, no index — the same grants and the
    same query always produce the same rows in the same order.

    Ordering is by `(entitlement code, grant id)`, always. Arbitrary but *stable*, which is the
    property a paged admin screen and a reproducible test both actually need."""

    def search(
        self, grants: Sequence[CommercialGrant], query: EntitlementSearchQuery
    ) -> EntitlementSearchResult:
        matched = [g for g in grants if self._matches(g, query)]
        matched.sort(key=lambda g: (g.entitlement_code, g.id))
        start = max(query.offset, 0)
        page = tuple(matched[start : start + max(query.limit, 0)])
        return EntitlementSearchResult(grants=page, total=len(matched))

    def _matches(self, grant: CommercialGrant, query: EntitlementSearchQuery) -> bool:
        return (
            self._text_matches(
                query.text,
                grant.entitlement_code,
                grant.bundle_code,
                grant.source_ref,
                grant.reason,
            )
            and (not query.codes or grant.entitlement_code in query.codes)
            and (not query.states or grant.state in query.states)
            and (not query.sources or grant.source in query.sources)
            and (not query.account_id or grant.account_id == query.account_id)
            and (not query.bundle_code or grant.bundle_code == query.bundle_code)
            and (not query.source_ref or grant.source_ref == query.source_ref)
            and (query.effective_at is None or grant.is_effective_at(query.effective_at))
            and (not query.live_only or grant.is_live)
            and (not query.bundled_only or grant.is_bundled)
            and (not query.unlimited_only or grant.value.is_unlimited)
            and (not query.denials_only or grant.value.is_denial)
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (value or "").casefold() for value in fields)


# ── inspection ──────────────────────────────────────────────────────────────────


class EntitlementInspector:
    """Answers "why?" about an account's entitlements, and reports what is structurally wrong.

    Inspection and diagnostics are one class because they are the same act at two altitudes: one
    explains a single code, the other sweeps the profile for the shapes that make explanation
    impossible — undeclared codes, unclassified gates, contested grants, dead intent."""

    def __init__(
        self,
        resolver: EntitlementPrecedenceResolver | None = None,
        validator: EntitlementValidator | None = None,
    ) -> None:
        self._resolver = resolver or EntitlementPrecedenceResolver()
        self._validator = validator or EntitlementValidator()

    def explain(
        self,
        profile: EntitlementProfile,
        definitions: Sequence[EntitlementDefinition],
        code: str,
        *,
        at: float,
    ) -> EntitlementExplanation:
        return self._resolver.explain(profile.grants, definitions, code, at=at)

    def diagnose(
        self,
        profile: EntitlementProfile,
        definitions: Sequence[EntitlementDefinition],
        *,
        at: float,
    ) -> BillingDiagnostics:
        """Everything structurally wrong with this account's entitlement position.

        Reports *all* findings rather than the first, deterministically ordered, and never
        raises — a screen that shows one problem at a time turns one fix into five visits."""
        known = frozenset(d.code for d in definitions)
        out = list(self._validator.validate_profile(profile, known_codes=known, at=at).findings)
        by_code = {d.code: d for d in definitions}

        for code in sorted({g.entitlement_code for g in profile.live_grants}):
            definition = by_code.get(code)
            if definition is None:
                continue
            explanation = self._resolver.explain(profile.grants, definitions, code, at=at)
            if explanation.contested:
                winner = explanation.winner
                out.append(
                    _warn(
                        f"profile.entitlements[{code}]",
                        C.CONFLICTING_GRANT,
                        f"{sum(1 for c in explanation.considered if c.effective)} grants are in "
                        f"force for {code!r}; "
                        f"{winner.source.value if winner else 'none'} wins on precedence",
                        "expected for a contract overriding a plan; suspicious otherwise",
                    )
                )
        for grant in profile.live_grants:
            if grant.entitlement_code in by_code and by_code[grant.entitlement_code].is_retired:
                out.append(
                    _warn(
                        f"profile.grants[{grant.id}]",
                        C.RETIRED_ENTITLEMENT_DEFINITION,
                        f"grants a retired capability {grant.entitlement_code!r}",
                    )
                )
        return BillingDiagnostics.of(out)

    def activity_for(
        self, activity: Sequence[EntitlementActivity], action: EntitlementAction
    ) -> tuple[EntitlementActivity, ...]:
        return tuple(a for a in activity if a.action is action)
