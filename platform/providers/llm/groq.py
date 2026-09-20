"""`GroqLLM` — real streaming LLM behind the unchanged `LLMProvider` contract (M11.9).

The provider is **first-party and library-free**. It owns the context translation, the chunk
normalization, the failure taxonomy and the stream lifecycle; the vendor SDK is reached only
through the `CompletionClient` seam, whose one Groq implementation lives in `groq_client.py`
(AR-12, P-ADR-0002 §3 — Groq is the bound low-latency LLM).

## Genuinely incremental

`stream()` yields a `TokenDelta` for each piece of text the vendor produces, as it produces it.
There is no accumulation, no buffering and no reconstruction of streaming from a completed
response — ADR-0048 **PB2** forbids reintroducing a wait-for-whole-input step, and a provider that
gathered the answer before yielding it would put that step back one layer down where no pipeline
test could see it.

Empty chunks are **dropped, not yielded**. Vendors emit role-only and finish-only chunks carrying
no text, and a `TokenDelta(text="")` is not a token — it would make "the model has started
producing text" untrue at the `llm_first_token` tap.

## What is never invented

- **No text is altered.** Not stripped, not joined, not re-punctuated. Whether text forms a clause
  is the runtime's decision (`runtime/speech/clauses.py`), not a vendor plugin's.
- **No tool calls.** M11.9 does not implement tool calling, so `tools` is declared `False` and a
  caller that passes tools is **refused** rather than silently served a tool-free answer.
- **No structured output, no reasoning, no multilingual claim.** Only what is verified is declared.

## Failures

Every failure leaves as a `core.errors` type (ADR-0046, ADR-0049 CR6):

| cause | error |
|---|---|
| request deadline exceeded | `ProviderTimeout` |
| vendor unreachable, 5xx, unexpected failure | `ProviderUnavailable` |
| rejected credential | `ProviderAuthError` |
| rate limited or out of quota | `ProviderRateLimited` |
| unknown model, malformed request, unparseable answer | `ProviderProtocolError` |
| generation stopped without a finish reason | `ProviderStreamTruncated` |
| an undeclared capability was requested | `CapabilityUnsupported` |

`asyncio.CancelledError` is never translated: the caller withdrawing is not the vendor failing.
Breaker accounting, per-item deadlines and degradation recording belong to `ProviderGuard`, which
the pipeline already wraps around every `stream()` call — the provider does not duplicate them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from core.errors import (
    CapabilityUnsupported,
    ProviderAuthError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.llm import LLMCapabilities, LLMProvider, TokenDelta, ToolCallDelta

from .completion_stream import (
    CompletionClient,
    CompletionSpec,
    CompletionStreamError,
    FailureKind,
)

#: The model requested when the session selects none. Groq's production Llama model; named here
#: rather than in `runtime/` because a model identifier is vendor vocabulary (AR-21).
DEFAULT_MODEL = "llama-3.3-70b-versatile"

#: The context window **declared** for `DEFAULT_MODEL`, from the vendor's published model card.
#: It is a declared configuration value, not a measured one: no token was counted to establish it,
#: and it is a constructor argument precisely so an operator running a different model states the
#: right number instead of inheriting this one silently.
DEFAULT_CONTEXT_WINDOW = 131_072

#: Roles the neutral context may carry. A context is assembled by the runtime, so an unknown role
#: is a runtime defect rather than a caller error — but it is refused here too, because sending it
#: on would let the vendor decide what an unrecognized role means.
SUPPORTED_ROLES = frozenset({"system", "user", "assistant"})

_TYPED: dict[FailureKind, type[ProviderError]] = {
    FailureKind.TIMEOUT: ProviderTimeout,
    FailureKind.UNAVAILABLE: ProviderUnavailable,
    FailureKind.AUTH: ProviderAuthError,
    FailureKind.RATE_LIMITED: ProviderRateLimited,
    FailureKind.PROTOCOL: ProviderProtocolError,
    FailureKind.TRUNCATED: ProviderStreamTruncated,
}


def typed_error(exc: CompletionStreamError) -> ProviderError:
    """Translate a neutral stream failure into the platform taxonomy."""
    error_type = _TYPED.get(exc.kind, ProviderUnavailable)
    return error_type(f"groq llm: {exc.detail or exc.kind.value}")


def normalized_messages(context: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    """Translate the runtime's neutral context into the seam's shape.

    Validated rather than trusted: an empty context produces a confident answer to nothing, and a
    message whose content is not text is a defect that would otherwise surface as a vendor 400
    with a vendor message attached."""
    if not context:
        raise ProviderProtocolError("groq llm: the context is empty")
    messages: list[tuple[str, str]] = []
    for message in context:
        role = message.get("role")
        content = message.get("content")
        if role not in SUPPORTED_ROLES:
            raise ProviderProtocolError(f"groq llm: unsupported message role {role!r}")
        if not isinstance(content, str):
            raise ProviderProtocolError(f"groq llm: message content for {role!r} is not text")
        messages.append((role, content))
    return tuple(messages)


class GroqLLM(LLMProvider):
    """Streaming LLM over an injected `CompletionClient`."""

    name = "groq"

    def __init__(
        self,
        client: CompletionClient,
        *,
        model: str = DEFAULT_MODEL,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ) -> None:
        if not isinstance(client, CompletionClient):
            raise TypeError("GroqLLM requires a CompletionClient")
        if not model:
            raise ValueError("a model is required")
        if context_window <= 0:
            raise ValueError("context_window must be positive")
        self._client = client
        self._model = model
        self.capabilities = LLMCapabilities(
            # Both False because neither is implemented here, and a capability flag is read by the
            # runtime as a promise. Declaring `tools=True` for a provider that refuses tools would
            # be the "truthful capability flags" defect ADR-0045 SD9 names.
            tools=False,
            structured_output=False,
            context_window=context_window,
            # Empty on purpose: the contract defines empty as *"this provider does not enumerate
            # models"*, and the runtime then accepts any selection. Groq serves a catalogue that
            # changes without a release of ours, so a hardcoded whitelist would reject valid models
            # and would be a claim about the vendor's catalogue that nothing here verified.
            models=(),
        )

    async def stream(
        self,
        *,
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[TokenDelta | ToolCallDelta]:
        if tools:
            raise CapabilityUnsupported(
                "groq llm: tool calling is not implemented by this provider "
                "(capabilities.tools is False)"
            )
        spec = CompletionSpec(
            messages=normalized_messages(context), model=model or self._model
        )
        stream = await self._open(spec)
        try:
            try:
                async for chunk in stream:
                    if chunk.text:
                        yield TokenDelta(text=chunk.text)
            except CompletionStreamError as exc:
                raise typed_error(exc) from None
            except Exception:  # noqa: BLE001 — a misbehaving seam still leaves typed (CR6).
                # `CancelledError` and `GeneratorExit` are BaseExceptions and pass straight through.
                raise ProviderStreamTruncated(
                    "groq llm: the completion stream ended unexpectedly"
                ) from None
        finally:
            # Runs on every path — normal end, failure, and the caller closing the generator on
            # cancellation — so an abandoned turn cannot leave a vendor connection open.
            await stream.aclose()

    async def _open(self, spec: CompletionSpec):
        try:
            return await self._client.open(spec)
        except CompletionStreamError as exc:
            raise typed_error(exc) from None
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise  # cancellation during setup withdraws; it is not a vendor failure
            raise ProviderUnavailable("groq llm: the completion could not be opened") from None


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_MODEL",
    "SUPPORTED_ROLES",
    "GroqLLM",
    "normalized_messages",
    "typed_error",
]
