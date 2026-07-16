"""Adapter contract (SPEC §5). Guards the swap-the-engine boundary:
- the factory honours ENGINE,
- the LiveKit fallback is a loud stub (no silent fakes),
- any adapter conforms to the interface and dispatches transcripts.
"""

from __future__ import annotations

import pytest

from adapters import EngineAdapter, get_adapter
from adapters.base import EngineSession, WebConnInfo
from adapters.dograh import DograhAdapter
from adapters.factory import build_adapter
from adapters.livekit import LiveKitAdapter


def test_factory_returns_dograh_by_default(monkeypatch):
    # ENGINE=dograh is set in conftest; the built adapter must be Dograh.
    assert isinstance(build_adapter(), DograhAdapter)


def test_unknown_engine_raises():
    import adapters.factory as f
    from api.config import get_settings

    get_settings.cache_clear()
    import os

    old = os.environ.get("ENGINE")
    os.environ["ENGINE"] = "bogus"
    try:
        with pytest.raises(ValueError):
            f.build_adapter()
    finally:
        if old is not None:
            os.environ["ENGINE"] = old
        else:
            del os.environ["ENGINE"]
        get_settings.cache_clear()


def test_livekit_is_a_loud_stub():
    lk = LiveKitAdapter()
    assert isinstance(lk, EngineAdapter)


@pytest.mark.asyncio
async def test_livekit_methods_not_implemented():
    lk = LiveKitAdapter()
    with pytest.raises(NotImplementedError):
        await lk.start_session(None, None)
    with pytest.raises(NotImplementedError):
        await lk.end_session(None)


@pytest.mark.asyncio
async def test_active_adapter_dispatches_transcript():
    """on_transcript -> dispatch_transcript wiring works for the active adapter
    (the FakeAdapter installed by conftest, which uses the base storage)."""
    adapter = get_adapter()
    received: list[tuple] = []

    async def handler(engine_ref, role, text, ts):
        received.append((engine_ref, role, text, ts))

    await adapter.on_transcript(handler)
    await adapter.dispatch_transcript("ref1", "user", "hello", 0.5)
    assert received == [("ref1", "user", "hello", 0.5)]


def test_dograh_node_graph_shape():
    """Lock the live-validated Dograh workflow graph (nodes + edges with
    data:{label,condition}). Mirrors what passed /workflow/{id}/validate."""
    from types import SimpleNamespace

    adapter = DograhAdapter(base_url="http://x", api_key="k")
    agent = SimpleNamespace(
        name="Bot", system_prompt="be Sarah", first_message="Hi!",
        allow_interruptions=True,
    )
    defn = adapter._agent_to_workflow_definition(agent)
    types = [n["type"] for n in defn["nodes"]]
    assert types == ["globalNode", "startCall", "agentNode", "endCall"]
    # global node carries our system prompt; startCall speaks first_message.
    assert defn["nodes"][0]["data"]["prompt"] == "be Sarah"
    assert defn["nodes"][1]["data"]["greeting"] == "Hi!"
    # every edge has the required data shape.
    for e in defn["edges"]:
        assert set(e["data"]) == {"label", "condition"}


def test_engine_session_shape():
    es = EngineSession(
        engine_ref="r", web=WebConnInfo(token="t", url="u", room="r")
    )
    assert es.phone is None
    assert es.web.token == "t"
