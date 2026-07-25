"""LoopReplayEngine (M6.6B-2) — deterministic replay validation. No
adapters, no live re-run: validation is a pure recomputation of the hash
chain over the already-stored iteration-hash sequence, compared to the hash
captured at record time. Mirrors `agent.execution.replay_engine.
AgentReplayEngine`'s shape exactly, applied a fourth time.
"""

from __future__ import annotations

from ..core.ids import content_hash
from .domain import LoopReplayRecord, ReplayComparison


class LoopReplayEngine:
    def validate(self, record: LoopReplayRecord) -> ReplayComparison:
        recomputed = content_hash(record.iteration_hashes, record.stop_reason, "loop_replay")
        mismatches = () if recomputed == record.replay_hash else ("replay_hash",)
        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=recomputed,
            mismatches=mismatches, reason="" if not mismatches else "loop replay hash mismatch",
        )

    def compare(self, a: LoopReplayRecord, b: LoopReplayRecord) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "loop replay records diverge",
        )
