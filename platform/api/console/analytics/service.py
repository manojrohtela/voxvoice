"""Analytics projections (M12.6, LLD §7, `m12-analytics-definitions.md`).

**Nothing here is written, and nothing is a second source of truth** (ADR-0055 DP6, Amendment 2).
Every figure is derived, per request, from at most three reads:

* the **conversation population**: the organization's M11 `session` rows whose `started_at` falls
  in the range (and match the filters), one row each;
* the **turn population**: `metric.turn` rows in `evt_event` (sole producer `TurnLatencyTap`) that
  belong to those sessions, with the numeric fields extracted *in SQL* — the payload itself never
  reaches Python; and
* the **recording marks**: which of those sessions have a `session.started` event, i.e. whose turns
  are known. Nothing else in the log is read.

SQL does the tenancy, the range, the filters, the membership and the JSON extraction. Python does
the arithmetic, because two of the contract's figures cannot be computed portably in SQL across the
two dialects the M11 model supports without model changes (SQLite locally, Postgres in production):
**durations** (timestamp subtraction differs per dialect) and **percentiles** (SQLite has none).
Both reads are one row per conversation or per turn, bounded by `MAX_RANGE_DAYS`.

The rules every figure follows (definitions document §2):

* **Missing is never 0.** An unmeasured latency is not a sample; a conversation without an end has
  no duration; a figure with no population is `None`. A *count* of nothing is a genuine 0.
* **Range membership is `session.started_at`**, UTC, half-open `[start, end)`. A conversation and
  all of its turns belong to the day it started, so nothing is split or counted twice.
* **The populations are never joined into one row set** (turns select by `IN`), so a conversation
  is counted once no matter how many turns or events it has.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import or_
from sqlmodel import Session as DBSession
from sqlmodel import select

from core.events import EventType
from events.persistence.models import DurableEventRow

from ...models import Agent, Session
from ..conversations.service import PLAYGROUND_SOURCE, STAGES, duration_s, source_of
from ..errors import InvalidRequest, NotFound
from ..persistence import AgentStateRow

#: Presets, as a number of UTC calendar days ending with (and including) today.
PRESETS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}
DEFAULT_PRESET = "7d"
#: The longest custom range. Bounds the series (one bucket per day) and both reads.
MAX_RANGE_DAYS = 92

#: `source` filter values → how a session's creator tagged it (M12.4). Everything not tagged by the
#: Playground came through the developer API.
SOURCES = ("playground", "api")

#: Session lifecycle states of the M11 model (`api/models.py`). Reported as they are: the model
#: records no end *reason*, so no reason is inferred.
CONVERSATION_STATUSES = ("created", "active", "ended", "failed")

#: `metric.turn` terminal statuses (ADR-0038 TP6). Anything else is counted as `other`.
TURN_STATUSES = ("completed", "cancelled", "interrupted")

#: Latency figures, in contract order: the frozen headline (ADR-0038/ADR-0053), then its stages.
LATENCY_METRICS = ("voice_to_voice", *STAGES)


# ── range ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DateRange:
    """UTC calendar days `first`..`last` inclusive, i.e. instants `[start, end)`."""

    first: date
    last: date
    preset: str | None

    @property
    def start(self) -> datetime:
        return datetime.combine(self.first, time(), UTC)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.last + timedelta(days=1), time(), UTC)

    def days(self) -> list[date]:
        return [self.first + timedelta(days=n) for n in range((self.last - self.first).days + 1)]


def resolve_range(
    today: date, *, preset: str | None, first: date | None, last: date | None
) -> DateRange:
    """A preset ending today (UTC), or an explicit `from`/`to` pair of UTC days — never both."""
    if first is not None or last is not None:
        if preset is not None:
            raise InvalidRequest("Use either range or from/to, not both")
        if first is None or last is None:
            raise InvalidRequest("A custom range needs both from and to")
        if last < first:
            raise InvalidRequest("from must not be after to")
        if (last - first).days + 1 > MAX_RANGE_DAYS:
            raise InvalidRequest(f"A range can cover at most {MAX_RANGE_DAYS} days")
        return DateRange(first, last, None)
    name = preset or DEFAULT_PRESET
    if name not in PRESETS:
        raise InvalidRequest(f"range must be one of {', '.join(PRESETS)}")
    return DateRange(today - timedelta(days=PRESETS[name] - 1), today, name)


# ── the two reads ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Filters:
    agent_id: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ConversationFact:
    id: str
    agent_id: str
    status: str
    #: `playground` or `api`, by the same rule as the `source` filter.
    source: str
    day: date
    duration_s: float | None


@dataclass(frozen=True)
class TurnFact:
    session_id: str
    status: str | None
    #: `voice_to_voice` and each stage → ms, or None when that boundary was not measured.
    ms: dict[str, int | None]


def _naive(value: datetime) -> datetime:
    # M11 stores naive UTC (SQLite drops the zone); compare like with like.
    return value.astimezone(UTC).replace(tzinfo=None)


def check_filters(db: DBSession, org_id: str, filters: Filters) -> None:
    """An agent filter must name one of *this* organization's agents — archived ones included, since
    their history is still theirs. Another organization's agent is indistinguishable from a missing
    one (S4)."""
    if filters.source is not None and filters.source not in SOURCES:
        raise InvalidRequest(f"source must be one of {', '.join(SOURCES)}")
    if filters.agent_id is not None:
        agent = db.get(Agent, filters.agent_id)
        if agent is None or agent.org_id != org_id:
            raise NotFound("Agent not found")


def _population(org_id: str, window: DateRange, filters: Filters):  # noqa: ANN202 — a SQL clause list
    clauses = [
        Session.org_id == org_id,
        Session.started_at.is_not(None),  # type: ignore[union-attr]
        Session.started_at >= _naive(window.start),  # type: ignore[operator]
        Session.started_at < _naive(window.end),  # type: ignore[operator]
    ]
    if filters.agent_id is not None:
        clauses.append(Session.agent_id == filters.agent_id)
    if filters.source is not None:
        tag = Session.meta["source"].as_string()  # type: ignore[index]
        clauses.append(
            tag == PLAYGROUND_SOURCE
            if filters.source == "playground"
            else or_(tag.is_(None), tag != PLAYGROUND_SOURCE)
        )
    return clauses


def conversations(
    db: DBSession, org_id: str, window: DateRange, filters: Filters
) -> list[ConversationFact]:
    rows = db.exec(select(Session).where(*_population(org_id, window, filters))).all()
    facts = []
    for s in rows:
        assert s.started_at is not None  # guaranteed by the population predicate
        facts.append(
            ConversationFact(
                id=s.id,
                agent_id=s.agent_id,
                status=s.status,
                source="playground" if source_of(s) == PLAYGROUND_SOURCE else "api",
                day=(s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=UTC))
                .astimezone(UTC)
                .date(),
                duration_s=duration_s(s),
            )
        )
    return facts


def _sessions_in(org_id: str, window: DateRange, filters: Filters):  # noqa: ANN202 — a subquery
    return select(Session.id).where(*_population(org_id, window, filters))


def recorded_sessions(db: DBSession, org_id: str, window: DateRange, filters: Filters) -> set[str]:
    """Conversations the durable log observed: those with a `session.started` event. Only for these
    are turns *known* — for the rest (sessions from before the log was attached, or ones the runtime
    never started) the turn count is unknown, not zero."""
    rows = db.exec(
        select(DurableEventRow.session_id)
        .where(
            DurableEventRow.session_id.in_(_sessions_in(org_id, window, filters)),  # type: ignore[attr-defined]
            DurableEventRow.type == EventType.SESSION_STARTED.value,
            DurableEventRow.org_id == org_id,
        )
        .distinct()
    ).all()
    return set(rows)


def turns(db: DBSession, org_id: str, window: DateRange, filters: Filters) -> list[TurnFact]:
    """One row per `metric.turn`, each numeric field extracted by the database. Filtered on **both**
    the event's and the session's `org_id` (the M12.5 rule), so a malformed row cannot cross
    tenants. Membership is an `IN` over the population, not a join, so nothing can multiply."""
    data = DurableEventRow.data  # type: ignore[attr-defined]
    extracted = [data["voice_to_voice_ms"].as_integer().label("voice_to_voice")]
    extracted += [data[("ms", stage)].as_integer().label(stage) for stage in STAGES]
    rows = db.exec(
        select(  # type: ignore[call-overload]
            DurableEventRow.session_id, data["status"].as_string().label("status"), *extracted
        ).where(
            DurableEventRow.session_id.in_(_sessions_in(org_id, window, filters)),  # type: ignore[attr-defined]
            DurableEventRow.type == EventType.METRIC_TURN.value,
            DurableEventRow.org_id == org_id,
        )
    ).all()
    return [
        TurnFact(
            session_id=row[0],
            status=row[1],
            ms={
                name: _measured(value) for name, value in zip(LATENCY_METRICS, row[2:], strict=True)
            },
        )
        for row in rows
    ]


def _measured(value: object) -> int | None:
    # The producer emits an int or omits the key (never 0 for "absent", E11-10). A negative interval
    # is dropped at source (ADR-0047 ME6); anything that is not a non-negative int is not a sample.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


@dataclass(frozen=True)
class Snapshot:
    conversations: list[ConversationFact]
    recorded: set[str]
    turns: list[TurnFact]


def load(
    db: DBSession, org_id: str, window: DateRange, filters: Filters, *, with_turns: bool = True
) -> Snapshot:
    """The conversation population, then the turns and recording marks restricted to it. The
    restriction makes the reads mutually consistent even if a session starts between them: a turn
    whose conversation is not in the population is not counted anywhere."""
    facts = conversations(db, org_id, window, filters)
    ids = {f.id for f in facts}
    if not with_turns:
        return Snapshot(facts, set(), [])
    recorded = recorded_sessions(db, org_id, window, filters) & ids
    turn_facts = [t for t in turns(db, org_id, window, filters) if t.session_id in ids]
    return Snapshot(facts, recorded, turn_facts)


# ── arithmetic ────────────────────────────────────────────────────────────────


def percentile(sorted_values: list[int], p: int) -> int | None:
    """Nearest-rank percentile: the smallest observed value with at least `p`% of samples at or
    below it (rank ⌈p/100·n⌉). Always an observed value; `None` without samples."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def _mean(values: list[float] | list[int], digits: int) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


@dataclass
class LatencyStats:
    metric: str
    turns: int
    samples: int
    missing: int
    avg_ms: float | None
    min_ms: int | None
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


def latency_stats(metric: str, facts: Iterable[TurnFact]) -> LatencyStats:
    population = list(facts)
    values = sorted(v for f in population if (v := f.ms[metric]) is not None)
    return LatencyStats(
        metric=metric,
        turns=len(population),
        samples=len(values),
        missing=len(population) - len(values),
        avg_ms=_mean(values, 1),
        min_ms=values[0] if values else None,
        p50_ms=percentile(values, 50),
        p95_ms=percentile(values, 95),
        max_ms=values[-1] if values else None,
    )


@dataclass
class Summary:
    conversations: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    recorded: int
    not_recorded: int
    with_duration: int
    without_duration: int
    total_duration_s: float | None
    avg_duration_s: float | None
    turns: int
    turns_by_status: dict[str, int]
    avg_turns_per_conversation: float | None


def _turn_status(value: str | None) -> str:
    return value if value in TURN_STATUSES else "other"


def summarize(
    facts: list[ConversationFact], recorded: set[str], turn_facts: list[TurnFact]
) -> Summary:
    by_status = dict.fromkeys(CONVERSATION_STATUSES, 0)
    for f in facts:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    durations = [f.duration_s for f in facts if f.duration_s is not None]
    known = [f for f in facts if f.id in recorded]
    turns_by_status = dict.fromkeys((*TURN_STATUSES, "other"), 0)
    for t in turn_facts:
        turns_by_status[_turn_status(t.status)] += 1
    return Summary(
        conversations=len(facts),
        by_status=by_status,
        by_source={src: sum(1 for f in facts if f.source == src) for src in SOURCES},
        recorded=len(known),
        not_recorded=len(facts) - len(known),
        with_duration=len(durations),
        without_duration=len(facts) - len(durations),
        total_duration_s=round(sum(durations), 1) if durations else None,
        avg_duration_s=_mean(durations, 1),
        turns=len(turn_facts),
        turns_by_status=turns_by_status,
        avg_turns_per_conversation=(round(len(turn_facts) / len(known), 2) if known else None),
    )


@dataclass
class Bucket:
    day: date
    conversations: int = 0
    turns: int = 0
    durations: list[float] = field(default_factory=list)
    voice_to_voice: list[int] = field(default_factory=list)


def series(
    window: DateRange, facts: list[ConversationFact], turn_facts: list[TurnFact]
) -> list[Bucket]:
    """One bucket per UTC day in the range, empty days included (a real 0, not a gap)."""
    buckets = {day: Bucket(day) for day in window.days()}
    day_of = {f.id: f.day for f in facts}
    for f in facts:
        buckets[f.day].conversations += 1
        if f.duration_s is not None:
            buckets[f.day].durations.append(f.duration_s)
    for t in turn_facts:
        bucket = buckets[day_of[t.session_id]]
        bucket.turns += 1
        if (v := t.ms["voice_to_voice"]) is not None:
            bucket.voice_to_voice.append(v)
    return [buckets[day] for day in window.days()]


@dataclass
class AgentUsage:
    agent_id: str
    name: str | None
    archived: bool
    conversations: int
    recorded: int
    turns: int
    avg_duration_s: float | None
    avg_turns_per_conversation: float | None
    voice_to_voice: LatencyStats


def by_agent(
    db: DBSession,
    org_id: str,
    facts: list[ConversationFact],
    recorded: set[str],
    turn_facts: list[TurnFact],
) -> list[AgentUsage]:
    """Agents that had conversations in the range, most conversations first. Names and archive state
    are read once for all of them (no per-agent query)."""
    grouped: dict[str, list[ConversationFact]] = defaultdict(list)
    for f in facts:
        grouped[f.agent_id].append(f)
    agent_of = {f.id: f.agent_id for f in facts}
    turns_of: dict[str, list[TurnFact]] = defaultdict(list)
    for t in turn_facts:
        turns_of[agent_of[t.session_id]].append(t)
    ids = list(grouped)
    names = (
        dict(
            db.exec(
                select(Agent.id, Agent.name).where(Agent.org_id == org_id, Agent.id.in_(ids))
            ).all()  # type: ignore[attr-defined]
        )
        if ids
        else {}
    )
    archived = (
        set(
            db.exec(
                select(AgentStateRow.agent_id).where(
                    AgentStateRow.org_id == org_id,
                    AgentStateRow.agent_id.in_(ids),  # type: ignore[attr-defined]
                    AgentStateRow.archived_at.is_not(None),  # type: ignore[union-attr]
                )
            ).all()
        )
        if ids
        else set()
    )
    usage = []
    for agent_id, rows in grouped.items():
        known = [f for f in rows if f.id in recorded]
        durations = [f.duration_s for f in rows if f.duration_s is not None]
        agent_turns = turns_of.get(agent_id, [])
        usage.append(
            AgentUsage(
                agent_id=agent_id,
                name=names.get(agent_id),
                archived=agent_id in archived,
                conversations=len(rows),
                recorded=len(known),
                turns=len(agent_turns),
                avg_duration_s=_mean(durations, 1),
                avg_turns_per_conversation=(
                    round(len(agent_turns) / len(known), 2) if known else None
                ),
                voice_to_voice=latency_stats("voice_to_voice", agent_turns),
            )
        )
    usage.sort(key=lambda u: (-u.conversations, u.name or "", u.agent_id))
    return usage
