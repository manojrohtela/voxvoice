"""AgentReplayRecord builder (M6.5B-3) — everything needed to
deterministically re-verify one goal's decision chain later, without a live
`ReasoningPort`/`AgentWorkflowPort` reference. Mirrors `workflow.execution.
replay.workflow_replay_record_for`'s shape and the same "pure hash-chain,
never a live re-run" discipline.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import AgentDecision
from ..ids import content_hash
from .domain import AgentGoalResult, AgentReplayRecord
from .ids import replay_id_for


def agent_replay_record_for(
    decisions: Sequence[AgentDecision], result: AgentGoalResult, *, now: float = 0.0,
) -> AgentReplayRecord:
    decision_hashes = tuple(d.payload_hash for d in decisions)
    final_state = result.final_state.value
    replay_hash = content_hash(decision_hashes, final_state, "agent_replay")
    return AgentReplayRecord(
        replay_id=replay_id_for(result.goal_id, replay_hash),
        goal_id=result.goal_id, decision_hashes=decision_hashes, final_state=final_state,
        replay_hash=replay_hash, replay_timestamp=now,
    )
