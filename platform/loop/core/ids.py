"""Deterministic, content-hash identities (M6.6B-1).

A local copy of the `content_hash`/id-suffix pattern every closed runtime
uses (`tool/ids.py`, `agent/ids.py`, ...) — duplicated, not imported. Loop
Engine never imports Tool Runtime, Skill Runtime, Hook Framework, Workflow
Runtime, or Agent Runtime (the explicit M6.6A/M6.6B-1 dependency rule).
"""

from __future__ import annotations

import hashlib


def content_hash(*parts: object) -> str:
    """A stable 16-hex-char digest over the given parts (order-sensitive)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")  # unambiguous field separator
    return h.hexdigest()[:16]


def proposal_id_for(loop_id: str, iteration_number: int, created_at: float) -> str:
    return "lp_" + content_hash(loop_id, iteration_number, created_at, "proposal")


def critique_id_for(proposal_id: str, created_at: float) -> str:
    return "lc_" + content_hash(proposal_id, created_at, "critique")


def repair_id_for(critique_id: str, created_at: float) -> str:
    return "lr_" + content_hash(critique_id, created_at, "repair")


def validation_id_for(proposal_id: str, created_at: float) -> str:
    return "lv_" + content_hash(proposal_id, created_at, "validation")


def iteration_hash_for(iteration: object) -> str:
    """Deterministic content-hash over one `LoopIteration`'s own identifying
    fields — used both for convergence checks and, in a later bundle, for
    `LoopReplayRecord.iteration_hashes`. Reads plain attributes only, so it
    has no import-time dependency on `.domain.LoopIteration` itself."""
    proposal = iteration.proposal
    critique = iteration.critique
    repair = iteration.repair
    validation = iteration.validation
    return content_hash(
        iteration.iteration_number,
        proposal.proposal_id,
        proposal.content_ref,
        critique.critique_id if critique else "",
        critique.content_ref if critique else "",
        repair.repair_id if repair else "",
        repair.content_ref if repair else "",
        validation.validation_id,
        validation.passed,
        validation.confidence,
        iteration.stopped,
        iteration.stop_reason,
    )
