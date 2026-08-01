# VoxVoice — Troubleshooting Guide (M8.18, GA)

Correlate everything by the `request_id` in the error envelope and access log.

| Symptom | Likely cause | Resolution |
|---|---|---|
| Startup crashes immediately | config validation failed (§A2.6) | check `VOXVOICE_API_*`: prod needs a non-default JWT secret, non-`*` CORS, durable DB |
| `/ready` returns 503 | DB unreachable or config invalid | inspect the `checks` object: `configuration` / `database` / `application` |
| 401 on every request | missing/expired/mis-signed token, or key rotated out | reissue token; during rotation add the old secret to `JWT_SECONDARY_SECRETS` |
| 403 | RBAC / isolation | actor lacks the scope (e.g. `PROJECT_MANAGE`); or cross-tenant access blocked |
| 404 on a resource you created | wrong scope in the path | resources are org/workspace/project scoped; verify the path ids |
| 413 | request body over `MAX_REQUEST_BYTES` | raise the cap or shrink the payload |
| 429 + `Retry-After` | rate limit engaged | back off; raise `RATE_LIMIT_PER_MINUTE` if legitimate |
| 409 on deploy approve | separation of duties | the requester cannot approve their own deployment — use a second approver |
| 409 "no published version" | deploying a draft agent | publish the agent first |
| 500 "internal error" | unexpected server error (redacted) | correlate by `request_id` in the logs; never leaks internals by design |
| Metrics all zero | wrong scope or no events yet | metrics project the audit stream — generate activity or widen the scope |
| Deployment stuck pending_approval | production is approval-gated | approve as a workspace admin (≠ requester) |
| Knowledge search empty | not embedded/indexed | run `…/embeddings` then `…/index`; check `vector-status` |

## Diagnostic endpoints

- `GET /ready` — deep readiness (config/db/application).
- `GET …/observability/dashboards/system` — health + performance + firing alerts.
- `GET …/observability/logs?level=error` — recent error-level events (projected from audit).
- `GET …/observability/traces/{id}` — a distributed trace for one correlation key.
- `GET …/activity` — the immutable audit record.

## Escalation

If `/ready` is green but requests fail, capture the `request_id` + `correlation_id`, the failing
route, and the `…/observability/health` snapshot, then follow the Runbook.
