"""Deterministic checkpoint hashing (M6.4B-2) — checkpoint-after-every-
completed-node, never wall-clock-sampled (§6.2 of the M6.4A design).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..ids import content_hash
from .domain import WorkflowCheckpoint
from .ids import checkpoint_id_for


def compute_variables_hash(variables: Mapping[str, Any]) -> str:
    return content_hash(sorted(variables.items()))


def compute_checkpoint_hash(
    execution_id: str, node_id: str, sequence: int, variables_hash: str,
) -> str:
    return content_hash(execution_id, node_id, sequence, variables_hash)


def build_checkpoint(
    execution_id: str, node_id: str, sequence: int, variables: Mapping[str, Any],
    *, now: float = 0.0,
) -> WorkflowCheckpoint:
    variables_hash = compute_variables_hash(variables)
    checkpoint_hash = compute_checkpoint_hash(execution_id, node_id, sequence, variables_hash)
    return WorkflowCheckpoint(
        checkpoint_id=checkpoint_id_for(execution_id, node_id, sequence),
        execution_id=execution_id, node_id=node_id, sequence=sequence,
        variables_hash=variables_hash, checkpoint_hash=checkpoint_hash, created_at=now,
    )
