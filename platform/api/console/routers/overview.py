"""`/v1/console/overview` — the organization at a glance (M12.8, LLD §8a).

Parameters: `range=today|7d|30d` or `from/to` (exactly as Analytics) and `agent_id`.

A **composition**, not a source: it computes nothing of its own. One request resolves the same
`Scope` the analytics endpoints use (range, agent filter, organization from the console session,
404 for a foreign agent), loads **one** analytics snapshot, and hands it to the very functions
that build `/v1/console/analytics/*` — so every figure here is, by construction, the figure
Analytics shows for the same query. Beside it: the agent inventory from the M12.3 agents service
and the latest conversations from the M12.5 list. One round trip instead of six, and one snapshot
instead of four, so the panels can never describe different moments.

Read-only, `viewer` or higher, console session only (not part of the developer API).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..agents import service as agents
from ..conversations import service as conversations
from ..errors import InvalidRequest
from .analytics import (
    AgentUsageView,
    BucketView,
    ConversationCounts,
    DurationFigures,
    LatencyView,
    Query_,
    Scope,
    TurnFigures,
    agent_usage_views,
    bucket_views,
    latency_views,
    summary_figures,
)
from .conversations import ConversationSummary, summary_view

router = APIRouter(prefix="/v1/console/overview", tags=["console"])

#: Most recent conversations shown. Read with the M12.5 list's own limit, never more.
RECENT_LIMIT = 5
#: Agents shown in "agent activity" (most conversations first); the rest are counted.
ACTIVITY_LIMIT = 5


class AgentInventory(BaseModel):
    """The organization's agents **now**, regardless of the range and the agent filter."""

    active: int
    archived: int
    #: Active agents whose language the recognizer does not declare (their calls cannot work).
    unsupported_language: int


class Attention(BaseModel):
    """A deterministic, actionable condition read from the data above. No thresholds, no scores."""

    code: str
    message: str


class OverviewView(BaseModel):
    query: Query_
    agents: AgentInventory
    conversations: ConversationCounts
    duration: DurationFigures
    turns: TurnFigures
    #: Daily buckets, exactly `/analytics/timeseries`.
    usage: list[BucketView]
    #: Every latency measure, exactly `/analytics/latency`.
    latency: list[LatencyView]
    #: The first `ACTIVITY_LIMIT` entries of `/analytics/agents`.
    agent_activity: list[AgentUsageView]
    agent_activity_more: int
    #: The latest conversations **of any date** (agent filter applied), from the M12.5 list.
    recent_conversations: list[ConversationSummary]
    attention: list[Attention]


def _attention(inventory: agents.Inventory, conversation_total: int) -> list[Attention]:
    items = []
    if inventory.active == 0:
        items.append(Attention(code="no_agents", message="No agents yet. Create one to start."))
    if inventory.unsupported:
        items.append(
            Attention(
                code="unsupported_language",
                message=(
                    f"{inventory.unsupported} agent(s) use a language the speech "
                    "recognizer does not support, so their calls cannot work."
                ),
            )
        )
    if inventory.active and conversation_total == 0:
        items.append(
            Attention(
                code="no_conversations",
                message="No conversations in this range. Try the Playground.",
            )
        )
    return items


@router.get("", response_model=OverviewView)
def overview(request: Request, scope: Scope = Depends()) -> OverviewView:
    if scope.filters.source is not None:
        # The recent-conversations panel could not honour it; no panel may silently differ.
        raise InvalidRequest("source is an Analytics filter, not an Overview one")
    snap = scope.load()
    counts, duration, turns = summary_figures(snap)
    activity = agent_usage_views(scope.db, scope.org_id, snap)
    inventory = agents.inventory(scope.db, scope.org_id, request.app.state.console_agent_options)
    recent, _ = conversations.list_conversations(
        scope.db, scope.org_id, agent_id=scope.filters.agent_id, limit=RECENT_LIMIT
    )
    return OverviewView(
        query=scope.query,
        agents=AgentInventory(
            active=inventory.active,
            archived=inventory.archived,
            unsupported_language=inventory.unsupported,
        ),
        conversations=counts,
        duration=duration,
        turns=turns,
        usage=bucket_views(scope.window, snap),
        latency=latency_views(snap.turns),
        agent_activity=activity[:ACTIVITY_LIMIT],
        agent_activity_more=max(0, len(activity) - ACTIVITY_LIMIT),
        recent_conversations=[
            summary_view(r.session, r.agent_name, r.message_count) for r in recent
        ],
        attention=_attention(inventory, counts.total),
    )
