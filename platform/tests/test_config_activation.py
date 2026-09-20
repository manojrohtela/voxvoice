"""Configuration activation and validation (M11.4) — AR-24, AR-25, ADR-0046 FT-D7/FT-D9.

**AR-25:** *"Accepted config must act — every accepted field is honoured by the runtime or the
write is rejected."* **GOVERNANCE §4:** *"Silent acceptance is a release blocker."*

Three fields had been accepted by the Control Plane and read by nobody:

* `allow_interruptions` — so **barge-in was unreachable in production** despite M3.4/M3.5 being
  complete and tested;
* `llm_model` — resolved without a selector while `stt_provider`/`tts_provider` were honoured;
* `first_message` — no greeting has ever been played.

These tests are what stop each of them from silently reverting.

Hermetic: no vendor, no network, no credentials, no sleeps.
"""

from __future__ import annotations

import pytest

from bootstrap import build_local_registry
from core.audio import AudioFrame
from core.errors import ConfigError
from core.providers.llm import LLMCapabilities, LLMProvider, TokenDelta
from core.session_control import StartSessionRequest
from runtime.contracts import AgentSpec, GreetingState
from runtime.faults import (
    MAX_SAMPLE_RATE,
    GuardPolicy,
    greeting_state_for,
    require_valid_agent_spec,
    validate_agent_spec,
)
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService


def _install_llm(registry):
    """Make the recording provider the session's LLM.

    `replace()` swaps at a `(kind, name)` key, so registering under a *different* name would
    simply add a second provider and leave `EchoLLM` as the default — which is why the first
    draft of these tests saw an empty `seen` list. Unregister, then register as default."""
    registry.unregister(CapabilityKind.LLM, "echo")
    llm = _RecordingLLM()
    registry.register(CapabilityKind.LLM, llm, default=True)
    return llm


def _request(**kw) -> StartSessionRequest:
    base = dict(
        session_id="ses_cfg", org_id="o", agent_id="a", channel="web", system_prompt="be helpful"
    )
    base.update(kw)
    return StartSessionRequest(**base)


# ── AR-25 · allow_interruptions ─────────────────────────────────────────────


def test_the_request_carries_the_interruption_choice():
    assert _request().allow_interruptions is False
    assert _request(allow_interruptions=True).allow_interruptions is True


@pytest.mark.asyncio
async def test_allow_interruptions_reaches_agent_spec():
    """**The M11.4 discharge.** Before this the field could not cross the Control↔Runtime seam."""
    service = RuntimeSessionService(build_local_registry(include_voice_activity=True))
    await service.start(_request(session_id="ses_int", allow_interruptions=True))
    worker = service._workers["ses_int"]  # noqa: SLF001 — asserting the wiring itself
    assert worker.orchestrator._context.agent.allow_interrupt is True  # noqa: SLF001
    await service.end("ses_int")


@pytest.mark.asyncio
async def test_allow_interruptions_actually_enables_the_interrupt_path():
    """Not merely carried — *honoured*. The Interrupt engine exists only when it was asked for."""
    service = RuntimeSessionService(build_local_registry(include_voice_activity=True))
    await service.start(_request(session_id="ses_on", allow_interruptions=True))
    on = service._workers["ses_on"].orchestrator  # noqa: SLF001
    assert on.interrupt_engine is not None
    await service.end("ses_on")

    await service.start(_request(session_id="ses_off", allow_interruptions=False))
    off = service._workers["ses_off"].orchestrator  # noqa: SLF001
    assert off.interrupt_engine is None
    await service.end("ses_off")


def test_interruptions_without_a_voice_activity_provider_are_rejected():
    """Accepted-but-inert is what AR-25 forbids: barge-in needs a speech signal to detect."""
    registry = build_local_registry()  # no VAD
    issues = validate_agent_spec(AgentSpec(system_prompt="p", allow_interrupt=True), registry)
    assert [i.field for i in issues] == ["allow_interrupt"]


@pytest.mark.asyncio
async def test_the_local_profile_default_is_unchanged():
    """The 5528 floor depends on the default profile behaving exactly as it did."""
    service = RuntimeSessionService(build_local_registry())
    await service.start(_request(session_id="ses_default"))
    orchestrator = service._workers["ses_default"].orchestrator  # noqa: SLF001
    assert orchestrator.interrupt_engine is None
    await service.end("ses_default")


# ── AR-25 · llm_model ───────────────────────────────────────────────────────


class _RecordingLLM(LLMProvider):
    """Captures the model it was asked for — the only way to prove the value *arrived*."""

    name = "recording"
    capabilities = LLMCapabilities(
        tools=False, structured_output=False, context_window=8192, models=("fast", "smart")
    )

    def __init__(self) -> None:
        self.seen: list[str | None] = []

    async def stream(self, *, context, tools=None, model=None):
        self.seen.append(model)
        yield TokenDelta(text="ok")


@pytest.mark.asyncio
async def test_the_selected_model_reaches_the_provider():
    """**The AR-25 discharge for `llm_model`** — accepted since M2C.2, read by nothing until now."""
    registry = build_local_registry()
    llm = _install_llm(registry)

    service = RuntimeSessionService(registry)
    await service.start(_request(session_id="ses_model", llm_model="smart"))
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    worker = service._workers["ses_model"]  # noqa: SLF001
    await transport.feed("ses_model", AudioFrame(data=b"hello"))
    await transport.hangup("ses_model")
    await worker.wait_closed()
    await service.end("ses_model")

    assert llm.seen == ["smart"], f"the provider never received the selection: {llm.seen}"


@pytest.mark.asyncio
async def test_no_model_selection_passes_none_rather_than_a_guess():
    """`None` means *the provider's own default* — the runtime never invents a model."""
    registry = build_local_registry()
    llm = _install_llm(registry)

    service = RuntimeSessionService(registry)
    await service.start(_request(session_id="ses_nomodel"))
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    worker = service._workers["ses_nomodel"]  # noqa: SLF001
    await transport.feed("ses_nomodel", AudioFrame(data=b"hi"))
    await transport.hangup("ses_nomodel")
    await worker.wait_closed()
    await service.end("ses_nomodel")

    assert llm.seen == [None]


def test_an_unsupported_model_is_rejected_at_the_boundary():
    registry = build_local_registry()
    _install_llm(registry)
    issues = validate_agent_spec(AgentSpec(system_prompt="p", llm_model="nonexistent"), registry)
    assert [i.field for i in issues] == ["llm_model"]


def test_a_provider_that_enumerates_no_models_accepts_any_selection():
    """Capability truth belongs to the provider (AR-22). An empty catalogue is a declaration of
    *"I do not enumerate"* — not licence for the runtime to invent a whitelist."""
    registry = build_local_registry()  # EchoLLM declares no models
    assert validate_agent_spec(AgentSpec(system_prompt="p", llm_model="anything"), registry) == ()


# ── AR-25 · first_message ───────────────────────────────────────────────────


def test_a_configured_greeting_is_declared_pending_not_ignored():
    """M11.4 does not play the greeting — **D-11** gives that to M11.10's Playback Scheduler — but
    it refuses to leave the field unread. `PENDING_PLAYBACK` is an honest state, and deliberately
    **not** a claim that audio was produced."""
    assert greeting_state_for("hello there") is GreetingState.PENDING_PLAYBACK
    assert greeting_state_for(None) is GreetingState.ABSENT
    assert greeting_state_for("   ") is GreetingState.ABSENT


@pytest.mark.asyncio
async def test_the_greeting_state_is_set_from_the_request():
    service = RuntimeSessionService(build_local_registry())
    await service.start(_request(session_id="ses_greet", first_message="Hi!"))
    agent = service._workers["ses_greet"].orchestrator._context.agent  # noqa: SLF001
    assert agent.first_message == "Hi!"
    assert agent.greeting_state is GreetingState.PENDING_PLAYBACK
    await service.end("ses_greet")


def test_a_greeting_that_claims_the_wrong_state_is_rejected():
    """The field and its state must agree, in both directions."""
    registry = build_local_registry()
    ignored = AgentSpec(system_prompt="p", first_message="Hi", greeting_state=GreetingState.ABSENT)
    assert [i.field for i in validate_agent_spec(ignored, registry)] == ["first_message"]

    empty = AgentSpec(system_prompt="p", greeting_state=GreetingState.PENDING_PLAYBACK)
    assert [i.field for i in validate_agent_spec(empty, registry)] == ["greeting_state"]


def test_m11_4_does_not_claim_the_greeting_was_played():
    """The honesty guard: no state in this milestone asserts audio happened."""
    assert {s.value for s in GreetingState} == {"absent", "pending_playback"}


# ── config validation ───────────────────────────────────────────────────────


def test_an_out_of_range_sample_rate_is_rejected():
    registry = build_local_registry()
    issues = validate_agent_spec(AgentSpec(system_prompt="p", sample_rate=1), registry)
    assert [i.field for i in issues] == ["sample_rate"]
    top = AgentSpec(system_prompt="p", sample_rate=MAX_SAMPLE_RATE)
    assert validate_agent_spec(top, registry) == ()


def test_an_unregistered_provider_selection_is_rejected():
    registry = build_local_registry()
    issues = validate_agent_spec(AgentSpec(system_prompt="p", stt_provider="nope"), registry)
    assert [i.field for i in issues] == ["stt_provider"]


def test_default_selectors_resolve_to_the_registry_default():
    registry = build_local_registry()
    assert validate_agent_spec(AgentSpec(system_prompt="p", stt_provider="default"), registry) == ()


def test_an_invalid_breaker_policy_is_rejected():
    from provider.circuit_breaker import CircuitBreakerPolicy

    registry = build_local_registry()
    policy = GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=0))
    issues = validate_agent_spec(AgentSpec(system_prompt="p"), registry, policy=policy)
    assert [i.field for i in issues] == ["breaker.failure_threshold"]


def test_every_issue_is_reported_not_just_the_first():
    """A validator that stops at the first problem makes fixing a config a guessing game."""
    registry = build_local_registry()
    bad = AgentSpec(
        system_prompt="p", sample_rate=1, stt_provider="nope", allow_interrupt=True
    )
    assert len(validate_agent_spec(bad, registry)) == 3


def test_require_raises_a_config_error_listing_every_problem():
    registry = build_local_registry()
    with pytest.raises(ConfigError) as caught:
        require_valid_agent_spec(AgentSpec(system_prompt="p", sample_rate=1), registry)
    assert "sample_rate" in str(caught.value)


@pytest.mark.asyncio
async def test_an_impossible_session_is_refused_rather_than_started():
    """GOVERNANCE §4 — the write is rejected, not accepted and quietly altered."""
    service = RuntimeSessionService(build_local_registry())
    with pytest.raises(ConfigError):
        await service.start(_request(session_id="ses_bad", sample_rate=3))
    assert "ses_bad" not in service._workers  # noqa: SLF001


# ── AR-24 · degradation is observable ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_exposes_its_degradation_record():
    service = RuntimeSessionService(build_local_registry())
    await service.start(_request(session_id="ses_deg"))
    recorder = service._workers["ses_deg"].orchestrator.degradations  # noqa: SLF001
    assert recorder.as_session_metadata() == {"degradations": [], "degradation_counts": {}}
    await service.end("ses_deg")


@pytest.mark.asyncio
async def test_a_failing_auxiliary_vad_degrades_the_session_without_ending_it():
    """DL-0013 + AR-24 together: the call continues **and** the degradation is recorded.

    Before M11.4 the first half was true and the second was not — a failing VAD was a log line,
    which is precisely the silent degradation AR-24 forbids."""
    registry = build_local_registry(include_voice_activity=True)

    class _BoomVAD:
        name = "boom"
        capabilities = registry.resolve(CapabilityKind.VOICE_ACTIVITY).capabilities

        async def start(self, *, session_id: str) -> None:
            raise RuntimeError("model load failed")

        async def stop(self, *, session_id: str) -> None: ...

        def detect(self, frame, *, session_id: str):  # pragma: no cover — never reached
            raise AssertionError("unreachable")

    from core.providers.voice_activity import VoiceActivityProvider

    VoiceActivityProvider.register(_BoomVAD)  # structural conformance for the registry check
    # Same trap as the LLM helper above: a differently-named provider would be *added*, leaving
    # the local one as default and the failure path untested.
    registry.unregister(CapabilityKind.VOICE_ACTIVITY, "local")
    registry.register(CapabilityKind.VOICE_ACTIVITY, _BoomVAD(), default=True)

    service = RuntimeSessionService(registry)
    await service.start(_request(session_id="ses_vadfail"))
    worker = service._workers["ses_vadfail"]  # noqa: SLF001
    recorder = worker.orchestrator.degradations

    transport = registry.resolve(CapabilityKind.TRANSPORT)
    await transport.feed("ses_vadfail", AudioFrame(data=b"hello"))
    await transport.hangup("ses_vadfail")
    await worker.wait_closed()

    kinds = {d.kind.value for d in recorder.history}
    assert "auxiliary_disabled" in kinds, f"VAD failure was not recorded: {recorder.history}"
    assert recorder.history[0].fallback, "a degradation must say what happened instead"
    await service.end("ses_vadfail")
