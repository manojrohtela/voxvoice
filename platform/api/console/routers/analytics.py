"""`/v1/console/analytics/*` — aggregates over the organization's conversations (M12.6, LLD §7).

Read-only. `viewer` or higher. The organization is the console session's, never a parameter (S3);
an `agent_id` from another organization is 404, exactly like a missing one (S4). Every endpoint
takes the same query parameters and returns the resolved range beside its figures, so a panel can
always say which days it describes:

* `range=today|7d|30d` (default `7d`) — UTC calendar days ending today, **or**
* `from=YYYY-MM-DD&to=YYYY-MM-DD` — UTC days, both inclusive, at most `MAX_RANGE_DAYS`;
* `agent_id`, `source=playground|api` — optional filters.

Definitions: `docs/architecture/epic12/m12-analytics-definitions.md`. Missing is `null`, never 0.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ..analytics import service
from ..analytics.service import DateRange, Filters, LatencyStats
from ..identity.deps import Member, require_role

router = APIRouter(prefix="/v1/console/analytics", tags=["console"])


def analytics_today() -> date:
    """Today, in UTC — the only calendar analytics uses. A dependency so tests can pin it."""
    return datetime.now(UTC).date()


class RangeView(BaseModel):
    #: The preset used, or null for a custom `from`/`to` range.
    preset: str | None
    #: First and last UTC day, both inclusive.
    first_day: date
    last_day: date
    #: The same range as instants: `[start, end)`.
    start: datetime
    end: datetime
    timezone: Literal["UTC"] = "UTC"
    bucket: Literal["day"] = "day"


class FiltersView(BaseModel):
    agent_id: str | None
    source: str | None


class Query_(BaseModel):
    """What was asked, as resolved. Echoed on every response."""

    range: RangeView
    filters: FiltersView


def _query(window: DateRange, filters: Filters) -> Query_:
    return Query_(
        range=RangeView(
            preset=window.preset,
            first_day=window.first,
            last_day=window.last,
            start=window.start,
            end=window.end,
        ),
        filters=FiltersView(agent_id=filters.agent_id, source=filters.source),
    )


class Scope:
    """The shared parameters, resolved and checked against the member's organization once."""

    def __init__(
        self,
        range: Literal["today", "7d", "30d"] | None = Query(default=None),  # noqa: A002
        from_: date | None = Query(default=None, alias="from"),
        to: date | None = Query(default=None),
        agent_id: str | None = Query(default=None, max_length=64),
        source: Literal["playground", "api"] | None = Query(default=None),
        member: Member = Depends(require_role("viewer")),
        db: DBSession = Depends(get_session),
        today: date = Depends(analytics_today),
    ) -> None:
        self.window = service.resolve_range(today, preset=range, first=from_, last=to)
        self.filters = Filters(agent_id=agent_id, source=source)
        self.org_id = member.org.id
        self.db = db
        service.check_filters(db, self.org_id, self.filters)

    def load(self, *, with_turns: bool = True) -> service.Snapshot:
        return service.load(self.db, self.org_id, self.window, self.filters, with_turns=with_turns)

    @property
    def query(self) -> Query_:
        return _query(self.window, self.filters)


# ── summary ───────────────────────────────────────────────────────────────────


class ConversationCounts(BaseModel):
    total: int
    #: Every M11 lifecycle state, zeros included. The model records no end reason.
    by_status: dict[str, int]
    by_source: dict[str, int]
    #: Conversations the durable event log observed (turns known) / did not (turns unknown).
    recorded: int
    not_recorded: int


class DurationFigures(BaseModel):
    #: Conversations with both a start and an end — the population of every figure below.
    measured: int
    #: Conversations with no end yet (active, or failed before ending): no duration, not 0.
    unmeasured: int
    total_s: float | None
    avg_s: float | None


class TurnFigures(BaseModel):
    #: `metric.turn` records of recorded conversations.
    total: int
    by_status: dict[str, int]
    #: total / recorded conversations; null when no conversation was recorded.
    avg_per_conversation: float | None


class SummaryView(BaseModel):
    query: Query_
    conversations: ConversationCounts
    duration: DurationFigures
    turns: TurnFigures


def summary_figures(
    snap: service.Snapshot,
) -> tuple[ConversationCounts, DurationFigures, TurnFigures]:
    """The summary figures of one snapshot. Shared with the Overview (M12.8), so the two surfaces
    cannot disagree."""
    s = service.summarize(snap.conversations, snap.recorded, snap.turns)
    return (
        ConversationCounts(
            total=s.conversations,
            by_status=s.by_status,
            by_source=s.by_source,
            recorded=s.recorded,
            not_recorded=s.not_recorded,
        ),
        DurationFigures(
            measured=s.with_duration,
            unmeasured=s.without_duration,
            total_s=s.total_duration_s,
            avg_s=s.avg_duration_s,
        ),
        TurnFigures(
            total=s.turns,
            by_status=s.turns_by_status,
            avg_per_conversation=s.avg_turns_per_conversation,
        ),
    )


@router.get("/summary", response_model=SummaryView)
def summary(scope: Scope = Depends()) -> SummaryView:
    conversations, duration, turns = summary_figures(scope.load())
    return SummaryView(
        query=scope.query, conversations=conversations, duration=duration, turns=turns
    )


# ── latency ───────────────────────────────────────────────────────────────────


class LatencyView(BaseModel):
    #: `voice_to_voice`, or a stage of it: endpoint, stt, llm, tts, egress.
    metric: str
    #: Turns in the population; `samples` of them measured this metric, `missing` did not.
    turns: int
    samples: int
    missing: int
    #: All null when `samples` is 0. Percentiles are nearest-rank over the samples.
    avg_ms: float | None
    min_ms: int | None
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


def _latency(stats: LatencyStats) -> LatencyView:
    return LatencyView(**stats.__dict__)


class LatencyReport(BaseModel):
    query: Query_
    data: list[LatencyView]


def latency_views(turns: list[service.TurnFact]) -> list[LatencyView]:
    """Every latency measure, in contract order. Shared with the Overview (M12.8)."""
    return [_latency(service.latency_stats(m, turns)) for m in service.LATENCY_METRICS]


@router.get("/latency", response_model=LatencyReport)
def latency(scope: Scope = Depends()) -> LatencyReport:
    return LatencyReport(query=scope.query, data=latency_views(scope.load().turns))


# ── time series ───────────────────────────────────────────────────────────────


class BucketView(BaseModel):
    #: The UTC day. A conversation (and its turns) counts on the day it started.
    date: date
    conversations: int
    turns: int
    #: Mean over the day's conversations that have ended; null when none has.
    avg_duration_s: float | None
    voice_to_voice_samples: int
    #: Nearest-rank median of the day's measured voice-to-voice values; null without samples.
    voice_to_voice_p50_ms: int | None


class SeriesReport(BaseModel):
    query: Query_
    #: One bucket per day of the range, in order; days without conversations are real zeros.
    data: list[BucketView]


def bucket_views(window: DateRange, snap: service.Snapshot) -> list[BucketView]:
    """One bucket per UTC day of the range. Shared with the Overview (M12.8)."""
    return [
        BucketView(
            date=b.day,
            conversations=b.conversations,
            turns=b.turns,
            avg_duration_s=(round(sum(b.durations) / len(b.durations), 1) if b.durations else None),
            voice_to_voice_samples=len(b.voice_to_voice),
            voice_to_voice_p50_ms=service.percentile(sorted(b.voice_to_voice), 50),
        )
        for b in service.series(window, snap.conversations, snap.turns)
    ]


@router.get("/timeseries", response_model=SeriesReport)
def timeseries(scope: Scope = Depends()) -> SeriesReport:
    return SeriesReport(query=scope.query, data=bucket_views(scope.window, scope.load()))


# ── agents ────────────────────────────────────────────────────────────────────


class AgentUsageView(BaseModel):
    id: str
    #: Null when the agent row no longer exists (deleted through the developer API).
    name: str | None
    archived: bool
    conversations: int
    recorded: int
    turns: int
    avg_duration_s: float | None
    avg_turns_per_conversation: float | None
    voice_to_voice: LatencyView


class AgentReport(BaseModel):
    query: Query_
    #: Agents with at least one conversation in the range, most conversations first.
    data: list[AgentUsageView]


def agent_usage_views(db: DBSession, org_id: str, snap: service.Snapshot) -> list[AgentUsageView]:
    """Agents with conversations in the snapshot, most first. Shared with the Overview (M12.8)."""
    return [
        AgentUsageView(
            id=u.agent_id,
            name=u.name,
            archived=u.archived,
            conversations=u.conversations,
            recorded=u.recorded,
            turns=u.turns,
            avg_duration_s=u.avg_duration_s,
            avg_turns_per_conversation=u.avg_turns_per_conversation,
            voice_to_voice=_latency(u.voice_to_voice),
        )
        for u in service.by_agent(db, org_id, snap.conversations, snap.recorded, snap.turns)
    ]


@router.get("/agents", response_model=AgentReport)
def agents(scope: Scope = Depends()) -> AgentReport:
    return AgentReport(
        query=scope.query, data=agent_usage_views(scope.db, scope.org_id, scope.load())
    )
