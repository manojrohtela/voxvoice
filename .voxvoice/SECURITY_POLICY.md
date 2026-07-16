# SECURITY_POLICY.md — Security Requirements

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner (acting Security Lead) · **Authority:** Level 3 · **Enforces:** Constitution A11.2, A11.1.6. Threat model lives in `docs/security/` and is updated when attack surface changes.

## 1. Secret management

- Secrets exist only in environment/secret stores; never in code, logs, version control, examples, chat transcripts, or documentation. `.env` files are git-ignored and verified ignored **before** the first commit of any repo.
- `.env.example` documents every variable with placeholder values only.
- **Rotation:** on any suspected exposure, rotate first, investigate second. Standing item: the Phase-1-exposed keys (LLM/TTS/engine) are treated as compromised until rotated (migration M0). Rotation runbook in `docs/security/`.
- CI runs secret-scanning on every push; a hit blocks merge and triggers rotation.

## 2. Authentication

- **Consumers:** org-scoped API keys, stored as hashes only, shown once at creation (Phase-1 mechanism, retained). Constant-time comparison for all secret checks.
- **Callers/clients:** short-lived, session-scoped credentials (minutes, single session) issued at session start — never the org key in a browser/device.
- **Internal callbacks/ingestion endpoints:** dedicated shared secrets or signed requests; never unauthenticated; never default values in production (startup fails on placeholder secrets outside dev).

## 3. Authorization

- Every query on org-owned data is org-scoped; cross-org access returns 404 (indistinguishable from absent). This discipline is permanently under test (TESTING_POLICY rule 4).
- New resource types get scoping + tests in the same change-set or don't merge.
- Control-API commands (end/say/inject) are authorized against the session's org, and every command is audit-logged (the event log).

## 4. Logging & data protection

- No secrets, tokens, or credentials in logs — redaction filter at the sink is mandatory infrastructure, not per-call discipline.
- PII in transcripts: per-org redaction policy; transcripts and recordings are org-scoped data with per-org retention, export, and deletion (data-protection-regulation shaped: GDPR/DPDP-ready).
- Encryption: TLS for all external transport (API, webhooks, media signaling; media per transport capability); at-rest encryption via the managed System of Record/storage provider; no custom cryptography — standard libraries only (HMAC for webhook signing, standard hashes for keys).

## 5. Input & output validation

- Every external input (API bodies, webhook registrations, client events, provider responses) is schema-validated at the boundary; free-string enums are banned (CODING_STANDARDS §1).
- Consumer-supplied URLs (webhooks, future tools) are validated against SSRF: scheme allow-list, deny private/link-local address ranges, resolve-then-connect pinning — **before** the first self-serve URL feature ships (audit S7).
- Output: raw provider/vendor errors never reach consumers (typed translation, CODING_STANDARDS §3); rendered pages/SDK surfaces escape all user-derived text (audit S4: transcript XSS class).
- LLM output is treated as untrusted input to any downstream interpreter (tool arguments are schema-validated like any external input).

## 6. Dependency & vulnerability management

- Dependencies pinned via lockfile; automated vulnerability audit in CI (fail on high/critical with a triage note path); new dependencies logged (CODING_STANDARDS §7).
- Updates: security patches applied within one week (critical: 48 h); routine bumps monthly, batched, tested.
- **Vulnerability response:** rotate/contain → assess blast radius via logs and event log → fix → record in DECISION_LOG (and `docs/security/` post-mortem if consumer-affecting) → notify affected orgs per severity.

## 7. Audit logging

The event log is the audit log (Constitution A11.2): session lifecycle, control commands, config changes, key usage (`last_used`), and webhook deliveries are all evented and retained per org policy. Administrative actions (org/key provisioning) must be evented before any multi-operator stage.

## 8. Security reviews

- Every PR: the security dimension of REVIEW_CHECKLIST (§4) — new surface? scoping? secrets? validation?
- Every release: the release security pass (GOVERNANCE §10).
- Every stage promotion (A9) and every new externally reachable surface (telephony, tools, self-serve webhooks): a focused threat-model update in `docs/security/`.
