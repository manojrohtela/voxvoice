"""Revenue Recognition application layer (M9.18) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.17: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 10 commands,
16 queries, their use cases, and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Invoice (snapshot) ──read──▶ Recognition ──command──▶ Ledger  (PostJournalEntry)
                                      │
                                      └── emits billing.revenue.*

Two arrows, and only two. The read is an **issued invoice snapshot**, addressed by the digest the
document pinned at issue — never a live query against pricing, rating, tax or subscription state.
The write is M9.13's own command, carrying `PostingSource.REVENUE_RECOGNITION` and
`RecognitionState.RECOGNIZED`, with the Ledger deciding the period, the entry id and whether the
posting is a duplicate.

**There is no payment arrow and no collections arrow.** Paid is not earned. A paid invoice may sit
fully deferred for eleven months and an unpaid one may be fully recognized on the day it issued;
inferring either from the other would be wrong in both directions, so this module imports neither
domain and a test asserts it.

## The three phases, and where the Ledger may say no

Recognizing a period is:

    ┌─ transaction 1 ─────────────────────────────────────────────────────────┐
    │ read the schedule · guard the entry · **conditional PENDING→RECOGNIZED**│
    │ (one worker wins) · advance the schedule (CAS) · COMMIT                 │
    └─────────────────────────────────────────────────────────────────────────┘
                                  ↓
    ┌─ transaction 2 ─────────────────────────────────────────────────────────┐
    │ dispatch PostJournalEntry · record the outcome on the entry · COMMIT    │
    └─────────────────────────────────────────────────────────────────────────┘

Splitting them is the point. The Ledger can legitimately refuse — no accounting period is open —
and when it does, **the recognition decision is still a fact**. It survives as `UNPOSTED`, which
names exactly what needs repairing, and `PostRecognitionToLedger` is the repair. Losing the decision
because the posting failed is the M9.16 `UNPOSTED_CAPTURE` defect, and it is avoided here the same
way: catch `BillingError` — the Ledger's *considered refusal* — and let anything else propagate,
because a bug is not a refusal.

## Closed accounting periods

M9.13 already owns close semantics: `AccountingPeriod` has no edge out of `CLOSED`, and
`LateEntryDisposition` says a late fact lands in the next open period as an adjustment. This module
**reuses that rule and does not restate it**. Recognition posts; the Ledger decides `IN_PERIOD`,
`NEXT_OPEN_PERIOD` or `REJECTED_NO_OPEN_PERIOD`; and whichever it was is recorded on the entry so
the two months reconcile against each other rather than appearing to disagree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from billing.foundation import (
    AdjustmentKind,
    BillingError,
    BillingPeriod,
    Currency,
    EntryState,
    EntryTrigger,
    LedgerDirection,
    LedgerEntryType,
    Money,
    PeriodGranularity,
    PostingSource,
    RecognitionBasis,
    RecognitionEntry,
    RecognitionMethod,
    RecognitionPolicy,
    RecognitionState,
    RemainderRule,
    RevenueSchedule,
    RevenueSearchQuery,
    ScheduleState,
    ScheduleTrigger,
)
from billing.foundation import BillingConflict as _Conflict
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .ledger_engine import PostJournalEntry
from .mapping import MoneyView
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

#: How many times a worker re-reads and re-decides after losing a compare-and-set. Bounded: each
#: round either wins or discovers the schedule moved somewhere it must not touch.
_CAS_ATTEMPTS = 5


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublishRecognitionPolicy(_Command):
    """Publish one **immutable** recognition strategy version.

    There is no `UpdateRecognitionPolicy` and there never will be. A change publishes version
    *n+1*; every schedule built under *n* keeps naming *n* inside its basis digest."""

    code: str = ""
    version: int = 1
    method: RecognitionMethod = RecognitionMethod.STRAIGHT_LINE
    granularity: PeriodGranularity = PeriodGranularity.MONTH
    remainder: RemainderRule = RemainderRule.LARGEST_REMAINDER
    description: str = ""


@dataclass(frozen=True, slots=True)
class DeactivateRecognitionPolicy(_Command):
    policy_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleRevenue(_Command):
    """Derive a recognition schedule from an **issued** invoice document.

    Takes a document id, not an amount: the figure is read from the snapshot the document pinned at
    issue, so this command cannot be used to recognize a number nobody billed. `service_start` and
    `service_end` name the period the revenue is earned over — the one input a document does not
    always carry — and both are inside the basis digest.

    Idempotent on the **basis**, not on the request: the same document revision, amount, window,
    method and policy version derive the same schedule id, so two workers produce one schedule."""

    document_id: str = ""
    service_start: float = 0.0
    service_end: float = 0.0
    policy_code: str = ""
    method: RecognitionMethod | None = None
    milestones: tuple[tuple[float, int], ...] = ()
    subscription_id: str = ""


@dataclass(frozen=True, slots=True)
class ActivateRevenueSchedule(_Command):
    """Move a built schedule from `DRAFT` to `SCHEDULED` — the point it becomes recognizable."""

    schedule_id: str = ""


@dataclass(frozen=True, slots=True)
class RecognizeRevenue(_Command):
    """Recognize **one matured period**, and post the financial fact.

    `at` is explicit so a recognition is reproducible: replaying the same instant against the same
    immutable basis yields the same answer. A period that has not matured is refused."""

    entry_id: str = ""
    at: float | None = None


@dataclass(frozen=True, slots=True)
class PostRecognitionToLedger(_Command):
    """Repair a recognition whose ledger posting never landed.

    Exists because the Ledger can refuse after the recognition decision is committed: the revenue
    is decided, the financial fact is missing, and the repair must be an explicit, idempotent,
    attributed act rather than a background process nobody knows ran."""

    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class ReverseRecognition(_Command):
    """Correct a recognized amount with **a new fact**. The original entry is never edited."""

    entry_id: str = ""
    kind: AdjustmentKind = AdjustmentKind.REVERSAL
    reason: str = ""
    request_key: str = ""
    amount_minor: int = 0  # ignored for REVERSAL, which is exactly the negative of the original


@dataclass(frozen=True, slots=True)
class SupersedeRevenueSchedule(_Command):
    """The source document was revised. **What was already recognized stands.**

    The old schedule stops and keeps its entries exactly as they are; the caller then schedules the
    new revision, which derives a different basis digest and therefore a different schedule."""

    schedule_id: str = ""
    successor_schedule_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelRevenueSchedule(_Command):
    """The document was cancelled before anything was earned. Refused once revenue exists."""

    schedule_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SkipRecognitionEntry(_Command):
    """Retire a pending slice that will never be recognized — normally after supersession."""

    entry_id: str = ""
    reason: str = ""


ALL_REVENUE_COMMANDS = (
    PublishRecognitionPolicy,
    DeactivateRecognitionPolicy,
    ScheduleRevenue,
    ActivateRevenueSchedule,
    RecognizeRevenue,
    PostRecognitionToLedger,
    ReverseRecognition,
    SupersedeRevenueSchedule,
    CancelRevenueSchedule,
    SkipRecognitionEntry,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetRevenueSchedule(_Query):
    schedule_id: str


@dataclass(frozen=True, slots=True)
class ListRevenueSchedules(_Query):
    account_id: str = ""
    states: tuple[str, ...] = ()
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetSchedulesForDocument(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class GetRecognitionEntry(_Query):
    entry_id: str


@dataclass(frozen=True, slots=True)
class ListRecognitionEntries(_Query):
    schedule_id: str = ""


@dataclass(frozen=True, slots=True)
class ListRecognitionAdjustments(_Query):
    schedule_id: str = ""
    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class DueRecognitions(_Query):
    """**What has matured.** The read a scheduler outside this plane calls on a timer.

    A query, deliberately. There is no `RunRecognition` command and no worker anywhere in
    Billing."""

    at: float | None = None
    limit: int = 200


@dataclass(frozen=True, slots=True)
class UnpostedRecognitions(_Query):
    """The repair queue: revenue recognized that the Ledger has not accepted."""

    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetRecognitionPosition(_Query):
    schedule_id: str


@dataclass(frozen=True, slots=True)
class ExplainRecognition(_Query):
    schedule_id: str


@dataclass(frozen=True, slots=True)
class InspectRevenueSchedule(_Query):
    schedule_id: str
    at: float | None = None


@dataclass(frozen=True, slots=True)
class ListRecognitionPolicies(_Query):
    include_inactive: bool = False


@dataclass(frozen=True, slots=True)
class GetRecognitionPolicyVersions(_Query):
    code: str = ""


@dataclass(frozen=True, slots=True)
class SearchRevenueSchedules(_Query):
    account_id: str = ""
    document_id: str = ""
    states: tuple[str, ...] = ()
    limit: int = 100


@dataclass(frozen=True, slots=True)
class RecognitionTimeline(_Query):
    schedule_id: str
    limit: int = 200


@dataclass(frozen=True, slots=True)
class DeferredRevenueSummary(_Query):
    """Total deferred across an account. **Derived on every call, stored nowhere.**"""

    account_id: str = ""
    limit: int = 500


ALL_REVENUE_QUERIES = (
    GetRevenueSchedule,
    ListRevenueSchedules,
    GetSchedulesForDocument,
    GetRecognitionEntry,
    ListRecognitionEntries,
    ListRecognitionAdjustments,
    DueRecognitions,
    UnpostedRecognitions,
    GetRecognitionPosition,
    ExplainRecognition,
    InspectRevenueSchedule,
    ListRecognitionPolicies,
    GetRecognitionPolicyVersions,
    SearchRevenueSchedules,
    RecognitionTimeline,
    DeferredRevenueSummary,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecognitionPolicyView:
    id: str
    code: str
    version: int
    method: str
    granularity: str
    remainder: str
    is_active: bool
    description: str
    declared_at: float
    declared_by: str


@dataclass(frozen=True, slots=True)
class RecognitionBasisView:
    """The immutable evidence, on the wire. **This is what makes a recognition explainable.**"""

    document_id: str
    revision: int
    snapshot_digest: str
    basis_digest: str
    amount: MoneyView
    service_start: float
    service_end: float
    method: str
    granularity: str
    remainder: str
    policy_code: str
    policy_version: int
    subscription_id: str


@dataclass(frozen=True, slots=True)
class RevenueScheduleView:
    id: str
    account_id: str
    state: str
    basis: RecognitionBasisView
    entries_planned: int
    entries_recognized: int
    version: int
    superseded_by: str
    created_at: float
    closed_at: float | None
    closed_reason: str
    is_terminal: bool
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecognitionEntryView:
    id: str
    schedule_id: str
    sequence: int
    period_start: float
    period_end: float
    amount: MoneyView
    recognize_on: float
    state: str
    recognition: str
    ledger_entry_id: str
    ledger_period_key: str
    ledger_disposition: str
    recognized_at: float | None
    posted_at: float | None
    note: str
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecognitionAdjustmentView:
    id: str
    schedule_id: str
    entry_id: str
    kind: str
    amount: MoneyView
    reason: str
    document_id: str
    revision: int
    policy_code: str
    policy_version: int
    effective_at: float
    ledger_entry_id: str
    created_at: float
    created_by: str


@dataclass(frozen=True, slots=True)
class RecognitionPositionView:
    """Derived every time. `deferred` is `total - recognized - adjusted` and lives in no column."""

    schedule_id: str
    currency: str
    total: MoneyView
    recognized: MoneyView
    adjusted: MoneyView
    deferred: MoneyView
    entries: int
    recognized_entries: int
    posted_entries: int
    unposted_entries: int
    reversed_entries: int
    is_fully_recognized: bool
    has_unposted: bool


@dataclass(frozen=True, slots=True)
class RecognitionExplanationView:
    schedule_id: str
    state: str
    document_id: str
    revision: int
    snapshot_digest: str
    basis_digest: str
    policy_code: str
    policy_version: int
    method: str
    granularity: str
    remainder: str
    service_start: float
    service_end: float
    total_minor: int
    recognized_minor: int
    deferred_minor: int
    currency: str
    reason: str
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecognitionInspectionView:
    schedule: RevenueScheduleView
    position: RecognitionPositionView
    adjustments: int
    due_now: int
    next_recognize_on: float | None


@dataclass(frozen=True, slots=True)
class RecognitionActivityView:
    id: str
    schedule_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    entry_id: str


@dataclass(frozen=True, slots=True)
class DeferredRevenueView:
    """An account's deferred revenue. **Summed from schedules at read time, never stored.**"""

    account_id: str
    currency: str
    total: MoneyView
    recognized: MoneyView
    deferred: MoneyView
    schedules: int
    unposted_entries: int


@dataclass(frozen=True, slots=True)
class RecognitionReceiptView:
    """What one recognition did.

    `posted` and `recognized` are separate answers on purpose: a recognition whose ledger posting
    was refused is a *successful recognition* and an *unposted fact*, and a caller that could not
    tell them apart would report clean books while the repair queue grew."""

    entry: RecognitionEntryView
    schedule: RevenueScheduleView
    recognized: bool
    posted: bool
    ledger_entry_id: str
    ledger_outcome: str
    ledger_disposition: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class RevenueSearchView:
    schedules: tuple[RevenueScheduleView, ...]
    total: int
    truncated: bool


# ── view mapping ────────────────────────────────────────────────────────────────


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def revenue_policy_view(policy: RecognitionPolicy) -> RecognitionPolicyView:
    return RecognitionPolicyView(
        id=policy.id,
        code=policy.code,
        version=policy.version,
        method=policy.method.value,
        granularity=policy.granularity.value,
        remainder=policy.remainder.value,
        is_active=policy.is_active,
        description=policy.description,
        declared_at=policy.declared_at,
        declared_by=policy.declared_by,
    )


def basis_view(basis: RecognitionBasis) -> RecognitionBasisView:
    return RecognitionBasisView(
        document_id=basis.document_id,
        revision=basis.revision,
        snapshot_digest=basis.snapshot_digest,
        basis_digest=basis.digest,
        amount=_money(basis.amount),
        service_start=basis.service_period.start,
        service_end=basis.service_period.end,
        method=basis.method.value,
        granularity=basis.granularity.value,
        remainder=basis.remainder.value,
        policy_code=basis.policy_code,
        policy_version=basis.policy_version,
        subscription_id=basis.subscription_id,
    )


def schedule_view(schedule: RevenueSchedule, allowed: tuple = ()) -> RevenueScheduleView:
    return RevenueScheduleView(
        id=schedule.id,
        account_id=schedule.account_id,
        state=schedule.state.value,
        basis=basis_view(schedule.basis),
        entries_planned=schedule.entries_planned,
        entries_recognized=schedule.entries_recognized,
        version=schedule.version,
        superseded_by=schedule.superseded_by,
        created_at=schedule.created_at,
        closed_at=schedule.closed_at,
        closed_reason=schedule.closed_reason,
        is_terminal=schedule.is_terminal,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def recognition_entry_view(entry: RecognitionEntry, allowed: tuple = ()) -> RecognitionEntryView:
    return RecognitionEntryView(
        id=entry.id,
        schedule_id=entry.schedule_id,
        sequence=entry.sequence,
        period_start=entry.period.start,
        period_end=entry.period.end,
        amount=_money(entry.amount),
        recognize_on=entry.recognize_on,
        state=entry.state.value,
        recognition=entry.recognition.value,
        ledger_entry_id=entry.ledger_entry_id,
        ledger_period_key=entry.ledger_period_key,
        ledger_disposition=entry.ledger_disposition,
        recognized_at=entry.recognized_at,
        posted_at=entry.posted_at,
        note=entry.note,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def adjustment_view(adjustment) -> RecognitionAdjustmentView:
    return RecognitionAdjustmentView(
        id=adjustment.id,
        schedule_id=adjustment.schedule_id,
        entry_id=adjustment.entry_id,
        kind=adjustment.kind.value,
        amount=_money(adjustment.amount),
        reason=adjustment.reason,
        document_id=adjustment.document_id,
        revision=adjustment.revision,
        policy_code=adjustment.policy_code,
        policy_version=adjustment.policy_version,
        effective_at=adjustment.effective_at,
        ledger_entry_id=adjustment.ledger_entry_id,
        created_at=adjustment.created_at,
        created_by=adjustment.created_by,
    )


def revenue_position_view(position) -> RecognitionPositionView:
    return RecognitionPositionView(
        schedule_id=position.schedule_id,
        currency=position.currency.value,
        total=_money(position.total),
        recognized=_money(position.recognized),
        adjusted=_money(position.adjusted),
        deferred=_money(position.deferred),
        entries=position.entries,
        recognized_entries=position.recognized_entries,
        posted_entries=position.posted_entries,
        unposted_entries=position.unposted_entries,
        reversed_entries=position.reversed_entries,
        is_fully_recognized=position.is_fully_recognized,
        has_unposted=position.has_unposted,
    )


def revenue_explanation_view(explanation) -> RecognitionExplanationView:
    return RecognitionExplanationView(
        schedule_id=explanation.schedule_id,
        state=explanation.state.value,
        document_id=explanation.document_id,
        revision=explanation.revision,
        snapshot_digest=explanation.snapshot_digest,
        basis_digest=explanation.basis_digest,
        policy_code=explanation.policy_code,
        policy_version=explanation.policy_version,
        method=explanation.method.value,
        granularity=explanation.granularity.value,
        remainder=explanation.remainder.value,
        service_start=explanation.service_start,
        service_end=explanation.service_end,
        total_minor=explanation.total_minor,
        recognized_minor=explanation.recognized_minor,
        deferred_minor=explanation.deferred_minor,
        currency=explanation.currency,
        reason=explanation.reason,
        path=explanation.path,
    )


def revenue_activity_view(activity) -> RecognitionActivityView:
    return RecognitionActivityView(
        id=activity.id,
        schedule_id=activity.schedule_id,
        at=activity.at,
        kind=activity.kind,
        summary=activity.summary,
        actor_id=activity.actor_id,
        entry_id=activity.entry_id,
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RevenueRepositories:
    """The five recognition seams, injected. Interfaces only — implementations in persistence.

    Five, and only five. Nothing here reaches an invoice, ledger, payment, collection or
    subscription table; the invoice arrives as a *read* through the context and the ledger as a
    *command* dispatched into its own engine."""

    policies: object
    schedules: object
    entries: object
    adjustments: object
    activity: object


@dataclass(frozen=True, slots=True)
class RevenueServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    schedule_lifecycle: object
    entry_lifecycle: object
    guards: object
    scheduler: object
    positions: object
    schedules: object
    explainer: object
    inspector: object
    validator: object
    search: object


class _RevenueSupport:
    """Shared reads for the command and query sides.

    Both sides derive a position from the same facts, and computing it twice from two different
    sets of reads is how a deferred-revenue report and a schedule screen come to disagree."""

    def __init__(self, *, context, repos: RevenueRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services

    # ── the invoice read boundary ───────────────────────────────────────────────

    def issued_snapshot(self, org_id: str, document_id: str):
        """**The immutable source.** An issued document, resolved by the digest it pinned at issue.

        Read forward-only and content-addressed: `by_digest` is the resolution path, so what is
        recognized is the content that was *issued* rather than whichever revision happens to be
        newest. Nothing here totals, prices, rates or taxes anything — the figure is copied.

        A document that is not issued has no recognizable amount, which is a refusal rather than a
        zero: recognizing revenue from a draft would be recognizing a number nobody billed."""
        engine = getattr(self._ctx, "invoice_engine", None)
        documents = getattr(engine, "documents", None)
        snapshots = getattr(engine, "snapshots", None)
        if documents is None or snapshots is None:
            raise _Conflict(
                "the invoice engine is not wired; revenue is recognized from issued documents "
                "and there are none to read"
            )
        document = documents.get(org_id, document_id)
        if document is None:
            raise _NotFound(f"document {document_id!r} not found")
        digest = document.issued_snapshot_digest
        if not digest:
            raise _Conflict(
                f"document {document_id!r} has not been issued; only an issued document states an "
                "amount that can be recognized"
            )
        snapshot = snapshots.by_digest(org_id, digest)
        if snapshot is None:
            raise _NotFound(f"document {document_id!r} has no issued content at digest {digest!r}")
        return document, snapshot, digest

    @staticmethod
    def recognizable_amount(snapshot) -> Money:
        """What the snapshot says is recognizable. **Copied, never computed.**

        The **net** total is used, not the gross: tax is a liability collected on behalf of an
        authority and is never revenue. M9.15 already determined it and M9.14 already presented it;
        this reads the figure they agreed on and does no tax arithmetic of its own — there is no
        rate, no jurisdiction and no component breakdown anywhere in this module."""
        totals = getattr(snapshot, "totals", None)
        if totals is None:
            raise _Conflict("this document snapshot states no totals; nothing can be recognized")
        gross = getattr(totals, "total", None)
        tax = getattr(totals, "tax_total", None)
        if isinstance(gross, Money) and isinstance(tax, Money):
            # `total` is post-discount and post-credit; subtracting `tax_total` leaves the net
            # revenue. **Not `subtotal`** — that figure is pre-discount, and recognizing it would
            # earn revenue the customer was never charged. The only arithmetic in this module is
            # this one subtraction between two figures M9.14 already agreed on, in integer minor
            # units; there is no rate, no jurisdiction and no tax component anywhere near it.
            return Money(gross.minor - tax.minor, gross.currency)
        if isinstance(gross, Money):
            return gross
        raise _Conflict("this document snapshot states no recognizable amount")

    # ── lookups ─────────────────────────────────────────────────────────────────

    def schedule_or_404(self, org_id: str, schedule_id: str) -> RevenueSchedule:
        found = self._repos.schedules.get(org_id, schedule_id)
        if found is None:
            raise _NotFound(f"revenue schedule {schedule_id!r} not found")
        return found

    def entry_or_404(self, org_id: str, entry_id: str) -> RecognitionEntry:
        found = self._repos.entries.get(org_id, entry_id)
        if found is None:
            raise _NotFound(f"recognition entry {entry_id!r} not found")
        return found

    def policy_or_404(self, org_id: str, policy_id: str) -> RecognitionPolicy:
        found = self._repos.policies.get(org_id, policy_id)
        if found is None:
            raise _NotFound(f"recognition policy {policy_id!r} not found")
        return found

    def position_for(self, org_id: str, schedule: RevenueSchedule):
        return self._svc.positions.position(
            schedule,
            self._repos.entries.for_schedule(org_id, schedule.id),
            self._repos.adjustments.for_schedule(org_id, schedule.id),
        )


class RevenueUseCases(_RevenueSupport):
    """Recognition orchestration. Two transactions where the Ledger is involved, one everywhere."""

    def __init__(
        self,
        *,
        context,
        repos: RevenueRepositories,
        factory,
        services: RevenueServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
        ledger_post: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        # **The Ledger boundary.** M9.13's own command, dispatched into M9.13's own use cases.
        # Recognition writes nothing into ledger persistence; when the ledger is unwired a
        # recognition is still a recorded decision that simply produces no financial fact.
        self._ledger_post = ledger_post

    # ── policy ──────────────────────────────────────────────────────────────────

    def publish_policy(self, command: PublishRecognitionPolicy) -> RecognitionPolicyView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            built = self._factory.policy(
                org_id=command.org_id,
                code=command.code,
                version=command.version,
                method=command.method,
                granularity=command.granularity,
                remainder=command.remainder,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored, is_new = self._repos.policies.add(built)
            uow.stage(
                self._event(command, stored.id, code=stored.code, version=str(stored.version),
                            disposition="new" if is_new else "duplicate")
            )
            return revenue_policy_view(stored)

    def deactivate_policy(self, command: DeactivateRecognitionPolicy) -> RecognitionPolicyView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            policy = self.policy_or_404(command.org_id, command.policy_id)
            stored = self._repos.policies.deactivate(replace(policy, is_active=False))
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return revenue_policy_view(stored)

    # ── scheduling ──────────────────────────────────────────────────────────────

    def schedule_revenue(self, command: ScheduleRevenue) -> RevenueScheduleView:
        """Derive a schedule from an issued document. **Idempotent on the basis.**

        Every input that could change the answer is inside the derived id, so a re-run with
        unchanged inputs returns the existing schedule and writes nothing. Change the policy, the
        window, the method or the document revision and it is a *different* schedule — which is
        what stops an edit from silently restating history."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document, snapshot, digest = self.issued_snapshot(
                command.org_id, command.document_id
            )
            policy = self._repos.policies.latest(command.org_id, command.policy_code)
            if policy is None:
                raise _Conflict(
                    f"no active recognition policy {command.policy_code!r} is published; a "
                    "strategy must be declared before revenue can be scheduled"
                )
            amount = self.recognizable_amount(snapshot)
            basis = self._factory.basis(
                org_id=command.org_id,
                account_id=document.account_id,
                document_id=document.id,
                revision=getattr(snapshot, "revision", 1),
                snapshot_digest=digest,
                amount=amount,
                service_period=BillingPeriod(
                    start=command.service_start, end=command.service_end
                ),
                policy=policy,
                method=command.method,
                milestones=command.milestones,
                subscription_id=command.subscription_id,
            )
            built, slices = self._factory.schedule(
                basis, at=self._clock.now(), actor_id=command.actor_id
            )
            stored, is_new = self._repos.schedules.create(built)
            if is_new:
                for slice_ in slices:
                    self._repos.entries.append(self._factory.entry(stored, slice_))
                self._log(
                    command, stored, kind="scheduled",
                    summary=f"{len(slices)} period(s) from {basis.policy_code}"
                            f"@{basis.policy_version}",
                )
            uow.stage(
                self._event(
                    command, stored.id,
                    document_id=basis.document_id,
                    basis_digest=basis.digest,
                    periods=str(len(slices)),
                    disposition="new" if is_new else "duplicate",
                )
            )
            return self._schedule_view(stored)

    def activate_schedule(self, command: ActivateRevenueSchedule) -> RevenueScheduleView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            schedule = self.schedule_or_404(command.org_id, command.schedule_id)
            if schedule.state is ScheduleState.SCHEDULED:
                return self._schedule_view(schedule)  # already active; not an error
            moved = self._svc.schedule_lifecycle.apply(
                schedule, ScheduleTrigger.SCHEDULE, at=self._clock.now()
            )
            stored = self._repos.schedules.advance(moved, expected_version=schedule.version)
            self._factory.emit_schedule_state(stored, actor_id=command.actor_id)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return self._schedule_view(stored)

    # ── recognizing: two transactions, because the Ledger may refuse ────────────

    def recognize(self, command: RecognizeRevenue) -> RecognitionReceiptView:
        """Recognize one matured period, then post it.

        Phase 1 commits the *decision* — a conditional `PENDING → RECOGNIZED` that exactly one
        worker wins, plus the schedule's own compare-and-set. Phase 2 posts. If the Ledger refuses,
        the decision survives as `UNPOSTED` and names exactly what needs repairing."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = command.at if command.at is not None else self._clock.now()

        # ── phase 1: the decision ───────────────────────────────────────────────
        with self._uow.new():
            entry = self.entry_or_404(command.org_id, command.entry_id)
            schedule = self.schedule_or_404(command.org_id, entry.schedule_id)
            self._svc.guards.guard_schedulable(schedule)
            self._svc.guards.guard_recognizable(entry, at=at)
            recognized = self._svc.entry_lifecycle.apply(
                entry, EntryTrigger.RECOGNIZE, at=at
            )
            recognized = self._repos.entries.recognize(
                recognized, expected_state=EntryState.PENDING.value
            )
            schedule = self._advance_after_recognition(command, schedule, at=at)
            self._factory.emit_recognized(recognized, actor_id=command.actor_id)
            self._log(
                command, schedule, kind="recognized",
                summary=f"period {recognized.sequence} of {schedule.entries_planned}",
                entry_id=recognized.id,
            )

        # ── phase 2: the financial fact ─────────────────────────────────────────
        return self._post(command, recognized, schedule, at=at)

    def _advance_after_recognition(
        self, command, schedule: RevenueSchedule, *, at: float
    ) -> RevenueSchedule:
        """Move the schedule on under compare-and-set, and complete it when the last period lands.

        `entries_recognized` is a **count**, not a balance — what has been *earned* is summed from
        the entries every time it is asked for, because a stored revenue total is a second
        authority on a figure the Ledger owns."""
        count = schedule.entries_recognized + 1
        trigger = (
            ScheduleTrigger.COMPLETE
            if count >= schedule.entries_planned
            else ScheduleTrigger.RECOGNIZE
        )
        moved = replace(schedule, entries_recognized=count)
        if self._svc.schedule_lifecycle.can(moved.state, trigger):
            moved = self._svc.schedule_lifecycle.apply(moved, trigger, at=at)
        else:
            moved = replace(moved, version=moved.version + 1, updated_at=at)
        stored = self._repos.schedules.advance(moved, expected_version=schedule.version)
        self._factory.emit_schedule_state(stored, actor_id=command.actor_id)
        return stored

    def _post(
        self, command, entry: RecognitionEntry, schedule: RevenueSchedule, *, at: float
    ) -> RecognitionReceiptView:
        """**The Ledger boundary.** Post through M9.13's own command; record whatever it says.

        `BillingError` is caught and nothing else. A considered refusal — no open accounting
        period — leaves an `UNPOSTED` entry and a published fact; a `TypeError` from a wiring
        mistake propagates, because a bug is not a refusal. That distinction is exactly the M9.16-R1
        P-6 correction, applied here from the start."""
        if self._ledger_post is None:
            with self._uow.new():
                stored = self._repos.entries.settle(
                    self._svc.entry_lifecycle.apply(
                        entry, EntryTrigger.REFUSE, at=at,
                        note="no ledger is wired; the recognition is recorded, not posted",
                    )
                )
                self._factory.emit_unposted(stored, reason="no ledger engine is wired")
            return self._receipt(stored, schedule, posted=False, outcome="unwired")

        with self._uow.new() as uow:
            try:
                receipt = self._ledger_post(
                    PostJournalEntry(
                        actor_id=command.actor_id,
                        org_id=command.org_id,
                        account_id=entry.account_id,
                        entry_type=LedgerEntryType.CHARGE,
                        direction=LedgerDirection.CREDIT,
                        amount_minor=entry.amount.minor,
                        currency=entry.amount.currency,
                        reference=schedule.basis.document_id,
                        description=(
                            f"revenue recognized for {entry.note or entry.sequence} "
                            f"({schedule.basis.policy_code}@{schedule.basis.policy_version})"
                        ),
                        source=PostingSource.REVENUE_RECOGNITION,
                        source_id=entry.id,
                        recognition=RecognitionState.RECOGNIZED,
                    )
                )
            except BillingError as refusal:
                stored = self._repos.entries.settle(
                    self._svc.entry_lifecycle.apply(
                        entry, EntryTrigger.REFUSE, at=at, note=str(refusal)
                    )
                )
                self._factory.emit_unposted(stored, reason=str(refusal))
                uow.stage(self._event(command, stored.id, ledger="refused"))
                return self._receipt(
                    stored, schedule, posted=False, outcome="refused", note=str(refusal)
                )
            outcome = getattr(receipt, "outcome", "posted")
            stored = self._repos.entries.settle(
                self._svc.entry_lifecycle.apply(
                    entry,
                    EntryTrigger.POST,
                    at=at,
                    ledger_entry_id=getattr(receipt, "journal_entry_id", ""),
                    period_key_value=getattr(receipt, "period_key", ""),
                    disposition=getattr(receipt, "disposition", ""),
                )
            )
            uow.stage(self._event(command, stored.id, ledger=outcome))
            return self._receipt(stored, schedule, posted=True, outcome=outcome)

    def post_to_ledger(self, command: PostRecognitionToLedger) -> RecognitionReceiptView:
        """Repair an unposted recognition. Idempotent: the Ledger derives one entry id per
        source, so a repeated repair is a recorded duplicate rather than a second fact."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        entry = self.entry_or_404(command.org_id, command.entry_id)
        if entry.state is not EntryState.UNPOSTED:
            raise _Conflict(
                f"recognition entry {entry.id!r} is {entry.state.value}; only an unposted "
                "recognition needs repairing"
            )
        schedule = self.schedule_or_404(command.org_id, entry.schedule_id)
        return self._post(command, entry, schedule, at=self._clock.now())

    # ── corrections ─────────────────────────────────────────────────────────────

    def reverse(self, command: ReverseRecognition) -> RecognitionAdjustmentView:
        """Correct a recognized amount with a new fact. **The original entry is never edited.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            entry = self.entry_or_404(command.org_id, command.entry_id)
            schedule = self.schedule_or_404(command.org_id, entry.schedule_id)
            existing = self._repos.adjustments.for_entry(command.org_id, entry.id)
            self._svc.guards.guard_reversible(entry, existing)
            amount = (
                self._factory.reversal_amount(entry)
                if command.kind is AdjustmentKind.REVERSAL
                else Money(command.amount_minor, entry.amount.currency)
            )
            built = self._factory.adjustment(
                schedule,
                entry,
                kind=command.kind,
                amount=amount,
                reason=command.reason,
                request_key=command.request_key or entry.id,
                at=at,
                actor_id=command.actor_id,
            )
            stored, is_new = self._repos.adjustments.append(built)
            if is_new:
                self._repos.entries.settle(
                    self._svc.entry_lifecycle.apply(
                        entry, EntryTrigger.REVERSE, at=at, note=command.reason
                    )
                )
                stored = self._post_adjustment(command, stored, entry)
                self._log(
                    command, schedule, kind="reversed", summary=command.reason, entry_id=entry.id
                )
            uow.stage(self._event(command, stored.id, entry_id=entry.id, kind=command.kind.value))
            return adjustment_view(stored)

    def _post_adjustment(self, command, adjustment, entry: RecognitionEntry):
        """The correction as a financial fact, through the same one boundary."""
        if self._ledger_post is None:
            return adjustment
        try:
            receipt = self._ledger_post(
                PostJournalEntry(
                    actor_id=command.actor_id,
                    org_id=command.org_id,
                    account_id=adjustment.account_id,
                    entry_type=LedgerEntryType.ADJUSTMENT,
                    direction=(
                        LedgerDirection.DEBIT
                        if adjustment.amount.minor < 0
                        else LedgerDirection.CREDIT
                    ),
                    amount_minor=abs(adjustment.amount.minor),
                    currency=adjustment.amount.currency,
                    reference=adjustment.document_id,
                    description=f"revenue {adjustment.kind.value}: {adjustment.reason}",
                    source=PostingSource.REVENUE_RECOGNITION,
                    source_id=adjustment.id,
                    recognition=RecognitionState.RECOGNIZED,
                )
            )
        except BillingError:
            return adjustment
        return replace(adjustment, ledger_entry_id=getattr(receipt, "journal_entry_id", ""))

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def supersede(self, command: SupersedeRevenueSchedule) -> RevenueScheduleView:
        """The document was revised. **What was already recognized stands.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            schedule = self.schedule_or_404(command.org_id, command.schedule_id)
            moved = self._svc.schedule_lifecycle.apply(
                schedule, ScheduleTrigger.SUPERSEDE, at=at, reason=command.reason
            )
            moved = self._factory.supersede(
                moved, successor_id=command.successor_schedule_id, at=at
            )
            stored = self._repos.schedules.advance(moved, expected_version=schedule.version)
            self._skip_pending(command, stored, at=at)
            self._factory.emit_schedule_state(
                stored, actor_id=command.actor_id, reason=command.reason
            )
            self._log(command, stored, kind="superseded", summary=command.reason)
            uow.stage(self._event(command, stored.id, successor=command.successor_schedule_id))
            return self._schedule_view(stored)

    def cancel(self, command: CancelRevenueSchedule) -> RevenueScheduleView:
        """Refused once revenue exists — the way back from a recognition is a reversal."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            schedule = self.schedule_or_404(command.org_id, command.schedule_id)
            self._svc.guards.guard_not_earned(schedule)
            moved = self._svc.schedule_lifecycle.apply(
                schedule, ScheduleTrigger.CANCEL, at=at, reason=command.reason
            )
            stored = self._repos.schedules.advance(moved, expected_version=schedule.version)
            self._skip_pending(command, stored, at=at)
            self._factory.emit_schedule_state(
                stored, actor_id=command.actor_id, reason=command.reason
            )
            self._log(command, stored, kind="cancelled", summary=command.reason)
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return self._schedule_view(stored)

    def skip_entry(self, command: SkipRecognitionEntry) -> RecognitionEntryView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            entry = self.entry_or_404(command.org_id, command.entry_id)
            stored = self._repos.entries.settle(
                self._svc.entry_lifecycle.apply(
                    entry, EntryTrigger.SKIP, at=self._clock.now(), note=command.reason
                )
            )
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return self._entry_view(stored)

    def _skip_pending(self, command, schedule: RevenueSchedule, *, at: float) -> None:
        """Retire every unrecognized slice when a schedule stops. Recognized ones are untouched."""
        for entry in self._repos.entries.for_schedule(command.org_id, schedule.id):
            if entry.state is EntryState.PENDING:
                self._repos.entries.settle(
                    self._svc.entry_lifecycle.apply(
                        entry, EntryTrigger.SKIP, at=at, note=schedule.closed_reason
                    )
                )

    # ── internals ───────────────────────────────────────────────────────────────

    def _schedule_view(self, schedule: RevenueSchedule) -> RevenueScheduleView:
        return schedule_view(schedule, self._svc.schedule_lifecycle.allowed(schedule.state))

    def _entry_view(self, entry: RecognitionEntry) -> RecognitionEntryView:
        return recognition_entry_view(entry, self._svc.entry_lifecycle.allowed(entry.state))

    def _receipt(
        self,
        entry: RecognitionEntry,
        schedule: RevenueSchedule,
        *,
        posted: bool,
        outcome: str,
        note: str = "",
    ) -> RecognitionReceiptView:
        return RecognitionReceiptView(
            entry=self._entry_view(entry),
            schedule=self._schedule_view(schedule),
            recognized=True,
            posted=posted,
            ledger_entry_id=entry.ledger_entry_id,
            ledger_outcome=outcome,
            ledger_disposition=entry.ledger_disposition,
            note=note,
        )

    def _log(self, command, schedule, *, kind: str, summary: str = "", entry_id: str = "") -> None:
        self._repos.activity.append(
            self._factory.activity(
                schedule,
                kind=kind,
                summary=summary,
                actor_id=command.actor_id,
                entry_id=entry_id,
                at=self._clock.now(),
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.REVENUE_RECOGNIZED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class RevenueQueries(_RevenueSupport):
    """Read side. Derives every figure from the facts; stores and caches nothing."""

    def __init__(self, *, context, repos, services, authz, clock) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz
        self._clock = clock

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    def get_schedule(self, query: GetRevenueSchedule) -> RevenueScheduleView:
        self._read(query)
        schedule = self.schedule_or_404(query.org_id, query.schedule_id)
        return schedule_view(schedule, self._svc.schedule_lifecycle.allowed(schedule.state))

    def list_schedules(self, query: ListRevenueSchedules) -> tuple[RevenueScheduleView, ...]:
        self._read(query)
        if query.account_id:
            found = self._repos.schedules.for_account(query.org_id, query.account_id, query.limit)
        elif query.states:
            found = self._repos.schedules.by_state(query.org_id, query.states, query.limit)
        else:
            found = self._repos.schedules.all(query.org_id, query.limit)
        return tuple(
            schedule_view(s, self._svc.schedule_lifecycle.allowed(s.state)) for s in found
        )

    def schedules_for_document(
        self, query: GetSchedulesForDocument
    ) -> tuple[RevenueScheduleView, ...]:
        """Every schedule derived from one document. **The supersession trail**, oldest first."""
        self._read(query)
        found = self._repos.schedules.for_document(query.org_id, query.document_id)
        return tuple(
            schedule_view(s, self._svc.schedule_lifecycle.allowed(s.state)) for s in found
        )

    def get_entry(self, query: GetRecognitionEntry) -> RecognitionEntryView:
        self._read(query)
        entry = self.entry_or_404(query.org_id, query.entry_id)
        return recognition_entry_view(entry, self._svc.entry_lifecycle.allowed(entry.state))

    def list_entries(self, query: ListRecognitionEntries) -> tuple[RecognitionEntryView, ...]:
        self._read(query)
        found = self._repos.entries.for_schedule(query.org_id, query.schedule_id)
        return tuple(
            recognition_entry_view(e, self._svc.entry_lifecycle.allowed(e.state)) for e in found
        )

    def list_adjustments(
        self, query: ListRecognitionAdjustments
    ) -> tuple[RecognitionAdjustmentView, ...]:
        self._read(query)
        found = (
            self._repos.adjustments.for_entry(query.org_id, query.entry_id)
            if query.entry_id
            else self._repos.adjustments.for_schedule(query.org_id, query.schedule_id)
        )
        return tuple(adjustment_view(a) for a in found)

    def due(self, query: DueRecognitions) -> tuple[RecognitionEntryView, ...]:
        """What a scheduler outside this plane asks for. A read; nothing is recognized here."""
        self._read(query)
        at = query.at if query.at is not None else self._clock.now()
        found = self._repos.entries.due(query.org_id, at, query.limit)
        return tuple(
            recognition_entry_view(e, self._svc.entry_lifecycle.allowed(e.state)) for e in found
        )

    def unposted(self, query: UnpostedRecognitions) -> tuple[RecognitionEntryView, ...]:
        self._read(query)
        found = self._repos.entries.unposted(query.org_id, query.limit)
        return tuple(
            recognition_entry_view(e, self._svc.entry_lifecycle.allowed(e.state)) for e in found
        )

    def position(self, query: GetRecognitionPosition) -> RecognitionPositionView:
        self._read(query)
        schedule = self.schedule_or_404(query.org_id, query.schedule_id)
        return revenue_position_view(self.position_for(query.org_id, schedule))

    def explain(self, query: ExplainRecognition) -> RecognitionExplanationView:
        self._read(query)
        schedule = self.schedule_or_404(query.org_id, query.schedule_id)
        position = self.position_for(query.org_id, schedule)
        return revenue_explanation_view(self._svc.explainer.explain(schedule, position))

    def inspect(self, query: InspectRevenueSchedule) -> RecognitionInspectionView:
        self._read(query)
        at = query.at if query.at is not None else self._clock.now()
        schedule = self.schedule_or_404(query.org_id, query.schedule_id)
        entries = self._repos.entries.for_schedule(query.org_id, schedule.id)
        adjustments = self._repos.adjustments.for_schedule(query.org_id, schedule.id)
        position = self._svc.positions.position(schedule, entries, adjustments)
        inspection = self._svc.inspector.inspect(
            schedule, position, entries=entries, adjustments=adjustments, at=at
        )
        return RecognitionInspectionView(
            schedule=schedule_view(
                schedule, self._svc.schedule_lifecycle.allowed(schedule.state)
            ),
            position=revenue_position_view(position),
            adjustments=inspection.adjustments,
            due_now=inspection.due_now,
            next_recognize_on=inspection.next_recognize_on,
        )

    def list_policies(self, query: ListRecognitionPolicies) -> tuple[RecognitionPolicyView, ...]:
        self._read(query)
        found = self._repos.policies.all(query.org_id, query.include_inactive)
        return tuple(revenue_policy_view(p) for p in found)

    def policy_versions(
        self, query: GetRecognitionPolicyVersions
    ) -> tuple[RecognitionPolicyView, ...]:
        self._read(query)
        found = self._repos.policies.versions(query.org_id, query.code)
        return tuple(revenue_policy_view(p) for p in found)

    def search(self, query: SearchRevenueSchedules) -> RevenueSearchView:
        self._read(query)
        from billing.foundation import ScheduleState as _S

        states = tuple(_S(s) for s in query.states if s in {m.value for m in _S})
        candidates = (
            self._repos.schedules.for_account(
                query.org_id, query.account_id, max(query.limit * 4, 100)
            )
            if query.account_id
            else self._repos.schedules.all(query.org_id, max(query.limit * 4, 100))
        )
        result = self._svc.search.search(
            RevenueSearchQuery(
                org_id=query.org_id,
                account_id=query.account_id,
                document_id=query.document_id,
                states=states,
                limit=query.limit,
            ),
            candidates,
        )
        return RevenueSearchView(
            schedules=tuple(
                schedule_view(s, self._svc.schedule_lifecycle.allowed(s.state))
                for s in result.schedules
            ),
            total=result.total,
            truncated=result.truncated,
        )

    def timeline(self, query: RecognitionTimeline) -> tuple[RecognitionActivityView, ...]:
        self._read(query)
        found = self._repos.activity.for_schedule(query.org_id, query.schedule_id, query.limit)
        return tuple(revenue_activity_view(a) for a in found)

    def deferred_summary(self, query: DeferredRevenueSummary) -> DeferredRevenueView:
        """**Deferred revenue, computed from the facts on every call.**

        There is no `deferred_balance` column anywhere in this milestone, and this method is why
        there does not need to be one: total minus recognized minus adjusted, summed across the
        account's live schedules, in integer minor units."""
        self._read(query)
        schedules = (
            self._repos.schedules.for_account(query.org_id, query.account_id, query.limit)
            if query.account_id
            else self._repos.schedules.all(query.org_id, query.limit)
        )
        live = [s for s in schedules if s.state is not ScheduleState.SUPERSEDED]
        currency = live[0].currency if live else Currency.INR
        total = recognized = deferred = 0
        unposted = 0
        for schedule in live:
            position = self.position_for(query.org_id, schedule)
            if position.currency is not currency:
                # Mixed currencies are not summed. Converting would make this a foreign-exchange
                # opinion, which Billing does not hold (RS5: conversion is presentation, never
                # ledger). The caller filters by account, and an account has one currency.
                continue
            total += position.total.minor
            recognized += position.recognized.minor
            deferred += position.deferred.minor
            unposted += position.unposted_entries
        return DeferredRevenueView(
            account_id=query.account_id,
            currency=currency.value,
            total=MoneyView(minor=total, currency=currency.value),
            recognized=MoneyView(minor=recognized, currency=currency.value),
            deferred=MoneyView(minor=deferred, currency=currency.value),
            schedules=len(live),
            unposted_entries=unposted,
        )


def register_revenue_use_cases(command_bus, query_bus, uc: RevenueUseCases, q: RevenueQueries):
    """Wire the recognition vocabulary onto the shared buses. One handler per command."""
    command_bus.register(PublishRecognitionPolicy, uc.publish_policy)
    command_bus.register(DeactivateRecognitionPolicy, uc.deactivate_policy)
    command_bus.register(ScheduleRevenue, uc.schedule_revenue)
    command_bus.register(ActivateRevenueSchedule, uc.activate_schedule)
    command_bus.register(RecognizeRevenue, uc.recognize)
    command_bus.register(PostRecognitionToLedger, uc.post_to_ledger)
    command_bus.register(ReverseRecognition, uc.reverse)
    command_bus.register(SupersedeRevenueSchedule, uc.supersede)
    command_bus.register(CancelRevenueSchedule, uc.cancel)
    command_bus.register(SkipRecognitionEntry, uc.skip_entry)
    query_bus.register(GetRevenueSchedule, q.get_schedule)
    query_bus.register(ListRevenueSchedules, q.list_schedules)
    query_bus.register(GetSchedulesForDocument, q.schedules_for_document)
    query_bus.register(GetRecognitionEntry, q.get_entry)
    query_bus.register(ListRecognitionEntries, q.list_entries)
    query_bus.register(ListRecognitionAdjustments, q.list_adjustments)
    query_bus.register(DueRecognitions, q.due)
    query_bus.register(UnpostedRecognitions, q.unposted)
    query_bus.register(GetRecognitionPosition, q.position)
    query_bus.register(ExplainRecognition, q.explain)
    query_bus.register(InspectRevenueSchedule, q.inspect)
    query_bus.register(ListRecognitionPolicies, q.list_policies)
    query_bus.register(GetRecognitionPolicyVersions, q.policy_versions)
    query_bus.register(SearchRevenueSchedules, q.search)
    query_bus.register(RecognitionTimeline, q.timeline)
    query_bus.register(DeferredRevenueSummary, q.deferred_summary)


__all__ = [
    "ALL_REVENUE_COMMANDS",
    "ALL_REVENUE_QUERIES",
    "ActivateRevenueSchedule",
    "CancelRevenueSchedule",
    "DeactivateRecognitionPolicy",
    "DeferredRevenueSummary",
    "DeferredRevenueView",
    "DueRecognitions",
    "ExplainRecognition",
    "GetRecognitionEntry",
    "GetRecognitionPolicyVersions",
    "GetRecognitionPosition",
    "GetRevenueSchedule",
    "GetSchedulesForDocument",
    "InspectRevenueSchedule",
    "ListRecognitionAdjustments",
    "ListRecognitionEntries",
    "ListRecognitionPolicies",
    "ListRevenueSchedules",
    "PostRecognitionToLedger",
    "PublishRecognitionPolicy",
    "RecognitionActivityView",
    "RecognitionAdjustmentView",
    "RecognitionBasisView",
    "RecognitionEntryView",
    "RecognitionExplanationView",
    "RecognitionInspectionView",
    "RecognitionPolicyView",
    "RecognitionPositionView",
    "RecognitionReceiptView",
    "RecognitionTimeline",
    "RecognizeRevenue",
    "ReverseRecognition",
    "RevenueQueries",
    "RevenueRepositories",
    "RevenueScheduleView",
    "RevenueSearchView",
    "RevenueServices",
    "RevenueUseCases",
    "ScheduleRevenue",
    "SearchRevenueSchedules",
    "SkipRecognitionEntry",
    "SupersedeRevenueSchedule",
    "revenue_activity_view",
    "adjustment_view",
    "basis_view",
    "recognition_entry_view",
    "revenue_explanation_view",
    "revenue_policy_view",
    "revenue_position_view",
    "register_revenue_use_cases",
    "schedule_view",
]
