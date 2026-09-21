# Epic 12 — Developer Platform: Product Vision

| | |
|---|---|
| **Status** | Living (M12.1) |
| **Date** | 2026-09-21 |
| **Related** | [HLD](m12-hld.md) · [Frontend architecture](m12-frontend-architecture.md) · [Plan](../../roadmap/epic12-implementation-plan.md) |

## 1. The question Epic 12 answers

M11 answered *"Can VoxVoice run a real voice conversation?"* Epic 12 answers *"Can a developer
actually **use** VoxVoice as a product?"*

The finished Epic 12 experience, running locally:

```
LOGIN → OVERVIEW → CREATE AGENT → CONFIGURE → TEST (real voice) → VIEW CONVERSATION
      → VIEW METRICS → CREATE API KEY → INTEGRATE VIA API / SDK
```

## 2. Who it is for

* **The builder.** A developer shipping a voice feature. They want to hear an agent within
  minutes and then wire it into their own app with an API key and an SDK.
* **The operator.** The same person later, checking that calls work and are fast enough.

VoxVoice is in the same product category as hosted voice-agent platforms. It is **not** a clone of
any of them. Its differentiators come from the runtime it already has: streaming everywhere,
per-turn latency measured at every boundary, explicit degraded modes, and providers that are
plugins rather than architecture.

## 3. Product principles

1. **Voice first.** The Playground is the centre of the product. The shortest path is
   *sign up → create agent → talk to it*: three screens, not ten.
2. **Honest surfaces.** The console shows only what the runtime really does. An agent setting that
   does not reach the runtime is not shown (AR-25). A latency that was not measured is shown as
   "not measured", never 0. There are no invented usage or cost numbers.
3. **Real from the first click.** Every console feature calls the real API and the real runtime.
   Mocks exist only inside tests.
4. **Developer-grade clarity.** Status, IDs, timings and errors are visible and copyable, and every
   error says what to do next.
5. **Secure by default.** No key in the browser. One-time key reveal. Revocation that works
   immediately.

## 4. Information architecture

```
Sidebar                         Purpose                                         Milestone
├── Overview                    runtime status, getting-started, recent activity   M12.1 → M12.8
├── Agents                      list · create · configure · archive                M12.3
├── Playground                  pick an agent, talk to it, see live status         M12.4
├── Conversations               sessions, transcript, per-turn latency, timeline   M12.5
├── Analytics                   sessions, turns, duration, voice-to-voice p50/p95  M12.6
├── API keys & SDK              keys, quickstart, SDK docs, examples               M12.7 / M12.9
└── Settings                    organization, members, account                     M12.2
Top bar: organization · runtime status (transport, signalling) · user menu
```

An item whose capability the server does not declare is shown as *not yet available*, with the
milestone that brings it. Nothing is faked.

## 5. What Epic 12 will not do

Deployment (Stage 2, DNS, TLS, TURN) · billing or pricing · provider consumption metering · a
second runtime · changes to Epic 10 · telephony · team invitations and SSO (the design leaves room
for them) · converging with the Epic 8 `cp_*` platform (DL-0235).
