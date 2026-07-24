"""HookReplayRecord builder (M6.3B) — everything needed to deterministically
re-verify one hook run later, without a live registry/hook reference.
"""

from __future__ import annotations

from .domain import HookEvent, HookReplayRecord, HookRunResult
from .ids import content_hash, replay_id_for
from .serializer import HookSerializer


def hook_replay_record_for(
    event: HookEvent,
    result: HookRunResult,
    *,
    now: float = 0.0,
    serializer: HookSerializer | None = None,
) -> HookReplayRecord:
    s = serializer or HookSerializer()
    event_document = s.event_to_dict(event)
    result_document = s.run_result_to_dict(result)
    result_hash = content_hash(result_document)
    replay_hash = content_hash(event_document, result_document, "hook_replay")
    return HookReplayRecord(
        replay_id=replay_id_for(event.event_id, result_hash),
        event_document=event_document, result_document=result_document,
        replay_hash=replay_hash, replay_timestamp=now,
    )
