# VoxVoice — Support Handbook (M8.18, GA)

For customer-facing support engineers. Everything a customer does is via the Studio UI or the
Control Plane API; every action is audited.

## Product surface (what a customer can do)

| Area | Studio screen | API | Reference |
|---|---|---|---|
| Org / workspace / project | Org/Workspace/Project cards | `…/organizations`, `/workspaces`, `/projects` | M8.6–M8.8 |
| Agents | Agent Builder | `…/agents` | M8.10 |
| Tools | Tool Framework | `…/tools`, `…/agents/{id}/tools` | M8.11 |
| Conversations | Runtime Inspector / Playground | `…/playground/conversations`, `…/execute` | M8.12–M8.14 |
| Knowledge | Knowledge Dashboard | `…/knowledge/…` | M8.15 |
| Deployment | Deployment Dashboard | `…/deployments/…` | M8.16 |
| Observability | Metrics/Trace/Log/Health/Usage/Cost/Alert | `…/observability/…` | M8.17 |

## Common customer questions

- **"My agent won't deploy."** It must be **published** (draft agents never deploy). Production is
  approval-gated — a workspace admin (not the requester) must approve.
- **"Knowledge search returns nothing."** Upload → chunk → generate embeddings → index; check
  `vector-status`. Text only (no OCR/images).
- **"I can see another project's data."** You cannot — everything is org/workspace/project scoped.
  A 404 across scopes is expected isolation, not a bug.
- **"Why 429 / 413?"** Rate limit / payload cap — raise the limit or shrink the request.
- **"What do these costs mean?"** The Cost Dashboard is a **directional estimate**, not a bill
  (billing is out of scope).
- **"Can I see what happened?"** Yes — the Activity feed + Observability logs/traces are the
  complete, immutable record.

## Escalation to engineering

Collect: org id, the `request_id` from the error envelope, the failing route + status, and (if
relevant) the `…/observability/health` snapshot. Then hand to on-call with the Runbook section.

## Boundaries to set with customers (out of scope this GA)

Billing, Marketplace, Enterprise SSO, multi-region, autoscaling, notifications, OCR/image
processing. These are explicitly not in the GA and should be tracked as roadmap requests.
