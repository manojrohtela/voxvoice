# ADR-0054 — ICE server policy is explicit at the composition root, never inherited from the media library

| | |
|---|---|
| **Status** | **ACCEPTED 2026-09-20** (raised and resolved at M11.16.6). It records a *deployment/topology* decision and the implementation that enforces it. It **amends no Part A section** and changes no capability contract. |
| **Kind** | Transport/deployment ADR. |
| **Deciders** | Technical Owner |
| **Related** | Constitution §A4 (plane separation), **AR-12** (vendor SDK confined to the plugin), **AR-25** (no dead config); [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md); [ADR-0049](ADR-0049-provider-credential-injection.md); **R-71**; DL-0228, DL-0229 |

## Context

### What was discovered

M11.16.5 gathered ICE on a NAT'd host as read-only evidence and found **two** candidates where the
documentation promised one:

```
typ=host   scope=PRIVATE   count=1
typ=srflx  scope=PUBLIC    count=1
```

A server-reflexive candidate can only come from a STUN server, and **nothing in this repository
configured one**. The cause is in `aiortc` 1.15.0 (`rtcicetransport.py`):

```python
if iceServers is None:
    iceServers = self.getDefaultIceServers()   # -> [stun:stun.l.google.com:19302]
```

`AiortcMediaEndpoint.connect()` constructed `RTCPeerConnection()` with no configuration. Therefore
**every peer connection this platform has ever created contacted a third-party STUN service at
call setup**, from M11.5 onward.

### Why that is a defect rather than a detail

It is not that STUN is wrong. It is that **nobody decided it**:

* it appears in no ADR, no configuration surface and no deployment document;
* it is a **third-party outbound dependency on the media path** — an availability and privacy
  surface that no operator opted into and none can monitor;
* it **silently decides ICE behaviour**: a firewall that blocks it changes which candidates exist,
  and the symptom is a call that negotiates and then carries no audio;
* `docs/providers/webrtc-external-client.md` §8 stated the opposite — *"host candidates only. No
  STUN or TURN is configured"* — so the documentation and the behaviour disagreed, and the
  documentation was the one people read.

For a platform whose architecture is *providers are explicit and injected*, a network topology
chosen by a library default is the same class of problem as a provider that selects itself.

## Decision

### IC1 — The composition root always states the policy

An `RTCConfiguration` is passed on **every** `RTCPeerConnection` construction, including when the
policy is "no ICE servers". The library's `iceServers is None` branch is never reached.

### IC2 — The default is *explicitly none*

`NO_ICE_SERVERS` is passed as `iceServers=[]`. **Passing nothing and passing nothing-to-use are
different facts**, and only the second is a decision. The default is none because:

* a voice platform should not reach a third party on the media path unless its operator said so;
* the authorized Stage-2 shape is *"any host with a public IP"*, which needs no STUN;
* an undeclared dependency is an unmonitored one.

### IC3 — STUN is configured, not assumed

A deployment that needs server-reflexive candidates states them:
`VOXVOICE_ICE_STUN_URLS=stun:host:port[,…]`. The value is carried through
`WebRTCSettings` as plain strings and translated into an `IcePolicy` by the composition root
(`api.webrtc.app.resolve_ice_policy`), which is the layer permitted to know the provider plane
exists (AR-14). `api.webrtc.config` imports no provider module, asserted by test.

### IC4 — TURN is refused, not ignored

`turn:`/`turns:` URLs raise at **startup**. TURN relaying is **not implemented**, and accepting its
configuration would let a deployment believe it had relaying that does not exist — a failure that
presents as silence during a call rather than as an error before one.

### IC5 — The policy is observable

`AiortcMediaEndpoint` logs the policy once at construction, and `/health` reports it. An operator
can ask a running deployment what it decided instead of inferring it from a packet capture.

### IC6 — What STUN is, stated so documentation cannot drift again

STUN lets a host **discover** the public address/port its NAT mapped for it. It **does not relay
media** and **does not guarantee traversal**; symmetric NATs, restrictive firewalls and blocked UDP
all defeat it. When direct connectivity is genuinely impossible the answer is TURN, which relays,
and which remains a **deployment prerequisite** rather than a component of this system.

## Consequences

- `providers/transport/ice.py` (new, library-free) holds `IcePolicy`, its validation and
  `NO_ICE_SERVERS`. `aiortc_endpoint.py` — the one module permitted to import the library — is the
  only place that translates it into `RTCConfiguration`.
- `bootstrap.build_webrtc_media_endpoint(..., ice_policy=…)` threads it through the documented
  composition path.
- **A deployment implication that must be verified on the real host:** where a cloud VM's public
  IPv4 is 1:1 NAT-mapped rather than carried on the interface, the only host candidate will be an
  unroutable private address, and such a deployment **must** configure STUN or it will negotiate
  and carry no media. This is stated as a prerequisite to check, not as a measured fact — no
  Stage-2 host exists yet.
- **R-71 is closed** by this decision plus its implementation, tests and documentation correction.
  Closing it on the documentation fix alone would have left the library default in place.
- No behaviour change for the hermetic suites: they never reached a real network, and the
  aiortc↔aiortc tests gather host candidates on loopback either way.

## Alternatives considered

- **Keep the library default and just document it.** Rejected: it leaves a third-party dependency
  on the media path that no operator chose, and it makes the platform's behaviour a function of a
  dependency's internals.
- **Hard-code Google's STUN server explicitly.** Rejected: it makes the dependency visible but
  still imposes it, and it points a voice platform's media path at one company's free service with
  no contract, no SLA and no configuration.
- **Ship STUN on by default with an override.** Rejected: a default that reaches the network is
  the thing being fixed. Deployments that need it are exactly the ones able to say so.
- **Add TURN configuration now.** Rejected as scope: M11.16.6 explicitly forbids building relay
  infrastructure, and configuration for a component that does not exist is worse than its absence.
- **Put the policy on `AgentSpec`.** Rejected: ICE topology is a property of the deployment, not of
  an agent, exactly as transport selection is.
