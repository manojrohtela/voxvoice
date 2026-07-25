"""AgentReplayEngine (M6.5B-3) — deterministic replay validation. No
adapters, no live re-plan: validation is a pure recomputation of the hash
chain over the already-stored decision-hash sequence, compared to the hash
captured at record time.
"""

from __future__ import annotations

from ..ids import content_hash
from .domain import AgentReplayRecord, ReplayComparison


class AgentReplayEngine:
    def validate(self, record: AgentReplayRecord) -> ReplayComparison:
        recomputed = content_hash(record.decision_hashes, record.final_state, "agent_replay")
        mismatches = () if recomputed == record.replay_hash else ("replay_hash",)
        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=recomputed,
            mismatches=mismatches, reason="" if not mismatches else "agent replay hash mismatch",
        )

    def compare(self, a: AgentReplayRecord, b: AgentReplayRecord) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "agent replay records diverge",
        )
