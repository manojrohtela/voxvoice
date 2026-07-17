"""LocalSTT — deterministic in-process STT for local dev + tests (M2C.2).

Implements the `STTProvider` contract. For the local-dev profile an audio frame
carries its transcript as UTF-8 bytes, so transcription is deterministic and
network-free; each non-empty frame is one completed utterance (a Partial then a
Final). A real streaming STT vendor is a drop-in replacement behind the contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.audio import AudioFrame
from core.providers.stt import Final, Partial, STTCapabilities, STTProvider


class LocalSTT(STTProvider):
    name = "local"
    capabilities = STTCapabilities(streaming=True, partials=True, languages=("en",))

    async def transcribe(
        self, frames: AsyncIterator[AudioFrame], *, language: str, sample_rate: int
    ) -> AsyncIterator[Partial | Final]:
        async for frame in frames:
            text = frame.data.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            yield Partial(text=text, stability=1.0)
            yield Final(text=text)
