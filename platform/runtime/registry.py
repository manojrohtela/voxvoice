"""ProviderRegistry — capability-based provider reference management (M2C.1).

Component specification (Constitution §A11.3):
  responsibilities : register/unregister/resolve provider *instances* by
                     capability kind (Constitution §A6); validate that a
                     registered instance satisfies the capability contract
                     (isinstance + declared identity/metadata); prevent
                     duplicate registrations; expose provider metadata for
                     discovery; support a single default per capability;
                     support atomic hot-swap (`replace`). NOTHING else — no
                     provider construction, no execution, no network calls, no
                     runtime resource allocation, no vendor-specific behaviour.
  inputs           : already-constructed provider instances (the Runtime
                     receives providers via dependency injection; the registry
                     never creates one) + a capability kind + an optional
                     provider-declared `name`.
  outputs          : resolved provider instances (by reference only);
                     `ProviderMetadata` snapshots; registry lifecycle
                     notifications (`RegistryEvent`).
  dependencies     : `core.providers` (the capability contracts) and
                     `runtime.errors`. Pure stdlib otherwise (`threading`,
                     `time`). Never imports `api`.
  lifecycle        : none of its own — the registry has no start/stop; it is a
                     synchronous, always-available bookkeeping structure held
                     by whatever constructs it (a future runtime bootstrap).
  emitted events   : registry lifecycle notifications only (`registered` /
                     `unregistered`) via an optional observer + logs. This is
                     NOT the Event Bus (M4); a bridge can forward these later.
  failure modes    : duplicate registration, capability mismatch, capability
                     not registered, invalid registration, unsupported
                     capability kind — all typed, all raised explicitly. No
                     silent fallback.
  recovery         : none needed — the registry holds no failable resources;
                     callers correct the illegal call and retry.

Ownership: per the Constitution's layer rule (§A4.1), `runtime` imports `core`
and provider *interfaces* (resolved via this registry). The registry is
therefore a Runtime Plane component, not a `core` one (`core` holds contracts
and definitions only, no stateful behaviour — see `core/__init__.py`), and not
a `providers/` one (that package holds *implementations*, not the management
structure over them). See DL-0008.

Vendor neutrality: `name` is an opaque, provider-declared identifier (a
dictionary key), never used for branching logic. The registry has no
`if name == "<vendor>"` anywhere — callers request a *capability*, optionally
by that opaque name, never by vendor identity.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.providers import (
    AnalyticsProvider,
    LLMProvider,
    SemanticTurnEvaluator,
    StorageProvider,
    STTProvider,
    TransportProvider,
    TTSProvider,
    VoiceActivityProvider,
)

from .errors import (
    CapabilityNotRegistered,
    DuplicateProviderRegistration,
    InvalidProviderRegistration,
    ProviderCapabilityMismatch,
    UnsupportedCapabilityKind,
)

log = logging.getLogger("voxvoice.runtime.registry")


class CapabilityKind(StrEnum):
    """Known capability kinds (Constitution §A6). A new kind is a purely
    additive extension (§A12): add its base-class mapping below; nothing else
    in the runtime changes."""

    STT = "stt"
    TTS = "tts"
    LLM = "llm"
    TRANSPORT = "transport"
    STORAGE = "storage"
    ANALYTICS = "analytics"
    VOICE_ACTIVITY = "voice_activity"
    SEMANTIC_TURN = "semantic_turn"


# Maps a capability kind to the ABC every registered provider must satisfy.
_CAPABILITY_BASES: dict[str, type] = {
    CapabilityKind.STT: STTProvider,
    CapabilityKind.TTS: TTSProvider,
    CapabilityKind.LLM: LLMProvider,
    CapabilityKind.TRANSPORT: TransportProvider,
    CapabilityKind.STORAGE: StorageProvider,
    CapabilityKind.ANALYTICS: AnalyticsProvider,
    CapabilityKind.VOICE_ACTIVITY: VoiceActivityProvider,
    CapabilityKind.SEMANTIC_TURN: SemanticTurnEvaluator,
}

# The union of provider ABCs the registry accepts (for type hints only; the
# actual validation is the isinstance check against the resolved capability's
# specific base class).
ProviderInstance = (
    STTProvider
    | TTSProvider
    | LLMProvider
    | TransportProvider
    | StorageProvider
    | AnalyticsProvider
    | VoiceActivityProvider
    | SemanticTurnEvaluator
)


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Read-only discovery metadata for one registered provider. Exposes the
    provider's declared capability *flags* — never the provider itself, and
    never anything that would let a caller execute it."""

    kind: str
    name: str
    capabilities: Any
    is_default: bool
    registered_at: float


@dataclass(frozen=True, slots=True)
class RegistryEvent:
    """One registry lifecycle notification. NOT the Event Bus (M4) — a
    synchronous, best-effort observer hook, forwardable later."""

    kind: str
    name: str
    action: str  # "registered" | "unregistered"
    ts: float


RegistryObserver = Callable[[RegistryEvent], None]


class ProviderRegistry:
    """Thread-safe, deterministic management of capability-provider
    *references*. Never constructs, initializes, or calls a provider."""

    def __init__(self, *, on_change: RegistryObserver | None = None) -> None:
        self._lock = threading.RLock()
        self._providers: dict[tuple[str, str], Any] = {}
        self._meta: dict[tuple[str, str], ProviderMetadata] = {}
        self._defaults: dict[str, str] = {}  # kind -> name
        self._on_change = on_change

    # ── registration ─────────────────────────────────────────────────────
    def register(
        self,
        kind: CapabilityKind | str,
        provider: ProviderInstance,
        *,
        default: bool = False,
    ) -> ProviderMetadata:
        """Register an already-constructed provider instance for a capability
        kind. Validates compatibility; rejects duplicates and unsupported
        kinds. Never constructs or calls the provider."""
        with self._lock:
            kind_value = self._validate_kind(kind)
            self._validate_provider(kind_value, provider)
            name = provider.name
            key = (kind_value, name)
            if key in self._providers:
                raise DuplicateProviderRegistration(
                    f"provider '{name}' already registered for capability '{kind_value}'"
                )
            if default and kind_value in self._defaults:
                raise DuplicateProviderRegistration(
                    f"a default provider is already set for capability '{kind_value}'"
                )
            meta = ProviderMetadata(
                kind=kind_value,
                name=name,
                capabilities=provider.capabilities,
                is_default=default,
                registered_at=time.time(),
            )
            self._providers[key] = provider
            self._meta[key] = meta
            if default:
                self._defaults[kind_value] = name
            self._notify(kind_value, name, "registered")
            return meta

    def unregister(self, kind: CapabilityKind | str, name: str) -> None:
        """Remove a registered provider. Raises if it isn't registered — no
        silent no-op."""
        with self._lock:
            kind_value = self._validate_kind(kind)
            key = (kind_value, name)
            if key not in self._providers:
                raise CapabilityNotRegistered(
                    f"no provider '{name}' registered for capability '{kind_value}'"
                )
            del self._providers[key]
            del self._meta[key]
            if self._defaults.get(kind_value) == name:
                del self._defaults[kind_value]
            self._notify(kind_value, name, "unregistered")

    def replace(
        self, kind: CapabilityKind | str, provider: ProviderInstance, *, default: bool = False
    ) -> ProviderMetadata:
        """Atomic hot-swap: unregister any existing provider at the same
        (kind, name) then register the new instance. Supports future
        hot-swapping without the registry ever executing anything."""
        with self._lock:
            kind_value = self._validate_kind(kind)
            name = getattr(provider, "name", None)
            if name and (kind_value, name) in self._providers:
                self.unregister(kind_value, name)
            return self.register(kind_value, provider, default=default)

    # ── resolution ───────────────────────────────────────────────────────
    def resolve(self, kind: CapabilityKind | str, name: str | None = None) -> ProviderInstance:
        """Resolve a provider *instance* by capability, optionally by its
        provider-declared name. Callers request capabilities, never vendor
        identities — `name` is an opaque identifier, not a vendor branch."""
        with self._lock:
            kind_value = self._validate_kind(kind)
            candidates = {n: p for (k, n), p in self._providers.items() if k == kind_value}
            if not candidates:
                raise CapabilityNotRegistered(
                    f"no provider registered for capability '{kind_value}'"
                )
            if name is not None:
                if name not in candidates:
                    raise CapabilityNotRegistered(
                        f"no provider '{name}' registered for capability '{kind_value}'"
                    )
                return candidates[name]
            default_name = self._defaults.get(kind_value)
            if default_name is not None:
                return candidates[default_name]
            if len(candidates) == 1:
                return next(iter(candidates.values()))
            raise CapabilityNotRegistered(
                f"multiple providers registered for capability '{kind_value}' and no "
                "default is set; resolve() requires an explicit name"
            )

    # ── discovery ────────────────────────────────────────────────────────
    def has(self, kind: CapabilityKind | str, name: str | None = None) -> bool:
        with self._lock:
            try:
                kind_value = self._validate_kind(kind)
            except UnsupportedCapabilityKind:
                return False
            if name is not None:
                return (kind_value, name) in self._providers
            return any(k == kind_value for k, _ in self._providers)

    def list(self, kind: CapabilityKind | str | None = None) -> tuple[ProviderMetadata, ...]:
        """An immutable, deterministically ordered (registration order)
        snapshot of provider metadata. Never exposes provider instances."""
        with self._lock:
            if kind is None:
                return tuple(self._meta.values())
            kind_value = self._validate_kind(kind)
            return tuple(m for m in self._meta.values() if m.kind == kind_value)

    # ── internals ────────────────────────────────────────────────────────
    @staticmethod
    def _validate_kind(kind: CapabilityKind | str) -> str:
        value = kind.value if isinstance(kind, CapabilityKind) else str(kind)
        if value not in _CAPABILITY_BASES:
            raise UnsupportedCapabilityKind(f"unsupported capability kind: '{value}'")
        return value

    @staticmethod
    def _validate_provider(kind_value: str, provider: Any) -> None:
        if provider is None:
            raise InvalidProviderRegistration("provider must not be None")
        base = _CAPABILITY_BASES[kind_value]
        if not isinstance(provider, base):
            raise ProviderCapabilityMismatch(
                f"provider does not implement the '{kind_value}' capability "
                f"contract ({base.__name__})"
            )
        name = getattr(provider, "name", None)
        if not name or not isinstance(name, str):
            raise InvalidProviderRegistration("provider must declare a non-empty 'name'")
        if getattr(provider, "capabilities", None) is None:
            raise InvalidProviderRegistration("provider must declare 'capabilities'")

    def _notify(self, kind: str, name: str, action: str) -> None:
        log.info("provider registry: %s %s/%s", action, kind, name)
        if self._on_change is None:
            return
        event = RegistryEvent(kind=kind, name=name, action=action, ts=time.time())
        try:
            self._on_change(event)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort
            log.warning("registry observer raised (ignored): %s", exc)
