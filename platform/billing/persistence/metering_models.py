"""Metering Engine tables (M9.11) — additive to the M9.4–M9.10 schema.

Six new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including
M9.2's `cp_billing_usage_record`, `cp_billing_usage_aggregate`, and `cp_billing_usage_quarantine`,
which stay exactly as they are.

Three are **append-only by construction**: commercial usage, corrections, and metering activity.

## The exactly-once constraint

`uq_cp_billing_commercial_usage_source` on
`(org_id, source_event_id, meter_code, corrects_usage_id)` is the load-bearing object in this
milestone.

`corrects_usage_id` is part of the key and empty on an original, so the constraint says exactly
what the invariant says: **at most one original usage record per source event per meter**, plus
at most one correction per `(source event, correction target)`. Without it the two invariants
collide — an append-only correction derived from the same source event is indistinguishable from
a duplicate ingestion of it, and the correction is silently swallowed. Found at M9.11 by a test
that asserted a correction produced an adjustment and got the original back.

The constraint is what makes exactly-once a property of the *database* rather than of a code path
that could be raced:

* two application instances ingesting the same event concurrently both attempt an insert; one
  wins, the other's `IntegrityError` is caught by the repository and turned into a read of the
  winner. The caller of either instance gets the same record;
* a retry after a crash mid-transaction cannot produce a second row, because the primary key is
  *derived* from `(org_id, event_id, meter_code)` and is therefore identical on the retry;
* a replay months later recomputes the same key and is a no-op.

The unique constraint is scoped by `org_id` deliberately. M9.2's equivalent constraint is on
`(source_event_id, meter_code)` alone, which is stricter than tenancy requires and would make two
organizations' independent event-id spaces collide. Adding `org_id` here is not a relaxation of
that table — it is a separate table with a correctly scoped key, and M9.2's is untouched.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingCommercialMeterRow(SQLModel, table=True):
    """The meter registry. `(org_id, code)` is unique — one definition per code; changing a
    meter publishes a new *version* of it rather than a second row."""

    __tablename__ = "cp_billing_commercial_meter"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    display_name: str = ""
    unit: str = Field(default="unit", index=True)
    aggregation: str = Field(default="sum", index=True)
    source: str = Field(default="runtime.session", index=True)
    state: str = Field(default="draft", index=True)
    version: int = Field(default=1, index=True)
    late_arrival_window_seconds: float = Field(default=172_800.0, index=True)
    normalization_numerator: int = 1
    normalization_denominator: int = 1
    normalization_rounding: str = Field(default="half_up", index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    activated_at: float | None = Field(default=None, index=True)
    retired_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_commercial_meter"),)


class BillingCommercialUsageRow(SQLModel, table=True):
    """The authoritative commercial usage record. **Append-only and exactly-once.**

    The primary key is *derived* from `(org_id, event_id, meter_code)`, and the unique constraint
    says the same thing a second way. Two defences rather than one because this is the invariant
    that cannot be recovered from: a double-billed event needs a credit note and a conversation,
    not a re-run."""

    __tablename__ = "cp_billing_commercial_usage"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    source_event_id: str = Field(index=True)
    meter_code: str = Field(index=True)
    meter_version: int = Field(default=1, index=True)
    units: int = Field(default=0, index=True)
    unit: str = Field(default="unit", index=True)
    scope_id: str = Field(default="", index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    observed_at: float = Field(default=0.0, index=True)
    recorded_at: float = Field(default=0.0, index=True)
    status: str = Field(default="recorded", index=True)
    disposition: str = Field(default="in_period", index=True)
    subscription_id: str = Field(default="", index=True)
    corrects_usage_id: str = Field(default="", index=True)
    digest: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "source_event_id",
            "meter_code",
            "corrects_usage_id",
            name="uq_cp_billing_commercial_usage_source",
        ),
    )


class BillingUsagePeriodRow(SQLModel, table=True):
    """A billing period's metering state. `(org_id, account_id, period_start, period_end)` is
    unique — two rows for one window would split a customer's usage across both."""

    __tablename__ = "cp_billing_usage_period"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    state: str = Field(default="open", index=True)
    record_count: int = 0
    adjustment_count: int = 0
    quarantined_count: int = 0
    seal_digest: str = Field(default="", index=True)
    opened_at: float = Field(default=0.0, index=True)
    closing_at: float | None = None
    sealed_at: float | None = Field(default=None, index=True)
    sealed_by: str = ""
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "period_start", "period_end", name="uq_cp_billing_usage_period"
        ),
    )


class BillingUsageAggregateSnapshotRow(SQLModel, table=True):
    """Derived windowed aggregates. The id is derived from `(account, meter, granularity, window
    start)`, so a rebuild *replaces* rather than accumulating beside the previous total."""

    __tablename__ = "cp_billing_usage_aggregate_snapshot"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    meter_code: str = Field(index=True)
    scope_id: str = Field(default="", index=True)
    granularity: str = Field(default="period", index=True)
    window_start: float = Field(default=0.0, index=True)
    window_end: float = Field(default=0.0, index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    aggregation: str = Field(default="sum", index=True)
    units: int = 0
    unit: str = "unit"
    record_count: int = 0
    adjustment_count: int = 0
    source_digest: str = Field(default="", index=True)
    sealed: bool = Field(default=False, index=True)
    computed_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingQuarantineEntryRow(SQLModel, table=True):
    """Events that could not become commercial usage. Never deleted — unattributable usage must
    stay visible (ADR-0023 M6)."""

    __tablename__ = "cp_billing_quarantine_entry"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    source_event_id: str = Field(index=True)
    event_type: str = Field(default="", index=True)
    meter_code: str = Field(default="", index=True)
    reason: str = Field(default="", index=True)
    state: str = Field(default="pending", index=True)
    account_id: str = Field(default="", index=True)
    scope_id: str = Field(default="", index=True)
    raw_units: int = 0
    raw_unit: str = ""
    observed_at: float = Field(default=0.0, index=True)
    received_at: float = Field(default=0.0, index=True)
    resolved_at: float | None = Field(default=None, index=True)
    resolved_by: str = ""
    resulting_usage_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingUsageCorrectionRow(SQLModel, table=True):
    """Append-only corrections. The id is derived, so applying one twice is one record."""

    __tablename__ = "cp_billing_usage_correction"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    corrects_usage_id: str = Field(index=True)
    kind: str = Field(default="correction", index=True)
    delta_units: int = 0
    unit: str = "unit"
    reason_code: str = Field(default="", index=True)
    period_start: float | None = Field(default=None, index=True)
    period_end: float | None = None
    created_at: float = Field(default=0.0, index=True)
    created_by: str = ""
    resulting_usage_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingMeteringActivityRow(SQLModel, table=True):
    """Append-only metering history, duplicate ingestions included — "we saw this again and did
    nothing" is the evidence idempotency worked."""

    __tablename__ = "cp_billing_metering_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    meter_code: str = Field(default="", index=True)
    event_id: str = Field(default="", index=True)
    usage_id: str = Field(default="", index=True)
    period_start: float | None = Field(default=None, index=True)
    period_end: float | None = None
    payload: dict = _payload()  # type: ignore[assignment]


METERING_TABLES = (
    BillingCommercialMeterRow,
    BillingCommercialUsageRow,
    BillingUsagePeriodRow,
    BillingUsageAggregateSnapshotRow,
    BillingQuarantineEntryRow,
    BillingUsageCorrectionRow,
    BillingMeteringActivityRow,
)

METERING_TABLE_NAMES = tuple(t.__tablename__ for t in METERING_TABLES)
