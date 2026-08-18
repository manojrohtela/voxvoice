"""Event-spine boundary tests (M10.2) — the executable form of ADR-0034.

The ADR grants exactly one scoped exception: `events/persistence/**` may import
`sqlmodel`/`sqlalchemy`; nothing else in `events/` may, and no other package's budget moves.
A scoped exception is only worth anything if the scope is asserted, so this file checks
**both directions** — permitted *here*, forbidden *one directory up* — following the
ADR-0031 IR1a precedent (`tests/test_billing_foundation.py::test_ir1_billing_imports_...`),
which tightened its boundary suite into per-layer checks rather than relaxing it globally.

These are static AST checks: they read the source, so they catch an import that a runtime
test would only catch if that line happened to execute.
"""

from __future__ import annotations

import ast
import pathlib

_STDLIB = {
    "__future__",
    "abc",
    "collections",
    "dataclasses",
    "enum",
    "hashlib",
    "hmac",
    "json",
    "logging",
    "time",
    "types",
    "typing",
    "uuid",
}

# `httpx` is pre-existing: `events/webhooks.py` has used it since M1 for outbound webhook
# delivery. It is recorded here as an accepted fact of the plane, not introduced by M10.2 —
# and M10.2 changes no webhook delivery semantics.
_EVENTS_ALLOWED = {"core", "events"} | _STDLIB | {"httpx"}

# The one scoped exception (ADR-0034 DS3/DS4).
_STORAGE_LIBS = {"sqlmodel", "sqlalchemy"}


def _imported_tops(path: pathlib.Path) -> set[str]:
    """Top-level module names imported by one source file."""
    tops: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def _files(pkg: str) -> list[pathlib.Path]:
    return sorted(pathlib.Path(pkg).rglob("*.py"))


def _outside_persistence() -> list[pathlib.Path]:
    return [p for p in _files("events") if "persistence" not in p.parts]


def _inside_persistence() -> list[pathlib.Path]:
    return [p for p in _files("events") if "persistence" in p.parts]


# ── the scoped exception: permitted inside persistence ──────────────────────────


def test_events_persistence_may_import_storage_libraries():
    """ADR-0034 DS4. The exception exists and is actually used — if this ever fails
    because nothing imports them, the exception should be withdrawn, not kept 'just in
    case'."""
    files = _inside_persistence()
    assert files, "events/persistence must exist"
    used: set[str] = set()
    for path in files:
        used |= _imported_tops(path)
    assert used & _STORAGE_LIBS, "events/persistence is expected to use sqlmodel/sqlalchemy"


def test_events_persistence_imports_nothing_unexpected():
    allowed = _EVENTS_ALLOWED | _STORAGE_LIBS
    for path in _inside_persistence():
        unexpected = _imported_tops(path) - allowed
        assert not unexpected, f"{path} imports unexpected top-level modules: {sorted(unexpected)}"


# ── the scope: forbidden outside persistence ────────────────────────────────────


def test_events_outside_persistence_never_imports_storage_libraries():
    """The whole point of DS3/DS7. `events/bus.py` in particular must stay a pure
    in-process dispatcher with no notion of storage."""
    offenders = []
    for path in _outside_persistence():
        if _imported_tops(path) & _STORAGE_LIBS:
            offenders.append(str(path))
    assert not offenders, f"only events/persistence may import sqlmodel/sqlalchemy: {offenders}"


def test_events_bus_mentions_no_sql_session_or_table():
    """A per-layer content check beside the import check: the bus should not acquire
    storage vocabulary even through an indirection."""
    source = pathlib.Path("events/bus.py").read_text().lower()
    for token in ("sqlmodel", "sqlalchemy", "create_engine", "__tablename__"):
        assert token not in source, f"events/bus.py mentions {token}"


def test_events_outside_persistence_imports_nothing_unexpected():
    for path in _outside_persistence():
        unexpected = _imported_tops(path) - _EVENTS_ALLOWED
        assert not unexpected, f"{path} imports unexpected top-level modules: {sorted(unexpected)}"


# ── dependency direction (ADR-0034 §9, AR-10/AR-14/AR-15) ───────────────────────


def test_events_never_imports_api_or_another_plane():
    forbidden = {
        "api",
        "billing",
        "product",
        "actuator",
        "runtime",
        "replay",
        "gateway",
        "observability",
        "providers",
    }
    for path in _files("events"):
        leaked = _imported_tops(path) & forbidden
        assert not leaked, f"{path} imports forbidden plane(s): {sorted(leaked)}"


def test_core_still_imports_nothing_above_it():
    """AR-10 — unchanged by M10.2. The storage seam must not have leaked downward."""
    forbidden = {"api", "events", "billing", "runtime", "observability", "sqlmodel", "sqlalchemy"}
    for path in _files("core"):
        leaked = _imported_tops(path) & forbidden
        assert not leaked, f"{path} imports forbidden module(s): {sorted(leaked)}"


def test_observability_budget_did_not_move():
    """ADR-0034 DS7/SV12 — the exception is scoped to events/persistence and must not have
    leaked into the other `core`-only edge package Epic 10 will build next (M10.9)."""
    for path in _files("observability"):
        unexpected = _imported_tops(path) - ({"core", "observability"} | _STDLIB)
        assert not unexpected, f"{path} imports unexpected top-level modules: {sorted(unexpected)}"


def test_events_package_init_does_not_import_persistence():
    """Re-exporting persistence from `events/__init__` would drag sqlmodel into every
    importer of `events` and dissolve the scoping entirely — the exception would be
    nominal. Callers import `events.persistence` explicitly at a composition root."""
    source = pathlib.Path("events/__init__.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "persistence" not in node.module, "events/__init__ must not import persistence"
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            assert node.module != "persistence"
