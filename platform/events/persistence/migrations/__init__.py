"""Event-spine persistence migrations (M10.2) — declarative schema only.

This package **continues the platform's single migration chain**, it does not start a third
one: the Customer Platform's chain runs `0001`–`0012` (`api/persistence/migrations`),
Billing's runs `0013`–`0033` (`billing/persistence/migrations`), so the events plane begins
at **`0034`**. One global sequence, ordered and contiguous, is what keeps "which schema
version is this database at?" a question with one answer — the reason Billing gave for not
starting a second chain, applied unchanged (ADR-0034 DS8).

Each module is idempotent (create-if-absent), touches only `evt_*` tables, and contains
**no application logic**. No Epic 5–9 table is read, altered, or reused, and no migration at
or before `0033` is modified, renumbered, or backfilled.

A migration tool (e.g. Alembic — still not a dependency) can adopt this same ordered
structure later without reshaping it.
"""

from __future__ import annotations

from . import v0034_durable_event_log, v0035_durable_outbox

# Ordered migration registry. Append future migrations; never reorder or mutate applied
# entries (append-only, like the event log itself). `0035` (M10.6) was appended without
# touching `0034`, which keeps an applied migration's scope fixed after the fact.
MIGRATIONS = (v0034_durable_event_log, v0035_durable_outbox)

# The chain this one continues (`billing/persistence/migrations` 0013–0033), asserted by
# the migration-chain test rather than assumed — the M9.4 idiom.
CHAIN_STARTS_AFTER = "0033"


def upgrade_all(database) -> tuple[str, ...]:
    """Apply every event-spine migration in order. Returns the versions applied."""
    for module in MIGRATIONS:
        module.upgrade(database)
    return tuple(module.VERSION for module in MIGRATIONS)
