"""LoopReplayRecord builder (M6.6B-2) — everything needed to
deterministically re-verify one loop's iteration sequence later, without a
live `ReasoningPort`/`LoopValidator` reference. Mirrors `workflow.execution.
replay.workflow_replay_record_for`/`agent.execution.replay.
agent_replay_record_for`'s shape and the same "pure hash-chain, never a
live re-run" discipline, applied a fourth time.
"""

from __future__ import annotations

from ..core.domain import LoopResult
from ..core.ids import content_hash, iteration_hash_for
from .domain import LoopReplayRecord
from .ids import replay_id_for


def loop_replay_record_for(result: LoopResult, *, now: float = 0.0) -> LoopReplayRecord:
    iteration_hashes = tuple(iteration_hash_for(it) for it in result.iterations)
    replay_hash = content_hash(iteration_hashes, result.stop_reason, "loop_replay")
    return LoopReplayRecord(
        replay_id=replay_id_for(result.loop_id, replay_hash),
        loop_id=result.loop_id, iteration_hashes=iteration_hashes,
        stop_reason=result.stop_reason, replay_hash=replay_hash, replay_timestamp=now,
    )
