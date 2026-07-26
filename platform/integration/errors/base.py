"""IntegrationError (M6.7B-1) — the one new boundary error type.

Per the M6.7A design §12: Agent Platform Integration adds **exactly one** new
error, raised only at a composition-root boundary itself (e.g. a required
capability instance was never wired into the composition root). It never
wraps or re-raises a capability's own typed error — those subclass
`core.errors.PlatformError` already and propagate verbatim to the caller.
"""

from __future__ import annotations

from core.errors import PlatformError


class IntegrationError(PlatformError):
    """Raised only at a composition-root boundary itself — never a wrapper
    around another capability's own typed error (design §12)."""
