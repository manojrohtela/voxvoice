"""WorkflowReplayRecord builder (M6.4B-2) — everything needed to
deterministically re-verify one execution's checkpoint chain later, without
a live registry/port reference. Mirrors `hooks.replay.hook_replay_record_for`'s
shape and the same "pure hash-chain, never a live re-run" discipline.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..ids import content_hash
from .domain import WorkflowCheckpoint, WorkflowExecutionResult, WorkflowReplayRecord
from .ids import replay_id_for
from .serializer import WorkflowExecutionSerializer


def workflow_replay_record_for(
    checkpoints: Sequence[WorkflowCheckpoint],
    result: WorkflowExecutionResult,
    *,
    now: float = 0.0,
    serializer: WorkflowExecutionSerializer | None = None,
) -> WorkflowReplayRecord:
    s = serializer or WorkflowExecutionSerializer()
    checkpoints_document = tuple(s.checkpoint_to_dict(c) for c in checkpoints)
    result_document = s.result_to_dict(result)
    result_hash = content_hash(result_document)
    replay_hash = content_hash(checkpoints_document, result_document, "workflow_replay")
    return WorkflowReplayRecord(
        replay_id=replay_id_for(result.execution_id, result_hash),
        checkpoints_document=checkpoints_document, result_document=result_document,
        replay_hash=replay_hash, replay_timestamp=now,
    )
