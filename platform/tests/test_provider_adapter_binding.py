"""Provider Adapter Binding vertical slice (Epic 8, M8.14) tests.

Covers the new `api.adapters` layer + the execution binding end-to-end: the deterministic
transport seam, all five **LLM** providers (real request-shaping + response-parsing, offline),
the four **voice** providers, the six **tool** executors, resilience (retry / circuit-breaker /
rate-limit / cancellation), the `ProviderExecutionEngine` (execute + **streaming**), the durable
**message store** (memory adapter), and the full Control Plane API (the Definition-of-Done flow)
with audit + isolation + determinism. **No network, no provider SDK, no real API call.**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.adapters import (
    LLM_PROVIDERS,
    VOICE_PROVIDERS,
    CircuitBreaker,
    EchoTransport,
    FailingTransport,
    ProviderUnavailableError,
    RateLimiter,
    build_execution_engine,
    build_llm_adapter,
    build_voice_adapter,
    default_executors,
)
from api.adapters.resilience import CancelToken, RetrySpec, execute_with_resilience
from api.adapters.tools import ToolExecutorDispatcher
from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    FixedClock,
    InMemoryConversationMessageRepository,
    SequentialIdGenerator,
)

# ── adapter unit tests (offline, deterministic) ──────────────────────────────────


def test_all_llm_providers_translate_and_parse():
    assert set(LLM_PROVIDERS) == {
        "anthropic", "openai", "google_gemini", "groq", "azure_openai"
    }
    for name in LLM_PROVIDERS:
        adapter = build_llm_adapter(name, EchoTransport(), model="m", api_key="k")
        out = adapter.complete("a prompt of some length", options={"temperature": 0.5})
        assert out["provider"] == name
        assert out["token_usage"]["input_tokens"] > 0
        assert out["text"] and out["latency_ms"] > 0
    # identical input → identical output (deterministic)
    a = build_llm_adapter("anthropic", EchoTransport(), model="m").complete("x", options={})
    b = build_llm_adapter("anthropic", EchoTransport(), model="m").complete("x", options={})
    assert a == b


def test_all_voice_providers_metadata_only():
    assert set(VOICE_PROVIDERS) == {"deepgram", "elevenlabs", "cartesia", "openai_voice"}
    for name in VOICE_PROVIDERS:
        v = build_voice_adapter(name, EchoTransport(), voice="nova")
        out = v.synthesize("hello world", options={"voice_name": "nova"})
        assert out["provider"] == name and out["audio"] is None
        assert isinstance(v.transcribe(b"", options={}), str)


def test_all_tool_categories_execute():
    executors = default_executors(EchoTransport())
    for category in ("http_api", "webhook", "function", "database", "mcp", "custom", "knowledge"):
        assert category in executors
    disp = ToolExecutorDispatcher(executors)
    for category in ("http_api", "webhook", "function", "database", "mcp", "custom"):
        step = {"tool_id": f"t_{category}", "category": category, "config": {"url": "https://x"}}
        result = disp.invoke_step(step, arguments={"input": "hi"})
        assert result["status"] == "ok" and result["tool_id"] == f"t_{category}"


def test_resilience_retry_breaker_ratelimit_cancel():
    # retry exhaustion → unavailable
    calls = {"n": 0}

    def failing():
        calls["n"] += 1
        raise __import__("api.adapters.transport", fromlist=["TransportError"]).TransportError("x")

    with pytest.raises(ProviderUnavailableError):
        execute_with_resilience(failing, retry=RetrySpec(max_retries=2))
    assert calls["n"] == 3  # initial + 2 retries
    # circuit breaker opens and blocks
    cb = CircuitBreaker(threshold=1)
    cb.record_failure()
    with pytest.raises(ProviderUnavailableError):
        execute_with_resilience(lambda: 1, breaker=cb)
    # rate limit
    rl = RateLimiter(capacity=1, tokens=0)
    with pytest.raises(ProviderUnavailableError):
        execute_with_resilience(lambda: 1, limiter=rl)
    # cancellation
    from api.adapters import ExecutionCancelledError
    tok = CancelToken(cancelled=True)
    with pytest.raises(ExecutionCancelledError):
        execute_with_resilience(lambda: 1, cancel=tok)


def test_engine_execute_and_stream_offline():
    from api.foundation import (
        ConversationState,
        ExecutionPlan,
        ResolvedPrompt,
        ToolPlanStep,
    )
    state = ConversationState(
        resolved_prompt=ResolvedPrompt(text="[SYSTEM] hi"),
        tool_plan=ExecutionPlan(
            steps=(ToolPlanStep(0, "tool_1", "HTTP", "http_api", True, {"url": "u"}),),
            required_adapters={"llm": True, "voice": False, "tool_executor": True},
        ),
    )
    engine = build_execution_engine(
        InMemoryConversationMessageRepository(), transport=EchoTransport()
    )
    opts = {"model": {"provider": "openai", "model": "gpt-4o"}, "voice": {}}
    r = engine.execute(state, "hello", options=opts)
    assert r.provider_metadata["provider"] == "openai" and r.token_usage.total > 0
    assert [tr["tool_id"] for tr in r.tool_results] == ["tool_1"]
    events = list(engine.stream(state, "hello", options=opts))
    types = {e.type.value for e in events}
    assert "token" in types and "tool" in types and "done" in types
    # provider failure raises through the engine
    failing = build_execution_engine(
        InMemoryConversationMessageRepository(), transport=FailingTransport()
    )
    with pytest.raises(ProviderUnavailableError):
        failing.execute(state, "x", options=opts)


# ── API + persistence integration (Definition of Done) ───────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _bootstrap_conversation(c):
    owner = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()["id"]
    tok = c.post("/v1/auth/token", json={"user_id": owner}).json()["access_token"]
    oh = {"Authorization": f"Bearer {tok}"}
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    wid = c.post(
        f"/v1/organizations/{oid}/workspaces", json={"name": "T", "slug": "t"}, headers=oh
    ).json()["id"]
    pid = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects",
        json={"name": "P", "slug": "p"}, headers=oh,
    ).json()["id"]
    ab = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
    aid = c.post(ab, json={
        "name": "Bot",
        "config": {"prompt": {"system_prompt": "Hi {{company_name}}"},
                   "model": {"provider": "anthropic", "model": "claude"}},
    }, headers=oh).json()["id"]
    t = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools",
        json={"name": "HTTP", "category": "http_api"}, headers=oh,
    ).json()["id"]
    c.post(f"{ab}/{aid}/tools", json={"tool_id": t}, headers=oh)
    c.post(f"{ab}/{aid}/publish", json={"description": "v1"}, headers=oh)
    pg = f"{ab}/{aid}/playground/conversations"
    sid = c.post(pg, json={"runtime_variables": {"company_name": "Acme"}}, headers=oh).json()["id"]
    return oh, oid, wid, pid, f"{pg}/{sid}"


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, conv = _bootstrap_conversation(c)  # 1-2. publish + start
        # 3. execute through a real provider
        r = c.post(f"{conv}/execute", json={"user_input": "hello"}, headers=oh)
        assert r.status_code == 200
        body = r.json()
        assert body["provider_metadata"]["provider"] == "anthropic"
        assert body["token_usage"]["total_tokens"] > 0 and body["latency_ms"] > 0
        # 5. tools executed
        assert [tr["tool_id"] for tr in body["tool_results"]] == ["tool_1"]
        # 4. stream responses
        sr = c.post(f"{conv}/stream", json={"user_input": "again"}, headers=oh)
        assert sr.status_code == 200
        lines = [ln for ln in sr.text.strip().split("\n") if ln]
        import json as _json
        types = {_json.loads(ln)["type"] for ln in lines}
        assert "token" in types and "done" in types
        # 6. persist messages (durable memory)
        msgs = c.get(f"{conv}/messages", headers=oh).json()
        assert len(msgs) >= 4 and {m["role"] for m in msgs} >= {"user", "assistant"}
        # 7. resume (the conversation session is still active)
        sid = conv.rsplit("/", 1)[1]
        base = conv.rsplit("/playground/", 1)[0]
        assert c.post(f"{base}/sessions/{sid}/resume", headers=oh).json()["status"] == "active"
        # 8. inspect provider metadata; 9. execution diagnostics
        diag = c.get(f"{conv}/provider", headers=oh).json()
        assert diag["llm"]["provider"] == "anthropic" and diag["circuit_breaker_open"] is False


def test_real_execution_audited():
    with _client() as c:
        oh, oid, wid, pid, conv = _bootstrap_conversation(c)
        c.post(f"{conv}/execute", json={"user_input": "hi"}, headers=oh)
        act = c.get(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/activity", headers=oh
        ).json()
        types = {f["type"] for f in act["feed"]}
        assert {"execution.started", "provider.selected", "tool.executed",
                "execution.completed"} <= types


def test_isolation_cross_org_and_viewer():
    with _client() as c:
        oh, oid, wid, pid, conv = _bootstrap_conversation(c)
        # cross-org owner cannot execute here (403)
        b = c.post("/v1/auth/register", json={"email": "b@x.io", "display_name": "B"}).json()["id"]
        btok = c.post("/v1/auth/token", json={"user_id": b}).json()["access_token"]
        bh = {"Authorization": f"Bearer {btok}"}
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        assert c.post(f"{conv}/execute", json={"user_input": "x"}, headers=bh).status_code == 403
        # viewer cannot execute (needs PROJECT_MANAGE)
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": v, "tier": "viewer"}, headers=oh)
        vtok = c.post("/v1/auth/token", json={"user_id": v}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        assert c.post(f"{conv}/execute", json={"user_input": "x"}, headers=vh).status_code == 403


def test_execution_is_deterministic():
    def run():
        with _client() as c:
            oh, oid, wid, pid, conv = _bootstrap_conversation(c)
            r = c.post(f"{conv}/execute", json={"user_input": "hello"}, headers=oh).json()
            return (r["assistant_response"], r["token_usage"], r["provider_metadata"])

    assert run() == run()


def test_studio_ui_serves_provider_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in ("Streaming View", "Provider Inspector", "Token Usage", "Latency",
                       "Retry Timeline", "Execution Timeline", "Provider Diagnostics"):
            assert marker in r.text, marker
