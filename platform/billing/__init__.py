"""VoxVoice Billing Platform (Epic 9) — the revenue plane.

Placement, dependency direction, composition, import rules, and the public surface are
frozen by **ADR-0031**; ownership is frozen by **ADR-0023**: *Billing owns revenue and
nothing else.* The plane imports `platform/core/` and the standard library only (one
capped read-only `product` exception lands with `billing/refs.py` at M9.20).

Layers (ADR-0031 §8):

* ``billing.foundation`` — pure domain: aggregates, lifecycles, policies, validation,
  diagnostics, events, repository interfaces, stateless services, factories, DTOs.
  **No I/O, no networking, no persistence, no HTTP, no vendor SDK.**
* application / persistence / customer API / Studio — additive *inside the Epic 8
  layers* (M9.3+), which import this plane and are never imported by it.

Only the names re-exported here are public (ADR-0031 §12); every submodule is internal
and refactorable without an ADR (§13).
"""

from __future__ import annotations

from .foundation import *  # noqa: F403  (curated re-export; see foundation.__all__)
from .foundation import __all__ as _foundation_all

__all__ = list(_foundation_all)
