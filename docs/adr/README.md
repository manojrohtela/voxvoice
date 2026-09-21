# Architecture Decision Records (ADR)

ADRs are the second-highest authority in VoxVoice (below the Engineering
Constitution, above governance) — see [`.voxvoice/README.md`](../../.voxvoice/README.md)
authority hierarchy. Process: [`.voxvoice/DOCUMENTATION_RULES.md`](../../.voxvoice/DOCUMENTATION_RULES.md) §3.

## Kinds

- **Constitutional ADR** (`ADR-NNNN-slug.md`) — amends Constitution **Part A** (timeless
  architecture). Full template: Problem · Context · Alternatives Considered ·
  Selected Solution · Trade-offs · Risks · Future Impact. Owner approval required.
- **Profile ADR** (`P-ADR-NNNN-slug.md`) — amends Constitution **Part B** (Implementation
  Profile — today's vendors/frameworks). Lightweight: what row changes, why, migration note.
- **Epic 6 Agent Platform ADR** (`ADR-NNNN-slug.md`, same numbering sequence as
  Constitutional ADRs — no separate prefix) — records a load-bearing architectural
  decision within the Epic 6 Agent Platform (`docs/architecture/epic6/`) that does
  not itself amend Constitution Part A or B. Template: Status · Context · Decision ·
  Consequences · Alternatives Considered (per the M6.6.5 Documentation & ADR Closure
  milestone). Introduced starting at ADR-0008; the two existing kinds above predate
  Epic 6 and did not anticipate capability-internal architecture decisions of this
  kind, which is why this is recorded as a third kind rather than forced into either.

## Rules
- Numbers are sequential and never reused. Status: `Proposed → Accepted → Superseded by ADR-XXXX`.
- Accepted ADRs are **immutable**; to change course, write a superseding ADR (never edit).
- Every ADR is linked from [`docs/INDEX.md`](../INDEX.md) and cross-referenced from the docs it affects.

## Register

| ADR | Title | Amends | Status |
|---|---|---|---|
| [ADR-0001](ADR-0001-first-party-runtime.md) | First-party voice runtime; retire whole-engine adapter axis | Part A (D1) | Accepted 2026-07-16 |
| [ADR-0002](ADR-0002-substrate-behind-contracts.md) | Pipeline substrate confined behind owned contracts | Part A (D2) | Accepted 2026-07-16 |
| [ADR-0003](ADR-0003-two-plane-architecture.md) | Two-plane architecture (Control / Runtime) | Part A (D3) | Accepted 2026-07-16 |
| [ADR-0004](ADR-0004-event-driven-core.md) | Event-driven core with one typed envelope | Part A (D4) | Accepted 2026-07-16 |
| [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md) | Streaming-first ingestion + Turn Intelligence layer | Part A (D5) | Accepted 2026-07-16 |
| [ADR-0006](ADR-0006-single-system-of-record.md) | Single System of Record; brokers only when needed | Part A (D6) | Accepted 2026-07-16 |
| [ADR-0007](ADR-0007-keep-control-plane-delete-adapters.md) | Keep control-plane assets; delete engine-adapter axis | Part A (D7) | Accepted 2026-07-16 |
| [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md) | Adopt Implementation Profile v1 | Part B | Accepted 2026-07-16 |
| [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md) | Implementation Profile: Epic 11 — **retires the Pipecat pipeline-substrate binding** (ADR-0002's *"may"* exercised in the negative; **AR-13 retained**, now vacuously satisfied), replaces *"WebRTC via substrate's small-WebRTC transport"* with a **first-party WebRTC Provider Plugin** whose library is confined under **AR-12** — which dissolves the AR-11/AR-13/Part B three-way conflict **without any Part A amendment**; re-affirms the Deepgram / Sarvam / Cartesia / Groq / Anthropic / Supabase bindings unchanged; declines the `voicebot/bot.py` prototype's Groq-Whisper-as-STT divergence; binds **`uv`** with a committed lockfile as the release artifact and a CI vulnerability audit; and resolves the observed Python drift (Part B `3.12` vs `pyproject >=3.11` vs venv `3.13`) to **floor 3.12, interpreter pinned by lockfile + CI matrix**. **Supersedes two P-ADR-0001 rows; P-ADR-0001 is not edited.** Three Part B rows changed, one added | Part B | Accepted 2026-08-24 |
| [ADR-0008](ADR-0008-skill-manifest-not-code.md) | Skills are a versioned manifest, not code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) | Workflow execution state is the platform's one durable-repository exception | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0010](ADR-0010-hook-framework-cross-cutting-library.md) | The Hook Framework is a cross-cutting library, not a `core.ports` runtime | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| ADR-0011 | MCP/A2A are bridge-pattern adapters, never a `ToolPort`/`AgentPort` redesign | Epic 6 Agent Platform Architecture | Reserved — not yet ratified (MCP/A2A not yet designed) |
| [ADR-0012](ADR-0012-tool-contract-manifest-plus-adapter.md) | Tool contract is manifest + registered adapter, never manifest-supplied code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0013](ADR-0013-dual-fsm-goal-scoped-for-agent.md) | Dual-FSM closed-runtime pattern; Agent's second FSM attaches to the Goal, never the identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0014](ADR-0014-structure-intelligence-correctness-split.md) | Structure, intelligence, and correctness are distinct ownership categories | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0015](ADR-0015-agent-workflow-port-seam.md) | Agent Runtime delegates via its own `AgentWorkflowPort` seam, not literal `WorkflowPort` | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0016](ADR-0016-loop-engine-no-registrable-identity.md) | Loop Engine has no registrable identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0017](ADR-0017-execution-context-duplication-lineage.md) | Execution-context shapes extend by structural duplication within one delegation lineage; Loop Engine is deliberately outside it | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) | Product Runtime is a declarative composition layer above the frozen Execution Platform, in a new `platform/product/` package | Epic 7 Product Platform (new) | Accepted 2026-07-26 |
| [ADR-0019](ADR-0019-binding-resolution-against-registry-snapshot.md) | Binding resolution resolves against an injected immutable RegistrySnapshot, never live Execution Platform runtimes | Epic 7 Product Platform (refines ADR-0018) | Accepted 2026-07-26 — **terminology superseded by ADR-0020**: the type ADR-0019 calls `RegistrySnapshot`/`RegistryEntry` is implemented as `BindingSnapshot`/`BindingSnapshotEntry` (`product.compiler`); ADR-0019's substance is unchanged |
| [ADR-0020](ADR-0020-registry-nouns-reserved-for-product-registry.md) | "Registry" nouns are reserved for the Product Registry of packages; the compiler's binding catalog is a `BindingSnapshot` | Epic 7 Product Platform (refines ADR-0019 terminology) | Accepted 2026-07-27 |
| [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) | VoxVoice Studio is a Consumer + Management Surface; it owns no runtime state (`Studio → Platform`, one-way) | Epic 8 Customer Platform (new) | Accepted 2026-07-27 |
| [ADR-0022](ADR-0022-deployment-actuator-runtime-actuation.md) | The Deployment Actuator is the sole owner of deployment execution; Product Runtime stays declarative (`actuator → product`, read-only, one-way) | Epic 8 Customer Platform (new) | Accepted 2026-07-27 |
| ADR-0024 | Coach (AI Product Manager) autonomy runs through the same request/approval/audit path, never a privileged one | Epic 8 Customer Platform | **Reserved — not yet ratified** (recommended by ADR-0021; Coach autonomy not yet designed) |
| [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md) | The Billing Platform is the sole owner of revenue; it owns nothing else and meters the authoritative `metric.*` stream (`Billing → Platform`, read-only, one-way) | Epic 9 Billing & Subscription Platform (new) | Accepted 2026-08-01 |
| ADR-0025…ADR-0030 | Recommended Epic 9 architecture batch — entitlement mechanics · money/ledger discipline · payment & tax rented edges · critical-path prohibition · commerce/licensing reuse · billing-account hierarchy | Epic 9 Billing & Subscription Platform | **Reserved — recommended, not yet ratified** ([Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26) |
| [ADR-0031](ADR-0031-billing-plane-repository-placement.md) | The Billing Platform lives permanently in `platform/billing/`, a self-contained top-level plane importing `core` only; surfaced additively through the Epic 8 layers; never wired into the runtime composition root | Epic 9 repository topology (structural placement) | Accepted 2026-08-01 |
| [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) | Event durability and delivery semantics: **at-least-once**, append-only, one event identity and one sequence authority, transactional outbox, bounded retry ladder, distinct DLQ and quarantine, explicit retention — all behind the **unchanged** `core.events.EventBus` contract with no producer change (DL-0017's deferred work) | Epic 10 Durable Event & Telemetry Spine (new) | Accepted 2026-08-18 |
| [ADR-0033](ADR-0033-metric-stream-seam.md) | The metric-stream seam is **pull-shaped**: a platform-owned buffer subscribes to the `EventBus` and Billing pulls through the frozen `MetricStreamPort`, never subscribing itself; `metric.*` is extended additively under AR-32; runtime `metric.*` is owned by `platform/observability/` while **`metric.billing.*` stays reserved to Billing** | Epic 10 (implements against ADR-0023 + ADR-0031; amends neither) | Accepted 2026-08-18 |
| [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) | Durable storage seam for `core`-only edge packages: a scoped `platform/events/persistence/**` sub-package may import `sqlmodel`/`sqlalchemy` and owns its own `Database` (the ADR-0031 IR1a precedent generalized), enforced by per-layer boundary assertions; migrations continue the single global chain at **0034** with the **`evt_*`** prefix | Epic 10 structural/dependency-inversion seam | Accepted 2026-08-18 |
| [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) | Durable write interposition on the in-process Event Bus — **completes ADR-0032 where it is silent, supersedes nothing**: for a producer with no originating transaction the durable append **is** the state change (ED6 unweakened); an additive, default-disabled persistence seam inside `InProcessEventBus` writes **after `seq` stamping and before enqueue**, so no subscriber ever observes a non-durable event; a failed durable write never raises into the producer, is never dispatched, never rewinds `seq`, and surfaces through a typed hook; immutable `evt_event` is separated from a mutable **`evt_outbox`** (one row per event) that supplies ED9's `un-drained` predicate | Epic 10 M10.6 (durable write-side integration) | Accepted 2026-08-18 |
| [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) | Outbox retry ladder, dead-letter and quarantine — **completes ADR-0032 ED7/ED8 and continues ADR-0035 DW5/DW6; supersedes nothing**. A five-state lifecycle declared with `core.fsm` (`PENDING`/`RETRYING`/`DELIVERED`/`DEAD_LETTERED`/`QUARANTINED`, the last three **terminal**); a **pure, I/O-free** `OutboxLadder` that decides but never sleeps, loops, polls, schedules or dispatches; `DeliveryOutcome` reaching it through an **injected classifier** (no transport taxonomy in `events/`); `core.policies.RetryPolicy` reused with delivery values (5 attempts, 30s ×2 capped 1h, **no jitter**); append-only **`evt_outbox_failure`** as authoritative error evidence; transition + history in **one transaction**; migration `0036`. **Resolves the M10.7/M10.8 boundary**: recovery, restart re-dispatch and the driver are **M10.8** | Epic 10 M10.7 (retry ladder + DLQ + quarantine) | Accepted 2026-08-19 |
| [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) | Outbox recovery and restart re-dispatch — **completes ADR-0035 DW7 and ADR-0036 RL1; supersedes nothing**. A **caller-driven, deterministic** `recover(now, limit)` sweep on the `context`/`memory` `expire()` precedent — **no scheduler, worker, thread, timer or loop**; one sweep over `PENDING` + due `RETRYING`, ordered `(seq, event_id)`, terminal states never selected, **zero new FSM transitions**; `begin_attempt` before delivery; `DeliveryTransport` stays a **pure port with no production implementation**; concurrency an **accepted non-claim** (no claim/lease/lock/dedup); **no migration and no schema change**; `MetricStreamPort.replay()` stays **M10.10** | Epic 10 M10.8 (recovery + restart re-dispatch) | Accepted 2026-08-19 |
| [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) | Runtime latency taps and the `metric.turn` producer — **completes ADR-0033 MS8; supersedes nothing**. Makes **AR-42** satisfiable: plan §1.12 excludes Epic 11 rework, not the minimal **additive, optional, notify-only** seam on `RuntimePipeline`. Six enumerated boundaries; `playback_start` + terminal outcome are lifecycle markers; the pipeline is **segment-driven** so derived durations follow the real order and **never go negative**; one **injected monotonic** clock owned by the tap; **one emission per terminal turn** (publish is synchronous run-to-completion); absent boundary ≠ 0; bounded per-turn state; logged failure isolation; adds `EventSource.OBSERVABILITY`; no migration, buffer, retention or new `EventType` | Epic 10 M10.9 (observability build-out + `metric.turn`) | Accepted 2026-08-19 |
| [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) | The platform metric-stream buffer and the first `api → billing` edge — **completes ADR-0033 MS3–MS7; supersedes nothing**. Resolves the M10.10 conflict between **MS3**/plan §M10.10 (*"subscribes to the bus"*) and **DW11** (*"an outbox destination under DW5, not an in-process subscriber"*): **owner precedence ruling — in-process `EventBus` subscription**, scoped strictly to M10.10's delivery mechanism; **ADR-0035 is not amended, not superseded, not reinterpreted**, and DW11 stays in force for every other purpose. The outbox path is not executable here — RC9 defers a concrete `DeliveryTransport`, DW10/RL17/RC16 keep durable runtime wiring unwired — and an in-process synchronous subscriber sits correctly **outside** DW5. One process-wide **bounded** in-memory buffer (fixed **10 000** in a named constant, no configuration surface, **drop-oldest** + counter, rate-limited `WARNING`, **no persistence/migration/flush/shutdown hook — lost on process exit**); **enforceable hot-path budget** (bounded O(1), no I/O, blocking, reentrant `publish()`, thread or hot-path lock, on a `deque(maxlen=…)` equivalent); **destructive** org-keyed insertion-ordered `drain`, **best-effort non-destructive `replay` with no historical guarantee**, no cursor model; `Event.seq` as **provenance only** (per-bus, per-session, no global ordering, no second sequence authority); three distinct counters for malformed / missing `voice_to_voice_ms` / empty `org_id`, each producing **no signal** rather than invented usage (CV10, DL-0013); first **`api → billing`** edge, acyclic under AR-15, Billing untouched and `NullMetricStream` still its default. **Architecture only — does not authorize implementation**. **Amendment 1 (2026-08-19, additive; MB1–MB13 unchanged, not superseded)** fixes the decoder mapping (`producer = "observability.turn_latency"`, `payload_digest = SHA-256` over canonical sorted-key/compact/UTF-8 JSON of **`Event.data` only**, `sequence = event.seq`, `observed_at = event.ts`, `raw_unit = "ms"`) and records a **frozen-boundary blocker**: `MeterSource` has **no `observability` member**, and `RUNTIME_SESSION`/`CONTROL_PLANE_API` would let `accepts()`'s empty allow-list **silently fold turn latency into an unrelated commercial meter** — so `MeterSource.OBSERVABILITY` needs a **separate additive Billing vocabulary authorization**, ungranted, and **implementation stays blocked behind it** | Epic 10 M10.10 (metric-stream seam) | Accepted 2026-08-19 |
| [ADR-0040](ADR-0040-durable-log-historical-re-derivation-for-shadow-validation.md) | The durable event log is the historical source for M10.11 shadow validation — **completes ADR-0033's M10.11 standing rule; supersedes nothing**, and **is** the *"different mechanism, separately ratified"* **ADR-0039 MB6** required. M10.11 was **not executable**: Billing's frozen `replay_period`/`ReconcileUsagePeriod` read `MetricStreamPort.replay()`, but MB5's **destructive `drain`** removes exactly the signals MB6's **resident-only** replay would need, so every closed period replays as `()` and the standing rule would report **guaranteed total divergence**. **SV1** the **durable log** (`evt_event`) is the historical source; **SV2** the buffer gains **nothing** (MB3/MB5/MB6 unchanged); **SV3** `MetricStreamPort` frozen, the path is **validation**, not live metering; **SV4** Billing frozen **even when it disagrees**; **SV5** **hermetic proof, no production wiring**; **SV6** the whole flow exercised; **SV7** **no second metering algorithm** — Billing's engine is the comparison authority; **SV8** agreement **and** injected-discrepancy divergence both proven; **SV9** **not** under `api/metering/`, its edge and `events` prohibition unweakened, tests preferred; **SV10** `DurableEventLog` as-is, in-memory `read_all()` filter, not a production query path. SI1–SI11, R1–R8. **Architecture only — does not authorize implementation** | Epic 10 M10.11 (shadow validation) | Accepted 2026-08-20 |
| [ADR-0041](ADR-0041-retention-guards-drained-by-outbox-and-the-metered-watermark.md) | Retention guards — *"drained"* is durable outbox state, *"metered"* is an injected attestation — **completes ADR-0032 ED9 and ADR-0034 DS12/DS13; supersedes nothing**. Unblocks **M10.5**, deferred since M10.2. **RT1** resolves a three-way conflict by authority: **ADR-0035 DW5** and `events/ports.py` outrank **DL-0141** (Level-3) and **plan §M10.5**, so **ADR-0035 is not amended**; **RT2** confirms it semantically — `publish()` is run-to-completion after the DW3 durable write, so an in-process subscriber can never lag and has nothing to guard. **RT3** `DELIVERED` = drained; **`DEAD_LETTERED`/`QUARANTINED` are not** — terminality is not consumption. **RT4** ADR-0039 **MB1/MB3/MB5/MB6 unamended** and are the *reason* durable outbox state is used. **RT6** the events-declared **`MeteredWatermark`** port is supplied from outside the plane, is an input never a derivation, and absent ⇒ retain — `events/ → billing` stays forbidden, **no PL-2/IR4 widening** (**RT10**). **RT7** five-part safe-to-delete; **RT8** default-deny as behaviour; **RT9** hermetic, **no production wiring**, and not the *"inert subset"* DL-0141 rejected; **RT11** no migration, EV4 intact; **RT13** **DL-0141 stays Open**. RI1–RI12, RR1–RR9, RV1–RV13. **Architecture only — does not authorize implementation** | Epic 10 M10.5 (retention) | Accepted 2026-08-20 |
| [ADR-0042](ADR-0042-m10.12-hardening.md) | M10.12 Hardening: structural latency assurance, failure drills, and read-only outbox operator counts — **completes plan §M10.12; supersedes nothing**. §M10.12 was two lines naming four workstreams with no design gate, no acceptance criteria and no non-goals — the only Epic 10 milestone with architectural content lacking one. **HD1–HD3** performance is **structural assurance**, not measurement: ADR-0038's post-terminal ruling and TP10 are the baseline and M10.12 adds regression guards only; no benchmark and no production p50/p95, since PERFORMANCE_POLICY §5.1 gates that at *Stage 2+* against a baseline `docs/performance/` does not yet contain, and **I1 stays an explicit Epic 11 obligation**. **HD4–HD6** failure drills are **deterministic, hermetic, caller-driven test scenarios** over seven already-defined failure modes — no new state, transition, API, scheduler, thread, timer or `sleep`. **HD7–HD10** exactly `count_dead_lettered()` and `count_quarantined()` on `DurableOutbox` — point-in-time, total, read-only, over the existing indexed `state`; **observability, not operator control**, leaving **RL3/RL4** intact. **HD11** no migration (`0036` holds). **HD12** no production wiring. **HD13** `DurableEventLog`, retention, EV4/EV8/SV9 and `events → billing` unchanged. **HD14** guards additive-only. **HD15** M10.13+ and Epic 11/12 out of scope. **HD16** implementation, validation and commit each separately authorized | Epic 10 M10.12 (hardening) | Accepted 2026-08-21 |
| [ADR-0043](ADR-0043-m10.13-certification.md) | M10.13 Certification: a fresh Epic 10 control set, its two historical exceptions, and what it may not touch — **completes plan §M10.13; supersedes nothing**. Unlike §M10.12, §M10.13 is **well specified** — thirteen named minimum controls — but a read-only pre-flight found **two of them factually false against authorized repository state** and **one mandatory action already discharged**. **CD1** a fresh **`E10-1` … `E10-13`** namespace, disjoint from Epic 9's `DT/NS/OWN/PL/PY/RS/ST/TX`; **CD2** Epic 9's `catalogue.py`/`runner.py` **not edited** — 27 controls, nine limitations, `m9.24.1` all stand — and Epic 10's suite is an **independent module beside them**, never adding to, renumbering, reusing, wrapping or re-exporting them; **CD3** Epic 9's 27 are **never executed** as M10.13 controls; **CD4** every control **runs**, and `INCONCLUSIVE` does **not** pass. **CD5** `billing/**` is certified unchanged since `v4.0.0` **except the single additive `MeterSource.OBSERVABILITY` member** (ADR-0039 Amendment 1 **MB14**) — a **closed, exact allow-list** of file, symbol and authorizing decision, never a glob or waiver; a **narrow historical exception, not a general billing exception**, and **it does not grow**. **CD6** *"digests reproduced verbatim as historical state"* means the **original** values (`eed69222…`, `39790b95…`) stay **provable from `v4.0.0`**; the authorized post-M10.10 artifacts are **not reverted**, both states are certified as **distinct**, and M10.10 is not reopened. **CD7** *"no pre-existing `api/` file modified"* means **modification**, so `api/metering/**` **additions** are not violations. **CD8** digest ⟂ verdict. **CD9** `NS-5`, `ST-3` and nine `LIM-` limitations stay open with **unchanged owners**; **CD10** LIM-7 inherited honestly — certification declared **self-administered**. **CD11** the *"mandatory DL-0139 revisit"* is **satisfied**: discharged at **M10.10**, its own text saying the revisit *"is no longer required"* — decision log outranks plan, as **ADR-0041 RT5** held for DL-0141 — so M10.13 **records** the disposition, does **not** reopen it, and **no decision-log entry** accompanies ratification. **CD12** certification **reads, never writes**. **CD13** no migration. **CD14** no production wiring. **CD15** no Accepted ADR modified. **CD16** no existing test weakened; the three Epic 9 release assertions keep their DL-0139 disposition, which an Epic 10 suite **does not** resolve. **CD17** M10.14/M10.15, Epic 11/12 and the v5.0.0 freeze/release out of scope. **CD18** acceptance, registration, implementation, validation and commit each separately authorized. CI1–CI17, CR1–CR12, CV1–CV24 | Epic 10 M10.13 (certification) | Accepted 2026-08-21 |
| [ADR-0044](ADR-0044-audio-representation-resampling-and-media-buffers.md) | Audio representation, session sample rate, the two resampling points, and bounded media buffers — **supersedes nothing**; makes §A8 and **AR-41** satisfiable for the first time. `AudioFrame` extended **additively** (`encoding`, `channels`, `captured_at`, `seq`), with `AudioEncoding.**OPAQUE**` as the default so the text-loopback profile stays **truthful and unchanged** — a `PCM16` default would make local frames falsely declare PCM. Sample rate becomes a **session property** end to end, replacing a hardcoded `16000` the orchestrator never overrides. Exactly **one ingress and one egress** resampler, owned by `runtime/pipeline/audio/` — the only location satisfying AR-10, AR-11, AR-41 and §A14 at once; **providers never resample**. Ingress sits **before** the VAD tap so VAD and STT see identical frames. Media buffers bounded with capacity in **milliseconds**, not frames; **inbound `DROP_OLDEST`**, **outbound back-pressure / never drop** (dropping playback frames produces audible gaps); overflow is a recorded **degradation** emitting at most **one event per turn** (TP10's reasoning) and **never raises** into the pipeline (DL-0013). Follows `gateway/stream.py`'s declared-overflow-policy **pattern**, not its class | Epic 11 M11.2 (audio foundation) | Accepted 2026-08-24 |
| [ADR-0045](ADR-0045-streaming-adapter-pattern-and-vad-execution.md) | The streaming provider adapter pattern — **three capabilities, three different answers**, because each stands in a different relationship to its §A6 essence. **STT: contract unchanged** — its generator signature already matches §A6's *"open stream → partials and finals"*; duplex vendors are wrapped, with an **explicitly held** send task (§A11.1.7 bans fire-and-forget), bounded receive buffering (AR-40), and **typed mid-stream failure** so a vendor disconnect is never indistinguishable from a caller hangup (**AR-6** — `run()` treats iterator exhaustion as hangup today). **TTS: additively extended** — §A6's essence is *"accept text chunks"* and `synthesize(text)` is one-shot, so a streaming-input method plus a `streaming_input` flag are added; `synthesize()` and `LocalTTS` unchanged. **VAD: contract unchanged** — `detect()` specified **O(1) and non-blocking**, a cheap read of provider-owned windowed inference; **`frame_ms` stops being decorative** and becomes load-bearing. Capability-flag untruthfulness is merge-blocking | Epic 11 M11.5–M11.10 (real providers) | Accepted 2026-08-24 |
| [ADR-0046](ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) | Media-path fault tolerance — **AR-23 is satisfied, never scoped down**. Records the **dependency validation M11.0a made a precondition**, which returned a **split result**: **FT-V1** `provider.CircuitBreaker` imports **only stdlib** and is cleanly reusable by direct import; **FT-V2** `provider.FailoverController` is **not**, because `from .router import RoutingDecision` transitively binds `provider.registry`/`catalog`/`domain`/`policies` — yet the coupling is one annotation over one attribute (`decision.candidates`); **FT-V3** `provider/` never imports `runtime/`, so the edge is **acyclic** and AR-15 holds; **FT-V6** no fifth `ProviderRegistry`. **FT-D3** discharges M11.0a's *lift, don't rebuild* fallback by **widening the seam, not copying the code**: a structural `Protocol` on the `core` floor that `RoutingDecision` already satisfies, deleting `failover.py`'s `.router` import. **FT-D2** the voice path is **not** routed through `platform/provider/`'s synchronous, request/response-shaped, declared-closed execution runtime. **FT-D7** AR-24 degradation recording becomes real — `stt.py`'s docstring has claimed it while no code did. **FT-D9** AR-25 discharged for `allow_interruptions` (**barge-in is unreachable in production today**), `llm_model` and `first_message`. **FT-D10** the four-way `ProviderRegistry` collision is accepted debt under AR-53 | Epic 11 M11.4 (fault tolerance) | Accepted 2026-08-24 |
| [ADR-0047](ADR-0047-audio-driven-endpointing-and-metric-turn.md) | Audio-driven endpointing and the additive `metric.turn` remap — **completes [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) TP3a; supersedes nothing** (TP1–TP11, AR-2, AR-31, AR-32, AR-42 all preserved). Real VAD supplies **true end-of-speech**, so the order becomes §A8's: `speech_end → endpoint_decided → stt_final`. Verification collapsed the perceived risk: `MetricSignal` carries **no stage payload** — only `raw_units` (from the fixed `MEASUREMENT_KEY = "voice_to_voice_ms"`), `raw_unit = "ms"` and a content `payload_digest` — so **Billing never reads `ms{}` or `at{}`** and stage keys cannot break the frozen type. **ME2** additive, **no version bump**; **ME3** no new event type; **ME4** `voice_to_voice_ms` frozen in name, unit and meaning, its *semantics improving*; **ME8** an unreached boundary stays an **absent key, never `0`** — a fabricated zero is indistinguishable from a real one; **ME9** VAD-off therefore yields `missing_measurement`, making **M11.12 hard-depend on M11.7** | Epic 11 M11.8 (audio-driven endpointing) | Accepted 2026-08-24 |
| [ADR-0048](ADR-0048-llm-tts-clause-pipelining-and-playback.md) | Clause-boundary LLM→TTS pipelining, pre-warming, and the Playback Engine / Scheduler — **removes a standing AR-43 violation rather than introducing one**: `_respond()` today drains the **entire** LLM stream before calling TTS, a wait-for-whole-input step that makes I1 unreachable by construction with a real LLM. **PB1/PB2** clause-boundary streaming per §A8, the whole-input wait removed and not relocated; **PB3** consumes ADR-0045 SD5, degrading and **recording** where `streaming_input=False`; **PB4** pre-warming that may not mutate conversation state (AR-5); **PB5–PB7** the two §A8 components that **do not exist anywhere** — ordered frames, bounded flush, priority slots, filler policy, greeting pre-cache, crossfade; **PB8** `first_message` — accepted by the API and **read nowhere** — is **implemented** via the greeting pre-cache, not deprecated, because AR-33's deprecation flow costs more than the integration §A8 already mandates | Epic 11 M11.9–M11.10 (pipelining + playback) | Accepted 2026-08-24 |
| [ADR-0049](ADR-0049-provider-credential-injection.md) | Provider credential injection — the existing `api/adapters/secrets.py` seam (`CredentialProvider`, `EnvCredentialProvider`, the `ChainedCredentialProvider` rotation seam, `_SECRET_MARKERS` redaction) is **reused, not replaced**; the problem is purely **location**, since **AR-11** bars `providers/` from importing `api/`. **CR1** the ABC moves to the `core` floor (an interface — legal where behaviour is not); **CR2** implementations stay outside `core`; **CR3** `bootstrap.py`, already the only place providers are constructed, is the single resolution point; **CR4** providers receive **resolved** credentials and never resolve arbitrary secrets — *a plugin that resolves its own secrets can read any secret; a plugin that receives one can read only its own*; **CR5** redaction at every sink; **CR6** vendor errors typed before surfacing (SECURITY_POLICY §5). The seam must exist **before** the first SDK, or the first integration sets the precedent by accident | Epic 11 M11.4–M11.5 (credentials) | Accepted 2026-08-24 |
| [ADR-0050](ADR-0050-epic10-spine-telemetry-metering-production-wiring.md) | Production wiring of the Epic 10 durable spine, latency tap and metering seam — **the architectural authorization for M11.12 and nothing earlier**, discharging **DW10 / RL17 / RC16 / RT9 / HD12**, which *deferred* wiring without authorizing it. ADR-0032…ADR-0043 all remain **immutable**; ED/DS/DW/RL/RC/MS/TP/MB/SV/RT/HD series preserved in full. **SW2** wiring is a constructor argument through the seam Epic 10 already built (`InProcessEventBus(durable=…)`, `is_durable` default `False`) — **no Epic 10 module is modified**; **SW3** Epic 11 **duplicates nothing** Epic 10 delivered; **SW5** `DeliveryTransport` gets its first implementation (RC9 shipped the contract without one) and is the sole new component; **SW8** **TP10 is absolute** — a durable sink makes `publish()` costlier, so post-terminal publication becomes *more* load-bearing; **SW10/SW11** Billing still pulls through the frozen port, `events → billing` stays forbidden, and MB6 (the buffer is not durable) is not quietly relaxed; **SW12** depends on real VAD (ADR-0047 ME9); **SW13** wiring follows M11.11 so a wiring failure and a provider failure are never diagnosed together | Epic 11 M11.12 (spine wiring) | Accepted 2026-08-24 |
| [ADR-0051](ADR-0051-epic11-certification-control-set.md) | The Epic 11 certification control set — a fresh **`E11-*`** namespace in an **independent module**, following ADR-0043 CD1/CD2: Epic 9's and Epic 10's suites are never edited, imported, wrapped, re-exported or executed as Epic 11 controls. **CE3** `INCONCLUSIVE` does not pass; **CE4** certification reads, never writes. **CE5** sixteen control areas including the 5447 regression floor, import boundaries (asserting the authorized `runtime → provider` edge **and the absence of its reverse**), provider conformance with **truthful capability flags**, AR-23/24/25, AR-40/41/43, telemetry, the Epic 10 boundary, security/dependencies, and Stage-2 live-ish verification. **CE6** unlike Epic 10's deliberately *structural* controls, **control 12 requires a real number** — I1 measured through the production taps against a reproducible `docs/performance/` baseline — **discharging ADR-0042 HD3**, because GOVERNANCE §10 makes latency budgets a mandatory release gate. **CE7** CI is a certification prerequisite implemented in the **M11.3 window**: seven **[CI]**-marked rules are presently enforced by nothing | Epic 11 M11.16–M11.17 (certification, freeze, release) | Accepted 2026-08-24 |
| [ADR-0052](ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md) | A certification is a statement about a tree that **was** certified, not a permanent lock on the repository — **supersedes nothing**; ADR-0043 CD1–CD18 intact and no Epic 9/10 finding, verdict, digest, control or limitation changes. Repairs one shared mechanical defect: historical assertions written with an **implicit** far end (`git diff A -- path` compares against the *working tree*) silently changed question the moment `HEAD` moved past the frozen state, so Epic 10 failed itself over Epic 11 work it has no authority over — while the real freeze range `db8c508 → 3779e93` is **empty**. **HC2** two pinned endpoints always; **HC3** `ls-tree` for inventory, which makes the ADR-numbering claim durable instead of asserting numbering never passes 0043; **HC4** digests **recomputed in full** in a detached worktree of the released tree (reproduces `c044b3f6…` exactly); **HC5** nothing weakened, CD2 holds, `catalogue.py`/`runner.py` untouched; **HC6** no epic-specific exception; **HC7** negative guards preserved; **HC8** releases advance the pins | M11.2.1 (governance repair) | Accepted 2026-08-26 |
| [ADR-0052 Amendment 1](ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md) | Historical **counts** are digest inputs too — **additive; HC1–HC8 unchanged**. `examined` feeds the certification digest, and E10-5 derived it from a **live** `EventType` count, so four legitimate AR-6 additions moved a frozen digest with every control still `satisfied`. **HC9** certified-state values come from `CERTIFICATION_COMMIT`, explicitly including tree/member/module counts; **HC10** the live tree still enforces ongoing prohibitions (E10-3 prefix · E10-5 `metric.billing.*` · E10-6 bus subscription), each with a negative test proving it still fires; **HC11** no digest re-baselined — the repair is validated *by* the digest not moving; **HC12** no epic-specific exception | M11.5.1 (certification counting repair) | Accepted 2026-09-07 |
| [ADR-0052 Amendment 2](ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md) | HC9 applied to E10-7 — **no new rule; HC1–HC12 unchanged** | **ACCEPTED** 2026-09-21 (M12.1.1) | E10-7 derived `examined` from the **live** count of `api/` changes since `v4.0.0`. CD7 permits additions, so M11's and M12's legitimate files moved the Epic 10 digests (F-6: 3 tests failed on a clean `m11-local-baseline`). `examined` now comes from `v4.0.0..db8c508` (2 paths). Violations are still read from the live tree. Digests reproduce `7e57bc14…`/`d3d4858d…` exactly, with no re-baseline. E10-2 is noted as latent and not changed. |
| [ADR-0053](ADR-0053-stt-latency-is-measured-from-end-of-audio-not-from-the-endpoint-decision.md) | **PROPOSED — not ratified.** The `stt` stage is measured against the wrong two instants. §A8's *"STT final ≤ 150 ms **after endpoint**"* encodes a **serial** pipeline shape that ADR-0047 **ME1** invalidated by making endpointing audio-driven: the recognizer now finalizes *during* the trailing-silence hold, so `endpoint_decided → stt_final` is **negative** and ME6 drops it — measured at **−46.5 ms** (`stt_final` +193.6 ms, `endpoint_decided` +240.1 ms from true end-of-speech). The negative value means the transcript was ready **46.5 ms early**; the recognizer is not on the critical path. **SM1** derive `stt` from `speech_end → stt_final`; **SM2** no instant moves, no boundary added, no second timing chain, `voice_to_voice_ms` unchanged; **SM3** the 150 ms budget is not changed; **SM4** a hermetic profile measures harness frame pacing, not a recognizer — the +193.6 ms must never be quoted as STT latency; **SM5** ratifying it does **not** unblock E11-12, which still needs live Deepgram | M11.16.1 (R-66 diagnosis) | **Proposed 2026-09-19** |
| [ADR-0054](ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md) | ICE server policy is explicit at the composition root, never inherited from the media library | **ACCEPTED** 2026-09-20 | Closes R-71: aiortc substituted `stun:stun.l.google.com:19302` whenever `iceServers` was `None`, so every call setup contacted a third party nobody chose. The composition root now always states the policy; the default is **explicitly none**; STUN is configured via `VOXVOICE_ICE_STUN_URLS`; TURN is refused at startup and remains a deployment prerequisite. |
| [ADR-0055](ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) | The Developer Platform (Epic 12) productizes the M11 runtime model through additive modules | **ACCEPTED** 2026-09-21 | Opens Epic 12. The M11 `org`/`agent`/`session` model is the product spine, and Epic 8's `cp_*` platform is untouched and unused (owner ruling, DL-0235). All product code goes in new modules under `api/console/`, because `E10-7` freezes every pre-`v4.0.0` `api/` file. One local app wraps `build_webrtc_app`. There are three auth planes, and no key ever reaches a browser. The Playground calls the frozen `create_session` path. Conversations and analytics are read-only projections that write no events. The console is a React+TS+Vite Consumer at `console/` (owner ruling, DL-0236). |
| [ADR-0055 Amendment 1](ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) | Console runtime event log and endpointing (M12.5) | **ACCEPTED** 2026-09-21 | DP10: `SqlDurableEventLog` attached through ADR-0050 SW2's seam, with no outbox (no delivery transport exists here); SW4–SW6 stay ADR-0050's. DP11: `AudioEndpointingController` on the real profile only (F-7: without it `voice_to_voice_ms` was missing on every product-path turn), switchable via `VOXVOICE_CONSOLE_AUDIO_ENDPOINTING`. |

> **Terminology note (ADR-0019 → ADR-0020).** ADR-0019 was written using the name
> `RegistrySnapshot` (and `RegistryEntry`) for the compiler's injected, immutable
> binding-resolution catalog. ADR-0020 reserved the "Registry" nouns for the Product
> Registry of packages and renamed that catalog to **`BindingSnapshot`** /
> **`BindingSnapshotEntry`** (`product/compiler/snapshot.py`). ADR-0019 remains
> Accepted and immutable; only the *name* of the type it describes changed — read
> `RegistrySnapshot` in ADR-0019 as `BindingSnapshot`. The resolve-against-an-injected-
> immutable-snapshot decision itself is unchanged.

> **Epic 7 freeze (M7.0F).** ADR-0018, ADR-0019, and ADR-0020 are ratified and
> implemented under **Product Runtime v1.0** (🔒 frozen — see
> [`docs/releases/epic7-product-runtime-v1.0.md`](../releases/epic7-product-runtime-v1.0.md)).
> They are binding until superseded; changing an Epic 7 architectural invariant they
> encode requires a new superseding ADR.

> **Epic 8 architecture freeze (M8.20).** ADR-0021 and ADR-0022 are ratified and
> implemented under **Customer Platform v1.0** (🔒 architecture frozen — see
> [`docs/releases/epic8-customer-platform-v1.0.md`](../releases/epic8-customer-platform-v1.0.md)).
> They are binding until superseded; changing an Epic 8 architectural invariant they
> encode (Studio-owns-no-runtime-state; Deployment Actuator = sole owner of deployment
> execution) requires a new superseding ADR + major version + new baseline. This is a
> governance freeze of the architecture only — the Epic 8 implementation remains
> uncommitted in the working tree (recorded in the freeze manifest §22); no commit,
> tag, or release is claimed.

> **Epic 9 opening (M9.0B).** ADR-0023 ratifies the Billing Platform's ownership boundary
> **before** any Epic 9 implementation: **Billing owns revenue and nothing else** — it
> consumes `metric.*` read-only (it never measures), never blocks Runtime or Deployment,
> never performs a permission check, and publishes immutable entitlement snapshots the
> owning platform enforces locally. `Billing → Platform` is one-way; no frozen Epic may
> depend on Billing. **ADR-0024 stays reserved for the Coach-autonomy decision** recommended
> by ADR-0021; the remaining Epic 9 decisions are recommended as **ADR-0025–ADR-0030**
> ([Epic 9 Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26) and are
> ratified separately. Epics 5/6/7/8 remain frozen and byte-for-byte unchanged.

> **Epic 9 repository topology (M9.1A).** ADR-0031 is the **structural placement** record
> CODING_STANDARDS §2 requires for a new top-level directory (the ADR-0018 / ADR-0022
> precedent). It fixes *where* the plane ADR-0023 ratified physically lives —
> `platform/billing/`, importing `platform/core/` + stdlib only, with exactly one capped
> read-only exception (`billing/refs.py` → `product`, mirroring `actuator/planning.py`) —
> plus its dependency direction, composition root, import rules, public surface, and
> extension points. It took **ADR-0031** rather than a reserved number: **ADR-0024** stays
> reserved for Coach and **ADR-0025–ADR-0030** stay reserved for the recommended Epic 9
> architecture batch. After it, Epic 9 repository placement is frozen: no future placement
> ADR is needed and no package movement is expected.

> **Epic 10 opening (M10.1).** ADR-0032, ADR-0033 and ADR-0034 are ratified **before** any Epic 10
> implementation (DOCUMENTATION_RULES §3.7). Together they fix the **Durable Event & Telemetry
> Spine** beneath the already-built runtime: ADR-0032 the delivery *semantics*, ADR-0034 the storage
> *structure*, ADR-0033 the `metric.*` producer side and namespace partition. They discharge the
> durability work **DL-0017** deferred at M4.1 — *"durable outbox + retry/DLQ + async/non-blocking
> delivery … implement the same `EventBus` contract — no producer change"* — and they take **only**
> that: a distributed broker, external transports and WS/SSE gateway expansion remain deferred with
> DL-0017; real providers (Epic 11) and the SDK / read API / mid-call control (Epic 12) are out of
> scope. Numbering continues at **0032** because **ADR-0011** (MCP/A2A), **ADR-0024** (Coach) and
> **ADR-0025–ADR-0030** (the recommended Epic 9 batch) all remain **Reserved — not yet ratified**.
> **ADR-0023 and ADR-0031 are referenced, not amended**: Epic 10 implements against the frozen
> Billing contract and modifies no file under `platform/billing/**`. Epics 5–9 remain frozen; the
> `v4.0.0` baseline, both Epic 9 digests, migration head `0033`, the 27 controls, advisories NS-5
> and ST-3, and all nine declared limitations are preserved as historical state.

> **Epic 10 continuation (M10.6).** **ADR-0035** is ratified **before** the M10.6 implementation, for
> the reason the [Epic 10 plan](../roadmap/epic10-implementation-plan.md) anticipated in writing: the
> M10.6 design gate found that ADR-0032 **ED6** cannot be executed literally, because the runtime
> producers it governs hold no database transaction — `platform/runtime/**` has no `Database`,
> `Session` or `UnitOfWork`, and `InProcessEventBus.publish()` stamps `seq` and dispatches in one
> method with no seam between them. ADR-0035 **completes ADR-0032 where ADR-0032 is silent and
> supersedes nothing**: ADR-0032 remains immutable, ED1–ED12 are preserved in full, ADR-0033 and
> ADR-0034 are unchanged, and AR-31, AR-32 and DL-0017 continue to bind. It decides only the three
> questions ADR-0032 left open — the transaction model, the write/dispatch interposition and
> ordering, and durable-write failure semantics — plus the `evt_event` / `evt_outbox` separation that
> **ED5** forces. It authorizes **M10.6 only**: retry, DLQ and quarantine stay M10.7; recovery stays
> M10.8; retention stays M10.5 (deferred, DL-0141); the metric seam stays M10.10. **M10.6 provides
> the durable write-side capability and proves its semantics, but the production runtime is not yet
> wired to the durable sink.**

> **Epic 10 continuation (M10.7).** **ADR-0036** is ratified before the M10.7 implementation. It
> **completes ADR-0032 ED7/ED8 and continues ADR-0035 DW5/DW6, and supersedes nothing** — ADR-0032
> and ADR-0035 stay immutable, ED1–ED12 and DW1–DW11 are preserved, ADR-0033/0034 are unchanged, and
> AR-31/AR-32/DL-0017 continue to bind. Its load-bearing ruling is **RL1**, which resolves a genuine
> contradiction the M10.7 design gate surfaced: ADR-0035 **DW7** assigned restart re-dispatch to
> M10.8 while the Epic 10 plan described M10.8 as *"read-only and side-effect-free"*. **DW7 remains
> authoritative**; the plan's description is narrowed, and **M10.8 owns recovery, restart
> re-dispatch and the driver that performs it**. M10.7 therefore ships **no worker, scheduler, poll,
> loop, `sleep`, timer or thread** — it is a *decision-and-record* layer that computes what should
> happen and persists it, exposing `due(now)` as a query it never acts on. It also records what it
> deliberately does **not** do: **no operator redrive** (`DEAD_LETTERED` is terminal), **no
> operator-driven quarantine**, and **no concrete webhook transport** —
> `platform/events/webhooks.py` stays byte-for-byte unchanged so no second retry mechanism appears
> (blueprint R-11). Retention stays M10.5 (deferred, DL-0141), metrics stay M10.10, and **the
> production runtime remains unwired**.

> **Epic 10 continuation (M10.8).** **ADR-0037** is ratified before the M10.8 implementation. It
> **completes ADR-0035 DW7 and ADR-0036 RL1, and supersedes nothing** — ADR-0032, ADR-0035 and
> ADR-0036 stay immutable, ED1–ED12, DW1–DW11 and RL1–RL17 are preserved, ADR-0033/0034 are
> unchanged, and AR-31/AR-32/DL-0017 continue to bind. DW7 and RL1 *assigned* recovery to M10.8 but
> decided none of its questions; this ADR decides them, and decides them from the repository rather
> than from generic outbox architecture. Its load-bearing ruling is that **a periodic sweep over
> persisted state is caller-driven here, not scheduled**: `context/runtime.py` and
> `memory/runtime.py` already ship exactly this shape — *"Deterministic — no background timer; the
> caller (or a scheduler) invokes it. Returns the count expired."* — so `recover(now, limit)`
> follows it and the scheduler stays **outside** the module, where both precedents put it. One sweep
> selects `PENDING` (immediately eligible) **and** due `RETRYING` rows in `(seq, event_id)` order;
> terminal states are never selected and **no FSM transition is added** — the seven ratified edges
> are exactly sufficient. `begin_attempt` runs **before** delivery, so a crash costs an attempt
> instead of granting one. `DeliveryTransport` remains a **pure port with no production
> implementation**, which is what keeps `platform/events/webhooks.py` byte-for-byte unchanged and
> prevents a second delivery mechanism (blueprint R-11). Concurrency is written down as an
> **accepted non-claim**: two concurrent sweeps may deliver the same event, which is *correct* under
> ED2/ED12 rather than a defect, and **no claim/lease, row lock, `FOR UPDATE`, advisory lock or
> dedup table** is introduced — a static test keeps that true. It also resolves a second plan
> contradiction: **`MetricStreamPort.replay()` belongs to M10.10**, not M10.8, and M10.8 owns only
> the read-only **durable-log re-derivation** capability. **M10.8 requires no migration and no
> schema change**; retention stays M10.5 (DL-0141), metrics stay M10.8a/M10.9/M10.10, and **the
> production runtime remains unwired**.

> **Epic 10 continuation (M10.9).** **ADR-0038** is ratified before the M10.9 implementation. It
> **completes ADR-0033 MS8 and supersedes nothing** — ADR-0032/0033/0034/0035/0036/0037 stay
> immutable, and MS1–MS10, ED1–ED12, DW1–DW11, RL1–RL17, RC1–RC16, AR-2, AR-31, AR-32, AR-42 and
> DL-0013 continue to bind. It makes **AR-42** — *"every stage boundary in the conversation loop
> carries a latency tap"* — satisfiable for the first time since it was written. Its load-bearing
> ruling resolves a conflict the M10.9 gate surfaced: plan §1.12 lists *"Conversation Runtime
> changes"* as out of scope for the whole epic, while three of the six required boundaries occur
> **inside `async for` loops in `RuntimePipeline._respond` and emit nothing**, so no bus-derived
> design can observe them. The phrase is **defined nowhere**, the Blueprint's own non-goals omit
> runtime changes, the frozen boundary F1–F17 does **not** freeze `platform/runtime/**`, and
> Blueprint §12 assigns the taps to Epic 10 — so §1.12 is interpreted as excluding **Epic 11
> vendor/runtime rework**, not the minimal **additive, optional, notify-only** observer the runtime
> already carries six of. It also records something a later reader would otherwise rediscover as a
> bug: the pipeline is **segment-driven**, so `stt_final` precedes `endpoint_decided` — the inverse
> of Constitution v2's budget ordering — and derived durations therefore follow the **real** order so
> that **no negative duration is ever emitted**, with v2's ordering kept as target semantics for
> Epic 11 and the raw instants preserved so M10.11 can re-derive. The tap owns **one injected
> monotonic clock** (`TurnTiming` is wall-clock and is left untouched, as is `TurnEngine`), captures
> cheaply on the hot path, and publishes **exactly once per terminal turn** — never per boundary,
> because `InProcessEventBus.publish()` is synchronous run-to-completion and would run every
> subscriber, and potentially a durable write, inside the ≤1.0 s voice-to-voice budget (Blueprint
> risk R-9). An unreached boundary is an **absent key, never `0`**; per-turn state is bounded by
> in-flight turns; observation is best-effort with explicitly logged failure isolation (DL-0013),
> while the FSM stays strict. `EventSource.OBSERVABILITY` is added additively. **No migration,
> schema, buffer, retention, scheduler, thread or new `EventType`** — the metric buffer is M10.10 and
> shadow validation is M10.11.

> **Epic 10 continuation (M10.10).** **ADR-0039** is ratified before any M10.10 implementation — and,
> unusually, **ratifies architecture only: it does not authorize implementation**, which requires a
> separate explicit authorization. It **completes ADR-0033 MS3–MS7 and supersedes nothing** —
> ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037 and ADR-0038 stay
> immutable, and MS1–MS10, ED1–ED12, DW1–DW11, RL1–RL17, RC1–RC16, TP1–TP11, AR-2, AR-15, AR-31,
> AR-32, AR-42 and DL-0013 continue to bind. Its load-bearing ruling resolves a **direct conflict
> between two ratified ADRs** that the M10.10 gate surfaced: **ADR-0033 MS3** and plan §M10.10 say the
> platform buffer *"subscribes to the `EventBus`"*, while **ADR-0035 DW11** says it is *"an outbox
> destination under DW5, not an in-process subscriber"* — and **DW5** excludes in-process synchronous
> subscribers from outbox destinations, so the two are mutually exclusive. The owner ruled for
> **in-process `EventBus` subscription**, scoped **strictly** to M10.10's delivery mechanism: ADR-0033
> owns this seam, ADR-0035 disclaims MS-level authority in its own text, and the outbox path is not
> executable inside M10.10 because **RC9** defers a concrete `DeliveryTransport` and **DW10/RL17/RC16**
> keep durable runtime wiring explicitly unwired — choosing it would pull transport and runtime wiring
> into the milestone. **ADR-0035 is not amended, not superseded and not reinterpreted generally**, and
> DW11 remains in force for every other purpose; as an in-process synchronous subscriber the buffer
> sits correctly **outside** DW5's category rather than against it. The ADR then settles six properties
> no ratified document specified: **boundedness** (one process-wide in-memory buffer, fixed capacity
> **10 000** in a single named constant with **no configuration surface**, **drop-oldest** with a
> counter and rate-limited `WARNING`, and **no persistence, migration, flush or shutdown hook — the
> contents are lost on process exit**, stated plainly because MS5 makes a Billing outage an expected
> steady state and an unbounded buffer would therefore be a guaranteed OOM); an **enforceable hot-path
> budget** (bounded O(1) in-memory work only — no I/O, blocking, sleeping, provider call, reentrant
> `publish()`, thread/task/timer or hot-path lock — on a `deque(maxlen=…)` equivalent so bus-side
> append and API-side drain need no mutual exclusion, because `publish()` is synchronous
> run-to-completion **inside the conversation coroutine** and dispatcher isolation protects correctness
> but never latency); **destructive**, org-keyed, insertion-ordered `drain` with **no cursor model**,
> and a **non-destructive, best-effort `replay` that guarantees nothing historical** and can never
> recover what overflow or process exit removed; `Event.seq` carried as **provenance only** — per-bus,
> per-session, **no global ordering claim**, no second sequence authority (ED3); and three **distinct**
> counters for malformed envelopes, a missing `voice_to_voice_ms` and an empty `org_id`, each of which
> produces **no `MetricSignal` at all** rather than a fabricated `0`, a substituted scalar, a
> `scope=None` or synthesized attribution — **CV10**: ingest nothing rather than invent usage, with
> DL-0013's graceful, logged degradation and **no second quarantine mechanism**. It records the first
> **`api → billing`** edge in the repository, acyclic under AR-15, with Billing still never
> subscribing, never holding a runtime resource, `MetricStreamPort` still the single inbound path, and
> `NullMetricStream` still the default so an unwired port ingests nothing. **`billing/**` and every
> pre-existing `api/**` file are untouched; no migration, schema, transport, scheduler, thread, worker,
> persistence, configuration or `EventType`/`EventSource` change** — shadow validation stays M10.11.

> **Epic 10 continuation (M10.11).** **ADR-0040** is ratified before any M10.11 implementation and,
> like ADR-0039, **ratifies architecture only — it does not authorize implementation**. It **completes
> ADR-0033's M10.11 standing rule and supersedes nothing** — ADR-0023, ADR-0031, ADR-0032, ADR-0033,
> ADR-0034, ADR-0035, ADR-0036, ADR-0037, ADR-0038 and **ADR-0039** stay immutable, and MS1–MS10,
> ED1–ED12, DW1–DW11, RL1–RL17, RC1–RC16, TP1–TP11, **MB1–MB16**, AR-2, AR-15, AR-31, AR-32 and AR-42
> continue to bind. The M10.11 gate found the milestone **not executable as written**: the comparison
> engine already exists and is *Billing's* — `replay_period` re-derives a period *"using the **same
> pure ingestion decision** the live path uses"* — but it reads `MetricStreamPort.replay()`, and
> **MB5's destructive `drain` removes exactly the signals MB6's resident-only `replay` would need**.
> A closed period therefore replays as `()`, and ADR-0033's standing rule (*"any divergence is an Epic
> 10 defect"*) would fire on **every period, permanently**. ADR-0040 is the **separately ratified
> mechanism MB6 itself demanded**, and it obeys both halves of that instruction: it states plainly
> that M10.10 provides no historical coverage, and supplies a different source rather than extending
> the buffer. **SV1** makes the **durable event log** (`evt_event`) that source — append-only,
> restart-safe, `seq`-ordered, idempotent on identity, envelope preserved verbatim, which is precisely
> what a bounded in-memory buffer is not and (**SV2**) must never become. **SV5** keeps M10.11
> **hermetic**: no tap, durable sink, buffer, `metric_stream` rebinding, worker, thread, timer,
> transport or outbox enters production, because DW10/RL17/RC16 deliberately leave the durable runtime
> unwired and CV10 makes `NullMetricStream` the default — wiring the chain to test it would break three
> ratified rules to exercise a fourth. **SV7** forbids a second metering algorithm: meter resolution,
> normalization, idempotency and comparison stay Billing's, reused as the comparison authority, which
> is the drift `replay_period`'s own docstring warns about. **SV8** requires the proof to demonstrate
> **both** agreement on a valid period **and** divergence on an injected historical discrepancy — a
> validation that can only report success is indistinguishable from one that does not look. **SV4**
> holds Billing frozen *even when it disagrees*: a divergence is a finding, never a number to
> reconcile away. **SV9** keeps the re-derivation **out of `api/metering/`**, leaving M10.10's single
> `billing.foundation` edge and its `events` prohibition unweakened, and prefers a test-side
> composition; **SV10** uses `DurableEventLog` exactly as it stands, with in-memory `read_all()`
> filtering that is explicitly **not** a production historical-query feature. **No schema, migration,
> persistence, wiring, transport, scheduler, `EventType`/`EventSource`/`MeterSource` change, and no
> certification, freeze or release artifact is touched** — DL-0139 stays resolved and is not reopened.

> **Epic 10 continuation (M10.5).** **ADR-0041** is ratified before any M10.5 implementation and,
> like ADR-0039 and ADR-0040, **ratifies architecture only — it does not authorize implementation**.
> It **completes ADR-0032 ED9 and ADR-0034 DS12/DS13 and supersedes nothing** — ADR-0023, ADR-0031,
> ADR-0032, ADR-0033, ADR-0034, **ADR-0035**, ADR-0036, ADR-0037, ADR-0038, **ADR-0039** and ADR-0040
> stay immutable, and ED1–ED12, DS1–DS13, DW1–DW11, RL1–RL17, RC1–RC16, MS1–MS10, TP1–TP11,
> **MB1–MB16**, SV1–SV12, AR-2, AR-15, AR-31 and AR-32 continue to bind. It unblocks **M10.5**,
> deferred since M10.2 by **DL-0141** because ED9's two guards tested state that did not exist. The
> M10.5 gate found the prerequisites **half met**, and a three-way conflict over the word *drained*:
> **ADR-0035 DW5** and `events/ports.py` define it as *"an `evt_outbox` row not in `DELIVERED`"*,
> while DL-0141 and plan §M10.5 define it as metric-buffer consumption. Those agreed only while
> **DW11** called the buffer an outbox destination — which **ADR-0039 MB1 overruled**, and MB5/MB6
> leave no durable buffer state to consult instead. **RT1** resolves it by the ratified authority
> hierarchy: an **ADR** outranks a Level-3 decision-log entry and a plan, so the higher artifact
> stands and the lower ones are corrected — **ADR-0035 is not amended**. **RT2** shows the ruling is
> also *correct*, not merely higher-ranked: under **DW3** the durable write precedes dispatch and
> `publish()` is **synchronous run-to-completion**, so every in-process subscriber has already been
> called when it returns. The metric buffer has **nothing to guard** — not because ED9 was weakened
> but because the lag state it tests **cannot arise** for a synchronous consumer, which is what DW5's
> exclusion says structurally. **RT3** admits **only `DELIVERED`**: `DEAD_LETTERED` and `QUARANTINED`
> are terminal but were *never delivered*, and deleting them would destroy an operator's evidence
> exactly when delivery failed. **RT4** is emphatic that **ADR-0039 is not worked around** — MB5/MB6
> are correct, in force, and are *the reason* retention reaches for a durable artifact rather than a
> bounded, destructively-drained, process-lifetime buffer. **RT6** solves the harder guard by
> **inversion**: `events/` **declares** a `MeteredWatermark` port, something outside the plane
> implements it, the attestation is **an input never a derivation**, and absent ⇒ retain — so
> `events/ → billing` stays forbidden (**IR4**), **PL-2 keeps its single `api/metering/` edge**
> (**RT10**), the two databases stay separate, and **no Billing query, repository call or access path
> is invented**. **RT7** states the five-part safe-to-delete predicate and **RT8** makes **default-deny**
> a behaviour rather than error handling: *"the failure mode of this component is to retain too much,
> which costs storage, rather than to delete too early, which costs revenue and evidence."* **RT9**
> keeps M10.5 **hermetic** — no wiring, worker, scheduler, timer or transport (DW10/RL17/RC16,
> ADR-0040 SV5) — and argues explicitly that this is **not** the *"inert subset"* DL-0141 rejected,
> because both guards are now genuinely evaluable and **EV8/SV9 become real assertions**, which was
> DL-0141's own resumption test. **RT11** adds **no migration** and leaves **EV4** intact with
> retention as its sole sanctioned exception to append-only. **`MetricTurnBuffer`, `MetricStreamPort`,
> `DurableEventLog` and `billing/**` are unchanged**, no certification, freeze or release artifact is
> touched, **DL-0139 stays Resolved**, and **DL-0141 stays Open** until M10.5 lands (**RT13**).
