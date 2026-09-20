"""The `metric.*` event vocabulary (M10.8a) — ADR-0033 MS1, AR-32, AR-42.

**Vocabulary only.** M10.8a adds enum members and nothing else: no producer, no taps, no
buffer, no persistence. `platform/observability/` is still a skeleton and stays that way until
M10.9, so these tests prove the *names and values* exist and that **nothing else moved** —
which, for an additive extension to a frozen shared enum, is the whole risk.

The interesting assertions here are the negative ones. `core.events.EventType` is imported by
every plane in the repository, including frozen Billing, so a rename or a changed value would be
a silent breaking change that no type checker would catch (`StrEnum` members compare equal to
their strings). AR-32's *"never by silent mutation"* is what these tests make executable.
"""

from __future__ import annotations

import ast
import pathlib

from core.events import EventSource, EventType

# The `metric.*` vocabulary ratified for Epic 10, exhaustively. Only `metric.turn` is named by
# any ratified document (ADR-0033 MS1, plan §M10.8a, AR-42); `metric.billing.*` is reserved to
# Billing and deliberately absent. Written as an exact set, not a subset check, so an
# unauthorized addition fails here rather than reaching Billing through the seam.
RATIFIED_METRIC_VOCABULARY = {"metric.turn"}


# ── 1-3. the member exists, with the exact name and value ───────────────────


def test_the_metric_turn_member_exists():
    assert hasattr(EventType, "METRIC_TURN")


def test_metric_turn_has_its_exact_ratified_value():
    """AR-42 names `metric.turn`; the value is the wire contract, not a detail."""
    assert EventType.METRIC_TURN.value == "metric.turn"
    assert EventType.METRIC_TURN == "metric.turn"  # StrEnum: equals its own string


def test_metric_turn_is_reachable_by_value_lookup():
    """A decoder receiving the wire string must resolve it — the M10.10 buffer will."""
    assert EventType("metric.turn") is EventType.METRIC_TURN


# ── 4. values are unique ────────────────────────────────────────────────────


def test_every_event_type_value_is_unique():
    values = [m.value for m in EventType]
    assert len(values) == len(set(values)), "duplicate EventType value"


def test_every_event_type_name_is_unique():
    names = [m.name for m in EventType]
    assert len(names) == len(set(names))


# ── 5. no existing member changed ───────────────────────────────────────────


def test_the_pre_m108a_vocabulary_is_unchanged():
    """The load-bearing assertion. Every member present before M10.8a, by exact name and value.

    Pinned as a literal rather than derived, because a derived expectation would move with the
    code it is supposed to be guarding.
    """
    expected = {
        "RUNTIME_STARTED": "runtime.started",
        "RUNTIME_STOPPED": "runtime.stopped",
        "RUNTIME_ERROR": "runtime.error",
        "SESSION_STARTED": "session.started",
        "SESSION_ENDED": "session.ended",
        "TURN_STARTED": "turn.started",
        "TURN_COMPLETED": "turn.completed",
        "TURN_CANCELLED": "turn.cancelled",
        "SPEECH_STARTED": "speech.started",
        "SPEECH_STOPPED": "speech.stopped",
        "ENDPOINT_DETECTED": "endpoint.detected",
        "ENDPOINT_CANCELLED": "endpoint.cancelled",
        "INTERRUPT_DETECTED": "interrupt.detected",
        "INTERRUPT_CONFIRMED": "interrupt.confirmed",
        "INTERRUPT_CANCELLED": "interrupt.cancelled",
        "ASSISTANT_STARTED_SPEAKING": "assistant.started_speaking",
        "ASSISTANT_STOPPED_SPEAKING": "assistant.stopped_speaking",
        "SMART_TURN_DECISION": "smart_turn.decision",
        "PROVIDER_CONNECTED": "provider.connected",
        "PROVIDER_DISCONNECTED": "provider.disconnected",
        "COST_UPDATED": "cost.updated",
        "TOOL_METRICS_UPDATED": "tool.metrics.updated",
    }
    for name, value in expected.items():
        assert hasattr(EventType, name), f"{name} disappeared"
        assert getattr(EventType, name).value == value, f"{name} changed value"


def test_m108a_added_exactly_one_metric_member():
    """M10.8a is a **single** additive extension: it introduced the `metric.*` vocabulary and
    exactly one member in it.

    Stated as *"exactly one `metric.*` member"* rather than *"the enum totals 104"* (M11.5). The
    total was never M10.8a's claim — it is a measurement of the whole repository, so any later
    epic's legitimate additive event broke it while M10.8a's own scope was untouched. This is the
    same live-count shape ADR-0052 Amendment 1 **HC9** removed from the certification controls,
    and the sharper form here actually tests what the name says: the guard above already proves no
    baseline member was removed or altered."""
    metric_members = sorted(m.name for m in EventType if m.value.startswith("metric."))
    assert metric_members == ["METRIC_TURN"]
    assert EventType.METRIC_TURN.value == "metric.turn"


def test_event_source_gained_only_observability():
    """M10.9 (ADR-0038 TP4) added OBSERVABILITY additively; MS8 names it the producer."""
    assert EventSource.OBSERVABILITY.value == "observability"
    assert len(list(EventSource)) == 25


# ── 6. adjacent members keep their exact values ─────────────────────────────


def test_cost_updated_stays_outside_the_metric_namespace():
    """OD-6 — not renamed, not replaced, not merged, not reinterpreted. Merging it into
    `metric.*` would be precisely the silent mutation AR-32 forbids."""
    assert EventType.COST_UPDATED.value == "cost.updated"
    assert not EventType.COST_UPDATED.value.startswith("metric.")


def test_tool_metrics_updated_is_untouched_and_not_a_metric_member():
    """Metric-*adjacent* by name, but it belongs to the Tool plane's `tool.*` namespace and is
    not part of the runtime `metric.*` vocabulary."""
    assert EventType.TOOL_METRICS_UPDATED.value == "tool.metrics.updated"
    assert not EventType.TOOL_METRICS_UPDATED.value.startswith("metric.")


# ── 7-8. the namespace is reserved correctly ────────────────────────────────


def test_the_metric_namespace_contains_exactly_the_ratified_vocabulary():
    """Exact set equality — an unratified `metric.*` member fails here."""
    present = {m.value for m in EventType if m.value.startswith("metric.")}
    assert present == RATIFIED_METRIC_VOCABULARY


def test_no_metric_billing_member_is_introduced():
    """ADR-0031 IR9 / ADR-0033 MS9 / OD-9 — `metric.billing.*` is reserved to Billing, and
    Epic 10 defines and emits none of it."""
    assert not [m for m in EventType if m.value.startswith("metric.billing")]


def test_the_metric_namespace_follows_the_dotted_lowercase_convention():
    for member in EventType:
        if member.value.startswith("metric."):
            assert member.value == member.value.lower()
            assert member.name == member.name.upper()
            assert member.name == member.value.replace(".", "_").upper()


# ── 9. the additive vocabulary rides the existing bus unchanged ─────────────


def test_the_existing_publisher_carries_a_metric_event_without_change():
    """DL-0017 — *"the same `EventBus` contract, no producer change"*. The vocabulary needs no
    bus change to be carriable, which is exactly why M10.8a can precede M10.9."""
    from core.events import EventMetadata
    from events import InProcessEventBus

    bus = InProcessEventBus()
    seen: list[EventType] = []
    bus.subscribe(lambda e: seen.append(e.type), types=EventType.METRIC_TURN)

    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.RUNTIME, org_id="o-1")
    )
    published = publisher.publish(EventType.METRIC_TURN, {"stage": "total", "ms": 12})

    assert seen == [EventType.METRIC_TURN]
    assert published.type is EventType.METRIC_TURN


# ── M10.8a is vocabulary only — no producer shipped ─────────────────────────


def test_observability_ships_only_the_turn_latency_producer():
    """M10.9 builds the AR-42 tap and nothing else: no tracing, cost meter or logging config."""
    modules = sorted(p.name for p in pathlib.Path("observability").glob("*.py"))
    assert modules == ["__init__.py", "turn_latency.py"]
    for future in ("tracing.py", "cost.py", "logging_config.py"):
        assert not (pathlib.Path("observability") / future).exists()


def _production_modules() -> list[pathlib.Path]:
    return [
        path
        for path in pathlib.Path(".").glob("*/**/*.py")
        if path.parts[0] not in {"tests", ".venv"} and "__pycache__" not in path.parts
    ]


def _publishes_metric_turn(source: str) -> bool:
    """Whether this module *publishes* `metric.turn` — a `publish`/`emit` call naming it.

    An AST call check, not a substring scan. Consuming an event necessarily names its type (a
    subscription filter is the whole point), so presence of the symbol says nothing about who
    produces it; the call is what AR-2 is actually about.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called not in {"publish", "emit"}:
            continue
        arguments = [*node.args, *(kw.value for kw in node.keywords)]
        if any("METRIC_TURN" in ast.dump(argument) for argument in arguments):
            return True
    return False


def test_only_observability_emits_metric_turn():
    """**AR-2 — one producer per event type**, tested as *emission* rather than as mention.

    Rewritten at M10.10 (owner ruling, 2026-08-20). The original assertion pinned the set of files
    whose text contained `METRIC_TURN`, which was exactly right while nothing consumed the event —
    at M10.8a/M10.9 the only modules that could name it were the enum and its producer. M10.10
    ships the first consumer (`api/metering/metric_buffer.py` subscribes through
    `EventType.METRIC_TURN`), and a subscription is not a production. The invariant is unchanged
    and, if anything, sharpened: **exactly one production module publishes `metric.turn`**, and it
    is still the observability tap. `core/events.py` drops out because defining the vocabulary was
    never emitting it.
    """
    emitters = {path for path in _production_modules() if _publishes_metric_turn(path.read_text())}
    assert emitters == {pathlib.Path("observability/turn_latency.py")}, emitters


def test_a_second_publisher_of_metric_turn_would_fail_the_guard():
    """The guard has teeth: a module that publishes `metric.turn` is detected, wherever it lives.

    Without this, an emission check that silently matched nothing would look identical to a
    correctly-enforced invariant."""
    assert _publishes_metric_turn("publisher.publish(EventType.METRIC_TURN, {})")
    assert _publishes_metric_turn("self._bus.emit(type=EventType.METRIC_TURN)")


def test_consuming_metric_turn_is_not_emitting_it():
    """M10.10's buffer names the type to *subscribe* to it, and publishes nothing (ADR-0039 MB4)."""
    consumer = pathlib.Path("api/metering/metric_buffer.py")
    source = consumer.read_text()
    assert "METRIC_TURN" in source
    assert not _publishes_metric_turn(source)
