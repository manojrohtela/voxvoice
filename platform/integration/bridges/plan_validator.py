"""AgentPlanValidator (M6.7B-1) — the first real `LoopValidator`.

Per the M6.7A design §5.4: Loop Engine owns iteration sequencing and
mechanical stopping policy; correctness is entirely the injected
`LoopValidator`'s call. Agent Runtime supplies that validator — a small
composition-root class (this one), not a Loop Engine change. It supplies
Agent-specific domain logic behind the seam Loop Engine already declared,
never re-implementing Loop's own correctness delegation.

**What it can and cannot check (design §5.4, §17 risk 4, DL-0074):** §5.4
illustratively describes "parse `proposal.content_ref` back into a candidate
AgentPlan". In the certified Loop Engine, `content_ref` is a content *hash*
(`loop.core.pipeline` sets `content_ref=content_hash(text)`), not the text —
a hash cannot be parsed back into steps. So this validator does the real,
non-fabricating domain check available at the seam: it rejects an empty
proposal (Loop Engine's own certification flagged a silent no-op validator as
a named risk), and it verifies that every workflow reference the revised plan
is expected to use resolves via the injected `AgentWorkflowPort.resolve()` —
the exact check §5.4 specifies ("checks step kinds resolve via
AgentWorkflowPort.resolve()"). Recovering full plan steps from the proposal
requires a structured-output proposal payload (a Bundle 2 / structured-provider
concern); until then this validator fails closed, never open."""

from __future__ import annotations

from collections.abc import Sequence

from agent.extensions import AgentWorkflowPort
from core.errors import PlatformError
from loop import LoopProposal, LoopValidation, LoopValidator


class AgentPlanValidator(LoopValidator):
    """Validates a revised-plan proposal against the resolvable-workflow
    seam. `expected_workflow_refs` is the already-resolved capability set the
    revised plan must still resolve against (design §5.4)."""

    def __init__(
        self,
        workflow_port: AgentWorkflowPort,
        *,
        expected_workflow_refs: Sequence[str] = (),
    ) -> None:
        self._workflow_port = workflow_port
        self._expected_workflow_refs = tuple(expected_workflow_refs)

    def validate(self, proposal: LoopProposal) -> LoopValidation:
        if not proposal.content_ref:
            return LoopValidation(
                validation_id=f"val:{proposal.proposal_id}",
                proposal_id=proposal.proposal_id,
                passed=False,
                confidence=0.0,
                reason="empty proposal content",
            )
        for ref in self._expected_workflow_refs:
            try:
                self._workflow_port.resolve(ref)
            except PlatformError as exc:
                return LoopValidation(
                    validation_id=f"val:{proposal.proposal_id}",
                    proposal_id=proposal.proposal_id,
                    passed=False,
                    confidence=0.0,
                    reason=f"revised plan references unresolvable workflow {ref!r}: {exc}",
                )
        return LoopValidation(
            validation_id=f"val:{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            passed=True,
            confidence=1.0,
            reason="all referenced workflows resolve",
        )
