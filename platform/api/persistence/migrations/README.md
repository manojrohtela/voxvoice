# Customer-Platform Persistence Migrations (M8.4)

Structure only — **no application logic**. Each migration is an ordered, idempotent module
exposing `VERSION`, `DESCRIPTION`, `upgrade(database)`, and `downgrade(database)` that
perform declarative schema operations on the `cp_*` Foundation tables.

- `0001` — [`initial.py`](initial.py): create the eight `cp_*` tables.

## Applying

Today the bootstrap is `Database.create_all()` (equivalent to `0001.upgrade`). The ordered
`MIGRATIONS` registry in [`__init__.py`](__init__.py) is append-only — new migrations are
added as `NNNN_<slug>.py` and appended; applied entries are never reordered or edited (the
append-only discipline of the event log).

## Future

A migration runner (e.g. Alembic — not yet a dependency) can adopt this same ordered
structure without reshaping it. Foreign-key constraints and additional indexes, if desired,
land as later migrations rather than by editing `0001`.
