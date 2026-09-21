# Epic 12 — Analytics Definitions (M12.6)

| | |
|---|---|
| **Status** | Living · implemented at M12.6 · **the contract for `/v1/console/analytics/*`, and for any future SDK/API exposure of these figures** |
| **Date** | 2026-09-21 |
| **Authority** | [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP6 and Amendment 2 (DP12) |
| **Code** | `platform/api/console/analytics/service.py`, `platform/api/console/routers/analytics.py` |
| **Tests** | `platform/tests/test_console_analytics.py`, `console/src/features/analytics/Analytics.test.tsx` |

Analytics is a **read/query layer**. It owns no data. Each figure below is computed per request from
facts that already exist, and a change to any definition here is a contract change.

## 1. Source-of-truth matrix

Established by reading the code at M12.6, not assumed. "Owner" is the component that writes the fact.

| Fact | Where it lives | Owner | Used by analytics |
|---|---|---|---|
| Conversation (existence, identity) | `session` row (`api/models.py` `Session`) | M11 session API (`create_session`) | yes: one row = one conversation (M12.5) |
| Organization | `session.org_id` | M11 | yes: the tenancy predicate |
| Agent | `session.agent_id` → `agent` (name) · `dp_agent_state.archived_at` | M11 · M12.3 | yes |
| Start | `session.started_at` (naive UTC; set when the row is created) | M11 `create_session` | yes: **range membership** and duration |
| End | `session.ended_at` (naive UTC) | M11 `end_session`, its only writer | yes: duration |
| Lifecycle state | `session.status` ∈ `created · active · ended · failed` | M11. `failed` = the runtime refused to start. `ended` = `end_session` ran. | yes: outcomes |
| **End reason** | Not on the session. `session.ended.data.reason` in `evt_event` is the runtime's *internal* close path (`stopped`, `cancelled`, …). Every console and API hang-up arrives as the same `end_session` call. | runtime orchestrator | **no**: it does not distinguish caller, system or error endings, so no reason is inferred |
| Origin | `session.metadata.source` = `console_playground` (M12.4), else the developer API | M12.4 | yes: the `source` filter |
| Turn | one `metric.turn` event in `evt_event` | Epic 10/M11 `TurnLatencyTap` (sole producer, ADR-0038) | yes |
| Turn outcome | `metric.turn.data.status` ∈ `completed · cancelled · interrupted` (ADR-0038 TP6) | `TurnLatencyTap` | yes |
| Voice-to-voice latency | `metric.turn.data.voice_to_voice_ms` (int, or absent/null) | `TurnLatencyTap` (ADR-0038/0047/0053) | yes |
| Stage latency | `metric.turn.data.ms.{endpoint, stt, llm, tts, egress}` (absent when unmeasured; a negative interval is dropped at source, ADR-0047 ME6) | `TurnLatencyTap` | yes |
| Caller speech length | `metric.turn.data.ms.user_speech` | `TurnLatencyTap` | no: it is how long the caller spoke, not a latency |
| Whether the log observed a conversation | a `session.started` event in `evt_event` for that session | runtime orchestrator via the durable log (DP10) | yes: "recorded" |
| Transcript timing | `transcript.ts` (seconds since start, M12.5 F-3) | M11 WebRTC sink | no |
| Provider identity / cost / tokens | not recorded as usage anywhere authoritative | none | **no**: see §7 |
| Barge-in success | `interrupt.*` events exist, but barge-in is off on the product path (F-1, unresolved) | none | **no**: see §7 |

There is no new table, event, producer, writer or index. Analytics reads `session`, `agent`,
`dp_agent_state` and `evt_event`.

## 2. Rules that apply to every figure

1. **Missing is never 0.** An unmeasured latency is not a sample. A conversation without an end has
   no duration. A conversation the log never observed has *unknown* turns. An average, percentile,
   minimum or maximum over an empty population is `null`. The console shows `null` as
   "not measured".
2. **A count of nothing is 0.** Zero conversations, zero turns, zero samples, an empty day, and a
   measured value of `0 ms` or `0 s` are real zeros.
3. **Range membership is by `session.started_at`.** A conversation, *and every turn it contains*,
   belongs to the UTC day it started. Nothing is split across days or counted twice. A session with
   no `started_at` is outside every range. None exist on the product path, since `create_session`
   always sets it.
4. **Tenancy is server-side.** The organization is the console session's, never a parameter.
   Session rows are filtered on `session.org_id`, and events on **both** `evt_event.org_id` and
   membership in that organization's session set (the M12.5 rule), so an event filed under the
   wrong organization is never counted.
5. **No join multiplication.** The conversation population and the turn population are read
   separately. Turns select their sessions by `IN (…)`, not by joining, so a conversation counts
   once no matter how many turns, events or transcript rows it has. A test builds exactly that case.
6. **Mutually consistent reads.** Turns and "recorded" marks are kept only for conversations in the
   population read by the same request.

## 3. Time range

* **Canonical timezone: UTC.** Storage is naive UTC (the M11 model), and the query, the range and
  the buckets are all UTC. The browser never supplies a time zone or local time. The console labels
  the range "UTC" and formats days in UTC.
* **Presets:** `today` (1 day), `7d`, `30d`. These are UTC calendar days ending with and including
  the server's current UTC day. The default is `7d`.
* **Custom:** `from=YYYY-MM-DD&to=YYYY-MM-DD`. These are UTC days, **both inclusive**, with
  `from ≤ to`, at most **92** days, and only both together. `range` and `from/to` together are
  refused (400).
* **Instants:** days `[first, last]` are the half-open interval `[first 00:00Z, (last+1) 00:00Z)`. A
  conversation that starts exactly at `start` is in the range, and one that starts exactly at `end`
  is not.
* **Echo:** every response carries `query.range` (`preset`, `first_day`, `last_day`, `start`, `end`,
  `timezone: "UTC"`, `bucket: "day"`) and `query.filters`.
* **Buckets:** always one per UTC day. Every day of the range is present, in order, including days
  with no conversations.
* **Deferred:** buckets in the viewer's local timezone. They need timezone-aware grouping, not an
  offset: a fixed offset is wrong across DST.

## 4. Filters

| Filter | Values | Semantics |
|---|---|---|
| organization | implicit | the console session's active organization, never overridable |
| `agent_id` | an agent id | Must be an agent of the organization, archived or not: the history belongs to it. Another organization's agent, or a missing one, gives the same **404 `not_found` "Agent not found"**. |
| `source` | `playground` · `api` | `playground` = `metadata.source == "console_playground"`. `api` = everything else, including sessions with no metadata. The same rule sets `by_source`. |

## 5. Metric definitions

Notation: **C** = conversation population (the organization's sessions with `started_at` in the
range, after filters). **R ⊆ C** = recorded conversations (have `session.started` in the log).
**T** = `metric.turn` records whose session is in C. Every T-record's session is in R in practice,
since the runtime publishes `session.started` first.

### 5.1 Summary: `GET /v1/console/analytics/summary`

| Name | Definition | Population | Null semantics | Known limitations |
|---|---|---|---|---|
| `conversations.total` | \|C\| | C | never null (0 when empty) | |
| `conversations.by_status` | \|C\| per `session.status`. All four states are always present. | C | never null | This is the lifecycle state, **not an end reason** (§1). `failed` means the runtime refused to start. A later `end_session` on a failed session turns it `ended` (frozen M11 behaviour). |
| `conversations.by_source` | \|C\| per origin (§4) | C | never null | |
| `conversations.recorded` / `not_recorded` | \|R\| / \|C∖R\| | C | never null | `not_recorded` covers sessions from before the log was attached (pre-M12.5), sessions from the frozen default app, and sessions the runtime never started. |
| `duration.measured` / `unmeasured` | the number of conversations in C with both `started_at` and `ended_at` / the rest | C | never null | Unmeasured = active, or failed without ending. |
| `duration.total_s` | Σ duration over the measured conversations. Duration = `ended_at − started_at` in seconds, clamped at 0 and rounded to 0.1 s (M12.5's `duration_s`). | measured conversations | `null` if none is measured | Wall-clock time from session creation to `end_session`, **not** talk time. A zero-length conversation counts as a real 0. A call across midnight counts in full on its start day. |
| `duration.avg_s` | `total_s` / `measured`, rounded to 0.1 | measured conversations | `null` if none is measured | as above |
| `turns.total` | \|T\| | T | never null | This counts `metric.turn` **records**. With audio endpointing off (F-7, `VOXVOICE_CONSOLE_AUDIO_ENDPOINTING=false`), one spoken exchange can emit **two** records. Analytics counts them exactly as the conversation detail lists them (M12.5) and does not merge them. With endpointing on (the default since M12.5), it is one record per exchange. |
| `turns.by_status` | \|T\| per `data.status`: `completed`, `cancelled`, `interrupted`, and `other` for anything else, including a missing status | T | never null | |
| `turns.avg_per_conversation` | \|T\| / \|R\|, rounded to 0.01 | R | `null` if \|R\| = 0 | Unrecorded conversations are **excluded** from the denominator, because their turns are unknown, not 0. A recorded conversation with no turn *is* included, as a known 0. |

### 5.2 Latency: `GET /v1/console/analytics/latency`

One entry per measure, in this order: `voice_to_voice`, then the stages `endpoint`, `stt`, `llm`,
`tts`, `egress` (ADR-0038 TP5 / ADR-0047 ME1: the stages form a contiguous chain whose sum is
`voice_to_voice_ms` when all are present). Semantics are **those of the producer**. Analytics does
not redefine latency. `voice_to_voice_ms` runs from VAD end-of-speech to the first audio out
(ADR-0050 SW12, ADR-0053).

| Field | Definition | Null semantics |
|---|---|---|
| `turns` | \|T\| | never null |
| `samples` | the number of T-records carrying a **non-negative integer** for this measure | never null |
| `missing` | `turns − samples`. These records did not measure it: absent or `null` key, or an interval dropped at source. | never null |
| `avg_ms` | arithmetic mean of the samples, rounded to 0.1 ms | `null` if `samples = 0` |
| `min_ms` / `max_ms` | smallest / largest sample | `null` if `samples = 0` |
| `p50_ms` / `p95_ms` | **nearest-rank** percentile over the sorted samples: the value at rank ⌈p/100 · n⌉ (1-based). This is always an observed value, with no interpolation. | `null` if `samples = 0` |

* **Population:** all T-records regardless of turn status. A cancelled or interrupted turn that
  reached its first audio out has a real voice-to-voice measurement.
* **Small samples:** with few samples, p95 equals the maximum. The console always shows `samples`
  next to percentiles.
* A measured `0 ms` is a sample (it counts, and it can be the minimum).

### 5.3 Time series: `GET /v1/console/analytics/timeseries`

One bucket per UTC day of the range, in order. For each day *d* (C_d = conversations started on
*d*; T_d = T-records of C_d):

| Field | Definition | Null semantics |
|---|---|---|
| `date` | the UTC day | — |
| `conversations` | \|C_d\| | 0 on an empty day (a real zero: the day is known and had none) |
| `turns` | \|T_d\| | 0 when none |
| `avg_duration_s` | mean duration over the measured conversations in C_d (§5.1) | `null` if none is measured |
| `voice_to_voice_samples` | the number of T_d records with a voice-to-voice sample | 0 when none |
| `voice_to_voice_p50_ms` | nearest-rank median of those samples | `null` without samples |

The buckets sum to the summary: Σ `conversations` = `conversations.total`, and Σ `turns` =
`turns.total`.

### 5.4 Agents: `GET /v1/console/analytics/agents`

One entry per agent with at least one conversation in C, most conversations first (ties by name,
then id). The per-agent figures use exactly the definitions in §5.1 and §5.2, restricted to that
agent's conversations: `conversations`, `recorded`, `turns`, `avg_duration_s`,
`avg_turns_per_conversation`, and `voice_to_voice` (a full latency entry).

* `name` is `null` if the agent row no longer exists (a hard delete through the developer API).
* `archived` is `true` when `dp_agent_state.archived_at` is set. Archived agents keep their history.
* Names and archive state are read with one query each for all agents, never one per agent.

### 5.5 Outcomes

Outcomes are `conversations.by_status` and `turns.by_status` from the summary. They report
lifecycle states and turn outcomes as recorded. **No end-reason classification** is shown (§1).

## 6. Query design and performance

* **Reads per request:** at most three. (1) The conversation population: `session` filtered by org,
  range, agent and source. (2) T with the numeric fields **extracted in SQL** (`JSON` path
  extraction, compiled per dialect by SQLAlchemy), so the event payload never reaches Python.
  (3) The recorded marks.
* **Arithmetic in Python**, over one narrow row per conversation or per turn. Durations and
  percentiles cannot be computed portably in SQL across the dialects the M11 model supports
  without model changes: SQLite locally, Postgres in production (the Constitution, `api/config.py`).
  Timestamp subtraction differs per dialect, and SQLite has no percentile function. This does not
  load transcripts or event payloads. The 92-day cap bounds it.
* **Measured** on SQLite at 5,000 conversations and 845,000 events (40,000 `metric.turn`): 45–140 ms
  per request for `today`/`7d`/`30d`. Python arithmetic takes ≤ 10 ms of that.
* **Finding, index:** SQLite plans the turn read from `ix_evt_event_type`. Its cost therefore
  grows with the **total** number of `metric.turn` rows in the log (all tenants, all time), not with
  the range. Composite indexes that would bound it, `session(org_id, started_at)` and
  `evt_event(session_id, type)`, belong to frozen schemas (M11 `session`, Epic 10 `evt_event`).
  They are **not** added: at the measured scale none is needed, and adding one needs an owner
  decision on the frozen schema. When the log grows past this, the options in order are: that
  index, through an amendment; then the `dp_turn_metric` read model sketched in LLD §7, derived
  from the log, which remains the source of truth.
* No N+1: per-agent names and archive state use one query each, and the per-day and per-agent
  figures come from the same in-memory population.
* Postgres was not exercised: no Postgres driver is installed in the development environment, and
  adding one is a dependency decision. The SQL uses only portable constructs (comparisons, `IN`,
  `DISTINCT`, and SQLAlchemy's dialect-compiled JSON extraction).

## 7. Deliberately not provided

| Not provided | Why |
|---|---|
| Cost, spend, tokens, provider usage, estimated bill | **Cost analytics are deferred until authoritative metering exists.** Nothing in the M11/Epic 10 runtime data is authoritative provider usage, and prices are not recorded. Guessing would be a second, wrong source of truth (R-E12-6). |
| End-reason breakdown | The session records a lifecycle state, not a reason, and the log's close reason does not distinguish who ended the call (§1). |
| Barge-in / interruption success rate | F-1 is unresolved: barge-in does not reach the product path, so a rate would describe a feature that is off. |
| Per-provider breakdown | It would put vendor identity into generic analytics (E11-3). The data is not recorded per turn anyway. |
| Local-time buckets | See §3. |
| Percentiles per agent or day beyond p50, and interpolated percentiles | Kept to what small samples can support. They can be added under §5.2's definition. |
| Live (in-call) analytics, alerts, exports | Not in M12.6's scope. |
