"""The Groq SDK adapter — without the SDK (M11.9).

`groq_client.py` is the one module that imports `groq`. Its behaviour is proven by installing a
**stand-in module** in `sys.modules`, so the suite behaves identically whether or not the real
wheel is present (it is under `uv sync`; it is not in a bare dev venv). No test here opens a
socket, and none needs a credential.

What these tests do not prove is how Groq's servers actually behave — that needs a live account,
and M11.9 made no live call (R-54). What they do prove is that *whatever* the SDK raises, the
platform sees a neutral `FailureKind` and never a vendor object.
"""

from __future__ import annotations

import sys
import types

import pytest

from providers.llm.completion_stream import (
    CompletionChunk,
    CompletionSpec,
    CompletionStreamError,
    FailureKind,
)
from providers.llm.groq_client import (
    GroqCompletionClient,
    chunk_finished,
    chunk_text,
    classify_exception,
    classify_status,
    groq_available,
)

SPEC = CompletionSpec(messages=(("user", "hi"),), model="llama-3.3-70b-versatile")


# ── a stand-in for the vendor package ────────────────────────────────────────


class _Delta:
    def __init__(self, content=None):
        self.content = content


class _Choice:
    def __init__(self, content=None, finish_reason=None):
        self.delta = _Delta(content)
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, content=None, finish_reason=None, choices=None):
        self.choices = choices if choices is not None else [_Choice(content, finish_reason)]


class _Stream:
    """Stands in for `groq.AsyncStream`."""

    def __init__(self, chunks, error=None):
        self._chunks, self._error = chunks, error
        self.closed = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    async def close(self):
        self.closed += 1


def _module(*, stream=None, error=None, close_error=None):
    """A minimal `groq` module: the exception hierarchy plus an `AsyncGroq` that returns
    `stream`. The hierarchy mirrors the real one, which `test_the_stand_in_mirrors_the_real_sdk`
    checks whenever the real package is importable."""
    module = types.ModuleType("groq")

    class GroqError(Exception):
        pass

    class APIError(GroqError):
        pass

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        def __init__(self, message="", status_code=None):
            super().__init__(message)
            self.status_code = status_code

    class APIResponseValidationError(APIError):
        pass

    class _Completions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if error is not None:
                raise error
            return stream

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class AsyncGroq:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = _Chat()
            module.last_client = self

    module.GroqError = GroqError
    module.APIError = APIError
    module.APIConnectionError = APIConnectionError
    module.APITimeoutError = APITimeoutError
    module.APIStatusError = APIStatusError
    module.APIResponseValidationError = APIResponseValidationError
    module.AsyncGroq = AsyncGroq
    module.last_client = None
    return module


@pytest.fixture
def groq_module(monkeypatch):
    def install(**kwargs):
        module = _module(**kwargs)
        monkeypatch.setitem(sys.modules, "groq", module)
        return module

    return install


# ── status and exception classification ──────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, FailureKind.AUTH),
        (403, FailureKind.AUTH),
        (429, FailureKind.RATE_LIMITED),
        (400, FailureKind.PROTOCOL),
        (404, FailureKind.PROTOCOL),
        (422, FailureKind.PROTOCOL),
        (408, FailureKind.TIMEOUT),
        (504, FailureKind.TIMEOUT),
        (500, FailureKind.UNAVAILABLE),
        (503, FailureKind.UNAVAILABLE),
        (None, FailureKind.UNAVAILABLE),
    ],
)
def test_a_status_is_classified_by_what_the_runtime_can_do_about_it(status, expected):
    assert classify_status(status) is expected


def test_22_an_auth_failure_is_classified_as_auth(groq_module):
    module = groq_module()
    assert classify_exception(module.APIStatusError("nope", 401)) is FailureKind.AUTH


def test_23_a_rate_limit_is_classified_as_rate_limited(groq_module):
    module = groq_module()
    assert classify_exception(module.APIStatusError("slow down", 429)) is FailureKind.RATE_LIMITED


def test_21_a_timeout_is_classified_before_its_connection_superclass(groq_module):
    """`APITimeoutError` subclasses `APIConnectionError`; order matters, so it is asserted."""
    module = groq_module()
    assert classify_exception(module.APITimeoutError()) is FailureKind.TIMEOUT
    assert classify_exception(module.APIConnectionError()) is FailureKind.UNAVAILABLE


def test_24_an_unparseable_answer_is_a_protocol_failure(groq_module):
    module = groq_module()
    assert classify_exception(module.APIResponseValidationError()) is FailureKind.PROTOCOL


def test_an_unrecognized_vendor_error_is_unavailable_rather_than_unhandled(groq_module):
    module = groq_module()
    assert classify_exception(module.GroqError("something new")) is FailureKind.UNAVAILABLE
    assert classify_exception(ValueError("not even a vendor error")) is FailureKind.UNAVAILABLE


def test_classification_never_reads_the_message_text(groq_module):
    """Vendor messages are not a contract; a 401 is. Two errors with opposite-sounding text
    classify by status, not by wording."""
    module = groq_module()
    assert classify_exception(module.APIStatusError("rate limit exceeded", 401)) is (
        FailureKind.AUTH
    )
    assert classify_exception(module.APIStatusError("unauthorized", 429)) is (
        FailureKind.RATE_LIMITED
    )


def test_the_stand_in_mirrors_the_real_sdk():
    """Two branches, no skip: where the real package is present the stand-in's assumptions are
    checked against it; where it is absent the adapter's own table is checked for consistency.

    This is what stops the stand-in from drifting into a fiction that passes tests the real SDK
    would fail."""
    try:
        import groq as real
    except ImportError:
        assert classify_status(401) is FailureKind.AUTH
        assert classify_status(429) is FailureKind.RATE_LIMITED
        return
    assert issubclass(real.APITimeoutError, real.APIConnectionError)
    assert issubclass(real.APIConnectionError, real.APIError)
    assert issubclass(real.APIStatusError, real.APIError)
    assert real.AuthenticationError.status_code == 401
    assert real.PermissionDeniedError.status_code == 403
    assert real.RateLimitError.status_code == 429
    assert real.BadRequestError.status_code == 400
    assert hasattr(real.AsyncGroq, "__init__")


# ── chunk reading ────────────────────────────────────────────────────────────


def test_chunk_text_reads_the_delta():
    assert chunk_text(_Chunk("hello")) == "hello"


@pytest.mark.parametrize(
    "chunk",
    [_Chunk(None), _Chunk(choices=[]), _Chunk(choices=None), object()],
)
def test_a_chunk_without_text_reads_as_empty_rather_than_raising(chunk):
    """Role-only, finish-only and usage-only chunks are all normal traffic."""
    assert chunk_text(chunk) == ""


def test_a_finish_reason_is_recognized():
    assert chunk_finished(_Chunk("x", finish_reason="stop")) is True
    assert chunk_finished(_Chunk("x")) is False


# ── the client ───────────────────────────────────────────────────────────────


async def test_the_client_streams_normalized_chunks(groq_module):
    module = groq_module(
        stream=_Stream([_Chunk("Hello"), _Chunk(" there", finish_reason="stop")])
    )
    stream = await GroqCompletionClient("sk-test-value").open(SPEC)
    assert [c async for c in stream] == [
        CompletionChunk("Hello"),
        CompletionChunk(" there"),
    ]
    assert module.last_client.kwargs["max_retries"] == 0  # the guard owns retry policy, not the SDK


async def test_the_request_carries_the_spec(groq_module):
    module = groq_module(stream=_Stream([_Chunk("x", finish_reason="stop")]))
    await GroqCompletionClient("sk-test-value").open(
        CompletionSpec(messages=(("system", "be brief"), ("user", "hi")), model="m1")
    )
    call = module.last_client.chat.completions.calls[0]
    assert call["model"] == "m1"
    assert call["stream"] is True
    assert call["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


async def test_25_a_stream_without_a_finish_reason_is_reported_truncated(groq_module):
    """A connection that dies mid-sentence must not look like a completed answer."""
    groq_module(stream=_Stream([_Chunk("Half an ans")]))
    stream = await GroqCompletionClient("sk-test-value").open(SPEC)
    with pytest.raises(CompletionStreamError) as raised:
        [c async for c in stream]
    assert raised.value.kind is FailureKind.TRUNCATED


async def test_a_mid_stream_vendor_error_is_classified(groq_module):
    module = groq_module()
    stream_module = groq_module(
        stream=_Stream([_Chunk("Half")], error=module.APIStatusError("boom", 503))
    )
    stream = await GroqCompletionClient("sk-test-value").open(SPEC)
    with pytest.raises(CompletionStreamError) as raised:
        [c async for c in stream]
    assert raised.value.kind is FailureKind.UNAVAILABLE
    assert stream_module is not None


async def test_20_a_failure_to_open_is_classified(groq_module):
    module = _module()
    error = module.APIConnectionError()
    groq_module(error=error)
    with pytest.raises(CompletionStreamError) as raised:
        await GroqCompletionClient("sk-test-value").open(SPEC)
    assert raised.value.kind is FailureKind.UNAVAILABLE


async def test_a_vendor_message_never_reaches_the_neutral_error(groq_module):
    """ADR-0049 CR5/CR6: vendor text routinely embeds endpoints and key fragments."""
    module = _module()
    groq_module(error=module.APIStatusError("key sk-live-abc123 rejected at https://api", 401))
    with pytest.raises(CompletionStreamError) as raised:
        await GroqCompletionClient("sk-test-value").open(SPEC)
    assert "sk-live-abc123" not in str(raised.value)
    assert "api" not in raised.value.detail
    assert raised.value.__cause__ is None


async def test_closing_is_idempotent_and_never_raises(groq_module):
    stream_obj = _Stream([_Chunk("x", finish_reason="stop")])
    groq_module(stream=stream_obj)
    stream = await GroqCompletionClient("sk-test-value").open(SPEC)
    await stream.aclose()
    await stream.aclose()
    assert stream_obj.closed == 1


async def test_a_close_that_fails_does_not_fail_the_turn(groq_module):
    class Rude(_Stream):
        async def close(self):
            raise RuntimeError("connection already gone")

    groq_module(stream=Rude([_Chunk("x", finish_reason="stop")]))
    stream = await GroqCompletionClient("sk-test-value").open(SPEC)
    await stream.aclose()  # must not raise


async def test_a_missing_library_is_a_typed_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "groq", None)  # `import groq` raises ImportError
    assert groq_available() is False
    with pytest.raises(CompletionStreamError) as raised:
        await GroqCompletionClient("sk-test-value").open(SPEC)
    assert raised.value.kind is FailureKind.UNAVAILABLE
    assert "not installed" in raised.value.detail


async def test_an_importable_library_is_reported_available(groq_module):
    groq_module()
    assert groq_available() is True


# ── the credential ───────────────────────────────────────────────────────────


def test_the_api_key_is_never_rendered():
    client = GroqCompletionClient("sk-live-do-not-print")
    assert "sk-live" not in repr(client)
    assert "sk-live" not in str(client)


def test_the_api_key_is_not_reachable_as_a_public_attribute():
    client = GroqCompletionClient("sk-live-do-not-print")
    assert not [name for name in dir(client) if "key" in name and not name.startswith("_Groq")]


def test_an_empty_key_is_refused_at_construction():
    with pytest.raises(ValueError):
        GroqCompletionClient("")


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_an_invalid_timeout_is_refused(timeout):
    with pytest.raises(ValueError):
        GroqCompletionClient("sk-test-value", timeout_s=timeout)


async def test_the_credential_reaches_the_sdk_and_nowhere_else(groq_module):
    module = groq_module(stream=_Stream([_Chunk("x", finish_reason="stop")]))
    await GroqCompletionClient("sk-test-value").open(SPEC)
    assert module.last_client.kwargs["api_key"] == "sk-test-value"
    # and it is not copied into the request
    assert "api_key" not in module.last_client.chat.completions.calls[0]
