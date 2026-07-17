"""Local provider profile unit tests (M2C.2).

The local providers implement the refined capability contracts fully in-process.
They are first-class local-dev providers (not stubs) — a real vendor is a drop-in
replacement behind the same contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.audio import AudioFrame
from core.providers.stt import Final, Partial
from providers.llm.echo import EchoLLM
from providers.stt.local import LocalSTT
from providers.transport.local import LocalTransport
from providers.tts.local import LocalTTS


async def _frames(*texts: str) -> AsyncIterator[AudioFrame]:
    for t in texts:
        yield AudioFrame(data=t.encode("utf-8"))


async def test_local_stt_emits_partial_then_final_per_utterance():
    stream = LocalSTT().transcribe(_frames("hello", "bye"), language="en", sample_rate=16000)
    results = [r async for r in stream]
    assert results == [
        Partial(text="hello", stability=1.0),
        Final(text="hello"),
        Partial(text="bye", stability=1.0),
        Final(text="bye"),
    ]


async def test_local_stt_skips_empty_frames():
    stream = LocalSTT().transcribe(_frames("", "  "), language="en", sample_rate=16000)
    assert [r async for r in stream] == []


async def test_echo_llm_streams_response_from_last_user_message():
    context = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hello there"},
    ]
    tokens = [d.text async for d in EchoLLM().stream(context=context)]
    assert "".join(tokens).strip() == "You said: hello there"


async def test_echo_llm_default_when_no_user_message():
    tokens = [d.text async for d in EchoLLM().stream(context=[{"role": "system", "content": "x"}])]
    assert "".join(tokens).strip() == "Hello, how can I help?"


async def test_local_tts_synthesizes_text_to_frames():
    frames = [f async for f in LocalTTS().synthesize("hi there")]
    assert [f.data.decode() for f in frames] == ["hi there"]


async def test_local_transport_loopback_and_hangup():
    t = LocalTransport()
    await t.start(session_id="s1")
    await t.feed("s1", AudioFrame(data=b"one"))
    await t.feed("s1", AudioFrame(data=b"two"))
    await t.hangup("s1")
    received = [f.data.decode() async for f in t.inbound(session_id="s1")]
    assert received == ["one", "two"]
    await t.send(session_id="s1", frame=AudioFrame(data=b"reply"))
    assert [f.data.decode() for f in t.sent("s1")] == ["reply"]


async def test_local_transport_isolates_sessions():
    t = LocalTransport()
    await t.start(session_id="a")
    await t.start(session_id="b")
    await t.send(session_id="a", frame=AudioFrame(data=b"for-a"))
    assert [f.data.decode() for f in t.sent("a")] == ["for-a"]
    assert t.sent("b") == []
