"""RuntimePipeline end-to-end tests (M2C.2) — the first complete conversation.

Drives a real multi-turn conversation through the full pipeline (transport → STT
→ LLM → TTS → transport) using the local provider profile. This is the milestone's
headline: VoxVoice executes a complete voice conversation through the Runtime
Pipeline without bypassing any architectural boundary.
"""

from __future__ import annotations

import asyncio

import pytest

from core.audio import AudioFrame
from providers.llm.echo import EchoLLM
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.local import LocalTTS
from runtime.pipeline.pipeline import RuntimePipeline


def _pipeline(transport, transcripts, session_id="ses_1"):
    return RuntimePipeline(
        transport=transport,
        stt=LocalSTT(),
        llm=EchoLLM(),
        tts=LocalTTS(),
        session_id=session_id,
        system_prompt="be helpful",
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )


async def test_full_multi_turn_conversation():
    transport = LocalTransport()
    transcripts: list[tuple[str, str, bool]] = []
    pipe = _pipeline(transport, transcripts)

    await pipe.start()
    run_task = asyncio.create_task(pipe.run())

    await transport.feed("ses_1", AudioFrame(data=b"hello"))
    await transport.feed("ses_1", AudioFrame(data=b"how are you"))
    await transport.hangup("ses_1")  # caller hangs up → conversation ends

    await asyncio.wait_for(run_task, timeout=2.0)
    await pipe.stop()

    finals = [(r, t) for (r, t, f) in transcripts if f]
    # Each user utterance produced a user final + a streamed agent response.
    assert ("user", "hello") in finals
    assert ("agent", "You said: hello") in finals
    assert ("user", "how are you") in finals
    assert ("agent", "You said: how are you") in finals

    # The agent's speech was played back to the caller through the transport.
    spoken = [f.data.decode() for f in transport.sent("ses_1")]
    assert spoken == ["You said: hello", "You said: how are you"]


async def test_partial_transcripts_are_emitted():
    transport = LocalTransport()
    transcripts: list[tuple[str, str, bool]] = []
    pipe = _pipeline(transport, transcripts)
    await pipe.start()
    run_task = asyncio.create_task(pipe.run())
    await transport.feed("ses_1", AudioFrame(data=b"hi"))
    await transport.hangup("ses_1")
    await asyncio.wait_for(run_task, timeout=2.0)
    await pipe.stop()
    assert ("user", "hi", False) in transcripts  # a partial
    assert ("user", "hi", True) in transcripts  # a final


async def test_pipeline_stop_ends_conversation_promptly():
    transport = LocalTransport()
    pipe = _pipeline(transport, [])
    await pipe.start()
    run_task = asyncio.create_task(pipe.run())
    await asyncio.sleep(0)  # let run() start awaiting inbound
    await pipe.stop()  # stop → transport inbound ends → run() returns
    await asyncio.wait_for(run_task, timeout=2.0)
    assert run_task.done()


async def test_pipeline_propagates_stt_failure():
    class BoomSTT(LocalSTT):
        def transcribe(self, frames, *, language, sample_rate):
            raise RuntimeError("stt boom")

    transport = LocalTransport()
    pipe = RuntimePipeline(
        transport=transport, stt=BoomSTT(), llm=EchoLLM(), tts=LocalTTS(),
        session_id="ses_1", system_prompt="x",
    )
    await pipe.start()
    with pytest.raises(RuntimeError, match="stt boom"):
        await pipe.run()
    await pipe.stop()
