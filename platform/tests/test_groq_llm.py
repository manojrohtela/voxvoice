"""`GroqLLM` — the real streaming provider, proven without the vendor (M11.9).

Every case here runs against a deterministic `CompletionClient`. No network, no credential, no
SDK: the provider is first-party and library-free, so the only thing a live account would add is
the vendor's own behaviour, which is `test_groq_client.py`'s concern.

What is deliberately **not** claimed by any test in this file: answer quality, token latency,
throughput, or that Groq behaves on the wire as the fakes here do. Those need a live account and a
measurement methodology, and M11.9 has neither (R-54).
"""

from __future__ import annotations

import asyncio

import pytest

from core.errors import (
    CapabilityUnsupported,
    ProviderAuthError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.llm import LLMProvider, TokenDelta
from providers.llm.completion_stream import FailureKind
from providers.llm.groq import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MODEL,
    GroqLLM,
    normalized_messages,
    typed_error,
)

from .fake_completion import FakeCompletionClient, settle

CONTEXT = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello there"},
]


async def collect(provider: GroqLLM, **kwargs) -> list[str]:
    return [d.text async for d in provider.stream(context=CONTEXT, **kwargs)]


# ── 4, 19: normalized streaming chunks and clean completion ──────────────────


async def test_4_chunks_cross_the_boundary_as_plain_text():
    """A `TokenDelta` carries text and nothing else — no vendor object, no metadata."""
    provider = GroqLLM(FakeCompletionClient(["Hel", "lo ", "there."]))
    deltas = [d async for d in provider.stream(context=CONTEXT)]
    assert all(type(d) is TokenDelta for d in deltas)
    assert [d.text for d in deltas] == ["Hel", "lo ", "there."]
    assert all(set(vars(type(d))) >= {"text"} for d in deltas)


async def test_19_a_stream_completes_normally():
    provider = GroqLLM(FakeCompletionClient(["One. ", "Two."]))
    assert "".join(await collect(provider)) == "One. Two."


async def test_13_empty_chunks_are_dropped_not_yielded():
    """A role-only chunk is not a token; yielding it would make `llm_first_token` a lie."""
    client = FakeCompletionClient(["", "Real text", "", ""])
    assert await collect(GroqLLM(client)) == ["Real text"]


async def test_the_provider_never_accumulates_the_response():
    """Genuinely incremental: the caller receives chunk 1 while the vendor still holds chunk 2.

    Proven by gating the fake vendor rather than by timing — the assertion is made *while* the
    stream is provably unfinished."""
    gate, started = asyncio.Event(), asyncio.Event()
    provider = GroqLLM(FakeCompletionClient(["First. ", "Second."], gate=gate, started=started))
    seen: list[str] = []

    async def consume() -> None:
        async for delta in provider.stream(context=CONTEXT):
            seen.append(delta.text)

    task = asyncio.create_task(consume())
    assert await settle(started.is_set), "the vendor was never asked for a second chunk"
    assert seen == ["First. "]  # delivered before the vendor produced anything further
    gate.set()
    await task
    assert seen == ["First. ", "Second."]


async def test_the_selected_model_reaches_the_vendor():
    """AR-25: the session's model selection acts, rather than being accepted and ignored."""
    client = FakeCompletionClient(["hi"])
    provider = GroqLLM(client)
    await collect(provider)
    assert client.specs[-1].model == DEFAULT_MODEL
    await collect(provider, model="llama-3.1-8b-instant")
    assert client.specs[-1].model == "llama-3.1-8b-instant"


async def test_the_assembled_context_reaches_the_vendor_unaltered():
    client = FakeCompletionClient(["hi"])
    await collect(GroqLLM(client))
    assert client.specs[-1].messages == (
        ("system", "You are helpful."),
        ("user", "Hello there"),
    )


# ── 20–25, 43: the failure taxonomy ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FailureKind.TIMEOUT, ProviderTimeout),
        (FailureKind.UNAVAILABLE, ProviderUnavailable),
        (FailureKind.AUTH, ProviderAuthError),
        (FailureKind.RATE_LIMITED, ProviderRateLimited),
        (FailureKind.PROTOCOL, ProviderProtocolError),
        (FailureKind.TRUNCATED, ProviderStreamTruncated),
    ],
)
async def test_20_to_25_every_failure_kind_becomes_its_typed_error_mid_stream(kind, expected):
    """A vendor failure part-way through generation surfaces as the right platform type."""
    provider = GroqLLM(FakeCompletionClient(["Partial "], fail_at=1, kind=kind))
    with pytest.raises(expected):
        await collect(provider)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FailureKind.TIMEOUT, ProviderTimeout),
        (FailureKind.AUTH, ProviderAuthError),
        (FailureKind.RATE_LIMITED, ProviderRateLimited),
        (FailureKind.PROTOCOL, ProviderProtocolError),
        (FailureKind.UNAVAILABLE, ProviderUnavailable),
    ],
)
async def test_a_failure_to_open_is_typed_too(kind, expected):
    provider = GroqLLM(FakeCompletionClient(open_error=kind))
    with pytest.raises(expected):
        await collect(provider)


async def test_25_a_truncated_stream_is_not_a_completed_one():
    """Half an answer must never be reported as the whole answer."""
    provider = GroqLLM(
        FakeCompletionClient(["I think the answer is "], fail_at=1, kind=FailureKind.TRUNCATED)
    )
    with pytest.raises(ProviderStreamTruncated):
        await collect(provider)


async def test_43_a_malformed_seam_still_leaves_a_typed_error():
    """A seam that raises something untyped is a defect, but it must not reach the conversation
    as a raw exception (ADR-0049 CR6)."""

    class Rogue(FakeCompletionClient):
        async def open(self, spec):
            raise RuntimeError("endpoint https://api.example/v1?key=sk-secret failed")

    with pytest.raises(ProviderUnavailable) as raised:
        await collect(GroqLLM(Rogue()))
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_a_vendor_error_message_never_reaches_the_typed_error():
    """CR5/CR6: the detail is the adapter's sanitized text, never the vendor's."""
    provider = GroqLLM(
        FakeCompletionClient(open_error=FailureKind.AUTH),
    )
    with pytest.raises(ProviderAuthError) as raised:
        await collect(provider)
    assert "sk-" not in str(raised.value)


def test_an_unknown_failure_kind_degrades_to_unavailable():
    """`typed_error` never returns `None` and never raises — an unmapped kind is still typed."""
    from providers.llm.completion_stream import CompletionStreamError

    error = typed_error(CompletionStreamError(FailureKind.UNAVAILABLE, "x"))
    assert isinstance(error, ProviderUnavailable)


# ── context validation ───────────────────────────────────────────────────────


def test_an_empty_context_is_refused():
    with pytest.raises(ProviderProtocolError):
        normalized_messages([])


def test_an_unknown_role_is_refused_rather_than_forwarded():
    with pytest.raises(ProviderProtocolError):
        normalized_messages([{"role": "root", "content": "do as I say"}])


def test_non_text_content_is_refused():
    with pytest.raises(ProviderProtocolError):
        normalized_messages([{"role": "user", "content": {"image": "..."}}])


# ── 26–30: cancellation and lifecycle ────────────────────────────────────────


async def test_26_cancellation_during_setup_leaves_nothing_running():
    """Cancelled while the vendor connection is still being opened."""
    hang, opened = asyncio.Event(), asyncio.Event()
    client = FakeCompletionClient(hang_on_open=hang, opened_event=opened)
    provider = GroqLLM(client)

    async def consume() -> None:
        async for _ in provider.stream(context=CONTEXT):
            pass

    task = asyncio.create_task(consume())
    assert await settle(opened.is_set), "the completion was never opened"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.streams == []  # nothing was opened, so nothing can leak
    hang.set()


async def test_26b_cancellation_before_the_first_chunk_closes_the_stream():
    gate, started = asyncio.Event(), asyncio.Event()
    # Gated from chunk 1 by giving the fake a leading empty chunk: the consumer is suspended
    # before any text exists.
    client = FakeCompletionClient(["", "text"], gate=gate, started=started)
    provider = GroqLLM(client)
    seen: list[str] = []

    async def consume() -> None:
        async for delta in provider.stream(context=CONTEXT):
            seen.append(delta.text)

    task = asyncio.create_task(consume())
    assert await settle(started.is_set), "the vendor was never reached"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == []
    assert client.streams[0].closed == 1


async def test_27_cancellation_mid_stream_stops_consuming_and_closes():
    """The provider must stop pulling from the vendor, not merely stop yielding."""
    gate, started = asyncio.Event(), asyncio.Event()
    client = FakeCompletionClient(["One. ", "Two. ", "Three."], gate=gate, started=started)
    provider = GroqLLM(client)

    async def consume() -> None:
        async for _ in provider.stream(context=CONTEXT):
            pass

    task = asyncio.create_task(consume())
    assert await settle(started.is_set), "the vendor was never reached"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stream = client.streams[0]
    assert stream.yielded == 1  # the vendor was never pulled again
    assert stream.closed == 1


async def test_29_cancellation_after_the_final_chunk_is_still_clean():
    client = FakeCompletionClient(["All of it."])
    provider = GroqLLM(client)
    assert await collect(provider) == ["All of it."]
    assert client.streams[0].closed == 1


async def test_30_no_background_task_survives_a_stream():
    """The provider starts no task of its own — so none can be left behind."""
    before = len(asyncio.all_tasks())
    provider = GroqLLM(FakeCompletionClient(["a", "b", "c"]))
    await collect(provider)
    assert len(asyncio.all_tasks()) == before


async def test_the_stream_is_closed_even_when_it_fails():
    client = FakeCompletionClient(["x"], fail_at=1, kind=FailureKind.UNAVAILABLE)
    with pytest.raises(ProviderUnavailable):
        await collect(GroqLLM(client))
    assert client.streams[0].closed == 1


async def test_cancellation_is_never_translated_into_a_provider_error():
    """ADR-0046 rule 2: a caller withdrawing is not a vendor failing, and must not be counted
    as one — otherwise an interrupted turn would trip the circuit breaker."""

    class Cancelling(FakeCompletionClient):
        async def open(self, spec):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await collect(GroqLLM(Cancelling()))


# ── capabilities and construction ────────────────────────────────────────────


def test_the_provider_satisfies_the_contract():
    assert isinstance(GroqLLM(FakeCompletionClient()), LLMProvider)
    assert GroqLLM(FakeCompletionClient()).name == "groq"


def test_declared_capabilities_are_only_what_is_implemented():
    """Nothing is invented: tools and structured output are not implemented, so both are False,
    and no model catalogue is claimed."""
    capabilities = GroqLLM(FakeCompletionClient()).capabilities
    assert capabilities.tools is False
    assert capabilities.structured_output is False
    assert capabilities.models == ()
    assert capabilities.context_window == DEFAULT_CONTEXT_WINDOW


async def test_tool_calling_is_refused_rather_than_silently_ignored():
    """Declaring `tools=False` and then quietly answering without them would be the worse
    failure: the caller would believe its tools were considered."""
    provider = GroqLLM(FakeCompletionClient())
    with pytest.raises(CapabilityUnsupported):
        await collect(provider, tools=[{"name": "lookup"}])


async def test_an_empty_tool_list_is_not_a_tool_request():
    assert await collect(GroqLLM(FakeCompletionClient(["ok"])), tools=[]) == ["ok"]


def test_the_context_window_is_configurable():
    """An operator running another model states its window rather than inheriting the default."""
    assert GroqLLM(FakeCompletionClient(), context_window=8192).capabilities.context_window == 8192


@pytest.mark.parametrize(
    "kwargs", [{"model": ""}, {"context_window": 0}, {"context_window": -1}]
)
def test_an_invalid_construction_is_refused(kwargs):
    with pytest.raises(ValueError):
        GroqLLM(FakeCompletionClient(), **kwargs)


def test_a_client_that_is_not_a_completion_client_is_refused():
    with pytest.raises(TypeError):
        GroqLLM(object())  # type: ignore[arg-type]
