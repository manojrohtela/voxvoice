# Epic 12 — Database Design

| | |
|---|---|
| **Status** | Living (M12.3). §3.1–§3.4 are **implemented** (migrations `0037`–`0038`, `api/console/persistence/`). §3.5 is design (M12.7). |
| **Date** | 2026-09-21 |
| **Related** | [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP1 · [LLD](m12-lld.md) · [Security model](../../security/m12-security-model.md) |

## 1. Principles

1. **Reuse the runtime model.** `org`, `apikey`, `agent`, `session` and `transcript` stay the
   authoritative rows for tenant, developer credential, agent, session and transcript (DP1).
   Epic 12 never alters or duplicates them.
2. **Add only what is missing**, in `dp_*` tables ("developer platform"). The prefix is distinct
   from `cp_*` (Epic 8), `evt_*` (Epic 10) and billing.
3. **Tenant isolation is a column and a query rule.** Every `dp_*` row that belongs to a tenant
   carries `org_id`, and every read filters on it server-side.
4. **Hashes, never secrets.** Passwords use scrypt. Session tokens and API keys are stored as
   SHA-256. No plaintext credential is stored.
5. **Query what you filter on.** Columns that are filtered, joined or sorted are real columns
   with indexes. JSON is not used anywhere in `dp_*`.
6. **Own migration chain.** `dp_*` tables are created by an Epic 12 chain starting at **`0037`**,
   above the Epic 10 head (`0036`). Frozen migrations (`≤ 0036`) and the `create_all`-managed
   legacy tables are untouched (R-E12-8).
7. **Real foreign keys** to the legacy tables. This deliberately differs from Epic 8's
   index-only columns, because the legacy tables are the ones Epic 12 must stay consistent with.

## 2. Existing tables Epic 12 relies on (unchanged)

| Table | Key columns | Epic 12 use |
|---|---|---|
| `org` | `id` (`org_…`), `name`, webhook fields | the tenant ("Organization", DP9) |
| `apikey` | `id`, `org_id`, `key_hash` (sha256), `label`, `created_at`, `last_used_at` | developer API credential. Revocation deletes the row. |
| `agent` | `id`, `org_id`, prompt/model/voice fields, `created_at`, `updated_at` | the agent (LLD §4 lists which fields act) |
| `session` | `id`, `org_id`, `agent_id`, `status`, `started_at`, `ended_at`, `channel` | conversation list/detail |
| `transcript` | `id`, `session_id`, `role`, `text`, `ts` (always 0.0, F-3) | transcript text. Order comes from `evt_event`. |
| `evt_event` | `event_id`, `session_id`, `org_id`, `seq`, `type`, `data`, `recorded_at` | event timeline, `metric.turn` (read-only) |

## 3. New tables

### 3.1 `dp_user` (M12.2) ✅

A person who signs in to the console. Distinct from Epic 8's `cp_user`, which has no credential
and lives in a disjoint tenancy graph (DL-0235).

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `usr_…` via `core.ids.gen_id` |
| `email` | text, **unique** | stored lower-cased and trimmed |
| `display_name` | text | |
| `password_hash` | text | `scrypt$n$r$p$salt$hash` (stdlib `hashlib.scrypt`, no new dependency) |
| `status` | text, indexed | `active` \| `disabled` |
| `created_at` / `updated_at` | timestamptz | |
| `last_login_at` | timestamptz null | |

### 3.2 `dp_membership` (M12.2) ✅

A user's role in an organization. Many-to-many from day one, so team support is additive.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mem_…` |
| `org_id` | text FK → `org.id`, indexed | tenant |
| `user_id` | text FK → `dp_user.id`, indexed | |
| `role` | text | `owner` \| `admin` \| `developer` \| `viewer` (LLD §3) |
| `status` | text | `active` \| `removed` (soft; the row is kept for audit) |
| `created_at` | timestamptz | |
| | **unique** `(org_id, user_id)` | one membership per user per org |

### 3.3 `dp_console_session` (M12.2) ✅

`org_id` is nullable: a user whose last membership was removed still has a session that can
create or choose an organization.

Server-side console sessions. They are revocable, which a JWT is not.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `cs_…` |
| `token_hash` | text, **unique** | SHA-256 of the 32-byte cookie token |
| `user_id` | text FK → `dp_user.id`, indexed | |
| `org_id` | text FK → `org.id` | the active organization for this session |
| `created_at` | timestamptz | |
| `last_seen_at` | timestamptz | idle expiry |
| `expires_at` | timestamptz, indexed | absolute expiry |
| `revoked_at` | timestamptz null | logout / forced sign-out |

Expired and revoked rows are purged by a bounded startup sweep (M12.2). The session table is not
an audit log.

### 3.4 `dp_agent_state` (M12.3) ✅

Implemented by migration `0038`. `agent_id` is `ON DELETE CASCADE`, so the developer API's frozen hard delete is never blocked. There is no `created_at`/`updated_at` on this row: the agent row carries those.

Lifecycle metadata for the runtime `agent` row, which the frozen model cannot carry.

| Column | Type | Notes |
|---|---|---|
| `agent_id` | text PK, FK → `agent.id` | 1:1 |
| `org_id` | text FK → `org.id`, indexed | redundant with `agent.org_id` and **checked equal** on write (defence in depth) |
| `archived_at` | timestamptz null | archive replaces hard delete (F-5) |
| `archived_by` | text FK → `dp_user.id` null | |
| `created_by` | text FK → `dp_user.id` null | null for agents created via the developer API |

### 3.5 `dp_api_key_meta` (M12.7)

Console metadata and the audit trail for developer API keys. This row outlives the `apikey` row it
describes, which is why there is **no** foreign key on `api_key_id`.

| Column | Type | Notes |
|---|---|---|
| `api_key_id` | text PK | the `apikey.id` at creation |
| `org_id` | text FK → `org.id`, indexed | |
| `display_prefix` | text | first 8 characters of the raw key, e.g. `vf_Ab3xQ`. This is not enough to authenticate. |
| `created_by` | text FK → `dp_user.id` null | null for CLI-created keys |
| `revoked_at` | timestamptz null | set when the `apikey` row is deleted |
| `revoked_by` | text FK → `dp_user.id` null | |

## 4. What is deliberately *not* a table

| Not stored | Why |
|---|---|
| a conversation table | `session` already is one (§A2.5). |
| a turn table / latency table | `metric.turn` in `evt_event` is the source of truth (DP6). A derived read model is allowed later only if needed (LLD §7). |
| usage/billing counters | Epic 12 does not meter (R-E12-6). Billing is Epic 9's (ADR-0023). |
| provider credentials | They belong to the ADR-0049 `CredentialProvider`, never the database. |
| user preferences | Not needed yet. Epic 8's `cp_user_preferences` is not reused (DP1). |

## 5. Future pricing: data it will need (identified, not built)

Session count and duration (`session`), turns and voice-to-voice (`metric.turn`), and the per-org
attribution already present (`org_id` on `session` and `evt_event`). Billable voice minutes derive
from session duration. Provider consumption is **not** measured (M11.12) and must not be
inferred. Any pricing work goes through Epic 9's frozen `MetricStreamPort`, not through Epic 12
tables.
