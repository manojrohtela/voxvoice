"""Consolidation rules (M9.23) — scope resolution, period compatibility, aggregation, lifecycle.

Pure, deterministic, clock-free, database-free, network-free, provider-free, authorization-free.
Every temporal input is passed in explicitly; there is no `datetime.now()`, no `time.time()` and no
Runtime dependency at any depth. The Application supplies the accounts, the clock reading and the
source facts; this module decides what any of it *means*.

## The four refusals

Four things this module will not do, each of which would produce a consolidated number that looks
authoritative and is not:

1. **It never guesses attribution.** A source fact whose account is unknown, empty, or outside the
   resolved scope is `UNATTRIBUTABLE` — never assigned to the first active account, the payer, the
   parent, the newest account, or the one with the largest amount. `ScopeAttributionService` has no
   branch that picks a winner, because there is no correct way to pick one.
2. **It never converts a currency.** `ConsolidationAggregationService` refuses a mixed-currency
   input rather than combining it. There is no FX rate here — not from a provider, not from a
   table, not from a constant, not from an operator's assumption.
3. **It never treats a gap as a zero.** An unavailable source produces no amount at all
   (`amount_minor is None`), an exception, and a completeness value that is not `COMPLETE`.
4. **It never recomputes a financial fact.** No invoice total, no tax, no balance fold, no
   recognition, no payment interpretation, no reconciliation, no rating. The only arithmetic in
   this module is integer addition of minor units that an owning domain already decided, and there
   is exactly one function that performs it.

## What is called rather than reimplemented

`ScopeResolutionService` takes M9.8's `AccountHierarchyResolver` and `PayerResolver` as
constructor arguments and calls them. It contains no walker, no cycle detector and no depth
counter of its own — a second implementation of a graph walk is a second implementation of a bug.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.fsm import StateMachineDefinition

from .account_rules import MAX_HIERARCHY_DEPTH, AccountHierarchyResolver, PayerResolver
from .commercial_accounts import CommercialAccount
from .commercial_consolidation import (
    CONSOLIDATION_AGGREGATIONS,
    CONSOLIDATION_LIFECYCLE,
    MAX_SCOPE_MEMBERS,
    SCOPE_RULES,
    SOURCE_CAPABILITIES,
    AccountContribution,
    ConsolidatedAmount,
    ConsolidationCheck,
    ConsolidationCompleteness,
    ConsolidationException,
    ConsolidationExceptionKind,
    ConsolidationExceptionState,
    ConsolidationPeriod,
    ConsolidationProvenance,
    ConsolidationRun,
    ConsolidationSeverity,
    ConsolidationStatus,
    ConsolidationTrigger,
    ConsolidationValidation,
    EvidenceDomain,
    ExcludedAccount,
    ExclusionReason,
    InclusionReason,
    ScopeCurrencyMode,
    ScopeDefinition,
    ScopeMember,
    ScopeMode,
    ScopeSnapshot,
    SourceBinding,
    SourceCompleteness,
    derive_consolidation_exception_id,
    derive_scope_snapshot_id,
    membership_digest,
    scope_snapshot_digest,
)
from .commercial_investigations import canonical_instant
from .commercial_reporting import PeriodBasis, ReportTransformation, SourceStatus
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .money import Currency, Money

# ── errors ──────────────────────────────────────────────────────────────────────


class ConsolidationStale(BillingConflict):
    """A worker tried to advance a consolidation from a version that is no longer current.

    Raised by persistence when the compare-and-set matches no row, and declared **here, in
    Foundation, beside the domain it protects** — the Application must react to it and may not
    import persistence. Same placement and same reason as `CaseStale` (M9.17), `ScheduleStale`
    (M9.18), `FindingStale` (M9.19), `IncidentStale` (M9.20), `InvestigationStale` (M9.21) and
    `ReportStale` (M9.22)."""


class ConsolidationRefused(BillingConflict):
    """A consolidation action was asked for that this engine must not perform.

    Finalizing over a failed validation, finalizing with a blocking exception outstanding,
    correcting a run whose inputs have not moved, mutating a finalized consolidation, or reaching
    across an organization boundary all land here."""


class ScopeRefused(BillingConflict):
    """A scope was offered that cannot be resolved as stated.

    A mode without its required anchor, an `ACCOUNT_SET` with no accounts, a re-publication that
    changes a published version's meaning, or a membership beyond `MAX_SCOPE_MEMBERS`."""


class UnsupportedConsolidationRule(BillingConflict):
    """A consolidation asked for something the closed vocabulary cannot express.

    An aggregation outside `CONSOLIDATION_AGGREGATIONS`, or a scope mode with no entry in
    `SCOPE_RULES`. Raised rather than defaulted: a silent fallback to `COUNT` or to
    `SINGLE_ACCOUNT` would produce a consolidated artifact nobody asked for."""


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


# ── scope validation ────────────────────────────────────────────────────────────


class ScopeValidator:
    """Whether a scope definition is coherent under its own declared mode.

    Every rule consulted comes from `SCOPE_RULES`, so adding a mode without declaring its
    semantics fails here rather than resolving to something plausible."""

    def validate(self, definition: ScopeDefinition) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        rule = SCOPE_RULES.get(definition.mode)
        if rule is None:  # pragma: no cover - guarded by UnsupportedConsolidationRule upstream
            raise UnsupportedConsolidationRule(
                f"scope mode {definition.mode!r} has no declared rule; "
                "a mode without proven semantics must not resolve"
            )
        if not definition.scope_code:
            out.append(_err("scope.code", C.MISSING_REQUIRED_FIELD, "a scope needs a code"))
        if definition.version < 1:
            out.append(
                _err("scope.version", C.MISSING_REQUIRED_FIELD, "scope versions start at 1")
            )
        if rule.requires_anchor and not definition.anchor_account_id:
            out.append(
                _err(
                    "scope.anchor_account_id",
                    C.MISSING_REQUIRED_FIELD,
                    f"{definition.mode.value} is anchored and no anchor account was named",
                    "an anchored mode with no anchor would have to pick one, and that is a guess",
                )
            )
        if not rule.requires_anchor and definition.anchor_account_id:
            out.append(
                _err(
                    "scope.anchor_account_id",
                    C.MISSING_REQUIRED_FIELD,
                    f"{definition.mode.value} takes no anchor, but one was named",
                    "an ignored anchor reads as authority the mode does not have",
                )
            )
        if rule.requires_explicit_set and not definition.member_account_ids:
            out.append(
                _err(
                    "scope.member_account_ids",
                    C.MISSING_REQUIRED_FIELD,
                    "an account set with no accounts is not a scope",
                )
            )
        if not rule.requires_explicit_set and definition.member_account_ids:
            out.append(
                _err(
                    "scope.member_account_ids",
                    C.MISSING_REQUIRED_FIELD,
                    f"{definition.mode.value} derives its members and takes no explicit list",
                )
            )
        if (
            definition.currency_mode is ScopeCurrencyMode.SINGLE_DECLARED
            and definition.declared_currency is None
        ):
            out.append(
                _err(
                    "scope.declared_currency",
                    C.CURRENCY_MISMATCH,
                    "single_declared names one currency and none was declared",
                    "there is no conversion here; the currency has to be stated",
                )
            )
        if (
            definition.currency_mode is ScopeCurrencyMode.CURRENCY_SEPARATED
            and definition.declared_currency is not None
        ):
            out.append(
                _err(
                    "scope.declared_currency",
                    C.CURRENCY_MISMATCH,
                    "currency_separated keeps one aggregate per currency and declares none",
                )
            )
        overlap = set(definition.member_account_ids) & set(definition.excluded_account_ids)
        if overlap:
            out.append(
                _err(
                    "scope.excluded_account_ids",
                    C.MISSING_REQUIRED_FIELD,
                    f"{sorted(overlap)} are both enumerated and excluded",
                    "which one wins is exactly the kind of implicit rule this scope must not have",
                )
            )
        return BillingDiagnostics.of(out)


# ── scope resolution ────────────────────────────────────────────────────────────


class ScopeResolutionService:
    """**Turns a scope definition into an immutable membership.**

    Dispatches on `ScopeMode` with an exhaustive match and **no default branch** — an unhandled
    mode raises `UnsupportedConsolidationRule` rather than quietly resolving as something else,
    which is the "no hidden fallback between modes" rule as code.

    The hierarchy walkers are M9.8's, injected. Nothing here walks a graph."""

    def __init__(
        self,
        hierarchy: AccountHierarchyResolver | None = None,
        payers: PayerResolver | None = None,
    ) -> None:
        self._hierarchy = hierarchy or AccountHierarchyResolver()
        self._payers = payers or PayerResolver()

    def resolve(
        self,
        definition: ScopeDefinition,
        known: Sequence[CommercialAccount],
        *,
        at: float,
        resolved_by: str = "",
    ) -> ScopeSnapshot:
        """Resolve `definition` against `known`, as of `at`.

        `known` is the account set the Application read from M9.8 — for the hierarchy modes it is
        the traversal's working set, for `ORGANIZATION` it is the organization's accounts. `at` is
        recorded on the snapshot and is deliberately **not** part of its identity: resolving the
        same scope twice against an unchanged hierarchy must produce one snapshot, not two."""
        rule = SCOPE_RULES.get(definition.mode)
        if rule is None:
            raise UnsupportedConsolidationRule(
                f"scope mode {definition.mode!r} has no declared rule"
            )
        by_id = {a.id: a for a in known if a.org_id == definition.org_id}
        excluded_ids = set(definition.excluded_account_ids)

        candidates: list[tuple[str, InclusionReason, int, tuple[str, ...]]]
        excluded: list[ExcludedAccount] = []

        if definition.mode is ScopeMode.SINGLE_ACCOUNT:
            candidates = [(definition.anchor_account_id, InclusionReason.ANCHOR, 0, ())]
        elif definition.mode is ScopeMode.ACCOUNT_SET:
            candidates = [
                (aid, InclusionReason.ENUMERATED, 0, ())
                for aid in definition.member_account_ids
            ]
        elif definition.mode is ScopeMode.PARENT_GROUP:
            candidates, hierarchy_excluded = self._resolve_parent_group(definition, by_id)
            excluded.extend(hierarchy_excluded)
        elif definition.mode is ScopeMode.PAYER_GROUP:
            candidates, payer_excluded = self._resolve_payer_group(definition, by_id)
            excluded.extend(payer_excluded)
        elif definition.mode is ScopeMode.ORGANIZATION:
            candidates = [
                (a.id, InclusionReason.ORGANIZATION_MEMBER, 0, ())
                for a in sorted(by_id.values(), key=lambda a: a.id)
            ]
        else:  # pragma: no cover - unreachable while SCOPE_RULES and ScopeMode agree
            raise UnsupportedConsolidationRule(
                f"scope mode {definition.mode!r} is declared but not resolvable"
            )

        members: list[ScopeMember] = []
        seen: set[str] = set()
        for account_id, reason, depth, path in candidates:
            if account_id in seen:
                # Reached through two hierarchy paths. Included once, and the second arrival is
                # recorded rather than dropped — a silently deduplicated account is how one
                # account's ledger gets counted twice.
                excluded.append(
                    ExcludedAccount(
                        account_id=account_id,
                        reason=ExclusionReason.DUPLICATE_PATH,
                        detail=f"already included via {'→'.join(path) or 'a shorter path'}",
                    )
                )
                continue
            if account_id in excluded_ids:
                excluded.append(
                    ExcludedAccount(account_id, ExclusionReason.EXPLICITLY_EXCLUDED)
                )
                seen.add(account_id)
                continue
            account = by_id.get(account_id)
            if account is None:
                excluded.append(
                    ExcludedAccount(
                        account_id=account_id,
                        reason=ExclusionReason.ACCOUNT_NOT_FOUND,
                        detail="no such billing account in this organization",
                    )
                )
                seen.add(account_id)
                continue
            if (
                definition.currency_mode is ScopeCurrencyMode.SINGLE_DECLARED
                and account.currency is not definition.declared_currency
            ):
                excluded.append(
                    ExcludedAccount(
                        account_id=account_id,
                        reason=ExclusionReason.CURRENCY_NOT_PERMITTED,
                        detail=(
                            f"bills in {account.currency}, the scope declares "
                            f"{definition.declared_currency}; there is no conversion here"
                        ),
                    )
                )
                seen.add(account_id)
                continue
            seen.add(account_id)
            members.append(
                ScopeMember(
                    account_id=account_id,
                    inclusion=reason,
                    account_revision=self.account_revision(account),
                    currency=account.currency,
                    depth=depth,
                    hierarchy_path=path,
                )
            )

        if len(members) > MAX_SCOPE_MEMBERS:
            raise ScopeRefused(
                f"scope {definition.scope_code!r} resolves to {len(members)} accounts, beyond the "
                f"{MAX_SCOPE_MEMBERS} a snapshot may hold — see the M9.8 paginated account read "
                "recorded as a limitation"
            )

        member_tuple = tuple(sorted(members, key=lambda m: m.account_id))
        excluded_tuple = tuple(sorted(excluded, key=lambda e: e.account_id))
        digest = membership_digest(member_tuple, excluded_tuple)
        snapshot_digest = scope_snapshot_digest(
            definition.id, definition.version, definition.mode, rule.hierarchy, digest
        )
        return ScopeSnapshot(
            id=derive_scope_snapshot_id(
                definition.org_id, definition.id, definition.version, digest
            ),
            org_id=definition.org_id,
            scope_definition_id=definition.id,
            scope_version=definition.version,
            mode=definition.mode,
            hierarchy=rule.hierarchy,
            members=member_tuple,
            excluded=excluded_tuple,
            currency_mode=definition.currency_mode,
            declared_currency=definition.declared_currency,
            membership_digest=digest,
            snapshot_digest=snapshot_digest,
            effective_at=at,
            resolved_by=resolved_by,
        )

    @staticmethod
    def account_revision(account: CommercialAccount) -> str:
        """The account's state at resolution time, as a short stable string.

        `updated_at` plus the two relationship edges: what makes January's snapshot still say
        "A was under P" after A moves to Q in March."""
        return (
            f"{canonical_instant(account.updated_at)}"
            f":{account.parent_account_id or '-'}"
            f":{account.payer_account_id or '-'}"
        )

    def _resolve_parent_group(
        self, definition: ScopeDefinition, by_id: dict[str, CommercialAccount]
    ) -> tuple[list[tuple[str, InclusionReason, int, tuple[str, ...]]], list[ExcludedAccount]]:
        anchor = by_id.get(definition.anchor_account_id)
        if anchor is None:
            return [], [
                ExcludedAccount(
                    account_id=definition.anchor_account_id,
                    reason=ExclusionReason.ACCOUNT_NOT_FOUND,
                    detail="the anchor account was not found; the subtree cannot be walked",
                )
            ]
        known = tuple(by_id.values())
        if self._hierarchy.find_cycle(anchor, known):
            return [], [
                ExcludedAccount(
                    account_id=anchor.id,
                    reason=ExclusionReason.HIERARCHY_CYCLE,
                    detail="M9.8 reports a cycle through the anchor; the subtree is not walkable",
                )
            ]
        out: list[tuple[str, InclusionReason, int, tuple[str, ...]]] = [
            (anchor.id, InclusionReason.ANCHOR, 0, (anchor.id,))
        ]
        excluded: list[ExcludedAccount] = []
        frontier: list[tuple[CommercialAccount, int, tuple[str, ...]]] = [(anchor, 0, (anchor.id,))]
        while frontier:
            account, depth, path = frontier.pop(0)
            if depth >= MAX_HIERARCHY_DEPTH:
                for child in self._hierarchy.children(account, known):
                    excluded.append(
                        ExcludedAccount(
                            account_id=child.id,
                            reason=ExclusionReason.DEPTH_EXCEEDED,
                            detail=f"beyond M9.8's MAX_HIERARCHY_DEPTH of {MAX_HIERARCHY_DEPTH}",
                        )
                    )
                continue
            for child in self._hierarchy.children(account, known):
                child_path = (*path, child.id)
                out.append(
                    (child.id, InclusionReason.HIERARCHY_DESCENDANT, depth + 1, child_path)
                )
                frontier.append((child, depth + 1, child_path))
        return out, excluded

    def _resolve_payer_group(
        self, definition: ScopeDefinition, by_id: dict[str, CommercialAccount]
    ) -> tuple[list[tuple[str, InclusionReason, int, tuple[str, ...]]], list[ExcludedAccount]]:
        anchor = by_id.get(definition.anchor_account_id)
        if anchor is None:
            return [], [
                ExcludedAccount(
                    account_id=definition.anchor_account_id,
                    reason=ExclusionReason.ACCOUNT_NOT_FOUND,
                    detail="the anchor payer was not found; the payer graph cannot be walked",
                )
            ]
        known = tuple(by_id.values())
        out: list[tuple[str, InclusionReason, int, tuple[str, ...]]] = [
            (anchor.id, InclusionReason.ANCHOR, 0, (anchor.id,))
        ]
        excluded: list[ExcludedAccount] = []
        for account in sorted(by_id.values(), key=lambda a: a.id):
            if account.id == anchor.id:
                continue
            if self._payers.find_loop(account, known):
                excluded.append(
                    ExcludedAccount(
                        account_id=account.id,
                        reason=ExclusionReason.HIERARCHY_CYCLE,
                        detail="M9.8 reports a loop in this account's payer chain",
                    )
                )
                continue
            chain = self._payers.chain(account, known)
            if anchor.id in chain[1:]:
                out.append(
                    (
                        account.id,
                        InclusionReason.PAYER_DEPENDENT,
                        max(0, chain.index(anchor.id)),
                        chain[: chain.index(anchor.id) + 1],
                    )
                )
        return out, excluded


# ── period compatibility ────────────────────────────────────────────────────────


class ScopePeriodService:
    """**Whether a source can actually answer for the declared period basis.**

    The mandate's §7 in one service. `BillingPeriod`, `AccountingPeriod`, `TaxPeriod`,
    `RecognitionPeriod` and `ReportingPeriod` are never compared by assuming they share a start,
    an end, a timezone or a granularity — they are compared through `SOURCE_CAPABILITIES`, which
    records what each owning milestone's repository actually publishes.

    A basis a source cannot represent produces `PERIOD_NOT_SUPPORTED`. It never produces an
    approximation."""

    @staticmethod
    def capability(domain: EvidenceDomain):
        capability = SOURCE_CAPABILITIES.get(domain)
        if capability is None:
            raise UnsupportedConsolidationRule(
                f"no source capability is declared for {domain.value!r}; a consolidation may not "
                "read a domain whose contract has not been audited"
            )
        return capability

    def supports(self, domain: EvidenceDomain, basis: PeriodBasis) -> bool:
        capability = self.capability(domain)
        return capability.is_available and basis in capability.supported_bases

    def unsupported_reason(self, domain: EvidenceDomain, basis: PeriodBasis) -> str:
        """Why a domain cannot answer for this basis — never empty when `supports` is False."""
        capability = self.capability(domain)
        if capability.unavailable_reason:
            return capability.unavailable_reason
        if basis not in capability.supported_bases:
            supported = ", ".join(sorted(b.value for b in capability.supported_bases)) or "none"
            return (
                f"{capability.read_name} answers for {supported}; "
                f"{basis.value} is a different period concept and must not be approximated"
            )
        return ""

    def period_expected(self, domain: EvidenceDomain) -> bool:
        """Whether a missing period read is a **gap** or a **category**.

        A domain that describes things happening *in* a period and cannot answer for one has a
        hole. An operational queue read — "which divergences are open right now" — never had a
        period, and reporting its absence as a blocking failure would make the integrity boundary
        permanently unusable."""
        return self.capability(domain).period_expected

    def period_applied(self, domain: EvidenceDomain, basis: PeriodBasis) -> bool:
        """Whether the read this domain publishes actually filters by the period.

        Recorded on every binding, because "this figure covers the account, not the period" is a
        materially different claim from "this figure covers the period" and a consolidated artifact
        must not blur them."""
        return self.supports(domain, basis)

    def guard_period_matches(
        self, definition: ScopeDefinition, period: ConsolidationPeriod
    ) -> None:
        if definition.period_basis is not period.basis:
            raise ConsolidationRefused(
                f"scope {definition.scope_code!r} declares {definition.period_basis.value} and the "
                f"period is {period.basis.value}; two period concepts are never interchangeable"
            )


# ── attribution ─────────────────────────────────────────────────────────────────


class ScopeAttributionService:
    """**The attribution ceiling, held rather than papered over.**

    A source fact belongs to exactly one member of the scope, or it belongs to none. There is no
    third answer and no tie-break: this class contains no branch that picks the first active
    account, the payer, the parent, the newest account or the largest amount, and no branch that
    infers ownership from an invoice number, a customer name or a timestamp.

    An unattributable fact is **not consolidated, not silently dropped, and not moved** — it
    produces an `UNATTRIBUTABLE_SOURCE` exception naming the fact, and the run's completeness stops
    being `COMPLETE`."""

    @staticmethod
    def attribute(snapshot: ScopeSnapshot, account_id: str) -> str:
        """The member this fact attributes to, or `""` if there is not exactly one.

        `""` means unattributable. It never means "pick something sensible"."""
        if not account_id:
            return ""
        return account_id if snapshot.contains(account_id) else ""

    @staticmethod
    def classify(snapshot: ScopeSnapshot, account_id: str) -> ConsolidationExceptionKind | None:
        """Which named problem an unattributable fact is. Two distinct causes, deliberately kept
        apart: a fact with no account at all is M9.8's attribution ceiling; a fact whose account is
        real but outside the scope is a scope question."""
        if not account_id:
            return ConsolidationExceptionKind.UNATTRIBUTABLE_SOURCE
        if not snapshot.contains(account_id):
            return ConsolidationExceptionKind.ACCOUNT_NOT_IN_SCOPE
        return None


# ── aggregation ─────────────────────────────────────────────────────────────────


class ConsolidationAggregationService:
    """**The only place in this milestone where money is added.**

    Integer minor units, one currency at a time, over facts an owning domain already decided. A
    mixed-currency input raises rather than converting; a source that could not be read contributes
    nothing at all rather than a zero.

    There is no `Money.times`, no `allocate`, no `percent`, no `apply_rate`, no average, no
    proportion, no net-of and no float anywhere below. The other `sum()` calls in this module add
    integer **counts**; an AST test asserts that `_sum_minor` is the only function anywhere in the
    Foundation that sums a `.minor`."""

    @staticmethod
    def guard_aggregation(transformation: ReportTransformation) -> None:
        if transformation not in CONSOLIDATION_AGGREGATIONS:
            raise UnsupportedConsolidationRule(
                f"{transformation.value!r} is not a consolidation aggregation; the closed set is "
                + ", ".join(sorted(t.value for t in CONSOLIDATION_AGGREGATIONS))
            )

    @staticmethod
    def _sum_minor(amounts: Sequence[Money]) -> Money:
        """Add minor units of facts that already share a currency. Refuses a mixed input.

        The refusal is the feature. Combining INR and USD into one figure would produce a number
        with no meaning, and combining them *after converting* would produce one whose meaning
        depends on a rate nobody in this repository has approved."""
        if not amounts:
            raise UnsupportedConsolidationRule("nothing to sum; an empty aggregate is not zero")
        currencies = {a.currency for a in amounts}
        if len(currencies) > 1:
            raise ConsolidationRefused(
                "cannot combine "
                + ", ".join(sorted(str(c) for c in currencies))
                + " into one figure; this domain performs no currency conversion"
            )
        return Money(minor=sum(a.minor for a in amounts), currency=amounts[0].currency)

    def aggregate(
        self, bindings: Sequence[SourceBinding], snapshot: ScopeSnapshot
    ) -> tuple[ConsolidatedAmount, ...]:
        """Per `(domain, currency)` aggregates over the **resolved** bindings only.

        Unavailable and empty bindings are excluded from the arithmetic by construction: they
        carry `amount_minor is None`, so there is no value here that could be read as nought. Their
        absence is recorded by `ConsolidationCompletenessService`, not by a zero."""
        buckets: dict[tuple[EvidenceDomain, Currency], list[SourceBinding]] = {}
        for binding in bindings:
            if not binding.contributes or binding.amount_minor is None:
                continue
            if binding.currency is None:
                continue
            if not snapshot.contains(binding.account_id):
                # Defence in depth: a binding for an account outside the snapshot never
                # contributes, even if one were somehow written.
                continue
            buckets.setdefault((binding.domain, binding.currency), []).append(binding)

        if snapshot.currency_mode is ScopeCurrencyMode.SINGLE_DECLARED:
            foreign = {
                currency
                for (_, currency) in buckets
                if currency is not snapshot.declared_currency
            }
            if foreign:
                raise ConsolidationRefused(
                    "the scope declares "
                    f"{snapshot.declared_currency} and resolved sources in "
                    + ", ".join(sorted(str(c) for c in foreign))
                    + "; there is no conversion here"
                )

        out: list[ConsolidatedAmount] = []
        for (domain, currency), rows in sorted(
            buckets.items(), key=lambda kv: (kv[0][0].value, str(kv[0][1]))
        ):
            total = self._sum_minor(
                [Money(minor=r.amount_minor or 0, currency=currency) for r in rows]
            )
            out.append(
                ConsolidatedAmount(
                    domain=domain,
                    currency=currency,
                    minor=total.minor,
                    contributing_accounts=len({r.account_id for r in rows}),
                    contributing_records=sum(r.record_count for r in rows),
                    source_digest="",
                )
            )
        return tuple(out)


# ── completeness ────────────────────────────────────────────────────────────────


class ConsolidationCompletenessService:
    """Four values, and the distinctions are the point.

    **Missing accounts are never zero. Unavailable sources are never zero. Unattributed records are
    never zero.** Each produces a completeness value that is not `COMPLETE`, and each is named in
    an exception a reader can act on."""

    def assess(
        self,
        run: ConsolidationRun,
        snapshot: ScopeSnapshot,
        bindings: Sequence[SourceBinding],
        exceptions: Sequence[ConsolidationException],
    ) -> ConsolidationCompleteness:
        if any(e.is_blocking and e.state is not None for e in exceptions if e.is_blocking):
            return ConsolidationCompleteness.BLOCKED
        if not snapshot.members:
            return ConsolidationCompleteness.BLOCKED
        if not bindings:
            return ConsolidationCompleteness.UNAVAILABLE
        if all(b.status is SourceStatus.UNAVAILABLE for b in bindings):
            return ConsolidationCompleteness.UNAVAILABLE
        if run.collected_count < run.member_count:
            return ConsolidationCompleteness.PARTIAL
        if any(b.status is SourceStatus.UNAVAILABLE for b in bindings):
            return ConsolidationCompleteness.PARTIAL
        if any(b.completeness is not SourceCompleteness.SOURCE_COMPLETE for b in bindings):
            return ConsolidationCompleteness.PARTIAL
        if snapshot.excluded:
            # Deliberate exclusions do not make a consolidation incomplete — they are recorded
            # decisions. Only exclusions that represent a *failure* to include do.
            failed = {
                ExclusionReason.ACCOUNT_NOT_FOUND,
                ExclusionReason.HIERARCHY_CYCLE,
                ExclusionReason.DEPTH_EXCEEDED,
                ExclusionReason.HIERARCHY_UNAVAILABLE,
            }
            if any(e.reason in failed for e in snapshot.excluded):
                return ConsolidationCompleteness.PARTIAL
        return ConsolidationCompleteness.COMPLETE


# ── exceptions ──────────────────────────────────────────────────────────────────


class ConsolidationExceptionService:
    """Builds named exceptions. **There is no generic constructor** — every call site names a kind
    from the closed enum, so "consolidation failed" is not expressible."""

    #: Which kinds block finalization. Declared as a set rather than decided per call site, so
    #: "what stops a consolidation being finalized?" is answerable by reading one table.
    BLOCKING_KINDS: frozenset[ConsolidationExceptionKind] = frozenset(
        {
            ConsolidationExceptionKind.UNATTRIBUTABLE_SOURCE,
            ConsolidationExceptionKind.SOURCE_UNAVAILABLE,
            ConsolidationExceptionKind.SOURCE_READ_FAILED,
            ConsolidationExceptionKind.SOURCE_REVISION_CHANGED,
            ConsolidationExceptionKind.PERIOD_NOT_SUPPORTED,
            ConsolidationExceptionKind.PERIOD_MISMATCH,
            ConsolidationExceptionKind.SCOPE_EMPTY,
            ConsolidationExceptionKind.SCOPE_CHANGED,
            ConsolidationExceptionKind.SCOPE_TOO_LARGE,
            ConsolidationExceptionKind.CURRENCY_MISMATCH,
            ConsolidationExceptionKind.CURRENCY_UNSUPPORTED,
            ConsolidationExceptionKind.HIERARCHY_UNAVAILABLE,
        }
    )

    def build(
        self,
        run: ConsolidationRun,
        kind: ConsolidationExceptionKind,
        subject: str,
        diagnostic: str,
        *,
        at: float,
        account_id: str = "",
        domain: EvidenceDomain | None = None,
        period_key: str = "",
        severity: ConsolidationSeverity | None = None,
    ) -> ConsolidationException:
        if not diagnostic:
            raise UnsupportedConsolidationRule(
                f"{kind.value} was raised with no diagnostic; a named problem with no explanation "
                "is a generic failure wearing a specific label"
            )
        resolved_severity = severity or (
            ConsolidationSeverity.BLOCKING
            if kind in self.BLOCKING_KINDS
            else ConsolidationSeverity.ADVISORY
        )
        return ConsolidationException(
            id=derive_consolidation_exception_id(run.id, kind.value, subject),
            org_id=run.org_id,
            consolidation_id=run.id,
            kind=kind,
            severity=resolved_severity,
            subject=subject,
            diagnostic=diagnostic,
            account_id=account_id,
            domain=domain,
            period_key=period_key or run.period_key,
            created_at=at,
        )

    @staticmethod
    def guard_acknowledgeable(exception: ConsolidationException) -> None:
        if exception.state is ConsolidationExceptionState.ACKNOWLEDGED:
            raise ConsolidationRefused(
                f"exception {exception.id} is already acknowledged; "
                "an acknowledgement is a fact, not a toggle"
            )

    @staticmethod
    def blocking(
        exceptions: Sequence[ConsolidationException],
    ) -> tuple[ConsolidationException, ...]:
        """**Acknowledgement is not filtered out here, deliberately.**

        Somebody having seen a problem is a different fact from the problem being gone, and only
        the second may unblock a consolidation. An acknowledged blocking exception still blocks."""
        return tuple(e for e in exceptions if e.is_blocking)


# ── validation ──────────────────────────────────────────────────────────────────


class ConsolidationValidationService:
    """Validation is **not** reconciliation.

    Every check below asks about the *consolidation*: is the scope resolved, is the membership the
    one this run's identity derives from, did collection finish, is every required domain bound, is
    the currency handling coherent, are there blocking exceptions, is the integrity record settled.

    None compares two financial domains against each other. That is M9.19's question, it stays
    M9.19's, and the integrity check below **reads M9.19/M9.20's own verdict** rather than forming
    a second one."""

    def validate(
        self,
        run: ConsolidationRun,
        definition: ScopeDefinition,
        snapshot: ScopeSnapshot,
        bindings: Sequence[SourceBinding],
        exceptions: Sequence[ConsolidationException],
        *,
        at: float,
        validated_by: str = "",
        open_integrity_findings: int = 0,
    ) -> ConsolidationValidation:
        checks: list[ConsolidationCheck] = []

        checks.append(
            ConsolidationCheck(
                "scope_resolved",
                bool(snapshot.members),
                f"{len(snapshot.members)} accounts in scope",
            )
        )
        checks.append(
            ConsolidationCheck(
                "scope_snapshot_pinned",
                run.scope_snapshot_id == snapshot.id,
                "the run references the membership its identity derives from",
            )
        )
        checks.append(
            ConsolidationCheck(
                "scope_version_pinned",
                run.scope_version == snapshot.scope_version == definition.version,
                f"resolved under scope version {snapshot.scope_version}",
            )
        )
        checks.append(
            ConsolidationCheck(
                "period_basis_matches",
                run.period_basis is definition.period_basis,
                f"{run.period_basis.value}",
            )
        )
        checks.append(
            ConsolidationCheck(
                "collection_complete",
                run.collected_count >= run.member_count and run.member_count > 0,
                f"{run.collected_count}/{run.member_count} members collected",
            )
        )
        bound_domains = {b.domain for b in bindings}
        missing = tuple(
            sorted(d.value for d in definition.required_domains if d not in bound_domains)
        )
        checks.append(
            ConsolidationCheck(
                "required_domains_bound",
                not missing,
                f"unbound: {', '.join(missing)}" if missing else "every required domain is bound",
            )
        )
        unavailable = [b for b in bindings if b.status is SourceStatus.UNAVAILABLE]
        checks.append(
            ConsolidationCheck(
                "no_unavailable_sources",
                not unavailable,
                f"{len(unavailable)} sources could not be read"
                if unavailable
                else "every source answered",
            )
        )
        currencies = {b.currency for b in bindings if b.currency is not None}
        declared = {definition.declared_currency} if definition.declared_currency else set()
        currency_ok = (
            definition.currency_mode is ScopeCurrencyMode.CURRENCY_SEPARATED
            or currencies <= declared
        )
        checks.append(
            ConsolidationCheck(
                "currency_handling_coherent",
                currency_ok,
                f"{definition.currency_mode.value}; "
                + (", ".join(sorted(str(c) for c in currencies)) or "no monetary source"),
            )
        )
        blocking = ConsolidationExceptionService.blocking(exceptions)
        checks.append(
            ConsolidationCheck(
                "no_blocking_exceptions",
                not blocking,
                ", ".join(sorted({e.kind.value for e in blocking}))
                if blocking
                else "no blocking exception stands",
            )
        )
        checks.append(
            ConsolidationCheck(
                "integrity_settled",
                not (definition.integrity_blocks_finalization and open_integrity_findings > 0),
                f"{open_integrity_findings} open integrity findings"
                if open_integrity_findings
                else "the integrity record is settled",
            )
        )
        return ConsolidationValidation(
            consolidation_id=run.id,
            org_id=run.org_id,
            checks=tuple(checks),
            validated_at=at,
            validated_by=validated_by,
        )


# ── lifecycle & guards ──────────────────────────────────────────────────────────


class ConsolidationLifecycleService:
    """The FSM, and the reachability facts that make its invariants structural."""

    def __init__(self, definition: StateMachineDefinition = CONSOLIDATION_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: ConsolidationStatus, trigger: ConsolidationTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(
        self, current: ConsolidationStatus, trigger: ConsolidationTrigger
    ) -> ConsolidationStatus:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a consolidation cannot {trigger.value} from {current.value}; legal triggers: "
                f"{legal or ['(none — this consolidation is finalized)']}"
            )
        return edge.target

    def allowed(self, current: ConsolidationStatus) -> tuple[ConsolidationTrigger, ...]:
        """**The server's answer, and the only one Studio may act on.** A client that decided
        locally which buttons to show would be deriving lifecycle state, which the mandate
        forbids."""
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))


class ConsolidationGuardService:
    """The refusals that keep a consolidated artifact honest."""

    @staticmethod
    def guard_scope_publishable(
        existing: ScopeDefinition | None, offered: ScopeDefinition
    ) -> None:
        """Re-publishing a version with different content is refused.

        Every consolidation already bound to that version would silently change meaning — and the
        digest is what lets an identical re-publication be *proven* identical, not assumed."""
        if existing is None:
            return
        if existing.definition_digest != offered.definition_digest:
            raise ScopeRefused(
                f"scope {offered.scope_code!r} version {offered.version} is already published with "
                "different content; publish a new version rather than editing one that "
                "consolidations already used"
            )

    @staticmethod
    def guard_not_finalized(run: ConsolidationRun) -> None:
        if run.is_finalized:
            raise ConsolidationRefused(
                f"consolidation {run.id} is finalized; it has no outgoing transition and no "
                "in-place edit — a correction opens a new run beside it"
            )

    @staticmethod
    def guard_finalizable(
        run: ConsolidationRun,
        validation: ConsolidationValidation | None,
        exceptions: Sequence[ConsolidationException],
    ) -> None:
        """Re-read inside the transaction, and refused on any of three grounds.

        No operator confidence, override flag or retry path reaches `FINALIZED` around a failed
        validation — and the FSM has no edge that would let one."""
        if run.status is not ConsolidationStatus.READY:
            raise ConsolidationRefused(
                f"consolidation {run.id} is {run.status.value}; only a READY run finalizes, and "
                "READY is reachable only from a validation that passed"
            )
        if validation is None or not validation.passed:
            failed = (
                ", ".join(c.name for c in validation.failures) if validation else "not validated"
            )
            raise ConsolidationRefused(
                f"consolidation {run.id} cannot finalize: {failed}"
            )
        blocking = ConsolidationExceptionService.blocking(exceptions)
        if blocking:
            raise ConsolidationRefused(
                f"consolidation {run.id} has {len(blocking)} blocking exceptions outstanding: "
                + ", ".join(sorted({e.kind.value for e in blocking}))
                + " — acknowledging them records that somebody looked, not that they are gone"
            )
        if run.completeness not in (
            ConsolidationCompleteness.COMPLETE,
            ConsolidationCompleteness.PARTIAL,
        ):
            raise ConsolidationRefused(
                f"consolidation {run.id} is {run.completeness.value} and cannot be finalized"
            )

    @staticmethod
    def guard_correctable(original: ConsolidationRun) -> None:
        if not original.is_finalized:
            raise ConsolidationRefused(
                f"consolidation {original.id} is {original.status.value}; a correction supersedes "
                "a finalized consolidation — an unfinalized one is re-collected instead"
            )
        if original.superseded_by:
            raise ConsolidationRefused(
                f"consolidation {original.id} is already corrected by {original.superseded_by}; "
                "correct the correction, so the chain stays a chain"
            )

    @staticmethod
    def guard_correction_has_moved(
        original: ConsolidationRun, new_snapshot_id: str, new_source_digest: str
    ) -> None:
        """A correction whose scope and sources are both unmoved is refused.

        It would compute the identity of a consolidation that already exists, and filing the same
        figures twice under a "correction" label is not a correction."""
        if (
            original.scope_snapshot_id == new_snapshot_id
            and original.consolidation_digest
            and original.consolidation_digest == new_source_digest
        ):
            raise ConsolidationRefused(
                f"consolidation {original.id} would be corrected to identical scope and identical "
                "source revisions; nothing has moved, so there is nothing to correct"
            )

    @staticmethod
    def guard_single_org(org_id: str, *subjects: str) -> None:
        for subject in subjects:
            if subject and subject != org_id:
                raise ConsolidationRefused(
                    f"a consolidation may not span organizations: {org_id!r} vs {subject!r}"
                )


# ── provenance ──────────────────────────────────────────────────────────────────


class ConsolidationProvenanceService:
    """**Traceability without payload explosion.**

    A consolidated figure must answer: which accounts contributed, which source records, which
    revision, which period, which domain, which transformation, what was excluded and why. All
    eight are answered from stored rows — the per-account contribution below, the binding
    table, the snapshot's exclusion list — rather than by copying a million source ids into one
    JSON document."""

    def contributions(
        self, snapshot: ScopeSnapshot, bindings: Sequence[SourceBinding]
    ) -> tuple[AccountContribution, ...]:
        by_account: dict[str, list[SourceBinding]] = {}
        for binding in bindings:
            by_account.setdefault(binding.account_id, []).append(binding)
        out: list[AccountContribution] = []
        for member in snapshot.members:
            rows = by_account.get(member.account_id, [])
            out.append(
                AccountContribution(
                    account_id=member.account_id,
                    inclusion=member.inclusion,
                    resolved_sources=sum(1 for r in rows if r.status is SourceStatus.RESOLVED),
                    empty_sources=sum(1 for r in rows if r.status is SourceStatus.EMPTY),
                    unavailable_sources=sum(
                        1 for r in rows if r.status is SourceStatus.UNAVAILABLE
                    ),
                    record_count=sum(r.record_count for r in rows),
                    currency=member.currency,
                )
            )
        return tuple(out)

    def build(
        self,
        run: ConsolidationRun,
        snapshot: ScopeSnapshot,
        bindings: Sequence[SourceBinding],
    ) -> ConsolidationProvenance:
        return ConsolidationProvenance(
            consolidation_id=run.id,
            org_id=run.org_id,
            scope_snapshot_id=snapshot.id,
            scope_definition_id=snapshot.scope_definition_id,
            scope_version=snapshot.scope_version,
            period_basis=run.period_basis,
            period_key=run.period_key,
            hierarchy=snapshot.hierarchy,
            contributions=self.contributions(snapshot, bindings),
            excluded=snapshot.excluded,
            domains=tuple(sorted({b.domain for b in bindings}, key=lambda d: d.value)),
            source_digest=run.consolidation_digest,
            consolidation_digest=run.consolidation_digest,
            corrects=run.corrects,
            superseded_by=run.superseded_by,
        )


__all__ = [
    "ScopeAttributionService",
    "ConsolidationAggregationService",
    "ConsolidationCompletenessService",
    "ConsolidationExceptionService",
    "ConsolidationGuardService",
    "ConsolidationLifecycleService",
    "ConsolidationProvenanceService",
    "ConsolidationRefused",
    "ConsolidationStale",
    "ConsolidationValidationService",
    "ScopePeriodService",
    "ScopeRefused",
    "ScopeResolutionService",
    "ScopeValidator",
    "UnsupportedConsolidationRule",
]
