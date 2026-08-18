"""Consolidation factories and repository contracts (M9.23).

The factory mints aggregates from a clock the Application injects — Foundation reads no clock of
its own. The repository contracts below are abstract and org-scoped by construction: every read
takes `org_id` first, so a cross-tenant query is not expressible.

## What no contract here offers

There is no `update`, `overwrite` or `delete` on a scope snapshot, a source binding, a
consolidation snapshot or a trail entry — **not guarded, absent**. A finalized consolidation cannot
be edited because there is no method to call, which is a stronger guarantee than a check somebody
can forget to write.

`ConsolidationRepository` has exactly one mutation: `advance`, the compare-and-set on `version`.
`ConsolidationExceptionRepository` has exactly one: `acknowledge`, which writes the acknowledgement
columns and nothing else — an exception is never deleted, because "somebody saw this and
consolidated anyway" is precisely what an examiner asks about.

## Pagination is a contract, not an afterthought

`members()`, `bindings()` and `search()` take a page and a page size and return a total. §25 forbids
`all(org_id, 1000)` followed by a Python filter, and no method here invites it: the reads that
could return a hundred thousand rows are the ones that paginate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .commercial_accounts import CommercialAccount
from .commercial_consolidation import (
    ConsolidatedAmount,
    ConsolidationCompleteness,
    ConsolidationEvent,
    ConsolidationEventKind,
    ConsolidationException,
    ConsolidationPeriod,
    ConsolidationPosture,
    ConsolidationRun,
    ConsolidationSearchQuery,
    ConsolidationSearchResult,
    ConsolidationSnapshot,
    ConsolidationStatus,
    ConsolidationValidation,
    EvidenceDomain,
    EvidenceSourceType,
    ScopeCurrencyMode,
    ScopeDefinition,
    ScopeMember,
    ScopeSnapshot,
    SourceBinding,
    SourceCompleteness,
    consolidation_digest,
    derive_consolidation_event_id,
    derive_consolidation_id,
    derive_consolidation_snapshot_id,
    derive_scope_definition_id,
    derive_source_binding_id,
    scope_definition_digest,
)
from .commercial_reporting import PeriodBasis, SourceStatus
from .money import Currency
from .seams import Clock

#: Bumped when a change to this module would produce a different consolidation artifact from the
#: same inputs. Recorded on every snapshot, so a digest that does not reproduce can be explained by
#: a generator change rather than investigated as corruption.
CONSOLIDATION_GENERATOR_VERSION = "m9.23.1"

#: Which trail kinds also reach the application event bus. A strict subset: the trail is complete,
#: the bus carries the five moments another plane could plausibly react to.
CONSOLIDATION_EVENTS: dict[ConsolidationEventKind, str] = {
    ConsolidationEventKind.SCOPE_RESOLVED: "billing.consolidation.scope_resolved",
    ConsolidationEventKind.CONSOLIDATION_OPENED: "billing.consolidation.opened",
    ConsolidationEventKind.VALIDATION_FAILED: "billing.consolidation.validation_failed",
    ConsolidationEventKind.CONSOLIDATION_FINALIZED: "billing.consolidation.finalized",
    ConsolidationEventKind.CORRECTION_OPENED: "billing.consolidation.correction_opened",
}


class ConsolidationFactory:
    """Mints M9.23 aggregates. The clock is injected; nothing here calls one."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    # ── scope ───────────────────────────────────────────────────────────────────

    def scope_definition(
        self,
        *,
        org_id: str,
        scope_code: str,
        version: int,
        mode,
        title: str = "",
        anchor_account_id: str = "",
        member_account_ids: Sequence[str] = (),
        excluded_account_ids: Sequence[str] = (),
        currency_mode: ScopeCurrencyMode = ScopeCurrencyMode.CURRENCY_SEPARATED,
        declared_currency: Currency | None = None,
        period_basis: PeriodBasis = PeriodBasis.ACCOUNTING_PERIOD,
        required_domains: Sequence[EvidenceDomain] = (),
        integrity_blocks_finalization: bool = False,
        published_by: str = "",
    ) -> ScopeDefinition:
        draft = ScopeDefinition(
            id=derive_scope_definition_id(org_id, scope_code, version),
            org_id=org_id,
            scope_code=scope_code,
            version=version,
            mode=mode,
            title=title,
            anchor_account_id=anchor_account_id,
            member_account_ids=tuple(sorted(member_account_ids)),
            excluded_account_ids=tuple(sorted(excluded_account_ids)),
            currency_mode=currency_mode,
            declared_currency=declared_currency,
            period_basis=period_basis,
            required_domains=tuple(required_domains),
            integrity_blocks_finalization=integrity_blocks_finalization,
            published_at=self._clock.now(),
            published_by=published_by,
        )
        return replace(draft, definition_digest=scope_definition_digest(draft))

    # ── consolidation ───────────────────────────────────────────────────────────

    def consolidation(
        self,
        *,
        definition: ScopeDefinition,
        snapshot: ScopeSnapshot,
        period: ConsolidationPeriod,
        opened_by: str = "",
        corrects: str = "",
        correction_reason: str = "",
    ) -> ConsolidationRun:
        """A run over one scope snapshot and one period.

        The identity derives from `(org, snapshot, period, corrects)` — none of which is a clock
        reading, a random value or a mutable status. Two workers opening the same consolidation
        therefore produce one row."""
        return ConsolidationRun(
            id=derive_consolidation_id(definition.org_id, snapshot.id, period.id, corrects),
            org_id=definition.org_id,
            scope_snapshot_id=snapshot.id,
            scope_definition_id=definition.id,
            scope_version=definition.version,
            period_id=period.id,
            period_basis=period.basis,
            period_key=period.period_key,
            status=ConsolidationStatus.DRAFT,
            completeness=ConsolidationCompleteness.UNAVAILABLE,
            currency_mode=definition.currency_mode,
            declared_currency=definition.declared_currency,
            member_count=snapshot.member_count,
            corrects=corrects,
            correction_reason=correction_reason,
            opened_at=self._clock.now(),
            opened_by=opened_by,
        )

    def binding(
        self,
        *,
        run: ConsolidationRun,
        account_id: str,
        domain: EvidenceDomain,
        artifact: EvidenceSourceType,
        read_name: str,
        status: SourceStatus,
        completeness: SourceCompleteness = SourceCompleteness.SOURCE_COMPLETE,
        period_applied: bool = False,
        source_revision: str = "",
        source_digest: str = "",
        record_count: int = 0,
        amount_minor: int | None = None,
        currency: Currency | None = None,
        detail: str = "",
    ) -> SourceBinding:
        """One pinned source.

        **The amount is dropped unless the source resolved.** Passing `amount_minor` alongside an
        `UNAVAILABLE` status is not an error the caller has to avoid — it is discarded here, so no
        code path anywhere can produce a gap that carries a number."""
        resolved = status is SourceStatus.RESOLVED
        return SourceBinding(
            id=derive_source_binding_id(run.id, account_id, domain.value, artifact.value),
            org_id=run.org_id,
            consolidation_id=run.id,
            account_id=account_id,
            domain=domain,
            artifact=artifact,
            read_name=read_name,
            status=status,
            completeness=completeness,
            period_key=run.period_key,
            period_applied=period_applied,
            source_revision=source_revision,
            source_digest=source_digest,
            record_count=record_count if resolved else 0,
            amount_minor=amount_minor if resolved else None,
            currency=currency if resolved else None,
            detail=detail,
            bound_at=self._clock.now(),
        )

    def snapshot(
        self,
        *,
        run: ConsolidationRun,
        scope_snapshot: ScopeSnapshot,
        source_digest: str,
        amounts: Sequence[ConsolidatedAmount],
        finalized_by: str = "",
    ) -> ConsolidationSnapshot:
        """The finalized artifact, content-addressed on **pinned state only**."""
        digest = consolidation_digest(
            scope_snapshot.snapshot_digest,
            run.period_basis,
            run.period_key,
            source_digest,
            tuple(amounts),
            run.completeness,
        )
        return ConsolidationSnapshot(
            id=derive_consolidation_snapshot_id(run.id, digest),
            org_id=run.org_id,
            consolidation_id=run.id,
            consolidation_digest=digest,
            scope_snapshot_id=scope_snapshot.id,
            scope_snapshot_digest=scope_snapshot.snapshot_digest,
            period_key=run.period_key,
            representation=self.representation(run, scope_snapshot, source_digest, amounts),
            source_digest=source_digest,
            completeness=run.completeness,
            finalized_at=self._clock.now(),
            finalized_by=finalized_by,
        )

    @staticmethod
    def representation(
        run: ConsolidationRun,
        scope_snapshot: ScopeSnapshot,
        source_digest: str,
        amounts: Sequence[ConsolidatedAmount],
    ) -> str:
        """A stable text rendering of the finalized consolidation.

        Built from pinned state only — the scope snapshot digest, the period, the source digest and
        the aggregates as computed. Nothing here reads a live object, a clock or a `latest()`, so
        the representation is byte-identical after every source moves."""
        lines = [
            f"generator={CONSOLIDATION_GENERATOR_VERSION}",
            f"scope={scope_snapshot.snapshot_digest}",
            f"scope_version={run.scope_version}",
            f"basis={run.period_basis.value}",
            f"period={run.period_key}",
            f"members={run.member_count}",
            f"collected={run.collected_count}",
            f"completeness={run.completeness.value}",
            f"currency_mode={run.currency_mode.value}",
            f"sources={source_digest}",
        ]
        for amount in sorted(amounts, key=lambda a: (a.domain.value, str(a.currency))):
            lines.append(
                f"amount:{amount.domain.value}:{amount.currency}="
                f"{amount.minor}:{amount.contributing_accounts}:{amount.contributing_records}"
            )
        if run.corrects:
            lines.append(f"corrects={run.corrects}")
        return "\n".join(lines)

    def event(
        self,
        *,
        run_id: str,
        org_id: str,
        kind: ConsolidationEventKind,
        sequence: int,
        subject: str = "",
        detail: str = "",
        actor: str = "",
        data: dict | None = None,
    ) -> ConsolidationEvent:
        return ConsolidationEvent(
            id=derive_consolidation_event_id(run_id, kind.value, subject, sequence),
            org_id=org_id,
            consolidation_id=run_id,
            kind=kind,
            sequence=sequence,
            subject=subject,
            detail=detail,
            actor=actor,
            occurred_at=self._clock.now(),
            data=dict(data or {}),
        )


# ── repository contracts ────────────────────────────────────────────────────────


class ScopeDefinitionRepository(ABC):
    """Published scope rules. **No update path** — a version is published once."""

    @abstractmethod
    def publish(self, definition: ScopeDefinition) -> ScopeDefinition: ...
    @abstractmethod
    def get(self, org_id: str, definition_id: str) -> ScopeDefinition | None: ...
    @abstractmethod
    def by_code(self, org_id: str, scope_code: str, version: int) -> ScopeDefinition | None: ...
    @abstractmethod
    def latest_version(self, org_id: str, scope_code: str) -> ScopeDefinition | None:
        """**The one `latest()` in this milestone.**

        Permitted only while opening a new consolidation, where it selects the version to bind. A
        structural test asserts no other function reaches it, because a finalized consolidation
        that consulted `latest()` would change meaning every time somebody edited a scope."""

    @abstractmethod
    def all(self, org_id: str, limit: int = 200) -> tuple[ScopeDefinition, ...]: ...


class ScopeSnapshotRepository(ABC):
    """Immutable resolved memberships. There is no update and no delete."""

    @abstractmethod
    def create(self, snapshot: ScopeSnapshot) -> ScopeSnapshot:
        """Idempotent on the derived id: resolving an unchanged hierarchy twice writes one row."""

    @abstractmethod
    def get(self, org_id: str, snapshot_id: str) -> ScopeSnapshot | None: ...
    @abstractmethod
    def members(
        self, org_id: str, snapshot_id: str, page: int = 1, page_size: int = 100
    ) -> tuple[tuple[ScopeMember, ...], int]:
        """One page of members, plus the total. **The read that makes 100K accounts tractable.**"""

    @abstractmethod
    def member_ids(
        self, org_id: str, snapshot_id: str, after: str = "", limit: int = 100
    ) -> tuple[str, ...]:
        """Account ids after a cursor, in id order. What batched collection advances through —
        deterministic, so a restart mid-collection resumes rather than re-reads or skips."""

    @abstractmethod
    def for_definition(
        self, org_id: str, definition_id: str, limit: int = 50
    ) -> tuple[ScopeSnapshot, ...]: ...


class ConsolidationPeriodRepository(ABC):
    @abstractmethod
    def create(self, period: ConsolidationPeriod) -> ConsolidationPeriod: ...
    @abstractmethod
    def get(self, org_id: str, period_id: str) -> ConsolidationPeriod | None: ...
    @abstractmethod
    def by_key(
        self, org_id: str, basis: PeriodBasis, period_key: str
    ) -> ConsolidationPeriod | None: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 100) -> tuple[ConsolidationPeriod, ...]: ...


class ConsolidationRepository(ABC):
    """Consolidation runs. **Exactly one mutation**, and it is a compare-and-set."""

    @abstractmethod
    def create(self, run: ConsolidationRun) -> ConsolidationRun:
        """Idempotent on the derived id. Two workers opening the same consolidation write one row —
        a duplicate consolidation is inexpressible rather than merely rejected."""

    @abstractmethod
    def get(self, org_id: str, consolidation_id: str) -> ConsolidationRun | None: ...
    @abstractmethod
    def advance(self, run: ConsolidationRun, expected_version: int) -> ConsolidationRun:
        """Compare-and-set on `version`. Raises `ConsolidationStale` when no row matches —
        **the stale-worker defence**, and the reason a slow worker cannot overwrite a finalization
        somebody else performed."""

    @abstractmethod
    def search(self, query: ConsolidationSearchQuery) -> ConsolidationSearchResult: ...
    @abstractmethod
    def for_snapshot(
        self, org_id: str, snapshot_id: str, limit: int = 50
    ) -> tuple[ConsolidationRun, ...]: ...
    @abstractmethod
    def posture(self, org_id: str) -> ConsolidationPosture:
        """Counts by status and completeness, from `GROUP BY` aggregates. No money."""


class SourceBindingRepository(ABC):
    """Pinned sources. Append-only: a binding is written once and never edited."""

    @abstractmethod
    def bind(self, binding: SourceBinding) -> SourceBinding:
        """Idempotent on `(run, account, domain, artifact)`. Two workers collecting one batch
        concurrently write one row per source, not two."""

    @abstractmethod
    def get(self, org_id: str, binding_id: str) -> SourceBinding | None: ...
    @abstractmethod
    def page(
        self, org_id: str, consolidation_id: str, page: int = 1, page_size: int = 100
    ) -> tuple[tuple[SourceBinding, ...], int]: ...
    @abstractmethod
    def for_consolidation(
        self, org_id: str, consolidation_id: str, limit: int = 2000
    ) -> tuple[SourceBinding, ...]:
        """Every binding for a run, for aggregation and digesting. Bounded by
        `MAX_SCOPE_MEMBERS` × domains upstream; the paginated `page()` is what a UI reads."""

    @abstractmethod
    def counts(self, org_id: str, consolidation_id: str) -> dict[str, int]:
        """`GROUP BY status` — how many resolved, empty, unavailable. A database aggregate, not a
        Python fold over every row."""

    @abstractmethod
    def bound_accounts(self, org_id: str, consolidation_id: str) -> int:
        """`COUNT(DISTINCT account_id)`. What the collection cursor is checked against."""


class ConsolidationExceptionRepository(ABC):
    """Named problems. **Never deleted, never cleared** — one narrow acknowledgement path."""

    @abstractmethod
    def raise_exception(self, exception: ConsolidationException) -> ConsolidationException:
        """Idempotent on `(run, kind, subject)`: re-collecting a still-unavailable source does not
        stack a second copy of the same problem."""

    @abstractmethod
    def get(self, org_id: str, exception_id: str) -> ConsolidationException | None: ...
    @abstractmethod
    def acknowledge(self, exception: ConsolidationException) -> ConsolidationException:
        """Writes the acknowledgement columns and nothing else. **Does not unblock anything.**"""

    @abstractmethod
    def for_consolidation(
        self, org_id: str, consolidation_id: str, limit: int = 500
    ) -> tuple[ConsolidationException, ...]: ...
    @abstractmethod
    def blocking_count(self, org_id: str, consolidation_id: str) -> int: ...


class ConsolidationValidationRepository(ABC):
    @abstractmethod
    def record(self, validation: ConsolidationValidation) -> ConsolidationValidation: ...
    @abstractmethod
    def current(self, org_id: str, consolidation_id: str) -> ConsolidationValidation | None: ...


class ConsolidationSnapshotRepository(ABC):
    """Finalized artifacts. No update, no delete — there is no method to call."""

    @abstractmethod
    def create(self, snapshot: ConsolidationSnapshot) -> ConsolidationSnapshot:
        """Unique on `(org, consolidation)`: a consolidation finalizes once."""

    @abstractmethod
    def get(self, org_id: str, snapshot_id: str) -> ConsolidationSnapshot | None: ...
    @abstractmethod
    def for_consolidation(
        self, org_id: str, consolidation_id: str
    ) -> ConsolidationSnapshot | None: ...


class ConsolidationEventRepository(ABC):
    """The trail. Append-only; there is no update and no delete."""

    @abstractmethod
    def append(self, event: ConsolidationEvent) -> ConsolidationEvent: ...
    @abstractmethod
    def timeline(
        self, org_id: str, consolidation_id: str, limit: int = 500
    ) -> tuple[ConsolidationEvent, ...]:
        """Oldest first. **The authority**; `status` is a projection over this."""

    @abstractmethod
    def next_sequence(self, org_id: str, consolidation_id: str) -> int: ...


class ScopeAccountReader(ABC):
    """**The one read M9.23 makes into M9.8, declared as its own contract.**

    Separated from `CommercialAccountRepository` deliberately: this milestone consumes accounts and
    must be visibly unable to write one. There is no `add`, no `replace` and no `assign_parent`
    here — the hierarchy is M9.8's to change, and M9.23 has no method with which to change it."""

    @abstractmethod
    def get(self, org_id: str, account_id: str) -> CommercialAccount | None: ...
    @abstractmethod
    def children_of(self, org_id: str, parent_account_id: str) -> tuple[CommercialAccount, ...]:
        """One indexed row selection per parent. What the subtree walk advances through, so a
        reseller tree is resolved by N indexed reads rather than by loading the organization."""

    @abstractmethod
    def all(self, org_id: str) -> tuple[CommercialAccount, ...]:
        """Every account in the organization, org-scoped at the database.

        Used only by `ORGANIZATION` mode, where every row *is* the answer and no Python filter
        follows. Bounded upstream by `MAX_SCOPE_MEMBERS`; the paginated read that would remove the
        ceiling belongs to M9.8 and is recorded as a limitation."""


__all__ = [
    "CONSOLIDATION_EVENTS",
    "CONSOLIDATION_GENERATOR_VERSION",
    "ConsolidationEventRepository",
    "ConsolidationExceptionRepository",
    "ConsolidationFactory",
    "ConsolidationPeriodRepository",
    "ConsolidationRepository",
    "ConsolidationSnapshotRepository",
    "ConsolidationValidationRepository",
    "ScopeAccountReader",
    "ScopeDefinitionRepository",
    "ScopeSnapshotRepository",
    "SourceBindingRepository",
]
