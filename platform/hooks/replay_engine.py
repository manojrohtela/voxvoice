"""HookReplayEngine (M6.3B) — deterministic replay validation. No adapters, no
live registry re-derivation, no external I/O: validation is a pure
recomputation of the hash chain over the already-stored event/result
documents, comparing it to the hash captured at record time.
"""

from __future__ import annotations

from .domain import HookReplayRecord, ReplayComparison
from .ids import content_hash


class HookReplayEngine:
    def validate(self, record: HookReplayRecord) -> ReplayComparison:
        recomputed = content_hash(record.event_document, record.result_document, "hook_replay")
        mismatches = () if recomputed == record.replay_hash else ("replay_hash",)
        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=recomputed,
            mismatches=mismatches, reason="" if not mismatches else "hook replay hash mismatch",
        )

    def compare(self, a: HookReplayRecord, b: HookReplayRecord) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "hook replay records diverge",
        )
