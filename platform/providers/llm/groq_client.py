"""The **only** module in the platform that imports the Groq SDK (M11.9, AR-12).

Everything vendor-shaped stops here: the client object, its exception hierarchy, its chunk model.
What leaves is `providers.llm.completion_stream` — text and a neutral `FailureKind`, nothing else.

## What the SDK actually gives us

Verified against `groq` 1.7.0 by inspection of the installed package (no live API call was made,
and none is made by the test suite):

- `AsyncGroq(api_key=..., timeout=..., max_retries=...)` — an async client over `httpx`.
- `client.chat.completions.create(..., stream=True)` returns an `AsyncStream[ChatCompletionChunk]`
  supporting `__aiter__` and `close()`.
- A chunk carries `choices[i].delta.content` (`str | None`) and `choices[i].finish_reason`.
- Errors derive from `GroqError`; `APIStatusError` subclasses carry the HTTP status.

## Retries are switched off deliberately

The SDK retries twice by default. The platform already owns retry, breaker and failover policy in
`ProviderGuard` (AR-23, ADR-0046 FT-D3/FT-D5), and a second, invisible retry layer inside the SDK
would double the latency budget of a failing call and corrupt the breaker's failure accounting —
the guard would see one slow failure where three requests actually happened. `max_retries=0`
leaves exactly one authority for retry policy.

## Nothing here interprets text

The adapter does not strip, join, buffer or repair chunks. Splitting text at clause boundaries is
the runtime's job (`runtime/speech/clauses.py`), and doing any of it here would put a
conversational decision inside a vendor plugin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .completion_stream import (
    CompletionChunk,
    CompletionClient,
    CompletionSpec,
    CompletionStream,
    CompletionStreamError,
    FailureKind,
)

#: How long one request may take, end to end, before the SDK abandons it. The guard applies its
#: own per-item deadline on top (AR-23); this one exists so a vendor that accepts a connection and
#: then never writes cannot hold a socket open indefinitely underneath it.
DEFAULT_TIMEOUT_S = 30.0

#: HTTP statuses that mean *the credential was refused*, not *the request was wrong*.
_AUTH_STATUSES = frozenset({401, 403})

#: Statuses where the vendor answered coherently but rejected the request's shape. These are
#: configuration defects (an unknown model, a malformed message) and must never be mistaken for
#: transient unavailability, because retrying them is guaranteed to fail the same way.
_PROTOCOL_STATUSES = frozenset({400, 404, 405, 409, 415, 422})


def classify_status(status: int | None) -> FailureKind:
    """Map an HTTP status onto the neutral vocabulary."""
    if status is None:
        return FailureKind.UNAVAILABLE
    if status in _AUTH_STATUSES:
        return FailureKind.AUTH
    if status == 429:
        return FailureKind.RATE_LIMITED
    if status in _PROTOCOL_STATUSES:
        return FailureKind.PROTOCOL
    if status == 408 or status == 504:
        return FailureKind.TIMEOUT
    if status >= 500:
        return FailureKind.UNAVAILABLE
    return FailureKind.PROTOCOL


def classify_exception(exc: BaseException) -> FailureKind:
    """Map a vendor exception onto the neutral vocabulary.

    Deliberately written against the SDK's **exception classes and HTTP statuses**, never against
    message text: vendor messages are not a contract and change without notice, whereas a 401 is a
    401. Import-local so this module stays the only one that names the SDK."""
    import groq

    if isinstance(exc, groq.APITimeoutError):
        # Checked before APIConnectionError — it is a subclass of it.
        return FailureKind.TIMEOUT
    if isinstance(exc, groq.APIConnectionError):
        return FailureKind.UNAVAILABLE
    if isinstance(exc, groq.APIStatusError):
        return classify_status(getattr(exc, "status_code", None))
    if isinstance(exc, groq.APIResponseValidationError):
        # The vendor answered in a shape the SDK could not parse.
        return FailureKind.PROTOCOL
    return FailureKind.UNAVAILABLE


def chunk_text(chunk: Any) -> str:
    """The text of one vendor chunk, or `""`.

    Defensive by design rather than by superstition: a role-only or finish-only chunk genuinely
    has `delta.content is None`, and a chunk with no choices genuinely arrives (Groq sends one
    carrying usage after the final choice). Neither is an error, so neither raises."""
    choices = getattr(chunk, "choices", None) or ()
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    text = getattr(delta, "content", None) if delta is not None else None
    return text if isinstance(text, str) else ""


def chunk_finished(chunk: Any) -> bool:
    """Whether this vendor chunk states that generation stopped.

    Used only to tell a completed generation from a **truncated** one. A stream that simply stops
    arriving without any finish reason is a connection that died mid-sentence, and reporting that
    as a clean completion would hand the caller half an answer as though it were the whole one."""
    choices = getattr(chunk, "choices", None) or ()
    return any(getattr(choice, "finish_reason", None) for choice in choices)


class _GroqStream(CompletionStream):
    """One open Groq completion. Owns the vendor stream and closes it on every path."""

    __slots__ = ("_stream", "_closed")

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[CompletionChunk]:
        finished = False
        try:
            async for raw in self._stream:
                if chunk_finished(raw):
                    finished = True
                yield CompletionChunk(text=chunk_text(raw))
        except CompletionStreamError:
            raise
        except BaseException as exc:
            # `CancelledError` and `GeneratorExit` are BaseExceptions and must pass through
            # untouched: the caller withdrawing is not the vendor failing (ADR-0046 rule 2).
            if not isinstance(exc, Exception):
                raise
            raise CompletionStreamError(
                classify_exception(exc), "the completion stream failed"
            ) from None
        if not finished:
            raise CompletionStreamError(
                FailureKind.TRUNCATED, "the completion ended without a finish reason"
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.close()
        except Exception:  # noqa: BLE001 — closing is best-effort by contract; a vendor that
            # fails to close cleanly must not turn a completed turn into a failed one.
            pass


class GroqCompletionClient(CompletionClient):
    """Opens Groq completions with **one injected credential** (ADR-0049 CR4).

    The key is name-mangled and never rendered: `__repr__` is overridden because a dataclass-style
    default repr on a configuration object is one of the two ways credentials reach logs (the
    other is an exception message, handled by never passing vendor text through)."""

    __slots__ = ("_GroqCompletionClient__api_key", "_timeout_s", "_client")

    def __init__(self, api_key: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not api_key:
            raise ValueError("a groq api key is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.__api_key = api_key
        self._timeout_s = timeout_s
        self._client: Any | None = None

    def __repr__(self) -> str:
        return f"GroqCompletionClient(timeout_s={self._timeout_s})"

    async def open(self, spec: CompletionSpec) -> CompletionStream:
        client = self._ensure_client()
        try:
            stream = await client.chat.completions.create(
                messages=[{"role": role, "content": content} for role, content in spec.messages],
                model=spec.model,
                stream=True,
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise  # cancellation withdraws; it is not a vendor failure
            raise CompletionStreamError(
                classify_exception(exc), "the completion could not be opened"
            ) from None
        return _GroqStream(stream)

    def _ensure_client(self) -> Any:
        """Construct the vendor client on first use.

        Lazy because importing the SDK costs a package import a process that never selects this
        profile should not pay, and because construction is the first thing that can fail on a
        host where the library is absent."""
        if self._client is None:
            try:
                import groq
            except ImportError:
                raise CompletionStreamError(
                    FailureKind.UNAVAILABLE, "the groq package is not installed"
                ) from None
            self._client = groq.AsyncGroq(
                api_key=self.__api_key, timeout=self._timeout_s, max_retries=0
            )
        return self._client


def groq_available() -> bool:
    """Whether the vendor library can be imported on this host.

    Called by the composition root so a misconfigured host fails at startup rather than on the
    first caller's first sentence."""
    try:
        import groq  # noqa: F401
    except ImportError:
        return False
    return True


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "GroqCompletionClient",
    "chunk_finished",
    "chunk_text",
    "classify_exception",
    "classify_status",
    "groq_available",
]
