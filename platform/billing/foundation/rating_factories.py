"""Rating factories + repository contracts (M9.12).

`RatingFactory` is the one place a minimum commitment, a rating run, a rating snapshot, or an
activity entry is constructed, following the M9.2 factory discipline exactly: validate
structurally, **fail fast** on error-severity findings (`BillingValidationError`, §A2.6), compose
an immutable aggregate from the injected id and clock seams, and emit the corresponding
`billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all. The one that is
easy to miss here is the mirror of M9.11's duplicate-ignored fact: a rerate whose inputs have not
moved emits `billing.rating.unchanged`, not `billing.rating.completed`. "We rated this again and
nothing moved" is a distinct business fact, and it is the evidence that determinism held — a
nightly rerating job that emitted nothing would leave no trace that it checked.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

---

## Why the run id is not minted

Every other aggregate in Billing draws its id from the `IdGenerator` seam. A rating run does not:
it derives its id from `(org, subscription, period, basis digest)`. Three consequences follow
structurally rather than by convention.

* **Rerating unchanged inputs is a no-op at the database.** The derived id is identical, the
  unique constraint fires, and the repository returns the stored run. Idempotent rerating is
  therefore a property of the storage layer rather than of a code path that could be raced.
* **Replay is byte-comparable.** Two runs over the same inputs produce equal ids, equal charge
  ids (derived from the run id), and equal digests. "Replay produces byte-identical results" is
  checked with `==`, not asserted in prose.
* **A changed input cannot overwrite the figure it revises.** A different basis means a different
  id, so the new run is *appended beside* the old one. There is no code path in this milestone
  that could edit a rating run's charges, because the id of a revised run is never the id of the
  run it revises.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .commercial_rating import (
    MinimumCommitment,
    RatedCharge,
    RatingAction,
    RatingActivity,
    RatingBasis,
    RatingResult,
    RatingRun,
    RatingSnapshot,
    RatingState,
    derive_rating_run_id,
)
from .domain import BillingInterval, BillingPeriod
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Money
from .rating_rules import RatingValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_COMMITMENT = "cmm"
PREFIX_RATING_SNAPSHOT = "rsn"
PREFIX_RATING_ACTIVITY = "rac"


@dataclass(frozen=True, slots=True)
class RatingFactory:
    """Constructs rating aggregates from injected seams. Stateless; holds references only."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: RatingValidator = RatingValidator()

    # ── commitments ─────────────────────────────────────────────────────────────

    def define_commitment(
        self,
        *,
        org_id: str,
        account_id: str,
        code: str,
        amount: Money,
        subscription_id: str = "",
        price_codes: Sequence[str] = (),
        interval: BillingInterval = BillingInterval.MONTHLY,
        contract_id: str = "",
        description: str = "",
        effective_from: float = 0.0,
        effective_until: float | None = None,
        existing: Sequence[MinimumCommitment] = (),
        actor_id: str = "",
    ) -> MinimumCommitment:
        now = self.clock.now()
        commitment = MinimumCommitment(
            id=self.ids.new(PREFIX_COMMITMENT),
            org_id=org_id,
            account_id=account_id,
            code=code,
            amount=amount,
            subscription_id=subscription_id,
            price_codes=tuple(price_codes),
            interval=interval,
            contract_id=contract_id,
            description=description,
            effective_from=effective_from or now,
            effective_until=effective_until,
            created_at=now,
        )
        self._fail_fast(self.validator.validate_commitment(commitment))
        self._fail_fast(self.validator.validate_commitments([*existing, commitment]))
        self._emit(
            BillingEventType.COMMITMENT_DEFINED,
            org_id,
            commitment.id,
            actor_id,
            code=code,
            account_id=account_id,
            amount_minor=amount.minor,
            currency=amount.currency.value,
        )
        return commitment

    def retire_commitment(
        self, commitment: MinimumCommitment, *, actor_id: str = "", reason: str = ""
    ) -> MinimumCommitment:
        """Retirement is a state, never a deletion — a commitment that produced a true-up on an
        issued invoice must remain readable."""
        retired = replace(
            commitment,
            active=False,
            retired_at=self.clock.now(),
        )
        self._emit(
            BillingEventType.COMMITMENT_RETIRED,
            commitment.org_id,
            commitment.id,
            actor_id,
            code=commitment.code,
            reason=reason,
        )
        return retired

    # ── runs ────────────────────────────────────────────────────────────────────

    def open_run(
        self,
        *,
        basis: RatingBasis,
        sequence: int = 1,
        supersedes: str = "",
        adjusts: str = "",
        reason=None,
        detail: str = "",
        actor_id: str = "",
    ) -> RatingRun:
        """A DRAFT run with a **derived** id. No fact is emitted yet: opening a run is not a
        commercial event, and rating it is."""
        return RatingRun(
            id=derive_rating_run_id(
                basis.org_id,
                basis.subscription_id,
                basis.period.start,
                basis.period.end,
                basis.digest,
            ),
            org_id=basis.org_id,
            account_id=basis.account_id,
            subscription_id=basis.subscription_id,
            period=basis.period,
            currency=basis.currency,
            basis_digest=basis.digest,
            state=RatingState.DRAFT,
            sequence=sequence,
            reason=reason,
            supersedes=supersedes,
            adjusts=adjusts,
            detail=detail,
            rated_at=self.clock.now(),
            rated_by=actor_id,
        )

    def complete_run(
        self, run: RatingRun, result: RatingResult, *, state: RatingState, actor_id: str = ""
    ) -> RatingRun:
        """Attach the result and emit exactly one fact — `rerated` when this run revises another,
        `completed` when it is the first word on the period."""
        completed = replace(run, result=result, state=state, updated_at=self.clock.now())
        self._fail_fast(self.validator.validate_run(completed))
        self._emit(
            BillingEventType.RATING_RERATED if run.is_rerate else BillingEventType.RATING_COMPLETED,
            run.org_id,
            run.id,
            actor_id,
            subscription_id=run.subscription_id,
            account_id=run.account_id,
            total_minor=result.total.minor,
            currency=result.currency.value,
            charges=result.charge_count,
            basis_digest=run.basis_digest,
            result_digest=result.digest,
        )
        return completed

    def emit_unchanged(self, run: RatingRun, *, actor_id: str = "") -> None:
        """The fact that rating ran and nothing moved. Emitted instead of `completed`, never
        beside it — two facts for one act would double-count every nightly job."""
        self._emit(
            BillingEventType.RATING_UNCHANGED,
            run.org_id,
            run.id,
            actor_id,
            subscription_id=run.subscription_id,
            basis_digest=run.basis_digest,
        )

    def emit_run_fact(
        self, run: RatingRun, *, event_type: BillingEventType, actor_id: str = "", **data
    ) -> None:
        """The public emission path for a lifecycle move an orchestrator performed.

        Public deliberately: at M9.10 the Application was found reaching into a factory's private
        emitter, and a seam that must be reached around is not a seam."""
        self._emit(
            event_type,
            run.org_id,
            run.id,
            actor_id,
            subscription_id=run.subscription_id,
            account_id=run.account_id,
            state=run.state.value,
            **data,
        )

    # ── snapshots + history ─────────────────────────────────────────────────────

    def snapshot(
        self, run: RatingRun, *, actor_id: str = "", reason: str = "", sequence: int = 1
    ) -> RatingSnapshot:
        """An immutable, self-contained record of what a run decided."""
        if run.result is None:
            raise BillingValidationError(
                _one("rating.snapshot", "a run with no result cannot be snapshotted")
            )
        snapshot = RatingSnapshot(
            id=self.ids.new(PREFIX_RATING_SNAPSHOT),
            org_id=run.org_id,
            account_id=run.account_id,
            subscription_id=run.subscription_id,
            rating_run_id=run.id,
            period=run.period,
            sequence=sequence,
            result=run.result,
            result_digest=run.result_digest,
            basis_digest=run.basis_digest,
            taken_at=self.clock.now(),
            taken_by=actor_id,
            reason=reason,
        )
        self._emit(
            BillingEventType.RATING_SNAPSHOT_TAKEN,
            run.org_id,
            snapshot.id,
            actor_id,
            rating_run_id=run.id,
            result_digest=snapshot.result_digest,
        )
        return snapshot

    def activity(
        self,
        *,
        org_id: str,
        action: RatingAction,
        actor_id: str = "",
        account_id: str = "",
        subscription_id: str = "",
        rating_run_id: str = "",
        period: BillingPeriod | None = None,
        total: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> RatingActivity:
        return RatingActivity(
            id=self.ids.new(PREFIX_RATING_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self.clock.now(),
            account_id=account_id,
            subscription_id=subscription_id,
            rating_run_id=rating_run_id,
            period_start=period.start if period else None,
            period_end=period.end if period else None,
            total_minor=total.minor if total else 0,
            currency=total.currency if total else None,
            reason=reason,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        org_id: str,
        subject_id: str,
        actor_id: str,
        **data,
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


def _one(path: str, message: str):
    from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity

    return BillingDiagnostics.of(
        [Diagnostic(path, BillingDiagnosticCode.MISSING_REQUIRED_FIELD, Severity.ERROR, message)]
    )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class RatingRunRepository(ABC):
    """Rating runs. **Append-only in content, replaceable in state.**

    `append` must be idempotent on the derived id and must never raise on a duplicate: two
    instances rating the same period concurrently compute the same id, so one wins and the other
    reads the winner back. That is what turns "a nightly job and a manual rerate collided" into a
    non-event rather than a duplicated charge.

    `replace` exists because a run's *state* advances — rated → finalized, rated → superseded.
    It never amends a result: the lifecycle has no edge that would allow it, and the charges live
    in their own append-only table."""

    @abstractmethod
    def append(self, run: RatingRun) -> RatingRun:
        """Store, or return what is already stored for this basis. Never raises on a duplicate."""

    @abstractmethod
    def get(self, org_id: str, run_id: str) -> RatingRun | None: ...
    @abstractmethod
    def latest_for_period(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> RatingRun | None:
        """The run in force for this period: the newest non-superseded, non-discarded one."""

    @abstractmethod
    def for_period(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> tuple[RatingRun, ...]:
        """Every run for this period, oldest first — the rerating history of one period."""

    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[RatingRun, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[RatingRun, ...]: ...
    @abstractmethod
    def replace(self, run: RatingRun) -> RatingRun:
        """State only. A settled run's figures are a historical fact."""

    @abstractmethod
    def next_sequence(
        self, org_id: str, subscription_id: str, period_start: float, period_end: float
    ) -> int:
        """The next rerating sequence for this period. 1 for the first run."""


class RatedChargeRepository(ABC):
    """**Append-only.** There is no update method and no delete method — none exists to call,
    because a revised charge is a new charge in a new run."""

    @abstractmethod
    def append_all(
        self, charges: Sequence[RatedCharge], *, rating_run_id: str
    ) -> tuple[RatedCharge, ...]:
        """Store a run's charges. Idempotent: charge ids are derived, so a retry is a no-op."""

    @abstractmethod
    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatedCharge, ...]: ...
    @abstractmethod
    def get(self, org_id: str, charge_id: str) -> RatedCharge | None: ...
    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 500
    ) -> tuple[RatedCharge, ...]: ...


class RatingSnapshotRepository(ABC):
    """Append-only rating snapshots. No delete: a snapshot is what an invoice was raised from."""

    @abstractmethod
    def append(self, snapshot: RatingSnapshot) -> RatingSnapshot: ...
    @abstractmethod
    def get(self, org_id: str, snapshot_id: str) -> RatingSnapshot | None: ...
    @abstractmethod
    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatingSnapshot, ...]: ...
    @abstractmethod
    def for_subscription(
        self, org_id: str, subscription_id: str, limit: int = 100
    ) -> tuple[RatingSnapshot, ...]: ...


class MinimumCommitmentRepository(ABC):
    """Minimum commitments. Org-scoped by construction (§A11.2)."""

    @abstractmethod
    def add(self, commitment: MinimumCommitment) -> MinimumCommitment: ...
    @abstractmethod
    def get(self, org_id: str, commitment_id: str) -> MinimumCommitment | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> MinimumCommitment | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[MinimumCommitment, ...]: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[MinimumCommitment, ...]: ...
    @abstractmethod
    def replace(self, commitment: MinimumCommitment) -> MinimumCommitment:
        """Whole-record replacement. Retirement is a state, not a deletion."""


class RatingActivityRepository(ABC):
    """Append-only rating history."""

    @abstractmethod
    def append(self, activity: RatingActivity) -> RatingActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, subscription_id: str = "", limit: int = 100
    ) -> tuple[RatingActivity, ...]:
        """Newest first. `subscription_id=""` spans the whole organization."""

    @abstractmethod
    def for_run(self, org_id: str, rating_run_id: str) -> tuple[RatingActivity, ...]: ...


__all__ = [
    "PREFIX_COMMITMENT",
    "PREFIX_RATING_ACTIVITY",
    "PREFIX_RATING_SNAPSHOT",
    "MinimumCommitmentRepository",
    "RatedChargeRepository",
    "RatingActivityRepository",
    "RatingFactory",
    "RatingRunRepository",
    "RatingSnapshotRepository",
]
