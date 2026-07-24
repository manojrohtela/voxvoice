"""ReplayEngine (M6.1B-3) — deterministic replay validation. No adapters other
than `EchoToolAdapter` (never a live registry/vendor SDK — see the module
docstring in `stores/echo_adapter.py`); no external I/O.
"""

from __future__ import annotations

from ..ids import content_hash, result_hash_for
from ..serializer import ToolSerializer
from ..stores import EchoToolAdapter
from .domain import ReplayComparison, ReplayRecord


class ReplayEngine:
    """Re-executes a replayed request against the deterministic `EchoToolAdapter`
    and compares the recomputed hash chain to the one captured at record time."""

    def __init__(self, *, serializer: ToolSerializer | None = None) -> None:
        self._serializer = serializer or ToolSerializer()
        self._adapter = EchoToolAdapter()

    def validate(self, record: ReplayRecord) -> ReplayComparison:
        request = self._serializer.request_from_dict(record.request_document)
        outcome = self._adapter.execute(request)
        output_hash = result_hash_for(request.request_hash, outcome.output)
        replay_hash = content_hash(request.request_hash, output_hash, "completed")

        mismatches: list[str] = []
        if output_hash != record.execution_trace.output_hash:
            mismatches.append("output_hash")
        if replay_hash != record.replay_hash:
            mismatches.append("replay_hash")

        return ReplayComparison(
            matches=not mismatches, expected_hash=record.replay_hash, actual_hash=replay_hash,
            mismatches=tuple(mismatches),
            reason="" if not mismatches else f"mismatch in {', '.join(mismatches)}",
        )

    def compare(self, a: ReplayRecord, b: ReplayRecord) -> ReplayComparison:
        """Deterministic equivalence between two recorded replays (e.g. the same
        request replayed twice) — no re-execution, a pure hash comparison."""
        matches = a.replay_hash == b.replay_hash
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=() if matches else ("replay_hash",),
            reason="" if matches else "replay records diverge",
        )
