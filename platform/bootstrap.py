"""Composition root — the single place that wires the Runtime Plane (M2C.2).

This top-level module (NOT part of `api` or `runtime`) is the one sanctioned
place that imports both the runtime and the provider plugins to assemble a
`SessionController` for the Control Plane. Keeping it here preserves the layer
rule that `api` business code never imports `runtime` (AR-14) — the Control Plane
depends only on the `core.session_control.SessionController` interface and
receives a concrete instance from here (DL-0010).

The default profile registers the local, in-process provider set (Constitution
Part B). Real vendor plugins are registered here too once available — a drop-in
change, proving providers are fully replaceable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.audio import DEFAULT_SAMPLE_RATE
from core.session_control import SessionController, TranscriptSink
from providers.llm.completion_stream import CompletionClient
from providers.llm.echo import EchoLLM
from providers.llm.groq import GroqLLM
from providers.stt.deepgram import DEFAULT_LANGUAGES as DEEPGRAM_DEFAULT_LANGUAGES
from providers.stt.deepgram import DeepgramSTT
from providers.stt.local import LocalSTT
from providers.stt.transcription_session import TranscriptionClient
from providers.transport.aiortc_endpoint import AiortcMediaEndpoint
from providers.transport.ice import NO_ICE_SERVERS, IcePolicy
from providers.transport.local import LocalTransport
from providers.transport.media_endpoint import MediaEndpoint
from providers.transport.signalling import (
    DEFAULT_NEGOTIATION_TIMEOUT_S,
    PeerSignaller,
    SdpExchange,
)
from providers.transport.webrtc import WebRTCTransport
from providers.tts.cartesia import CartesiaTTS
from providers.tts.local import LocalTTS
from providers.tts.speech_stream import SpeechClient
from providers.voice_activity.local import LocalVoiceActivity
from providers.voice_activity.silero import SileroVoiceActivity
from providers.voice_activity.speech_model import SpeechModelFactory
from runtime.observability_plane import UNWIRED, ObservabilityPlane
from runtime.registry import CapabilityKind, ProviderRegistry
from runtime.service import RuntimeSessionService

if TYPE_CHECKING:  # annotation only — the composition root is handed a resolver, it builds none
    from api.adapters.secrets import CredentialProvider

#: Transport profiles the composition root can assemble (M11.5).
#:
#: `"local"` stays the default and is unchanged: the deterministic loopback transport that every
#: hermetic test runs against. `"webrtc"` selects the real media transport, and selecting it
#: **requires** a media endpoint — there is no implicit fallback, because a caller who asked for
#: real media and silently got the loopback would be told a lie about their own configuration.
TRANSPORT_LOCAL = "local"
TRANSPORT_WEBRTC = "webrtc"

#: STT profiles the composition root can assemble (M11.6).
#:
#: `"local"` stays the default and is unchanged. `"deepgram"` selects real streaming STT and
#: **requires** a Deepgram credential — resolved here, through the injected ADR-0049
#: `CredentialProvider`, and nowhere else. There is no fallback to `LocalSTT`: a caller who asked
#: for real transcription and silently got the echo transcriber would be told a lie about their own
#: configuration (the same rule M11.5 applied to transport).
STT_LOCAL = "local"
STT_DEEPGRAM = "deepgram"

#: The one credential name the Deepgram profile resolves — `VOXVOICE_SECRET_DEEPGRAM_API_KEY`
#: under the default `EnvCredentialProvider`. Documented by name only in `.env.example`
#: (ADR-0049 CR7).
DEEPGRAM_API_KEY = "deepgram_api_key"

#: Voice-activity profiles the composition root can assemble (M11.7).
#:
#: Voice activity stays **off** by default, exactly as before. `"local"` is the deterministic
#: `LocalVoiceActivity` (the same thing `include_voice_activity=True` selects). `"silero"` selects
#: real streaming VAD and **requires** the model library to be importable — checked here, at
#: startup, so a host without it fails before the first call rather than degrading mid-session.
#: There is no fallback to `LocalVoiceActivity`: it classifies text payloads, and an operator who
#: asked for real VAD and silently got it would be told a lie about their own configuration.
#: Silero needs no credential, so none is resolved (ADR-0049 is not invoked for it).
VOICE_ACTIVITY_LOCAL = "local"
VOICE_ACTIVITY_SILERO = "silero"

#: LLM profiles the composition root can assemble (M11.9).
#:
#: `"echo"` stays the default and is unchanged: the deterministic in-process `EchoLLM` every
#: hermetic test runs against. `"groq"` selects the real streaming LLM and **requires** a Groq
#: credential — resolved here, through the injected ADR-0049 `CredentialProvider`, and nowhere
#: else. There is no fallback to `EchoLLM`, for the third time and the same reason M11.5, M11.6 and
#: M11.7 gave: an operator who asked for a real model and silently got a canned echo would be told
#: a lie about their own configuration, and would discover it from a caller.
LLM_ECHO = "echo"
LLM_GROQ = "groq"

#: TTS profiles the composition root can assemble (M11.10).
#:
#: `"local"` stays the default and is unchanged: the deterministic text-loopback `LocalTTS` every
#: hermetic test runs against. `"cartesia"` selects real streaming synthesis and **requires** a
#: Cartesia credential and a configured voice — resolved here, through the injected ADR-0049
#: `CredentialProvider`, and nowhere else. No fallback to `LocalTTS`, for the fourth time and the
#: same reason: an operator who asked for a real voice and silently got the text-loopback codec
#: would be told a lie about their own configuration, and would learn it from a caller.
TTS_LOCAL = "local"
TTS_CARTESIA = "cartesia"

#: The one credential name the Cartesia profile resolves — `VOXVOICE_SECRET_CARTESIA_API_KEY`
#: under the default `EnvCredentialProvider`. Documented by name only in `.env.example` (CR7).
CARTESIA_API_KEY = "cartesia_api_key"

#: The one credential name the Groq profile resolves — `VOXVOICE_SECRET_GROQ_API_KEY` under the
#: default `EnvCredentialProvider`. Documented by name only in `.env.example` (ADR-0049 CR7).
GROQ_API_KEY = "groq_api_key"


def build_registry(
    *,
    transport: str = TRANSPORT_LOCAL,
    media_endpoint: MediaEndpoint | None = None,
    include_voice_activity: bool = False,
    stt: str = STT_LOCAL,
    credentials: CredentialProvider | None = None,
    stt_client: TranscriptionClient | None = None,
    voice_activity: str | None = None,
    speech_model_factory: SpeechModelFactory | None = None,
    llm: str = LLM_ECHO,
    llm_client: CompletionClient | None = None,
    llm_model: str | None = None,
    tts: str = TTS_LOCAL,
    tts_client: SpeechClient | None = None,
    tts_voice: str | None = None,
    tts_sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> ProviderRegistry:
    """A ProviderRegistry for the selected transport, STT and VAD profiles (M11.5–M11.7).

    The seam through which the **real** transport is chosen. `build_local_registry` remains the
    unchanged local profile and simply delegates here, so nothing that exists today changes
    behaviour by the mere existence of WebRTC (AR-25's spirit: a capability nobody selected must
    not start acting).

    The WebRTC transport is **constructed here and injected**, never built inside the
    Orchestrator: AR-20 gives provider provisioning to the registry, and the runtime resolves
    capabilities rather than instantiating them."""
    # Both selections are validated before anything is built, so a bad STT profile cannot leave a
    # half-assembled registry behind a successfully constructed transport.
    if transport not in (TRANSPORT_LOCAL, TRANSPORT_WEBRTC):
        raise ValueError(f"unknown transport profile {transport!r}")
    if stt not in (STT_LOCAL, STT_DEEPGRAM):
        raise ValueError(f"unknown stt profile {stt!r}")
    if voice_activity not in (None, VOICE_ACTIVITY_LOCAL, VOICE_ACTIVITY_SILERO):
        raise ValueError(f"unknown voice activity profile {voice_activity!r}")
    if llm not in (LLM_ECHO, LLM_GROQ):
        raise ValueError(f"unknown llm profile {llm!r}")
    if tts not in (TTS_LOCAL, TTS_CARTESIA):
        raise ValueError(f"unknown tts profile {tts!r}")
    if voice_activity == VOICE_ACTIVITY_SILERO and include_voice_activity:
        raise ValueError(
            "include_voice_activity=True selects the local detector and conflicts with the "
            "silero voice activity profile; choose one"
        )
    real_stt = _build_deepgram_stt(credentials, stt_client) if stt == STT_DEEPGRAM else None
    real_llm = (
        _build_groq_llm(credentials, llm_client, llm_model) if llm == LLM_GROQ else None
    )
    real_tts = (
        _build_cartesia_tts(credentials, tts_client, tts_voice, tts_sample_rate)
        if tts == TTS_CARTESIA
        else None
    )
    real_vad = (
        _build_silero_vad(speech_model_factory)
        if voice_activity == VOICE_ACTIVITY_SILERO
        else None
    )

    registry = build_local_registry(
        include_voice_activity=include_voice_activity or voice_activity == VOICE_ACTIVITY_LOCAL
    )
    if transport == TRANSPORT_WEBRTC:
        if media_endpoint is None:
            raise ValueError(
                "the webrtc transport profile requires a media endpoint; refusing to fall back "
                "to the loopback transport"
            )
        registry.unregister(CapabilityKind.TRANSPORT, LocalTransport.name)
        registry.register(CapabilityKind.TRANSPORT, WebRTCTransport(media_endpoint), default=True)
    if real_stt is not None:
        registry.unregister(CapabilityKind.STT, LocalSTT.name)
        registry.register(CapabilityKind.STT, real_stt, default=True)
    if real_vad is not None:
        registry.register(CapabilityKind.VOICE_ACTIVITY, real_vad, default=True)
    if real_llm is not None:
        registry.unregister(CapabilityKind.LLM, EchoLLM.name)
        registry.register(CapabilityKind.LLM, real_llm, default=True)
    if real_tts is not None:
        registry.unregister(CapabilityKind.TTS, LocalTTS.name)
        registry.register(CapabilityKind.TTS, real_tts, default=True)
    return registry


def _build_cartesia_tts(
    credentials: CredentialProvider | None,
    tts_client: SpeechClient | None,
    voice: str | None,
    sample_rate: int,
) -> CartesiaTTS:
    """Construct the Cartesia TTS provider with **one** resolved credential (ADR-0049 CR3/CR4).

    `tts_client` is the test seam: a deterministic `SpeechClient` replaces the network client, and
    when one is supplied no credential is resolved at all — a fake needs no secret, and resolving
    one anyway would make the hermetic suite depend on the environment.

    A voice is **required** for the real profile. Cartesia has no meaningful default voice, and a
    synthesis request without one is refused by the vendor mid-call; refusing at startup turns a
    caller-visible failure into a configuration error. The error messages name what is missing and
    never contain a value."""
    if tts_client is not None:
        return CartesiaTTS(tts_client, voice=voice, sample_rate=sample_rate)
    if credentials is None:
        raise ValueError(
            "the cartesia tts profile requires a credential provider; refusing to fall back to "
            "the local tts"
        )
    api_key = credentials.get(CARTESIA_API_KEY)
    if not api_key:
        raise ValueError(
            f"the cartesia tts profile requires the {CARTESIA_API_KEY!r} credential, which is not "
            "configured; refusing to fall back to the local tts"
        )
    if not voice:
        raise ValueError(
            "the cartesia tts profile requires a voice; refusing to synthesize with an "
            "unspecified voice"
        )
    # Imported here so that only a process that actually selects the profile loads the adapter
    # module — and so the SDK's absence is reported at startup rather than mid-call.
    from providers.tts.cartesia_client import CartesiaSpeechClient, cartesia_available

    if not cartesia_available():
        raise ValueError(
            "the cartesia tts profile requires the cartesia package, which is not importable on "
            "this host; refusing to fall back to the local tts"
        )
    return CartesiaTTS(CartesiaSpeechClient(api_key), voice=voice, sample_rate=sample_rate)


def _build_groq_llm(
    credentials: CredentialProvider | None,
    llm_client: CompletionClient | None,
    model: str | None,
) -> GroqLLM:
    """Construct the Groq LLM provider with **one** resolved credential (ADR-0049 CR3/CR4).

    `llm_client` is the test seam: a deterministic `CompletionClient` replaces the network client,
    and when one is supplied no credential is resolved at all — a fake needs no secret, and
    resolving one anyway would make the hermetic suite depend on the environment.

    The error messages name the credential and never contain a value."""
    model_kwargs = {"model": model} if model else {}
    if llm_client is not None:
        return GroqLLM(llm_client, **model_kwargs)
    if credentials is None:
        raise ValueError(
            "the groq llm profile requires a credential provider; refusing to fall back to the "
            "echo llm"
        )
    api_key = credentials.get(GROQ_API_KEY)
    if not api_key:
        raise ValueError(
            f"the groq llm profile requires the {GROQ_API_KEY!r} credential, which is not "
            "configured; refusing to fall back to the echo llm"
        )
    # Imported here so that only a process that actually selects the profile loads the adapter
    # module — and so the SDK's absence is reported at startup rather than mid-call.
    from providers.llm.groq_client import GroqCompletionClient, groq_available

    if not groq_available():
        raise ValueError(
            "the groq llm profile requires the groq package, which is not importable on this "
            "host; refusing to fall back to the echo llm"
        )
    return GroqLLM(GroqCompletionClient(api_key), **model_kwargs)


def _build_silero_vad(speech_model_factory: SpeechModelFactory | None) -> SileroVoiceActivity:
    """Construct the real VAD provider (M11.7).

    `speech_model_factory` is the test seam: a deterministic `SpeechModel` replaces the native
    model, so the hermetic suite needs neither the library nor its weights. Without one, the
    library must be importable **now** — the model itself is still loaded per session, off the
    event loop, inside `start()`."""
    if speech_model_factory is not None:
        return SileroVoiceActivity(speech_model_factory)
    # Imported here so only a process that selects the profile loads the adapter module.
    from providers.voice_activity.silero_model import load_silero_model, silero_available

    if not silero_available():
        raise ValueError(
            "the silero voice activity profile requires the pysilero-vad package, which is not "
            "importable on this host; refusing to fall back to the local detector"
        )
    return SileroVoiceActivity(load_silero_model)


def _build_deepgram_stt(
    credentials: CredentialProvider | None, stt_client: TranscriptionClient | None
) -> DeepgramSTT:
    """Construct the Deepgram STT provider with **one** resolved credential (ADR-0049 CR3/CR4).

    `stt_client` is the test seam: a deterministic `TranscriptionClient` replaces the network
    client, and when one is supplied no credential is resolved at all — a fake needs no secret, and
    resolving one anyway would make the hermetic suite depend on the environment.

    The error messages name the credential and never contain a value."""
    if stt_client is not None:
        return DeepgramSTT(stt_client)
    if credentials is None:
        raise ValueError(
            "the deepgram stt profile requires a credential provider; refusing to fall back to "
            "the local transcriber"
        )
    api_key = credentials.get(DEEPGRAM_API_KEY)
    if not api_key:
        raise ValueError(
            f"the deepgram stt profile requires the {DEEPGRAM_API_KEY!r} credential, which is not "
            "configured; refusing to fall back to the local transcriber"
        )
    # Imported here so that only a process that actually selects the profile loads the adapter
    # module. The SDK itself is imported later still, when the first stream opens.
    from providers.stt.deepgram_client import DeepgramTranscriptionClient

    return DeepgramSTT(DeepgramTranscriptionClient(api_key))


def build_local_registry(*, include_voice_activity: bool = False) -> ProviderRegistry:
    """A ProviderRegistry populated with the local-development provider profile.
    Every provider is registered as the default for its capability.

    Voice activity (M3.2) is off by default so the shipped local profile exercises
    the graceful-fallback path (no VAD → pipeline runs exactly as M3.1). Set
    `include_voice_activity=True` to register the deterministic `LocalVoiceActivity`
    detector and exercise the full Voice Activity Framework end-to-end."""
    registry = ProviderRegistry()
    registry.register(CapabilityKind.TRANSPORT, LocalTransport(), default=True)
    registry.register(CapabilityKind.STT, LocalSTT(), default=True)
    registry.register(CapabilityKind.LLM, EchoLLM(), default=True)
    registry.register(CapabilityKind.TTS, LocalTTS(), default=True)
    if include_voice_activity:
        registry.register(CapabilityKind.VOICE_ACTIVITY, LocalVoiceActivity(), default=True)
    return registry


def declared_stt_languages(stt: str = STT_LOCAL) -> tuple[str, ...]:
    """The languages the STT plugin of profile `stt` declares, exactly as `build_registry` builds it
    (M12.3, finding F-4).

    Read from the plugin's own declaration, never re-stated: the local transcriber's class
    capabilities, and the default the Deepgram plugin is constructed with here (no `languages`
    argument is passed to it). Nothing is constructed and no credential is read, so the developer
    platform can offer agent languages without touching the provider plane. An agent language
    outside this set is refused by the plugin at stream open, which is the call that "plays the
    greeting then disconnects" (`docs/runtime/local-run.md` §6)."""
    if stt == STT_LOCAL:
        return tuple(LocalSTT.capabilities.languages)
    if stt == STT_DEEPGRAM:
        return tuple(DEEPGRAM_DEFAULT_LANGUAGES)
    raise ValueError(f"unknown stt profile {stt!r}")


def build_session_controller(
    *,
    transcript_sink: TranscriptSink | None = None,
    transport: str = TRANSPORT_LOCAL,
    media_endpoint: MediaEndpoint | None = None,
    stt: str = STT_LOCAL,
    credentials: CredentialProvider | None = None,
    voice_activity: str | None = None,
    llm: str = LLM_ECHO,
    llm_model: str | None = None,
    tts: str = TTS_LOCAL,
    tts_voice: str | None = None,
    streaming_playback: bool = False,
    observability: ObservabilityPlane = UNWIRED,
) -> SessionController:
    """Assemble the runtime session controller for injection into the Control Plane.

    `transport` (M11.5), `stt` (M11.6), `voice_activity` (M11.7), `llm` (M11.9) and `tts` (M11.10)
    select the profiles. Every default is unchanged — **`local`**, **`local`**, off, **`echo`**,
    **`local`** — so the arrival of real providers does not alter what an existing caller gets.

    `streaming_playback` (M11.11) selects the clause-by-clause response path: text becomes audio
    while the model is still writing, through the Playback Scheduler and Engine. It defaults
    **False**, so an existing caller keeps the whole-utterance path exactly. It is a
    *composition* choice rather than an agent one, which is why it lives here and on
    `WorkerConfiguration` and not on `AgentSpec`."""
    return RuntimeSessionService(
        build_registry(
            transport=transport,
            media_endpoint=media_endpoint,
            stt=stt,
            credentials=credentials,
            voice_activity=voice_activity,
            llm=llm,
            llm_model=llm_model,
            tts=tts,
            tts_voice=tts_voice,
        ),
        on_transcript=transcript_sink,
        streaming_playback=streaming_playback,
        observability=observability,
    )


#: The real-provider conversation profile (M11.11).
#:
#: One named place that says what "the real stack" means, so a deployment does not have to
#: assemble five separate selections correctly and a test can assert the composition rather than
#: re-describe it. Every selection stays **explicit** — this constant names profiles, it does not
#: enable anything by itself, and `build_session_controller` must still be called with it.
REAL_PROVIDER_PROFILE = {
    "transport": TRANSPORT_WEBRTC,
    "stt": STT_DEEPGRAM,
    "voice_activity": VOICE_ACTIVITY_SILERO,
    "llm": LLM_GROQ,
    "tts": TTS_CARTESIA,
    "streaming_playback": True,
}


def build_real_session_controller(
    *,
    credentials: CredentialProvider,
    media_endpoint: MediaEndpoint,
    tts_voice: str,
    transcript_sink: TranscriptSink | None = None,
    llm_model: str | None = None,
) -> SessionController:
    """Assemble the **real** provider stack: WebRTC + Deepgram + Silero + Groq + Cartesia.

    A thin, explicit convenience over `build_session_controller` — it adds no capability and
    hides no choice. Every argument it does not take is a profile name fixed by
    `REAL_PROVIDER_PROFILE`, and every argument it does take is something only the deployment can
    supply: the credential source, the media endpoint, and the vendor voice id.

    It exists because M11.11's whole point is that the real stack is *composable at all*, and a
    deployment assembling five selections by hand is five chances to silently get a local
    provider. Nothing here falls back: each underlying profile refuses to start without its
    credential (ADR-0049 CR3/CR4), so a missing key is a startup error, never a quiet downgrade."""
    return build_session_controller(
        transcript_sink=transcript_sink,
        media_endpoint=media_endpoint,
        credentials=credentials,
        tts_voice=tts_voice,
        llm_model=llm_model,
        **REAL_PROVIDER_PROFILE,
    )


def build_webrtc_media_endpoint(
    exchange: SdpExchange,
    *,
    negotiation_timeout_s: float = DEFAULT_NEGOTIATION_TIMEOUT_S,
    ice_policy: IcePolicy = NO_ICE_SERVERS,
) -> MediaEndpoint:
    """Assemble the real WebRTC media endpoint over a deployment's own SDP exchange (M11.16.3).

    The composition path M11.16.2 found missing: `AiortcMediaEndpoint` existed and
    `build_real_session_controller` accepted a `media_endpoint`, but **nothing in the repository
    ever constructed one**, so no real peer could be obtained at any price.

    Explicitly **opt-in and never a default.** A deployment calls this and hands the result to
    `build_real_session_controller`; no profile, flag or installed library causes it to happen on
    its own. `build_registry`'s default transport is unchanged and remains `local`, so nothing
    silently switches to WebRTC because aiortc happens to be importable (§10).

    `exchange` is the only thing a deployment must write: one async method that delivers an offer
    and returns an answer over whatever channel it already has. Everything else — the negotiation
    sequence, its state machine, its bound, its validation — is first-party and lives in
    `providers/transport/signalling.py`.

    `ice_policy` (M11.16.6) states which ICE servers the peer connections offer. It defaults to
    **explicitly none**, and that default is passed to the library rather than omitted: aiortc
    substitutes a public STUN server whenever the configuration is absent, so "we configured
    nothing" and "we configured nothing to be used" were previously the same code and different
    facts (R-71, ADR-0054). A deployment that needs server-reflexive candidates states its STUN
    servers here; TURN is not implemented and a `turn:` URL is refused rather than ignored.

    Raises `MediaEndpointError` if aiortc is not installed, rather than degrading to a fake: a
    transport that quietly became a loopback would be the silent fallback the whole provider
    boundary exists to prevent."""
    return AiortcMediaEndpoint(
        PeerSignaller(exchange, negotiation_timeout_s=negotiation_timeout_s),
        ice_policy=ice_policy,
    )


def build_observability_plane(
    *,
    durable: object | None = None,
    metrics: object | None = None,
    telemetry: bool = True,
    on_durable_error: object | None = None,
) -> ObservabilityPlane:
    """Assemble the Epic 10 attachments a session's Event Bus is built through (M11.12).

    The composition root is the only layer allowed to see every plane at once, which is why this
    lives here: `runtime/` may not import any other plane, so it accepts the
    plane as opaque objects and this function supplies the concrete ones.

    Every part is optional and each is a separate decision:

    * `durable` — Epic 10's `DurableEventLog` (`events.persistence.SqlDurableEventSpine`). The bus
      writes every published envelope through it **before** dispatching, and a write failure never
      reaches the producer (AR-31); `on_durable_error` is the operator's only signal.
    * `metrics` — the platform metric buffer (`api.metering.MetricTurnBuffer`), which subscribes
      itself for `metric.turn` only. The commercial plane later *pulls* from it through its own
      frozen port; nothing here pushes into that plane, and nothing here prices anything.
    * `telemetry` — whether to produce `metric.turn` at all. On by default because a wired plane
      with no metric producer would leave the buffer permanently empty; off is still meaningful
      for a deployment that wants durable events and nothing else.

    **This adds no capability.** Every mechanism it references was built and frozen in Epic 10 and
    had simply never been given a producer."""
    factory = None
    if telemetry:
        # Imported here so a process that never wires telemetry does not load the producer.
        from observability import TurnLatencyTap

        def factory(publisher: object) -> object:  # noqa: F811 — the wired branch
            return TurnLatencyTap(publisher)

    return ObservabilityPlane(
        durable=durable,
        on_durable_error=on_durable_error,
        metrics=metrics,
        telemetry=factory,
    )
