"""Production voice provider adapters (M8.14).

Each implements the M8.12 `VoiceAdapter` interface, translating a synthesize/transcribe request
into that provider's request shape via the injected `Transport`. **Provider-specific code stays
here.** In M8.14 the transport is deterministic (offline), so these return synthesized/transcribed
**metadata** — real audio bytes are a production-transport concern. No provider SDK is imported.
"""

from __future__ import annotations

from typing import Any

from api.foundation import VoiceAdapter

from .transport import ProviderRequest, Transport


class BaseVoiceAdapter(VoiceAdapter):
    provider = "base"
    _tts_url = ""
    _stt_url = ""

    def __init__(
        self, transport: Transport, *, voice: str = "", api_key: str = "",
    ) -> None:
        self._transport = transport
        self._voice = voice
        self._api_key = api_key

    def synthesize(self, text: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        req = ProviderRequest(
            provider=self.provider, method="POST", url=self._tts_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            body={"text": text, "voice": options.get("voice_name", self._voice)},
        )
        resp = self._transport.request(req)
        return {
            "provider": self.provider,
            "voice_name": options.get("voice_name", self._voice),
            "chars": len(text),
            "estimated_duration_ms": len(text) * 10,
            "latency_ms": resp.latency_ms,
            "audio": None,  # metadata only in M8.14
        }

    def transcribe(self, audio: Any, *, options: dict[str, Any] | None = None) -> str:
        req = ProviderRequest(
            provider=self.provider, method="POST", url=self._stt_url,
            headers={"Authorization": f"Bearer {self._api_key}"}, body={"audio": "<bytes>"},
        )
        resp = self._transport.request(req)
        return resp.json.get("transcript", f"[{self.provider}-transcript]")


class DeepgramVoiceAdapter(BaseVoiceAdapter):
    provider = "deepgram"
    _tts_url = "https://api.deepgram.com/v1/speak"
    _stt_url = "https://api.deepgram.com/v1/listen"


class ElevenLabsVoiceAdapter(BaseVoiceAdapter):
    provider = "elevenlabs"
    _tts_url = "https://api.elevenlabs.io/v1/text-to-speech"
    _stt_url = "https://api.elevenlabs.io/v1/speech-to-text"


class CartesiaVoiceAdapter(BaseVoiceAdapter):
    provider = "cartesia"
    _tts_url = "https://api.cartesia.ai/tts/bytes"
    _stt_url = "https://api.cartesia.ai/stt"


class OpenAIVoiceAdapter(BaseVoiceAdapter):
    provider = "openai_voice"
    _tts_url = "https://api.openai.com/v1/audio/speech"
    _stt_url = "https://api.openai.com/v1/audio/transcriptions"


VOICE_PROVIDERS: dict[str, type[BaseVoiceAdapter]] = {
    "deepgram": DeepgramVoiceAdapter,
    "elevenlabs": ElevenLabsVoiceAdapter,
    "cartesia": CartesiaVoiceAdapter,
    "openai_voice": OpenAIVoiceAdapter,
}


def build_voice_adapter(
    provider: str, transport: Transport, *, voice: str = "", api_key: str = "",
) -> BaseVoiceAdapter:
    cls = VOICE_PROVIDERS.get(provider)
    if cls is None:
        raise KeyError(f"unknown voice provider {provider!r}")
    return cls(transport, voice=voice, api_key=api_key)
