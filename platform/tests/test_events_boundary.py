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


# ── M10.6 (ADR-0035 DW9): the durable seam did not move the bus's budget ────────


def test_events_bus_imports_only_the_framework_neutral_port():
    """ADR-0035 DW2/DW9. The seam is typed by `events/ports.py`, so `bus.py` may reference
    `DurableEventLog` — and must still import no storage library and no persistence module.
    A seam that dragged `events.persistence` into `bus.py` would put sqlmodel on the publish
    path of every process that imports the bus."""
    tops = _imported_tops(pathlib.Path("events/bus.py"))
    assert not (tops & _STORAGE_LIBS), f"events/bus.py imports storage libraries: {tops}"
    assert tops <= _EVENTS_ALLOWED, f"events/bus.py imports unexpected modules: {sorted(tops)}"


def test_events_bus_never_imports_the_persistence_package():
    source = pathlib.Path("events/bus.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "persistence" not in node.module, "events/bus.py must not import persistence"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "persistence" not in alias.name


def test_events_ports_stays_framework_neutral_after_the_outbox_port():
    """`DurableOutbox`, `OutboxState` and `OutboxEntry` (M10.6) are `core` + stdlib only.
    The port is the seam; the moment it needs a storage type, the inversion is gone."""
    tops = _imported_tops(pathlib.Path("events/ports.py"))
    assert not (tops & _STORAGE_LIBS)
    assert tops <= ({"core", "events"} | _STDLIB), f"events/ports.py: {sorted(tops)}"


def test_runtime_acquired_no_database_or_transaction():
    """ADR-0035 DW1/DW10, owner ruling D-1/D-6. The transaction model rests on runtime
    producers having no transaction, and M10.6 must not have quietly given them one — nor
    wired the durable sink into the runtime."""
    forbidden_imports = {"sqlmodel", "sqlalchemy"}
    forbidden_tokens = ("EventDatabase", "SqlDurableEventSpine", "SqlDurableEventLog",
                        "UnitOfWork", "create_engine", "__tablename__")
    for path in _files("runtime"):
        assert not (_imported_tops(path) & forbidden_imports), f"{path} imports storage"
        source = path.read_text()
        for token in forbidden_tokens:
            assert token not in source, f"{path} mentions {token} — runtime must stay in-memory"


def test_the_orchestrator_is_not_wired_to_a_durable_sink():
    """DW10 — M10.6 proves the capability; it does not make the production runtime durable."""
    source = pathlib.Path("runtime/orchestrator/orchestrator.py").read_text()
    assert "InProcessEventBus()" in source, "the orchestrator still constructs a plain bus"
    assert "durable" not in source


# ── M10.12 hardening (ADR-0042) ─────────────────────────────────────────────────
#
# Additive only (HD14). Nothing above is weakened, narrowed or removed; these guards pin
# properties that were previously proven only behaviourally, so a regression fails statically
# instead of waiting for a production incident.


def _calls_in(path: pathlib.Path, attr: str) -> list[str]:
    """Names of functions/methods in `path` that call `.<attr>(...)` anywhere in their body."""
    tree = ast.parse(path.read_text())
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == attr
            ):
                hits.append(node.name)
                break
    return hits


def test_the_latency_tap_publishes_only_from_its_terminal_method():
    """ADR-0038 TP10, made **structural** (ADR-0042 HD1).

    The existing M10.9 test proves *behaviourally* that one turn yields one `metric.turn`. This
    pins the reason: publication happens from the terminal path only. A publish added to
    `record()` would run unbounded subscriber work — and, once a durable sink is attached, a
    synchronous database write — inside a ≤ 1.0 s voice-to-voice budget, which is Blueprint risk
    **R-9** realised. That regression now fails here rather than in production.
    """
    publishers = set(_calls_in(pathlib.Path("observability/turn_latency.py"), "publish"))
    assert publishers <= {"complete"}, (
        f"metric.turn is published from {sorted(publishers)}; TP10 permits the terminal path only"
    )
    assert "record" not in publishers, "a boundary capture must never publish (TP10)"


def test_the_latency_tap_takes_no_blocking_or_scheduling_dependency():
    """HD1/HV7 — the tap stays a clock read and a dict write."""
    tops = _imported_tops(pathlib.Path("observability/turn_latency.py"))
    assert not (tops & {"threading", "sched", "concurrent", "queue"}), sorted(tops)
    assert not (tops & _STORAGE_LIBS), "the tap must acquire no storage dependency"


def test_the_outbox_operator_counts_are_declared_on_the_port():
    """ADR-0042 HD7/HD10 — the two counts live on `DurableOutbox`, so no caller reaches
    `evt_outbox` around the seam."""
    from events.ports import DurableOutbox

    assert {"count_dead_lettered", "count_quarantined"} <= set(DurableOutbox.__abstractmethods__)


def test_no_module_outside_persistence_uses_the_outbox_row_model():
    """HD10 — a reader that queried `evt_outbox` directly would become a second implementation of
    a ratified contract and dissolve the ADR-0034 storage seam.

    Structural, not textual: `ports.py`, `retention.py`, `recovery.py` and `events/__init__.py`
    all *name* `evt_outbox` in their prose — correctly, since they document what the port
    abstracts — and a substring scan would flag exactly the modules that are behaving. Reaching
    the table means using its **row model**, so that is what is checked.
    """
    offenders = []
    for package in ("events", "api", "runtime", "observability", "core"):
        for path in _files(package):
            if "persistence" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Name) and node.id == "DurableOutboxRow":
                    offenders.append(str(path))
                    break
                if isinstance(node, ast.ImportFrom) and node.names:
                    if any(a.name == "DurableOutboxRow" for a in node.names):
                        offenders.append(str(path))
                        break
    assert not offenders, f"the outbox row model is used outside persistence: {offenders}"


def test_the_operator_counts_introduce_no_control_capability():
    """HD9 / HV5 — ADR-0036 RL3/RL4 remain in force. Counting is observability; the deny-list
    guard in `test_events_outbox.py` still owns the method-name rule, and this asserts the
    complementary property: nothing M10.12 added can move an entry."""
    from events.ports import OUTBOX_LIFECYCLE, TERMINAL_OUTBOX_STATES, DurableOutbox

    surface = {n for n in dir(DurableOutbox) if not n.startswith("_")}
    control = {"redrive", "release", "requeue", "unquarantine", "reopen", "purge"}
    assert not (surface & control), f"operator-control leaked onto the port: {surface & control}"

    for state in TERMINAL_OUTBOX_STATES:
        assert not OUTBOX_LIFECYCLE.triggers_from(state), (
            f"{state} gained an outgoing edge; terminal states must stay terminal (RL3/RL4)"
        )


def test_m10_12_added_no_migration():
    """HD11 / HV10 — the counts read the existing indexed `state` column."""
    from events.persistence.migrations import MIGRATIONS

    assert tuple(m.VERSION for m in MIGRATIONS) == ("0034", "0035", "0036")
