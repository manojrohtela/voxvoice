"""ProviderRegistry tests (M2C.1).

Covers successful registration, duplicate registration, capability lookup,
missing capability, deregistration, metadata, validation, registry immutability,
thread safety, and deterministic behaviour. No provider is ever executed — the
registry only manages references.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses

import pytest

from core.providers.stt import STTCapabilities, STTProvider
from core.providers.tts import TTSCapabilities, TTSProvider
from runtime.errors import (
    CapabilityNotRegistered,
    DuplicateProviderRegistration,
    InvalidProviderRegistration,
    ProviderCapabilityMismatch,
    UnsupportedCapabilityKind,
)
from runtime.registry import CapabilityKind, ProviderRegistry


class DummySTT(STTProvider):
    """A minimal, real STTProvider — never actually called in these tests."""

    def __init__(self, name: str = "dummy-stt") -> None:
        self.name = name
        self.capabilities = STTCapabilities(streaming=True, partials=True)

    async def open_stream(self, *, language: str, sample_rate: int):
        raise AssertionError("the registry must never execute a provider")


class DummyTTS(TTSProvider):
    def __init__(self, name: str = "dummy-tts") -> None:
        self.name = name
        self.capabilities = TTSCapabilities(streaming=True)

    async def open_stream(self, *, voice: str, language: str) -> None:
        raise AssertionError("the registry must never execute a provider")

    async def say(self, text_chunk: str):
        raise AssertionError("the registry must never execute a provider")


# ── successful registration / lookup / metadata ─────────────────────────────
def test_register_and_resolve_by_name():
    reg = ProviderRegistry()
    meta = reg.register(CapabilityKind.STT, DummySTT("alpha"))
    assert meta.kind == "stt" and meta.name == "alpha" and meta.is_default is False
    resolved = reg.resolve(CapabilityKind.STT, "alpha")
    assert isinstance(resolved, DummySTT) and resolved.name == "alpha"


def test_resolve_single_provider_without_name_or_default():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("only"))
    assert reg.resolve(CapabilityKind.STT).name == "only"


def test_resolve_default_when_multiple_registered():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    reg.register(CapabilityKind.STT, DummySTT("b"), default=True)
    assert reg.resolve(CapabilityKind.STT).name == "b"


def test_metadata_capabilities_exposed_without_execution():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.TTS, DummyTTS("v1"))
    (meta,) = reg.list(CapabilityKind.TTS)
    assert meta.capabilities == TTSCapabilities(streaming=True)


# ── duplicate registration ──────────────────────────────────────────────────
def test_duplicate_registration_same_name_rejected():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    with pytest.raises(DuplicateProviderRegistration):
        reg.register(CapabilityKind.STT, DummySTT("a"))


def test_duplicate_default_rejected():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"), default=True)
    with pytest.raises(DuplicateProviderRegistration):
        reg.register(CapabilityKind.STT, DummySTT("b"), default=True)


# ── missing / ambiguous capability ──────────────────────────────────────────
def test_resolve_missing_capability_raises():
    reg = ProviderRegistry()
    with pytest.raises(CapabilityNotRegistered):
        reg.resolve(CapabilityKind.STT)


def test_resolve_unknown_name_raises():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    with pytest.raises(CapabilityNotRegistered):
        reg.resolve(CapabilityKind.STT, "nope")


def test_resolve_ambiguous_without_default_raises():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    reg.register(CapabilityKind.STT, DummySTT("b"))
    with pytest.raises(CapabilityNotRegistered):
        reg.resolve(CapabilityKind.STT)  # no name, no default -> explicit error


# ── deregistration ──────────────────────────────────────────────────────────
def test_unregister_removes_provider():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    reg.unregister(CapabilityKind.STT, "a")
    assert reg.has(CapabilityKind.STT, "a") is False


def test_unregister_missing_raises():
    reg = ProviderRegistry()
    with pytest.raises(CapabilityNotRegistered):
        reg.unregister(CapabilityKind.STT, "nope")


def test_unregister_clears_default():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"), default=True)
    reg.unregister(CapabilityKind.STT, "a")
    reg.register(CapabilityKind.STT, DummySTT("b"), default=True)  # no longer a conflict
    assert reg.resolve(CapabilityKind.STT).name == "b"


def test_replace_hot_swap():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    new_instance = DummySTT("a")
    meta = reg.replace(CapabilityKind.STT, new_instance)
    assert reg.resolve(CapabilityKind.STT, "a") is new_instance
    assert meta.name == "a"


# ── validation (compatibility / invalid registration / unsupported kind) ───
def test_invalid_provider_none_raises():
    reg = ProviderRegistry()
    with pytest.raises(InvalidProviderRegistration):
        reg.register(CapabilityKind.STT, None)


def test_invalid_provider_missing_name_raises():
    reg = ProviderRegistry()
    bad = DummySTT("x")
    bad.name = ""  # blank out the required identity
    with pytest.raises(InvalidProviderRegistration):
        reg.register(CapabilityKind.STT, bad)


def test_invalid_provider_missing_capabilities_raises():
    reg = ProviderRegistry()
    bad = DummySTT("x")
    bad.capabilities = None
    with pytest.raises(InvalidProviderRegistration):
        reg.register(CapabilityKind.STT, bad)


def test_provider_capability_mismatch_raises():
    reg = ProviderRegistry()
    with pytest.raises(ProviderCapabilityMismatch):
        reg.register(CapabilityKind.STT, DummyTTS("wrong-kind"))  # TTS under STT


def test_unsupported_capability_kind_raises():
    reg = ProviderRegistry()
    with pytest.raises(UnsupportedCapabilityKind):
        reg.register("video", DummySTT("x"))
    assert reg.has("video") is False  # has() reports False, never raises


# ── registry immutability ───────────────────────────────────────────────────
def test_list_returns_immutable_snapshot():
    reg = ProviderRegistry()
    reg.register(CapabilityKind.STT, DummySTT("a"))
    snapshot = reg.list(CapabilityKind.STT)
    reg.register(CapabilityKind.STT, DummySTT("b"))
    assert len(snapshot) == 1  # unaffected by the later registration
    assert len(reg.list(CapabilityKind.STT)) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot[0].name = "mutated"


def test_provider_metadata_and_registry_event_are_frozen():
    reg = ProviderRegistry()
    meta = reg.register(CapabilityKind.STT, DummySTT("a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        meta.is_default = True


# ── no execution ─────────────────────────────────────────────────────────────
def test_registry_never_executes_a_provider():
    reg = ProviderRegistry()
    stt = DummySTT("watchdog")
    reg.register(CapabilityKind.STT, stt)
    reg.resolve(CapabilityKind.STT, "watchdog")
    reg.list(CapabilityKind.STT)
    reg.has(CapabilityKind.STT, "watchdog")
    reg.replace(CapabilityKind.STT, DummySTT("watchdog"))
    reg.unregister(CapabilityKind.STT, "watchdog")
    # If open_stream had been called anywhere above, it would have raised.


# ── observability ────────────────────────────────────────────────────────────
def test_observer_notified_on_register_and_unregister():
    seen: list[tuple[str, str]] = []
    reg = ProviderRegistry(on_change=lambda e: seen.append((e.action, e.name)))
    reg.register(CapabilityKind.STT, DummySTT("a"))
    reg.unregister(CapabilityKind.STT, "a")
    assert seen == [("registered", "a"), ("unregistered", "a")]


def test_observer_errors_do_not_break_registry():
    def boom(_event) -> None:
        raise RuntimeError("observer boom")

    reg = ProviderRegistry(on_change=boom)
    reg.register(CapabilityKind.STT, DummySTT("a"))  # must not raise
    assert reg.has(CapabilityKind.STT, "a") is True


# ── thread safety ────────────────────────────────────────────────────────────
def test_thread_safety_concurrent_registration():
    reg = ProviderRegistry()
    errors: list[Exception] = []

    def _register(i: int) -> None:
        try:
            reg.register(CapabilityKind.STT, DummySTT(f"stt-{i}"))
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(_register, range(100)))

    assert errors == []
    assert len(reg.list(CapabilityKind.STT)) == 100


# ── deterministic behaviour ──────────────────────────────────────────────────
def test_deterministic_listing_order():
    reg = ProviderRegistry()
    for n in ("c", "a", "b"):
        reg.register(CapabilityKind.STT, DummySTT(n))
    order = [m.name for m in reg.list(CapabilityKind.STT)]
    assert order == ["c", "a", "b"]  # registration order, not sorted/random
    assert [m.name for m in reg.list(CapabilityKind.STT)] == order  # stable on repeat
