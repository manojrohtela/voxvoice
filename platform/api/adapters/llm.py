"""Production LLM provider adapters (M8.14).

Each provider implements the M8.12 `LLMAdapter` interface, translating a resolved prompt into
that provider's request shape (endpoint / auth header / body schema) and parsing its response
(assistant text + token usage). **All provider-specific code lives here** (golden rule:
adapters own translation); the socket is the injected `Transport`, so this real translation runs
offline in tests. No provider SDK is imported.
"""

from __future__ import annotations

from typing import Any

from api.foundation import LLMAdapter

from .transport import ProviderRequest, ProviderResponse, Transport


class BaseLLMAdapter(LLMAdapter):
    """Shared flow: build the provider request → transport → parse. Subclasses override
    `_request` (provider-specific) and `_extract` (provider-specific response parsing)."""

    provider = "base"

    def __init__(
        self, transport: Transport, *, model: str = "", api_key: str = "",
        options: dict[str, Any] | None = None,
    ) -> None:
        self._transport = transport
        self._model = model or self.default_model
        self._api_key = api_key
        self._options = options or {}

    default_model = "default"

    def complete(self, prompt: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        req = self._request(prompt, options)
        resp = self._transport.request(req)
        text, in_tok, out_tok = self._extract(resp)
        return {
            "provider": self.provider,
            "model": self._model,
            "text": text,
            "token_usage": {"input_tokens": in_tok, "output_tokens": out_tok},
            "latency_ms": resp.latency_ms,
        }

    # provider-specific request shaping — overridden per provider
    def _request(self, prompt: str, options: dict[str, Any]) -> ProviderRequest:
        raise NotImplementedError

    # provider-specific response parsing — defensive, with EchoTransport fallback
    def _extract(self, resp: ProviderResponse) -> tuple[str, int, int]:
        j = resp.json
        # EchoTransport fallback shape:
        text = j.get("reply") or resp.text
        usage = j.get("usage", {})
        return text, int(usage.get("input", 0) or 0), int(usage.get("output", 0) or 0)


class AnthropicLLMAdapter(BaseLLMAdapter):
    provider = "anthropic"
    default_model = "claude-3-5-sonnet"

    def _request(self, prompt: str, options: dict[str, Any]) -> ProviderRequest:
        return ProviderRequest(
            provider=self.provider, method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            body={
                "model": self._model,
                "max_tokens": int(options.get("max_tokens", 1024)),
                "temperature": float(options.get("temperature", 0.7)),
                "messages": [{"role": "user", "content": prompt}],
            },
        )

    def _extract(self, resp: ProviderResponse) -> tuple[str, int, int]:
        j = resp.json
        content = j.get("content")
        if isinstance(content, list) and content:
            usage = j.get("usage", {})
            return (
                content[0].get("text", ""),
                int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
            )
        return super()._extract(resp)


class OpenAILLMAdapter(BaseLLMAdapter):
    provider = "openai"
    default_model = "gpt-4o"
    _url = "https://api.openai.com/v1/chat/completions"

    def _request(self, prompt: str, options: dict[str, Any]) -> ProviderRequest:
        return ProviderRequest(
            provider=self.provider, method="POST", url=self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            body={
                "model": self._model,
                "temperature": float(options.get("temperature", 0.7)),
                "messages": [{"role": "user", "content": prompt}],
            },
        )

    def _extract(self, resp: ProviderResponse) -> tuple[str, int, int]:
        j = resp.json
        choices = j.get("choices")
        if isinstance(choices, list) and choices:
            usage = j.get("usage", {})
            return (
                choices[0].get("message", {}).get("content", ""),
                int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)),
            )
        return super()._extract(resp)


class GroqLLMAdapter(OpenAILLMAdapter):
    """Groq exposes an OpenAI-compatible chat-completions API."""

    provider = "groq"
    default_model = "llama-3.1-70b"
    _url = "https://api.groq.com/openai/v1/chat/completions"


class AzureOpenAILLMAdapter(OpenAILLMAdapter):
    """Azure OpenAI — deployment-scoped URL + `api-key` header (not Bearer)."""

    provider = "azure_openai"
    default_model = "gpt-4o"

    def _request(self, prompt: str, options: dict[str, Any]) -> ProviderRequest:
        endpoint = options.get("endpoint", "https://example.openai.azure.com")
        version = options.get("api_version", "2024-02-15-preview")
        return ProviderRequest(
            provider=self.provider, method="POST",
            url=f"{endpoint}/openai/deployments/{self._model}/chat/completions?api-version={version}",
            headers={"api-key": self._api_key},
            body={
                "temperature": float(options.get("temperature", 0.7)),
                "messages": [{"role": "user", "content": prompt}],
            },
        )


class GeminiLLMAdapter(BaseLLMAdapter):
    provider = "google_gemini"
    default_model = "gemini-1.5-pro"

    def _request(self, prompt: str, options: dict[str, Any]) -> ProviderRequest:
        base = "https://generativelanguage.googleapis.com/v1beta/models"
        return ProviderRequest(
            provider=self.provider, method="POST",
            url=f"{base}/{self._model}:generateContent?key={self._api_key}",
            headers={"Content-Type": "application/json"},
            body={"contents": [{"parts": [{"text": prompt}]}]},
        )

    def _extract(self, resp: ProviderResponse) -> tuple[str, int, int]:
        j = resp.json
        candidates = j.get("candidates")
        if isinstance(candidates, list) and candidates:
            parts = candidates[0].get("content", {}).get("parts", [{}])
            meta = j.get("usageMetadata", {})
            return (
                parts[0].get("text", ""),
                int(meta.get("promptTokenCount", 0)), int(meta.get("candidatesTokenCount", 0)),
            )
        return super()._extract(resp)


# The provider registry — name → adapter class. Additive: new providers append here.
LLM_PROVIDERS: dict[str, type[BaseLLMAdapter]] = {
    "anthropic": AnthropicLLMAdapter,
    "openai": OpenAILLMAdapter,
    "google_gemini": GeminiLLMAdapter,
    "groq": GroqLLMAdapter,
    "azure_openai": AzureOpenAILLMAdapter,
}


def build_llm_adapter(
    provider: str, transport: Transport, *, model: str = "", api_key: str = "",
    options: dict[str, Any] | None = None,
) -> BaseLLMAdapter:
    cls = LLM_PROVIDERS.get(provider)
    if cls is None:
        raise KeyError(f"unknown LLM provider {provider!r}")
    return cls(transport, model=model, api_key=api_key, options=options)
