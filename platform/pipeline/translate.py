"""Prompt → Reasoning translation (M5.7) — the composition-root seam.

Every closed-runtime design doc anticipated this exact hop: Reasoning's `InferenceRequest`
docstring says it is "built by the composition-root `PromptConsumer` bridge from a
`PromptPackage`." This is that bridge — a pure, deterministic mapping function (not a new
runtime, not new business logic): `PromptPackage.messages` project 1:1 onto
`InferenceRequest.messages` (`PromptRole`/`ChunkKind`-style string values already match), and
`prompt_hash`/`prompt_fingerprint`/`generation` carry through unchanged. Neither `prompt` nor
`reasoning` imports the other; only this composition-root module imports both.
"""

from __future__ import annotations

from prompt.domain import PromptPackage
from reasoning.domain import (
    GenerationParameters as ReasoningGenerationParameters,
)
from reasoning.domain import (
    InferenceMessage,
    InferenceRequest,
    ReasoningScope,
)


def prompt_package_to_inference_request(
    package: PromptPackage,
    *,
    provider: str,
    model: str | None = None,
    streaming: bool = True,
    cancellation_token: str | None = None,
) -> InferenceRequest:
    """Project a finished `PromptPackage` onto the neutral `InferenceRequest` Reasoning
    consumes. `provider` names the `ModelProvider` registered in Reasoning's own registry
    (e.g. the `ProviderModelBridge`'s name) — a different namespace from the Provider
    Runtime's own adapter names (see `bridge.ProviderModelBridge._to_provider_request`)."""
    gen = package.generation
    return InferenceRequest(
        session_id=package.session_id,
        messages=tuple(
            InferenceMessage(role=m.role.value, content=m.content, metadata=dict(m.metadata))
            for m in package.messages
        ),
        provider=provider,
        model=model or "default",
        generation=ReasoningGenerationParameters(
            temperature=gen.temperature, max_tokens=gen.max_tokens, top_p=gen.top_p,
            stop=gen.stop, seed=gen.seed, extra=dict(gen.extra),
        ),
        prompt_hash=package.prompt_hash,
        prompt_fingerprint=package.prompt_fingerprint,
        streaming=streaming,
        cancellation_token=cancellation_token,
        scope=ReasoningScope(
            org_id=package.scope.org_id, conversation_id=package.scope.conversation_id,
            session_id=package.scope.session_id,
        ),
    )
