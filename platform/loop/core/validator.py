"""LoopValidator — the one seam Loop Engine delegates correctness to
(M6.6B-1). Interface only, never implemented by Loop Engine itself (design
§8.4). A caller injects a domain-specific implementation; Loop Engine only
ever reads the returned `LoopValidation.passed`/`confidence`/`reason`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import LoopProposal, LoopValidation


class LoopValidator(ABC):
    @abstractmethod
    def validate(self, proposal: LoopProposal) -> LoopValidation:
        """Judge whether `proposal` is correct. Entirely the caller's own
        domain logic -- Loop Engine never evaluates correctness itself."""
