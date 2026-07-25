"""LoopReplayRecord (M6.6B-2) — per the M6.6A design §4.6. Everything
needed to deterministically re-verify one loop invocation's iteration
sequence later, without a live `ReasoningPort`/`LoopValidator` reference.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoopReplayRecord:
    replay_id: str
    loop_id: str
    iteration_hashes: tuple[str, ...]  # one content-hash per `LoopIteration`, ordered
    stop_reason: str
    replay_hash: str
    replay_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""
