"""integration.config — configuration shapes for the composition root.

Two shapes, per the M6.7A design's ownership matrix (§3 "Configuration flow",
§3.2):

- `IntegrationConfiguration` — the only *tunable knobs* this layer owns
  (whether plan revision is on, budgets forwarded verbatim to a capability's
  own field, the deterministic trace-id factory). It never holds a
  per-capability tuning knob a capability's own `*Configuration` already owns.
- `PlatformConfiguration` — the *set of already-built runtime instances* plus
  the `IntegrationConfiguration`, validated as a whole so a missing capability
  wire is caught at composition time (raising `IntegrationError`), never at
  first use.
"""

from .configuration import IntegrationConfiguration, PlatformConfiguration

__all__ = ["IntegrationConfiguration", "PlatformConfiguration"]
