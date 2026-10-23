"""Configuration for the WebRTC deployment profile (M11.16.4).

Kept here rather than on `api.config.Settings` for a reason that is not stylistic: **Epic 10's
`E10-7` freezes every pre-existing `platform/api/` file** (ADR-0043 CD7), and additions are
permitted where modifications are not. This module is an addition, and so is everything else the
external WebRTC path adds. `api/main.py`, `api/config.py`, `api/schemas.py` and
`api/routers/sessions.py` are byte-identical to the frozen baseline, which is also why the default
deployment is untouched **by construction** rather than by care.

Every default below is the behaviour that existed before this milestone: the loopback transport,
no signalling surface, no cross-origin access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class WebRTCConfigError(ValueError):
    """The WebRTC profile was selected incompletely or unsafely."""


@dataclass(frozen=True)
class WebRTCSettings:
    """What a WebRTC deployment must state. No secret appears here.

    Provider credentials are resolved by the composition root through the ADR-0049 seam and are
    never named, read or held here; `voice_id` is configuration, not a credential.

    It is also deliberately **vendor-neutral**: which TTS provider the real profile uses is
    `REAL_PROVIDER_PROFILE`'s to say, and naming one here would put a vendor identifier in the
    Control Plane (AR-21)."""

    #: `local` (the default everywhere else) or `webrtc`. Selecting `webrtc` selects the **whole**
    #: real stack (`bootstrap.REAL_PROVIDER_PROFILE`): a real transport with echo providers is a
    #: configuration nobody wants and a half-real one nobody can reason about.
    transport: str = "local"

    #: Mount `/v1/webrtc/*` and the reference client. Implied by `transport="webrtc"`; settable on
    #: its own only for signalling-level testing.
    signalling: bool = False

    #: Browser origins allowed to call the API. Empty adds **no** CORS middleware at all, so a
    #: deployment that never asked for cross-origin access has nothing permissive to misconfigure.
    allowed_origins: tuple[str, ...] = ()

    #: The voice id the real profile's TTS provider speaks with. Required when
    #: `transport="webrtc"`; the provider itself is chosen by `REAL_PROVIDER_PROFILE`.
    voice_id: str | None = None

    #: `dev` relaxes exactly one rule: a wildcard origin.
    app_env: str = "dev"

    #: STUN servers this deployment offers its peer connections. **Empty means explicitly none**,
    #: and that emptiness is passed to the media library rather than omitted (M11.16.6, R-71):
    #: aiortc substitutes a public STUN server of its own whenever the configuration is absent, so
    #: the default had to become a decision rather than a gap. A deployment whose host does not
    #: carry its own public address on an interface — a cloud VM behind 1:1 NAT, for instance —
    #: needs one here, or its only candidate will be an unroutable private address.
    #: TURN is **not** implemented; a `turn:` URL is refused, not ignored.
    ice_stun_urls: tuple[str, ...] = ()

    #: The half-open `[lo, hi)` UDP range media sockets must bind inside, as the raw
    #: `"LOW-HIGH"` specification. Empty (the default) leaves media ports to the operating
    #: system's ephemeral range, which is what every deployment had before M13.2. Carried as a
    #: string and validated by the composition root, for the same AR-14 reason as the ICE URLs
    #: above: parsing it here would mean `api.webrtc.config` importing `providers`.
    media_udp_port_range: str | None = None

    @property
    def webrtc_enabled(self) -> bool:
        return self.transport == "webrtc"

    @property
    def signalling_enabled(self) -> bool:
        return self.webrtc_enabled or self.signalling

    def validate(self) -> None:
        """Refuse an incomplete or unsafe configuration at startup (§A2.6, AR-25).

        Incomplete means *refused*, never *downgraded*: a deployment that asked for real media and
        silently got the loopback would be told a lie about its own configuration."""
        if self.transport not in ("local", "webrtc"):
            raise WebRTCConfigError(f"unknown transport profile {self.transport!r}")
        if self.webrtc_enabled and not self.voice_id:
            raise WebRTCConfigError("transport=webrtc requires VOXVOICE_TTS_VOICE_ID")
        if "*" in self.allowed_origins and self.app_env != "dev":
            raise WebRTCConfigError(
                "wildcard WEBRTC_ALLOWED_ORIGINS is refused outside APP_ENV=dev"
            )
        # The ICE URLs are carried here as plain strings and validated by the composition root
        # (`app.build_webrtc_app`), which is the layer permitted to know the provider plane exists
        # (AR-14). Validating here would mean `api.webrtc.config` importing `providers`.

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> WebRTCSettings:
        source = os.environ if env is None else env
        raw = (source.get("WEBRTC_ALLOWED_ORIGINS") or "").strip()
        stun = (source.get("VOXVOICE_ICE_STUN_URLS") or "").strip()
        ports = (source.get("VOXVOICE_MEDIA_UDP_PORT_RANGE") or "").strip()
        return cls(
            ice_stun_urls=tuple(u.strip() for u in stun.split(",") if u.strip()),
            media_udp_port_range=ports or None,
            transport=source.get("RUNTIME_TRANSPORT", "local"),
            signalling=source.get("WEBRTC_SIGNALLING", "false").lower() == "true",
            allowed_origins=tuple(o.strip() for o in raw.split(",") if o.strip()),
            voice_id=source.get("VOXVOICE_TTS_VOICE_ID") or None,
            app_env=source.get("APP_ENV", "dev"),
        )


__all__ = ["WebRTCConfigError", "WebRTCSettings"]
