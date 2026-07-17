# VoxVoice — Documentation Index

> Central index of all engineering documentation. Every audit, architecture
> review, design decision (ADR), and recommendation lives in this tree — never
> only in chat. See [README.md](README.md) for the documentation conventions.

_Last updated: 2026-07-16_

## Specification

| Document | Status | Description |
|---|---|---|
| [SPEC.md](SPEC.md) | Superseded in part | The original executable build spec. Authoritative for product goal, consumer model, org/auth contract, and Hinglish acceptance phrases; superseded by the Constitution on engine-adapter architecture, Dograh-primary, and repo layout (§22; banner at top of SPEC). `../CLAUDE.md` was a byte-identical copy — replaced at M0 with a governance pointer (no info lost; content lives here). |

## Audits

| Document | Date | Status | Description |
|---|---|---|---|
| [audits/phase-1-repository-audit.md](audits/phase-1-repository-audit.md) | 2026-07-16 | Final v1.0 | Complete repository audit: architecture, code quality, dead/duplicate code, performance, security, scalability, scores. Top findings: no version control (H1), live secrets on disk (H2), voice loop never closed end-to-end (H3). |
| [audits/phase-1.5-voice-runtime-audit.md](audits/phase-1.5-voice-runtime-audit.md) | 2026-07-16 | Final v1.0 | Voice-runtime-only audit vs. Vapi-class platforms. Central finding: VoxVoice owns no runtime — control plane over a black-box Dograh (no live events, no mid-call control, post-call transcript polling) + a disconnected Pipecat prototype with batch (non-streaming) STT and silence-timer turn detection. Runtime Maturity 22/100. Defines the strategic fork (deepen Dograh vs. first-party Pipecat engine — ADR required), top-20 improvements, target architecture, and complexity estimates. |

_Audit reports are immutable once final — new findings go in a new versioned document (e.g. `phase-1-repository-audit-v2.md` or the next phase's audit)._

## Governance (binding on every implementation)

| Document | Date | Status | Description |
|---|---|---|---|
| [../.voxvoice/README.md](../.voxvoice/README.md) | 2026-07-16 | Active v1.0 | **Engineering Governance System** — 13 documents defining how all human/AI engineering work happens within the ratified Constitution: GOVERNANCE (supreme policy + authority hierarchy), AI_AGENT_RULES, DEVELOPMENT_WORKFLOW (13 mandatory phases), CODING_STANDARDS, ARCHITECTURE_RULES (AR-1…54, CI-enforceable), DOCUMENTATION_RULES (incl. ADR workflow), TESTING_POLICY, PERFORMANCE_POLICY, SECURITY_POLICY, REVIEW_CHECKLIST (14 dimensions), DEFINITION_OF_DONE, GIT_WORKFLOW, DECISION_LOG (DL-0001…0004 open items incl. M0 key rotation + git init). Index page carries the governance↔Constitution cross-reference map. |

## Architecture

| Document | Date | Status | Description |
|---|---|---|---|
| [architecture/engineering-constitution-v2.md](architecture/engineering-constitution-v2.md) | 2026-07-16 | **RATIFIED v2.0** | **The binding Engineering Constitution (once ratified).** Part A: technology-neutral architecture — capability-based provider contracts, two-plane design, event-driven core, turn intelligence, layered memory, state machines, staged scalability, permanent engineering rules (component specs, interface versioning, no silent config). Part B: versioned Implementation Profile binding capabilities to current selections (Python/Pipecat/Postgres/Deepgram/Cartesia/Groq/Sarvam…) — replaceable via lightweight profile ADRs without amending Part A. Appendix R: full v1→v2 revision record, impact reports, readiness score 88/100. |
| [architecture/engineering-constitution.md](architecture/engineering-constitution.md) | 2026-07-16 | Superseded by v2 | v1 draft blueprint, retained unmodified as version history. Origin of decisions D1–D7 and the M0–M6 roadmap; v2 generalized it into Part A/Part B form. |

## Architecture Decision Records (ADR)

Register and process: [adr/README.md](adr/README.md). Extracted from Constitution decisions D1–D7 + Part B at milestone M0 (2026-07-16).

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](adr/ADR-0001-first-party-runtime.md) | First-party voice runtime; retire whole-engine adapter axis | Accepted |
| [ADR-0002](adr/ADR-0002-substrate-behind-contracts.md) | Pipeline substrate confined behind owned contracts | Accepted |
| [ADR-0003](adr/ADR-0003-two-plane-architecture.md) | Two-plane architecture (Control / Runtime) | Accepted |
| [ADR-0004](adr/ADR-0004-event-driven-core.md) | Event-driven core with one typed envelope | Accepted |
| [ADR-0005](adr/ADR-0005-streaming-first-turn-intelligence.md) | Streaming-first ingestion + Turn Intelligence | Accepted |
| [ADR-0006](adr/ADR-0006-single-system-of-record.md) | Single System of Record; brokers only when needed | Accepted |
| [ADR-0007](adr/ADR-0007-keep-control-plane-delete-adapters.md) | Keep control-plane assets; delete engine-adapter axis | Accepted |
| [P-ADR-0001](adr/P-ADR-0001-implementation-profile-v1.md) | Adopt Implementation Profile v1 (Part B bindings) | Accepted |

## Research (archived knowledge)

| Document | Description |
|---|---|
| [research/dograh-api-notes.md](research/dograh-api-notes.md) | Live-verified Dograh v1.35.0 API knowledge, archived at M0 before the adapter code is deleted at M1 (ADR-0007 / AR-54). |

## Deployment

_Currently lives beside the code (to be migrated or cross-linked here):_

| Document | Description |
|---|---|
| [../platform/README.md](../platform/README.md) | Run instructions, verified Dograh API mapping, engine-swap, transcript flow. |
| [../platform/DEPLOY.md](../platform/DEPLOY.md) | Deploy runbook: Dograh remote mode on a public-IP host; why local macOS voice can't work. |
| [../platform/ORACLE_DEPLOY.md](../platform/ORACLE_DEPLOY.md) | Step-by-step Oracle Cloud VM deploy (firewall, DNS, TLS, remote profile). |
| [../platform/voicebot/README.md](../platform/voicebot/README.md) | Local Pipecat voice-agent MVP (Groq STT/LLM + Cartesia TTS). |

## Runtime (component specs — Constitution §A11.3)

| Document | Date | Status | Description |
|---|---|---|---|
| [runtime/session-worker.md](runtime/session-worker.md) | 2026-07-16 | Implemented (M2A) | `SessionWorker` — owns one session's execution lifecycle (start/stop/cancel/cleanup, deterministic FSM, DI seam, health). No conversation/provider/AI logic (later milestones). |
| [runtime/conversation-state-machine.md](runtime/conversation-state-machine.md) | 2026-07-16 | Implemented (M2B.1) | `ConversationStateMachine` — deterministic conversation-lifecycle FSM over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications). Single source of truth for conversation state; consumed by the Orchestrator at M2B.2. Reconciliation of the milestone's example states in DL-0007. |
| [runtime/conversation-orchestrator.md](runtime/conversation-orchestrator.md) | 2026-07-16 | Implemented (M2B.2) | `ConversationOrchestrator` — sole coordinator/writer of conversation execution; drives the state machine (begin/run/end/cancel + failure routing); hosted by `SessionWorker`; provider capabilities via interfaces only (no execution yet). Establishes the runtime ownership boundaries. |
| [runtime/provider-registry.md](runtime/provider-registry.md) | 2026-07-16 | Implemented (M2C.1) | `ProviderRegistry` — capability-based provider reference management (register/unregister/resolve/list, validation, hot-swap, thread-safe, deterministic). Manages references only; never constructs/executes a provider. Placement rationale (runtime, not core/providers) in DL-0008. |
| [runtime/turn-engine.md](runtime/turn-engine.md) | 2026-07-17 | Implemented (M3.1) | Turn Engine (`TurnEngine`/`TurnCoordinator`/`TurnLifecycle`/`TurnContext`/`TurnPhase`) — the per-turn lifecycle foundation: deterministic phases + timing, notify-only pipeline integration (conversation behaviour unchanged). No VAD/endpointing/interruptions (M3.2+). `TurnPhase`-vs-`TurnState` reconciliation in DL-0012. |
| [runtime/runtime-pipeline.md](runtime/runtime-pipeline.md) | 2026-07-17 | Implemented (M2C.2) | `RuntimePipeline` + `RuntimeSessionService` — the streaming turn loop (Transport→STT→LLM→TTS→Transport via contracts) and the api↔runtime seam. First complete end-to-end conversation; `POST /v1/sessions` restored. Includes the runtime sequence diagram. |
| [providers/local-profile.md](providers/local-profile.md) | 2026-07-17 | Implemented (M2C.2) | The local development provider profile (LocalTransport/LocalSTT/EchoLLM/LocalTTS) + the M2C.2 contract refinement. First-class, in-process, deterministic; real vendors are drop-in replacements. |

## Roadmap

| Document | Date | Status | Description |
|---|---|---|---|
| [roadmap/migration-progress.md](roadmap/migration-progress.md) | 2026-07-16 | Living | M0–M6 migration tracker. M0 ✅, M1 ✅ (repo reshape; engine-adapter axis removed; session-start → 503; structure now matches Constitution §A14). M2 next. |
| [roadmap/baseline-M0.md](roadmap/baseline-M0.md) | 2026-07-16 | Baseline (immutable) | M0 Foundation Freeze snapshot — structure, dependencies, architecture/governance/documentation posture, statistics, known debt, deferred items, open risks. Git tag `foundation-v1`. The state every future milestone starts from. |

## Planned sections (created when first document lands)

Open: `architecture/` · `audits/` · `adr/` · `research/`.
Pending first document: `runtime/` (component specs, M2+) · `apis/` (event catalogue, M4) ·
`providers/` (capability matrices) · `performance/` (benchmark baselines) · `security/`
(threat model) · `testing/` · `deployment/` (migrate platform runbooks at M1) · `roadmap/`.

Empty directories are not pre-created; a section is opened by its first document
and added to this index in the same change.
