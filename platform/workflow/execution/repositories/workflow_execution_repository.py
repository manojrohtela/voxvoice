"""WorkflowExecutionRepository (M6.4B-2) — the platform's one deliberate
durable-repository exception (§7 of the M6.4A design).

Unlike every other closed runtime's temporary repository (which expires by
convention, or via an explicit TTL), workflow execution state must survive a
process restart by definition — an in-flight orchestration cannot simply
vanish. This repository never expires anything on its own; there is no
TTL-sweep method here at all (contrast with `SkillRepository`'s own
convention, which has none either, but for the opposite reason — the
registry, not the repository, is durable there; here, the repository
*itself* is the durable store).

The shipped default adapter (`workflow.stores.InMemoryDocumentStore`) is
still an in-memory dict — genuinely durable persistence requires configuring
a real adapter at deployment (§ `config.py`'s own docstring); this repository
never claims otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from .. import config as execution_config
from ..domain import (
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowReplayRecord,
    WorkflowSnapshot,
)
from ..serializer import WorkflowExecutionSerializer


class WorkflowExecutionRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: WorkflowExecutionSerializer,
        *,
        collections: execution_config.WorkflowExecutionCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or execution_config.WorkflowExecutionCollectionsConfig()

    # ── executions ────────────────────────────────────────────────────────────
    async def save_execution(self, execution: WorkflowExecution) -> None:
        await self._store.upsert(
            self._c.executions, execution.execution_id,
            self._serializer.execution_to_dict(execution),
        )

    async def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        d = await self._store.get(self._c.executions, execution_id)
        return self._serializer.execution_from_dict(d) if d is not None else None

    async def execution_count(self) -> int:
        return len(await self._store.query(self._c.executions, {}))

    # ── checkpoints (append-only, per execution) ─────────────────────────────
    async def add_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        await self._store.upsert(
            self._c.checkpoints, checkpoint.checkpoint_id,
            self._serializer.checkpoint_to_dict(checkpoint),
        )

    async def checkpoints_for(self, execution_id: str) -> Sequence[WorkflowCheckpoint]:
        rows = await self._store.query(self._c.checkpoints, {"execution_id": execution_id})
        checkpoints = [self._serializer.checkpoint_from_dict(r) for r in rows]
        checkpoints.sort(key=lambda c: c.sequence)
        return checkpoints

    # ── snapshots (latest-wins, one per execution) ───────────────────────────
    async def save_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        await self._store.upsert(
            self._c.snapshots, snapshot.execution_id, self._serializer.snapshot_to_dict(snapshot)
        )

    async def get_snapshot(self, execution_id: str) -> WorkflowSnapshot | None:
        d = await self._store.get(self._c.snapshots, execution_id)
        return self._serializer.snapshot_from_dict(d) if d is not None else None

    # ── replays ───────────────────────────────────────────────────────────────
    async def add_replay(self, record: WorkflowReplayRecord) -> None:
        await self._store.upsert(
            self._c.replays, record.replay_id, self._serializer.replay_record_to_document(record)
        )

    async def get_replay(self, replay_id: str) -> WorkflowReplayRecord | None:
        d = await self._store.get(self._c.replays, replay_id)
        return self._serializer.replay_record_from_document(d) if d is not None else None

    async def replay_count(self) -> int:
        return len(await self._store.query(self._c.replays, {}))

    # ── traces ────────────────────────────────────────────────────────────────
    async def add_trace(self, trace_id: str, document: dict) -> None:
        await self._store.upsert(self._c.traces, trace_id, document)

    async def trace_count(self) -> int:
        return len(await self._store.query(self._c.traces, {}))
