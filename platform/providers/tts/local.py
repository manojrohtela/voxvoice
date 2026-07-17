"""LocalTTS — deterministic in-process TTS for local dev + tests (M2C.2).

Implements the `TTSProvider` contract. For the local-dev profile it emits one
audio frame carrying the utterance text as UTF-8 bytes (a text loopback codec),
so the runtime → caller audio is deterministic and inspectable. A real TTS
vendor is a drop-in replacement behind the contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.audio import AudioFrame
from core.providers.tts import TTSCapabilities, TTSProvider


class LocalTTS(TTSProvider):
    name = "local"
    capabilities = TTSCapabilities(streaming=True, sample_rates=(16000,))

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        yield AudioFrame(data=text.encode("utf-8"), sample_rate=16000)
