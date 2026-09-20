"""ICE server policy — stated by the composition root, never inherited from the library (M11.16.6).

## Why this module exists

M11.16.5 measured ICE gathering on a NAT'd host and found **two** candidates where the
documentation promised one: a `host` candidate and a **`srflx`** candidate. A server-reflexive
candidate can only come from a STUN server, and nothing in this repository configured one.

The cause is in `aiortc` (1.15.0):

```python
if iceServers is None:
    iceServers = self.getDefaultIceServers()   # -> [stun:stun.l.google.com:19302]
```

`AiortcMediaEndpoint` constructed `RTCPeerConnection()` with no configuration, so **every call
setup silently contacted a third-party STUN service** — an outbound dependency that appeared in no
ADR, no configuration surface and no deployment document, and that silently decides ICE behaviour
if a firewall blocks it. That is **R-71**, and for a platform whose architecture is *providers are
explicit and injected*, a network topology decision made by a library default is the same class of
problem as a provider that picks itself.

## The policy

**The composition root always states the policy; the library default is never reached.** An
`RTCConfiguration` is passed on every construction, including when the answer is "no ICE servers"
— because *explicitly none* and *whatever the library felt like* are different facts, and only the
first one is a decision.

The **default is no ICE servers**, which is a deliberate choice rather than an absence:

* a voice platform should not reach a third party on the media path unless its operator said so;
* a deployment on a host whose interface carries its public address does not need STUN at all, and
  the authorized Stage-2 shape is *"any host with a public IP"*;
* a dependency nobody declared is a dependency nobody monitors.

A deployment that **does** need server-reflexive candidates states its STUN servers. See
`docs/adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md`.

## What STUN is, and is not

STUN lets a host **discover** the public address/port its NAT has mapped for it, so it can offer
that as a `srflx` candidate. That is all it does:

* STUN **does not relay media** — packets still flow host-to-host;
* STUN **does not guarantee traversal** — symmetric NATs, restrictive firewalls and blocked UDP
  all defeat it;
* when direct connectivity is genuinely impossible, the answer is **TURN**, which relays.

**TURN is not implemented here** and `turn:`/`turns:` URLs are refused rather than silently
ignored, so a deployment cannot believe it has configured relaying when it has not. TURN remains a
*deployment prerequisite* for topologies where direct connectivity fails.

This module imports no library. The translation into `RTCConfiguration` happens inside
`aiortc_endpoint.py`, which is the one module permitted to know aiortc exists (AR-12).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: Schemes this policy accepts. Deliberately not `turn`/`turns`: see the module docstring.
_STUN_SCHEMES = ("stun:", "stuns:")
_RELAY_SCHEMES = ("turn:", "turns:")


class IcePolicyError(ValueError):
    """An ICE policy was stated that this deployment cannot honour.

    A `ValueError` because it is a composition-time configuration fault, the same shape as an
    unknown provider profile — it should stop startup, not a call."""


@dataclass(frozen=True)
class IcePolicy:
    """The ICE servers this deployment offers its peer connections. Immutable and library-free.

    An empty `stun_urls` means **explicitly no ICE servers**, which is the default and is passed
    to the library as an empty list rather than omitted."""

    stun_urls: tuple[str, ...] = ()

    @property
    def uses_stun(self) -> bool:
        return bool(self.stun_urls)

    @classmethod
    def from_urls(cls, urls: Iterable[str] | None) -> IcePolicy:
        """Validate and build. Refuses anything that is not a STUN URL."""
        cleaned: list[str] = []
        for raw in urls or ():
            url = raw.strip()
            if not url:
                continue
            lowered = url.lower()
            if lowered.startswith(_RELAY_SCHEMES):
                raise IcePolicyError(
                    f"{url!r} is a TURN URL; TURN relaying is not implemented in this deployment. "
                    "Configuring it here would claim relaying that does not exist — TURN is a "
                    "deployment prerequisite, not a setting."
                )
            if not lowered.startswith(_STUN_SCHEMES):
                raise IcePolicyError(
                    f"{url!r} is not a STUN URL; expected one beginning with 'stun:' or 'stuns:'"
                )
            if url not in cleaned:
                cleaned.append(url)
        return cls(stun_urls=tuple(cleaned))

    def describe(self) -> str:
        """A log-safe one-liner. STUN URLs are hosts, not secrets, but the shape is what matters."""
        if not self.stun_urls:
            return "no ICE servers (explicit; host candidates only)"
        return f"{len(self.stun_urls)} STUN server(s) (host + server-reflexive candidates)"


#: The default: **explicitly** no ICE servers. Not the same thing as passing nothing (R-71).
NO_ICE_SERVERS = IcePolicy()


__all__ = ["NO_ICE_SERVERS", "IcePolicy", "IcePolicyError"]
