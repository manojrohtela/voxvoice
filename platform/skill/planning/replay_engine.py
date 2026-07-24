"""PlanReplayEngine (M6.2B-2) — deterministic planning-replay validation. No
adapters, no live registry re-derivation, no external I/O: validation is a
pure recomputation of the hash chain over the already-stored plan/context
documents, comparing it to the hash captured at record time (a tamper/
determinism check, exactly the boundary "Replay owns only planning replay"
draws — never a re-plan against live registry state).
"""

from __future__ import annotations

from ..ids import content_hash
from .domain import ReplayComparison, SkillPlanReplayRecord


class PlanReplayEngine:
    def validate(self, record: SkillPlanReplayRecord) -> ReplayComparison:
        recomputed = content_hash(
            record.plan_document, record.context_document, "planning_replay",
        )
        mismatches = () if recomputed == record.replay_hash else ("replay_hash",)
        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=recomputed,
            mismatches=mismatches,
            reason="" if not mismatches else "planning replay hash mismatch",
        )

    def compare(
        self, a: SkillPlanReplayRecord, b: SkillPlanReplayRecord
    ) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "planning replay records diverge",
        )
