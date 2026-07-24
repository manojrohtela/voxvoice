"""WorkflowReplayEngine (M6.4B-2) — deterministic replay validation. No
adapters, no live re-execution: validation is a pure recomputation of the
hash chain over the already-stored checkpoint/result documents, compared to
the hash captured at record time.
"""

from __future__ import annotations

from ..ids import content_hash
from .domain import ReplayComparison, WorkflowReplayRecord


class WorkflowReplayEngine:
    def validate(self, record: WorkflowReplayRecord) -> ReplayComparison:
        recomputed = content_hash(
            record.checkpoints_document, record.result_document, "workflow_replay",
        )
        mismatches = () if recomputed == record.replay_hash else ("replay_hash",)
        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=recomputed,
            mismatches=mismatches, reason="" if not mismatches else "workflow replay hash mismatch",
        )

    def compare(self, a: WorkflowReplayRecord, b: WorkflowReplayRecord) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "workflow replay records diverge",
        )
