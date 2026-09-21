"""Developer-platform migrations (Epic 12) — declarative schema only.

Continues the platform's single global chain, in the established form (events `0034`–`0036`,
ADR-0034 DS8): ordered, idempotent, create-if-absent modules with no migration framework. The
developer platform begins at **`0037`**. No earlier migration is read, altered or renumbered.
"""

from __future__ import annotations

from . import v0037_identity

MIGRATIONS = (v0037_identity,)

#: The chain this one continues (`events/persistence/migrations` 0034–0036).
CHAIN_STARTS_AFTER = "0036"


def upgrade_all(engine) -> tuple[str, ...]:
    """Apply every developer-platform migration in order. Returns the versions applied."""
    for module in MIGRATIONS:
        module.upgrade(engine)
    return tuple(module.VERSION for module in MIGRATIONS)
