# VoxVoice — Security Guide (M8.18, GA)

Inherits Constitution §A11.2 and ADR-0021. Verified by `test_production_hardening` +
`test_ga_certification` + the per-domain isolation tests.

## Tenancy & isolation

- Every domain fact carries `org_id`; every query is org-scoped by construction — a query can never
  cross tenants. Workspace and project scoping narrow further.
- Verified: cross-project reads 404, cross-org access is impossible, viewers can read but not mutate
  (per-domain isolation tests across agents/tools/knowledge/deployment/observability).

## AuthN / AuthZ

- **AuthN:** bearer JWT (HS256, stdlib, constant-time verify + expiry). The dev token endpoint is a
  seam a real IdP replaces; it mints identity only.
- **Key rotation:** verification tries all `verification_secrets` (primary + secondary); rotate with
  zero downtime and remove the old key after TTL.
- **AuthZ:** RBAC via existing scopes (`ORG_MANAGE`, `PROJECT_MANAGE`, `PROJECT_READ`, …). Writes
  authorize; reads are org-scoped. Deployment approvals enforce **separation of duties** (author ≠
  approver; production gated).

## Secrets & credentials

- Provider/infrastructure secrets never live in code, logs, or VCS. They resolve through the
  `CredentialProvider` seam (`EnvCredentialProvider` default; vault-backed in production) and are
  **redacted** everywhere structures are logged/serialised (`redact` / `redact_headers`).

## Transport security

- TLS terminated in front of the API; **HSTS** advertised over HTTPS only. Security headers on every
  response: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `X-XSS-Protection`, `Permissions-Policy`, `Cache-Control: no-store`.
- **Payload limits** (413) and **rate limiting** (429) guard the edge. CORS is explicit in prod
  (wildcard refused).

## Error handling

- 5xx responses are **redacted** (`internal error`, no stack/internal detail); 4xx carries the typed
  message. Every response carries a `request_id` for correlation.

## Audit integrity

- The event log **is** the audit log — append-only, immutable (no update/delete on the read seam;
  verified by `test_ga_certification::test_audit_stream_is_append_only`). Observability consumes it
  and never duplicates events.

## Configuration validation

- Production **fails fast** on: the default JWT secret, wildcard CORS, in-memory SQLite, non-positive
  limits — the process refuses to start (§A2.6).

## Out of scope (this GA)

Enterprise SSO, multi-region, a notification platform, billing — none are present; none are required
for the GA security envelope.
