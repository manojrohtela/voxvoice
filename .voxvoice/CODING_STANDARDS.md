# CODING_STANDARDS.md — Source Code Standards

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Scope:** all source code. Language-agnostic rules here; the current language/toolchain bindings are Constitution **Part B** (today: Python 3.12 + asyncio backend, TypeScript SDK). Technology-specific conventions live in `docs/testing/` and per-module READMEs when needed.

## 1. Naming

- Names come from the Constitution Glossary (A0) — `SessionWorker`, `TurnIntelligence`, `EventBus`, `SystemOfRecord`-adjacent terms; never synonyms (`CallManager`, `MessageBroker`…).
- Modules/packages: lowercase, single-responsibility nouns matching the folder map (A14). Classes: nouns. Functions: verb phrases. Booleans read as predicates (`is_`, `has_`, `allow_`). Events use the dotted vocabulary of A5 exactly.
- State-like values are typed enums/literals, never free strings (audit-proven defect class).
- No abbreviations that aren't in the glossary or universal (`id`, `ts`, `db`). One concept, one name, everywhere — including tests and docs.

## 2. Project structure & module boundaries

- The folder map is Constitution A14 and is not locally negotiable. New top-level directories require an ADR.
- Import layer rules (A4.1) are enforced by CI; see ARCHITECTURE_RULES AR-10…AR-14.
- A module exposes an explicit public surface (`__all__` / index exports); reaching into another module's internals is a defect.
- Circular imports are build failures, not warnings.

## 3. Error handling

- One typed error hierarchy rooted in `core/errors` (PlatformError). Every raised error is typed; bare/catch-all handlers that erase type information are banned (A11.1.7).
- Errors crossing a boundary are translated at that boundary: provider errors → typed capability errors; runtime errors → typed events + API error codes. Raw vendor errors never reach consumers.
- Every `except`/`catch` either handles (recovers, degrades per A10) or enriches-and-rethrows. Swallow-and-continue requires a comment citing the policy that permits it.
- Client-caused failures are 4xx with actionable messages; platform failures are 5xx with correlation ids, details in logs not responses.

## 4. Logging

- Structured logging only (key-value), correlation ids (session, turn, org) on every runtime line.
- Levels: `debug` (diagnosis), `info` (lifecycle facts), `warning` (degradation handled), `error` (failed operation, typed). No `print`.
- Secrets/PII never logged; redaction enforced at the sink (SECURITY_POLICY §4); log *that* a token exists, never its value.
- Logs narrate decisions ("interrupt rejected: backchannel"), not code steps ("entering function").

## 5. Asynchronous programming

- The runtime is async-first: **no blocking I/O in an event loop** — database, HTTP, file, sleep all use async forms; CPU-heavy work goes to executors.
- Every spawned task is owned: held in a task group/registry, cancelled on teardown. Fire-and-forget without a handle is banned (A11.1.7).
- Every await on an external system has a timeout (A10). Unbounded queues in media paths are banned; bounded with declared overflow policy (A8).
- Shared mutable state across tasks requires an owning actor or a lock with documented scope — prefer the actor (one writer per fact, A2.5).

## 6. Configuration

- All configuration flows through the typed settings schema; no ambient `getenv` in business code.
- Every field: typed, defaulted-or-required, documented in `.env.example`, **and honoured by the runtime or rejected at write time** (A2.6). Adding config the code ignores is a Severity-High defect.
- Tunables named in the Constitution (budgets, timeouts, policies) are per-agent or per-deployment configuration, never inline constants.

## 7. Dependency management

- Every new dependency is a decision: recorded in DECISION_LOG with purpose and blast radius; licence compatible with commercial use; pinned with a lockfile; security-scanned (SECURITY_POLICY §6).
- Vendor SDKs may be imported **only** inside that vendor's Provider Plugin; the Pipeline Substrate only inside its module (AR-12/13).
- Prefer the standard library; prefer one library per job (no two HTTP clients).

## 8. Comments & code documentation

- Comments state constraints and *why* — never what the next line does, never the change history (git owns history).
- Every module has a header docstring: responsibility + governing Constitution section.
- Public functions/classes: docstring with behaviour, failure modes, and units for any time/size values (`timeout_ms`, not `timeout`).
- Deferred work: only the `DEFERRED(DL-xxx)` form referencing a logged decision (AI_AGENT_RULES #13); naked TODO/FIXME fail CI.

## 9. Testing expectations (bar; taxonomy in TESTING_POLICY.md)

- New behaviour lands with its tests in the same change-set. Tests use fakes for capability contracts; no real vendor calls in unit CI.
- Test names describe the behaviour ("interrupt_rejected_for_single_word_backchannel"), not the method under test.
- Determinism: no sleeps for synchronization; use virtual time/events. Flaky = quarantined (GOVERNANCE §11).

## 10. Deprecation & versioning (per Constitution A11.4)

- Public interfaces (REST, WS events, webhooks, SDK, capability contracts) are versioned from their first release.
- Breaking change flow: introduce vN+1 → mark vN deprecated (docs + response header/changelog) → deprecation window (minimum one minor release or 90 days once there are external consumers) → remove with changelog entry.
- Internal interfaces may change freely **within** a change-set that updates all callers — half-migrated states don't merge.
- Deprecated code paths emit a `warning`-level log once per process, are listed in the changelog, and have a removal target logged in DECISION_LOG.
