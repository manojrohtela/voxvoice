"""Media-path fault tolerance (M11.4) — AR-23, AR-24, ADR-0046.

Everything here is deterministic: an **injected clock** drives every breaker assertion, so not one
test sleeps (TESTING_POLICY rule 2). No vendor, no network, no credentials (rule 1).

The three rules `runtime/faults/guard.py` is built around are each asserted directly, because each
is a way the layer could be subtly wrong rather than obviously broken:

1. a timeout is **not** a caller hangup;
2. cancellation is **not** a provider failure;
3. failover is **selection**, not retry.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

from core.errors import (
    ConfigError,
    ProviderError,
    ProvidersExhausted,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from provider.circuit_breaker import BreakerState, CircuitBreakerPolicy
from runtime.faults import (
    Degradation,
    DegradationKind,
    DegradationRecorder,
    GuardPolicy,
    ProviderGuard,
)


class _Clock:
    """A hand-driven clock. Breaker recovery is a *time* question, and this is how it gets
    answered without a sleep."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _guard(**kw) -> ProviderGuard:
    kw.setdefault("capability", "stt")
    return ProviderGuard(**kw)


async def _ok() -> str:
    return "ok"


async def _boom() -> str:
    raise RuntimeError("vendor exploded")


# ── timeout ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_slow_call_raises_a_typed_timeout():
    guard = _guard(policy=GuardPolicy(call_timeout_s=0.01))

    async def _never() -> str:
        await asyncio.Event().wait()
        return "unreachable"

    with pytest.raises(ProviderTimeout):
        await guard.call("p", _never, what="op")


@pytest.mark.asyncio
async def test_a_timeout_is_never_silently_a_hangup():
    """**Rule 1.** `RuntimePipeline.run()` reads stream exhaustion as *caller hung up*, so a
    deadline that ended a stream quietly would report the wrong real-world fact (AR-6)."""
    guard = _guard(policy=GuardPolicy(stream_idle_timeout_s=0.01))

    async def _stalls():
        yield "first"
        await asyncio.Event().wait()
        yield "never"  # pragma: no cover

    received = []
    with pytest.raises(ProviderTimeout):
        async for item in guard.stream("p", _stalls(), what="transcribe"):
            received.append(item)
    assert received == ["first"], "the stream ended silently instead of raising"


@pytest.mark.asyncio
async def test_a_timeout_is_recorded_as_a_degradation():
    recorder = DegradationRecorder(session_id="s")
    guard = _guard(policy=GuardPolicy(call_timeout_s=0.01), degradations=recorder)

    async def _never() -> str:
        await asyncio.Event().wait()
        return ""

    with pytest.raises(ProviderTimeout):
        await guard.call("p", _never)
    assert [d.kind for d in recorder.history] == [DegradationKind.TIMEOUT]


# ── typed translation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_raw_vendor_exception_becomes_a_typed_provider_error():
    """§A10 / SECURITY_POLICY §5 — a raw vendor error must never cross the boundary."""
    guard = _guard()
    with pytest.raises(ProviderUnavailable) as caught:
        await guard.call("p", _boom, what="op")
    assert isinstance(caught.value, ProviderError)
    assert isinstance(caught.value.__cause__, RuntimeError), "the cause must be preserved"


@pytest.mark.asyncio
async def test_an_already_typed_provider_error_passes_through_unchanged():
    """A plugin that mapped its own SDK properly (AR-12) is not re-wrapped into something vaguer."""
    from core.errors import ProviderAuthError

    async def _auth() -> str:
        raise ProviderAuthError("credentials rejected")

    with pytest.raises(ProviderAuthError):
        await _guard().call("p", _auth)


@pytest.mark.asyncio
async def test_a_rate_limit_keeps_its_own_type():
    from core.errors import ProviderRateLimited

    async def _limited() -> str:
        raise ProviderRateLimited("429")

    with pytest.raises(ProviderRateLimited):
        await _guard().call("p", _limited)


@pytest.mark.asyncio
async def test_a_malformed_response_keeps_its_own_type():
    from core.errors import ProviderProtocolError

    async def _garbage() -> str:
        raise ProviderProtocolError("not JSON")

    with pytest.raises(ProviderProtocolError):
        await _guard().call("p", _garbage)


@pytest.mark.asyncio
async def test_a_capability_mismatch_keeps_its_own_type():
    from core.errors import CapabilityUnsupported

    async def _unsupported() -> str:
        raise CapabilityUnsupported("no such voice")

    with pytest.raises(CapabilityUnsupported):
        await _guard().call("p", _unsupported)


@pytest.mark.asyncio
async def test_an_abrupt_stream_end_is_truncation_not_exhaustion():
    """**The distinction ADR-0045 SD4 exists for.** A vendor disconnect must not be readable as
    the caller hanging up."""

    async def _drops():
        yield "a"
        raise ConnectionResetError("socket closed")

    received = []
    with pytest.raises(ProviderStreamTruncated):
        async for item in _guard().stream("p", _drops(), what="transcribe"):
            received.append(item)
    assert received == ["a"]


@pytest.mark.asyncio
async def test_a_natural_stream_end_is_clean_exhaustion():
    """The other half: a stream that really did finish must **not** raise."""

    async def _finishes():
        yield "a"
        yield "b"

    assert [i async for i in _guard().stream("p", _finishes())] == ["a", "b"]


# ── cancellation (rule 2) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_into_a_provider_failure():
    guard = _guard()

    async def _cancelled() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await guard.call("p", _cancelled)


@pytest.mark.asyncio
async def test_cancellation_does_not_count_against_the_breaker():
    """A caller withdrawing must never look like a misbehaving vendor."""
    clock = _Clock()
    guard = _guard(
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=1)), clock=clock
    )

    async def _cancelled() -> str:
        raise asyncio.CancelledError

    for _ in range(5):
        with pytest.raises(asyncio.CancelledError):
            await guard.call("p", _cancelled)
    assert guard.breaker_state("p") is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_cancelling_a_guarded_stream_closes_the_source():
    """Generator closure is deterministic — the guard's `finally` closes what it opened."""
    closed = False

    async def _source():
        nonlocal closed
        try:
            yield "a"
            yield "b"
        finally:
            closed = True

    stream = _guard().stream("p", _source())
    async for _ in stream:
        break  # consumer walks away early
    await stream.aclose()  # deterministic close — no sleep, no GC dependency
    assert closed, "the guard left the provider stream open"


# ── circuit breaker ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_successful_call_does_not_open_the_breaker():
    clock = _Clock()
    guard = _guard(
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=2)), clock=clock
    )
    for _ in range(10):
        assert await guard.call("p", _ok) == "ok"
    assert guard.breaker_state("p") is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_repeated_failures_open_the_breaker():
    clock = _Clock()
    guard = _guard(
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=2)), clock=clock
    )
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await guard.call("p", _boom)
    assert guard.breaker_state("p") is BreakerState.OPEN


@pytest.mark.asyncio
async def test_an_open_breaker_refuses_the_call_without_invoking_the_provider():
    clock = _Clock()
    guard = _guard(
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=1)), clock=clock
    )
    with pytest.raises(ProviderUnavailable):
        await guard.call("p", _boom)

    invoked = False

    async def _should_not_run() -> str:
        nonlocal invoked
        invoked = True
        return "ok"

    with pytest.raises(ProviderUnavailable):
        await guard.call("p", _should_not_run)
    assert not invoked, "an open breaker still called the provider"


@pytest.mark.asyncio
async def test_the_breaker_recovers_on_the_injected_clock():
    """Recovery asserted by **advancing a clock**, never by sleeping."""
    clock = _Clock()
    guard = _guard(
        policy=GuardPolicy(
            breaker=CircuitBreakerPolicy(failure_threshold=1, reset_timeout_s=30.0)
        ),
        clock=clock,
    )
    with pytest.raises(ProviderUnavailable):
        await guard.call("p", _boom)
    assert guard.breaker_state("p") is BreakerState.OPEN

    clock.advance(31.0)
    assert await guard.call("p", _ok) == "ok"
    assert guard.breaker_state("p") is BreakerState.CLOSED


# ── failover (rule 3) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failover_walks_the_chain_in_order_and_stops_at_the_first_success():
    guard = _guard()
    seen: list[str] = []

    async def _op(candidate: str) -> str:
        seen.append(candidate)
        if candidate != "c":
            raise RuntimeError("down")
        return candidate

    assert await guard.call_with_failover(["a", "b", "c"], _op) == "c"
    assert seen == ["a", "b", "c"], "the chain was not walked best-first"


@pytest.mark.asyncio
async def test_failover_never_retries_the_same_candidate():
    """**Rule 3** — selection, not retry. Each candidate is attempted at most once."""
    guard = _guard()
    seen: list[str] = []

    async def _op(candidate: str) -> str:
        seen.append(candidate)
        raise RuntimeError("down")

    with pytest.raises(ProvidersExhausted):
        await guard.call_with_failover(["a", "b"], _op)
    assert seen == ["a", "b"], f"a candidate was retried: {seen}"


@pytest.mark.asyncio
async def test_exhausting_the_chain_raises_a_typed_terminus_with_the_last_cause():
    guard = _guard()

    async def _op(candidate: str) -> str:
        raise RuntimeError(f"{candidate} down")

    with pytest.raises(ProvidersExhausted) as caught:
        await guard.call_with_failover(["a", "b"], _op)
    assert isinstance(caught.value.__cause__, ProviderError)


@pytest.mark.asyncio
async def test_failover_records_each_hop_as_a_degradation():
    recorder = DegradationRecorder(session_id="s")
    guard = _guard(degradations=recorder)

    async def _op(candidate: str) -> str:
        if candidate == "b":
            return candidate
        raise RuntimeError("down")

    assert await guard.call_with_failover(["a", "b"], _op) == "b"
    assert DegradationKind.FAILED_OVER in {d.kind for d in recorder.history}


@pytest.mark.asyncio
async def test_cancellation_during_failover_is_not_failed_over():
    guard = _guard()
    seen: list[str] = []

    async def _op(candidate: str) -> str:
        seen.append(candidate)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await guard.call_with_failover(["a", "b", "c"], _op)
    assert seen == ["a"], "cancellation was treated as a candidate failure"


@pytest.mark.asyncio
async def test_an_empty_chain_is_a_typed_failure_not_a_hang():
    with pytest.raises(ProvidersExhausted):
        await _guard().call_with_failover([], lambda c: _ok())


# ── degradation recording (AR-24) ───────────────────────────────────────────


def test_a_degradation_is_declared_evented_and_recorded():
    published: list[Degradation] = []
    recorder = DegradationRecorder(session_id="s", sink=published.append)
    recorder.record(
        Degradation(capability="stt", kind=DegradationKind.NOT_STREAMING, provider="p")
    )
    assert len(published) == 1  # evented
    assert recorder.degraded("stt")  # recorded
    assert recorder.as_session_metadata()["degradations"][0]["kind"] == "not_streaming"  # declared


def test_a_declared_non_streaming_capability_is_representable_truthfully():
    """§A6.4 / ADR-0045 SD9 — a provider that cannot stream says so, and the runtime records it.

    This is the mechanism DL-0154's deferred target-language answer will use. It asserts the
    *representation*, not any vendor's actual capability — **no vendor has been measured**."""
    recorder = DegradationRecorder(session_id="s")
    guard = _guard(capability="stt", degradations=recorder)
    guard.declared_limit("some-provider", DegradationKind.NOT_STREAMING, "declares streaming=False")
    assert recorder.history[0].kind is DegradationKind.NOT_STREAMING


def test_degradation_history_is_bounded_but_counts_are_not():
    """AR-40's spirit applied to the record itself: a degrading session is the one most likely to
    degrade repeatedly, so the history is bounded — but the **count** survives truncation, or a
    long outage would look smaller than a brief one.

    **Updated at M11.15 (R-48), and the invariant it guards is unchanged.** This test previously
    fed 114 *identical* degradations and asserted the history held the last 64. That asserted the
    `maxlen` bound using the one input that M11.15 now coalesces: identical repeats are a single
    **episode**, so the history holds one entry, not 64. The property the test exists for — *"the
    count is not truncated with the history"* — is asserted below exactly as before, and the
    `maxlen` bound it also covered is now asserted with **distinct** degradations, which is the
    input that can actually fill the history. Coverage is strictly wider: the old version could
    not have detected a coalescing bug, and the new one asserts both bounds.
    """
    from runtime.faults import DEGRADATION_HISTORY

    recorder = DegradationRecorder(session_id="s")
    for _ in range(DEGRADATION_HISTORY + 50):
        recorder.record(Degradation(capability="stt", kind=DegradationKind.TIMEOUT))
    # One episode: the same capability failing the same way with the same provider.
    assert len(recorder.history) == 1
    # The count is exact and un-suppressed — a long outage still measures as a long outage.
    assert recorder.counts["stt:timeout"] == DEGRADATION_HISTORY + 50

    # The `maxlen` bound, exercised with inputs that are genuinely distinct episodes.
    distinct = DegradationRecorder(session_id="s")
    for index in range(DEGRADATION_HISTORY + 50):
        distinct.record(
            Degradation(
                capability="stt", kind=DegradationKind.TIMEOUT, provider=f"vendor-{index}"
            )
        )
    assert len(distinct.history) == DEGRADATION_HISTORY
    assert distinct.counts["stt:timeout"] == DEGRADATION_HISTORY + 50


def test_a_raising_sink_never_breaks_the_caller():
    """DL-0013 — recording a degradation must not itself become a second failure."""

    def _explode(_d: Degradation) -> None:
        raise RuntimeError("sink down")

    recorder = DegradationRecorder(session_id="s", sink=_explode)
    recorder.record(Degradation(capability="stt", kind=DegradationKind.TIMEOUT))
    assert len(recorder.history) == 1


def test_an_auxiliary_failure_is_recorded_with_its_fallback():
    recorder = DegradationRecorder(session_id="s")
    guard = _guard(capability="voice_activity", degradations=recorder)
    guard.auxiliary_failed("local", "observe failed", fallback="frame passed through")
    entry = recorder.history[0]
    assert entry.kind is DegradationKind.AUXILIARY_DISABLED
    assert entry.fallback == "frame passed through"


# ── policy validation ───────────────────────────────────────────────────────


def test_an_invalid_timeout_is_rejected_rather_than_coerced():
    with pytest.raises(ValueError, match="call_timeout_s"):
        GuardPolicy(call_timeout_s=0)
    with pytest.raises(ValueError, match="stream_idle_timeout_s"):
        GuardPolicy(stream_idle_timeout_s=-1)


# ── boundaries ──────────────────────────────────────────────────────────────

_FAULTS_DIR = pathlib.Path(inspect.getfile(ProviderGuard)).parent


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
    return roots


def test_the_guard_reuses_the_breaker_rather_than_reimplementing_it():
    """ADR-0046 FT-D3 — *reuse the mechanism*. A second breaker would be the duplication the
    decision exists to prevent."""
    source = pathlib.Path(inspect.getfile(ProviderGuard)).read_text(encoding="utf-8")
    assert "from provider.circuit_breaker import" in source
    assert "from provider.failover import" in source
    tree = ast.parse(source)
    declared = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
    }
    assert "CircuitBreaker" not in declared
    assert "FailoverController" not in declared


def test_the_fault_layer_declares_no_provider_registry():
    """ADR-0046 FT-V6 — the guard decorates calls; it never resolves providers."""
    for path in _FAULTS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name != "ProviderRegistry", path


def test_the_fault_layer_contains_no_vendor_branch():
    """AR-21 — vendor-conditional logic lives inside the vendor's plugin, nowhere else."""
    vendors = ("deepgram", "cartesia", "sarvam", "groq", "anthropic", "openai", "pipecat")
    for path in _FAULTS_DIR.rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        for vendor in vendors:
            assert vendor not in lowered, f"{path.name} mentions {vendor}"


def test_the_fault_layer_does_not_import_the_provider_execution_runtime():
    """ADR-0046 FT-D2 — the mechanisms are reused; the request/response runtime is not adopted."""
    forbidden = {"provider.runtime", "provider.router", "provider.registry", "provider.domain"}
    for path in _FAULTS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden, f"{path.name} imports {node.module}"


def test_the_runtime_to_provider_edge_is_still_acyclic():
    """ADR-0046 FT-V3/FT-V5 — reusing the breaker must not have created a cycle."""
    provider_dir = pathlib.Path(inspect.getfile(ProviderGuard)).parent.parent.parent / "provider"
    for path in provider_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        assert "runtime" not in _imports(path), f"{path.name} imports runtime"


def test_no_provider_call_in_the_pipeline_bypasses_the_guard():
    """**The AR-23 assertion.** Every capability call in the pipeline is routed through a guard.

    Read from the AST: any `await self._<capability>.<method>(...)` that is not lexically inside a
    `self._guard(...)` expression would be a direct raw call, which AR-23 names a defect."""
    from runtime.pipeline.pipeline import RuntimePipeline

    source = pathlib.Path(inspect.getfile(RuntimePipeline)).read_text(encoding="utf-8")
    guarded_methods = {
        "start", "stop", "send", "inbound", "transcribe", "synthesize", "stream",
    }
    tree = ast.parse(source)

    def _is_guard_call(node: ast.AST) -> bool:
        """`self._guard("x").call(...)` / `.stream(...)` — the seam every call must sit inside."""
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return False
        inner = node.func.value
        return (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "_guard"
        )

    # Every node lexically inside some guard call — the guarded region of the source.
    guarded_nodes: set[int] = set()
    for node in ast.walk(tree):
        if _is_guard_call(node):
            guarded_nodes.update(id(child) for child in ast.walk(node))

    provider_calls: list[str] = []
    unguarded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in guarded_methods:
            continue
        owner = node.value
        if not (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)):
            continue
        if owner.value.id != "self" or owner.attr not in ("_transport", "_stt", "_llm", "_tts"):
            continue
        segment = ast.get_source_segment(source, node) or "?"
        provider_calls.append(segment)
        if id(node) not in guarded_nodes:
            unguarded.append(segment)

    assert provider_calls, "no provider calls found — the guard cannot be proven"
    assert unguarded == [], f"AR-23: these provider calls bypass the guard: {unguarded}"


def test_config_error_is_distinct_from_a_degradation():
    """A configuration that cannot be honoured is refused, not degraded into (`ConfigError`'s own
    docstring: *fail fast, do not run degraded*)."""
    assert not issubclass(ConfigError, ProviderError)
