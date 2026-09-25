# Frontend Product Completeness Audit

| | |
|---|---|
| **Status** | Active v1.1 (M12.12 audit · M12.12A corrective pass) |
| **Date** | 2026-09-25 |
| **Scope** | The Epic 12 console (`console/`), audited as a product. No code was changed. |
| **Tree** | `a25df58` on `feat/m10.2-durable-event-foundation`, working tree clean |
| **Related** | [Product vision](../architecture/epic12/m12-product-vision.md) · [Frontend architecture](../architecture/epic12/m12-frontend-architecture.md) · [LLD](../architecture/epic12/m12-lld.md) · [Certification](m12-certification.md) · [E2E](m12-e2e.md) · [Developer API](../apis/developer-api.md) |

> **Sections 1–25 are the M12.12 audit, unchanged.** That milestone implemented nothing: every
> gap below is a finding, not a plan. Section 21 is the only list that claimed anything must
> change before deployment.
>
> **Section 26 is the M12.12A corrective pass**, which closed all four of those items and nothing
> else. Where §21 says "MUST FIX", read §26 for what was done.

---

## 1. Executive Summary

The console is **feature-complete for the product Epic 12 defined** and the core journey works end
to end in a real browser: sign up → create an agent → talk to it over real WebRTC → read the
transcript, the per-turn latency and the timeline → create an API key → call the developer API.
Re-run on this tree during the audit: **72/72 browser checks, 26/26 SDK checks**.

What the audit found is not missing features so much as **four honest-surface and consistency
defects**, all small, all frontend-only:

1. **The top bar claims "real providers" on the evidence of the transport alone** (§16, §21). The
   server's `/v1/console/meta` carries *no* provider information — deliberately, for security —
   so the console cannot know this. During this very audit the harness ran scripted TTS while the
   console displayed "WebRTC · real providers". This contradicts product principle 2 ("Honest
   surfaces") and is the one finding that could mislead an operator about whether a call was real.
2. **Conversation detail scrolls horizontally** at 761–768 px and at ~500 px (measured: 775 px and
   535 px minimum table width), because its Turns table is the one wide table not wrapped in
   `.table-scroll`. The frontend architecture doc states "nothing scrolls horizontally". M12.10
   never tested this page at narrow widths.
3. **The Playground offers a viewer an enabled "Start call" button**, asks for their microphone,
   and only then fails with `403`. The failure is graceful and correctly worded, but every other
   role-restricted surface (Agents, API keys) hides or explains the restriction *before* the
   attempt. Verified live with a real viewer account.
4. **The architecture doc claims "Contrast meets WCAG AA in both themes"** while the certification
   record says contrast was never tested. The audit resolved this in the product's favour by
   computing it: **every text token pair passes AA in both themes** (§18, ratios given). The doc's
   claim is true but was, until now, unevidenced — and it is still narrower than "WCAG AA".

No security gap, no data leakage, no broken navigation, no inaccessible core action and no
impossible onboarding was found. The genuinely absent product capabilities (conversation search,
transcript export, team invitations, cost, agent versioning, tools/RAG) are **either explicitly
out of Epic 12's scope by the product vision §5, or blocked on runtime and data the platform does
not have**. Inventing them in the frontend would mean inventing data.

**Verdict: the frontend is ready to move toward deployment**, once the four defects above are
decided on and the non-frontend gates from the certification record (real-provider call, Postgres,
password lifecycle) are met. None of the four is an Epic 12 certification blocker.

---

## 2. Current Product Surface

`console/` — React 19, react-router 8, TypeScript 7, Vite 8, Vitest 5. **6,022 lines** across 52
files. Zero runtime dependencies beyond React and the router. 87 component tests.

| Layer | Files | What it is |
|---|---|---|
| Shell | `components/AppShell, Sidebar, TopBar, ErrorBoundary` | Capability-driven navigation; the boundary is keyed by `org:path` |
| Data | `api/client.ts`, `api/useResource.ts` | One `fetch` door, one `ApiError`, one loading/ready/error resource |
| Auth | `app/auth.tsx` | `/v1/console/me` *is* the auth state; nothing in `localStorage` |
| Meta | `app/meta.tsx` | One `/v1/console/meta` read gates every surface |
| Voice | `voice/call.ts` | Framework-free `VoiceCall`; the browser holds only a signalling token |
| Features | 9 directories | Auth, onboarding, overview, agents, playground, conversations, analytics, developers, settings |

The console consumes **every** console API route except `GET /v1/console/organization` (it uses
`/me` instead). There is no orphaned frontend surface and no unused capability.

---

## 3. Route Inventory

Routes are defined in `console/src/app/App.tsx`. The list below is complete — the prompt's
assumed list was missing `/` (Overview is the index route, not `/overview`), the organization
chooser, and the catch-all.

### `/login` — `features/auth/SignInPage.tsx`

| | |
|---|---|
| **Purpose** | Sign in with email and password |
| **Features / actions** | Email, password, submit; link to `/signup` only when `meta.signup.available` |
| **API** | `POST /v1/console/auth/login`, `GET /v1/console/meta` |
| **Loading** | Button → "Signing in…", disabled |
| **Empty** | n/a |
| **Error** | `FormError` above the button, `role="alert"`, server message verbatim |
| **Success** | Auth state flips; `Navigate` to `/` |
| **Role / org** | n/a |
| **Responsive** | Centred card, `100dvh`, fine at 390 px |
| **E2E** | Covered (sign-in, redirect of every protected route) |
| **Missing** | No password reset, no "show password", no rate-limit feedback. The alternate link reads "Create the **first** account" even when sign-up mode is `open` and accounts already exist — mildly inaccurate copy. |

### `/signup` — `features/auth/SignUpPage.tsx`

| | |
|---|---|
| **Purpose** | Create the first (or, in `open` mode, another) account plus its organization |
| **Features / actions** | Name, email, password (min 12), organization name |
| **API** | `POST /v1/console/auth/signup` |
| **Loading** | "Creating…" |
| **Empty** | When sign-up is closed: the exact `bootstrap_owner` command to run |
| **Error** | `FormError` |
| **Success** | Signed in, redirected to Overview |
| **Missing** | No password-strength meter beyond `minLength`, no confirm field, no terms. None required for a local-first product. |

### `/` (index) — `features/overview/OverviewPage.tsx` — see §10

| | |
|---|---|
| **API** | `/v1/console/overview`, `/health`, `/v1/console/meta`, `/v1/console/agents` |
| **Loading** | Per-card `Loading` |
| **Empty** | "Needs attention" → "Create an agent"; empty cards per panel |
| **Error** | Per-card `ErrorState` with retry and request id |
| **Role / org** | `viewer`+; org-scoped server-side; remounted on org switch |
| **Responsive** | 1024 / 768 / 390 px covered by the harness |
| **E2E** | Covered, including *Overview figures ≡ Analytics figures* |
| **Missing** | The "Get started" steps are **not links** — they carry an "Available" pill but no navigation, and they do not reflect progress (they look identical whether you have 0 or 50 agents) |

### `/agents` and `/agents/new`, `/agents/:agentId` — see §6

| | |
|---|---|
| **API** | `GET/POST /v1/console/agents`, `GET /agents/options`, `GET/PATCH /agents/{id}`, `POST /agents/{id}/archive` |
| **Empty** | "No agents yet", worded differently for a role that cannot create |
| **Error** | `ErrorState` + retry; `FormError` on save |
| **Success** | "Saved" (`role="status"`); create navigates to the editor |
| **Role** | `New agent` hidden below `developer`; the form is a disabled `fieldset` with "You have read-only access" |
| **Missing** | No unsaved-changes guard; no duplicate; no archived-agent view; no delete (deliberate — F-5) |

### `/playground` — see §7

| | |
|---|---|
| **API** | `POST /playground/rendezvous`, `POST /playground/sessions`, `POST /sessions/{id}/end`, plus the frozen signalling endpoints |
| **Empty** | "No agent to call yet" / "Voice is not enabled on this server" (names the env var and the runbook) |
| **Error** | Inline `role="alert"` with the failure reason |
| **Role** | **Not gated in the UI** — see §13, defect 3 |
| **Missing** | No live transcript, no speaking indicator, no mic level, no device picker |

### `/conversations`, `/conversations/:conversationId` — see §8

| | |
|---|---|
| **API** | `GET /v1/console/conversations[?agent_id&status&cursor]`, `GET /conversations/{id}` |
| **Loading** | `Loading`; "Load more" has no pending state |
| **Empty** | Distinguishes "no conversations" from "no match for these filters" |
| **Responsive** | **Detail overflows at 761–768 px and ~500 px** — defect 2 |
| **Missing** | No search, no date filter, no export/copy, no auto-refresh for an active call; the breadcrumb drops the list's filters |

### `/analytics` — see §9

| | |
|---|---|
| **API** | `/analytics/{summary,latency,timeseries,agents}` — four independent resources, each with its own three states |
| **Empty** | Per panel, and a "Choose both days" state for an incomplete custom range |
| **Missing** | No CSV export, no comparison to a previous period |

### `/developers`, `/developers/docs` — see §11

| | |
|---|---|
| **API** | `GET/POST /v1/console/api-keys`, `POST /api-keys/{id}/revoke`, `GET /openapi.json` |
| **Role** | `admin`+ manage; everyone else gets the reason and the docs link |
| **Missing** | No copy button on code samples; examples hard-code `http://localhost:8000` rather than the serving origin |

### `/settings` — organization, members, account

| | |
|---|---|
| **API** | `GET /organization/members`, `PATCH /organization`, `POST /organizations` |
| **Role** | Rename is owner-only, with the reason shown otherwise |
| **Missing** | **No password change**, no display-name edit, no member role change or removal, no invitations (vision §5 defers them), no session list, no account deletion. No server endpoint exists for any of these — confirmed by enumerating every console route. |

### Organization chooser (no path) — `features/onboarding/CreateOrganizationPage.tsx`

Rendered by `Protected` when a signed-in user has no active organization. Lists memberships,
creates a new organization, signs out. Not reachable by URL, so it has no route of its own.

### `/developers/docs`, `*` catch-all

`*` renders "Page not found" inside the shell with working navigation. No dead route, no wrong
redirect, no broken link was found in the audit.

---

## 4. User Journey Audit

Walked as a new developer, against the running harness.

| # | Step | Can the user do it? | Clear? | Finding | Class |
|---|---|---|---|---|---|
| 1 | Open the product | Yes | Yes | `/console/` redirects a signed-out visitor to sign-in | — |
| 2 | Sign up | Yes | Yes | One form creates account + organization | — |
| 3 | Sign in | Yes | Yes | — | — |
| 4 | Understand the organization | Partly | **Partly** | The org name is in the top bar and Settings, but nothing ever explains *what* an organization is or that everything is scoped to it. The chooser explains it — and is only shown to users who have none. | GOOD TO HAVE |
| 5 | See the Overview | Yes | Yes | Runtime status, get-started, attention, figures | — |
| 6 | Know what to do next | Yes | Yes | "Needs attention → Create an agent" is the actionable path. The "Get started" card beside it is **not clickable**. | GOOD TO HAVE |
| 7 | Create an agent | Yes | Yes | Hint text on every field | — |
| 8 | Configure it | Yes | Yes | Only settings that reach the runtime are shown (AR-25) | — |
| 9 | Test it | Yes | Yes | "Talk to this agent" from the editor; the Playground preselects it | — |
| 10 | Make a voice call | Yes | Yes | Real `RTCPeerConnection`; states and a connection log | — |
| 11 | Inspect the conversation | Yes | Yes | Direct link from the Playground after hang-up | — |
| 12 | Inspect analytics | Yes | Yes | Same figures as the Overview, provably | — |
| 13 | Create an API key | Yes | Yes | One-time reveal, copy, "Done" | — |
| 14 | Read the docs | Yes | Yes | Auth, endpoints, errors, curl/Python/TS, live reference | — |
| 15 | Use the SDK | Partly | Yes | Accurate, but the SDK is unpublished: "install it from the repository's `sdk/` directory" with no further instruction | GOOD TO HAVE |
| 16 | Return to the agent | Yes | Yes | From Analytics, Overview and conversation detail | — |
| 17 | Switch organization | Yes | Yes | Every surface reloads; stale URL filters dropped | — |
| 18 | Sign out | Yes | Yes | Server-side invalidation; the old cookie is dead | — |

**No step is impossible.** Two are less clear than they should be (4, 6) and both are copy-level.

---

## 5. Onboarding Audit

What a brand-new developer can learn **from the console alone**, without the repository:

| Question | Answered? | Where |
|---|---|---|
| What is VoxVoice? | **No** | Nowhere. The sign-in lede says "manage your agents and talk to them"; there is no product statement. |
| What is an Agent? | Partly | "Each one is what a caller talks to" on the Agents page; the field hints explain each setting |
| How do I create one? | Yes | Attention item → "Create an agent"; primary button on Agents |
| How do I test it? | Yes | "Talk to this agent"; the get-started list names the Playground |
| How do I make a **real** call? | Partly | Only when voice is *off* does the console explain what real voice needs. When the transport is `webrtc` it says nothing about provider credentials — and claims "real providers" regardless (defect 1) |
| Where are conversations? | Yes | Playground links to the exact one; nav item; Overview "Recent conversations" |
| Where is analytics? | Yes | Nav; Overview links to the same range |
| How do I get an API key? | Yes | Nav item, and the get-started list |
| How do I use the API? | Yes | `/developers/docs` — auth, every endpoint, errors, three languages, live reference |
| How do I use the SDK? | Partly | Documented; installation is "from the repository" |

**The Overview empty state does guide**: an organization with no agents shows *Needs attention →
"No agents yet" → Create an agent*, verified by the harness check `empty org: needs attention →
create an agent`. The first run is sufficient to reach a working call.

**Gap:** there is no first-run explanation of *what the product is* or *what an organization is*,
and the get-started card is decorative rather than navigational. Both are **GOOD TO HAVE**, not
blockers: the attention path already carries the user through.

---

## 6. Agent Experience

Implemented: create, name, instructions, greeting, language (from the server's declared set),
model, voice, save (only changed fields), archive (with confirmation), persistence across reload,
`maxLength` on every field, `required` on name and instructions, an unsupported-language banner,
and a read-only mode for `viewer`.

The console shows **only fields that reach the runtime** (LLD §4). `allow_interruptions`,
endpointing delays, `tools` and the provider selectors are deliberately not shown because they
have no effect — and values set on them through the developer API survive every console edit.
**This is the right call and must not be "fixed" by exposing them.**

| Candidate control | Verdict | Why |
|---|---|---|
| Test agent | **NOT REQUIRED** | The Playground *is* it, and it is one click from the editor |
| Duplicate agent | **GOOD TO HAVE** | Pure frontend + existing `POST`; saves retyping a long prompt |
| Version / history | **FUTURE** | No `dp_agent_version` table and no history in the runtime row. Frontend + data model. |
| Draft / published | **NOT REQUIRED** | There is no deployment step for an agent; a saved agent is live. Inventing the state would be a lie. |
| Agent status (active/archived) | **GOOD TO HAVE** | Archived agents are simply absent from the console; there is no way to see or restore one. The data exists (`dp_agent_state`). |
| Advanced voice config | **FUTURE** | The runtime takes one `tts_voice` id; speed/stability/style are not plumbed |
| Prompt management (variables, templates) | **FUTURE** | No substitution exists in the runtime |
| Temperature / model parameters | **FUTURE** | `StartSessionRequest` carries the model id only |
| Knowledge / RAG | **FUTURE** | No retrieval anywhere in the platform. A large epic, not a frontend gap. |
| Tools / actions | **FUTURE** | The field is stored and ignored (LLD §4). Showing it would break AR-25. |
| Webhooks | **FUTURE** | No delivery mechanism exists |
| Metadata | **GOOD TO HAVE** | Sessions accept metadata; agents do not |

**Nothing is MUST HAVE NOW.** The two closest to it — duplicate and an archived-agent view — are
conveniences.

---

## 7. Playground Experience

A developer can currently see: the selected agent, eight call states (`Ready`, `Waiting for
microphone`, `Reserving`, `Connecting`, `Connected`, `Hanging up`, `Call ended`, `Failed`), a
`mm:ss` timer while connected, a timestamped connection log, mute with `aria-pressed`, hang-up,
the failure reason, and — after the call — a direct link to the conversation. Leaving the page or
closing the tab ends the session (`pagehide` + `keepalive`).

| Question | Answered? | How |
|---|---|---|
| Is my microphone on? | Partly | "Microphone ready" in the log and a Mute toggle — but **no live input level**, so a muted-at-OS-level or dead microphone looks identical to a working one |
| What is the connection doing? | Yes | State pill + `Peer connecting/connected/failed` in the log |
| Is the assistant speaking? | **No** | Audio plays through a hidden `<audio>`; there is no visual indication |
| Am I speaking / was I heard? | **No** | No live transcript, no VAD indicator |
| Was there an error? | Yes | Inline, `role="alert"`, with the actual reason |
| How long has the call run? | Yes | Timer |
| Did it end, and how? | Yes | "Call ended" / "Failed" + reason |

**After the call**, the UI links to the **conversation** — but not to Analytics or back to the
agent. That is a reasonable default (the conversation is the interesting artefact).

**Missing, classified:** a live transcript and speaking indicators are the two that would change
the product most, and both need the server to **push** runtime events to the browser (there is no
WebSocket or SSE surface; the durable log is read after the fact). **FRONTEND + BACKEND**, and
arguably FRONTEND + RUNTIME for a user-speaking signal. A microphone level meter is
**FRONTEND ONLY** (Web Audio on the local stream) and is the cheapest real improvement here.

**Defect (§13):** a `viewer` is offered an enabled "Start call", prompted for their microphone,
and then told "This action needs the developer role or higher".

---

## 8. Conversation Experience

| Question | Answered? | Where |
|---|---|---|
| What happened? | Yes | Transcript with caller/agent roles and offsets |
| Which agent? | Yes | Linked, with "deleted agent" when gone |
| When? | Yes | `formatWhen`, list and detail |
| How long? | Yes | Duration, or "not ended" |
| What did each side say? | Yes | Full transcript; a warning when ordering is unknown |
| How many turns? | Yes | Turns table; the list shows message count |
| How fast? | Yes | Voice-to-voice plus five stages per turn; unmeasured stages say so |
| Why did it end? | **No** | **The runtime does not record an end reason.** Analytics states this explicitly, and the developer API docs repeat it. |

Filters: agent, status. Pagination: cursor-based "Load more". Linkage: Playground → conversation,
agent → conversations, Analytics/Overview → conversations, conversation → agent.

| Candidate | Class | Dependency |
|---|---|---|
| Search (transcript text) | GOOD TO HAVE | FRONTEND + BACKEND (no search endpoint; `dp_*`/`evt_*` have no text index) |
| Date filters | GOOD TO HAVE | FRONTEND + API (analytics has ranges; the conversation list does not) |
| Pagination | **Present** | Cursor-based |
| Transcript copy / export | GOOD TO HAVE | **FRONTEND ONLY** — the data is already on the page. The cheapest high-value addition in this section. |
| Download (JSON/CSV) | LATER | FRONTEND ONLY for what is loaded |
| Replay (audio) | **NOT CURRENTLY SUPPORTED** | No audio is stored anywhere. Storing it is a privacy, storage and runtime decision — a new epic, not a frontend gap. |
| Annotations / tags / feedback | FUTURE | FRONTEND + DATA MODEL |
| Error details | FUTURE | FRONTEND + RUNTIME — the runtime records lifecycle state, not causes |
| Auto-refresh while active | GOOD TO HAVE | FRONTEND ONLY (poll) |
| Long-conversation rendering | Known debt | Already recorded as non-blocking in the certification record |

---

## 9. Analytics Experience

Present: conversations, turns, average duration, average turns per conversation, a daily usage
series, a full latency table (samples, **not measured**, P50, P95, average, min, max) for
voice-to-voice and all five stages, per-agent usage, and outcome breakdowns by conversation status
and turn status. Filters: preset or custom UTC range, agent, source. Every "not measured" is
counted and never shown as 0.

| Concept | Status | Dependency |
|---|---|---|
| Usage, volume, duration, turns, latency, per-agent, trends, outcomes | **SUPPORTED NOW** | — |
| Error rate | **REQUIRES RUNTIME** | Turn status is recorded; a *failure taxonomy* is not |
| Success rate / response quality | **NOT SUPPORTED — do not add** | There is no definition of success in the platform. Any number would be invented. |
| Abandonment | **REQUIRES RUNTIME** | Needs an end reason, which does not exist |
| Endpointing metrics | **REQUIRES BACKEND** | `metric.turn` carries an `endpoint` stage already surfaced per turn; distributions over it are a projection away |
| Barge-in metrics | **REQUIRES RUNTIME** | F-1 is unresolved; barge-in does not happen |
| Cost | **NOT SUPPORTED — do not add** | Epic 12 explicitly excludes billing and provider metering (vision §5). No token or provider usage is recorded anywhere. |
| Token usage | **REQUIRES RUNTIME** | Not emitted by the LLM plugin |
| Provider usage | **REQUIRES RUNTIME + would breach `E11-3`** | `api/**` must not name a vendor |
| CSV export | GOOD TO HAVE | FRONTEND ONLY |
| Period-over-period comparison | GOOD TO HAVE | FRONTEND + API |

**The honest-surface discipline here is the product's strongest asset and must be preserved.**

---

## 10. Overview Experience

| Question | Finding |
|---|---|
| What should a new user see? | They do: runtime status, an attention item pointing at agent creation, and a get-started list |
| What should an active developer see? | They do: summary, usage bars, latency snapshot, recent conversations, agent activity |
| What should an empty organization see? | Attention + per-card empty states. No fake zeros. |
| Are important actions one click away? | **Partly.** Attention items link. The get-started steps do not. There is no "New agent" or "Start a call" button on the Overview itself. |
| Are problems visible? | Yes — no agents, unsupported language, no conversations, plus degraded runtime and a console/server revision mismatch banner |
| Does it duplicate Analytics? | **No, by construction.** One `GET /v1/console/overview` composes the same projections; equality is proven in CI *and* in the browser harness |
| Are links contextual? | Yes — the Analytics link carries the current range and agent; an archived agent links to its conversations instead of a dead editor |

**No health score, no cost, no AI insight — and none should be added.** The only weakness is that
the get-started card is informational where it looks actionable.

---

## 11. Developer / API / SDK Experience

Can a developer go from "create an account" to "make my first API request" **without external
help**? **Yes** — and this was walked: sign up → `/developers` → create a key (label required,
one-time reveal, copy button, warning banner) → `/developers/docs` → the `curl` example needs only
the key pasted in.

| Item | Present |
|---|---|
| API key instructions | Yes — server-side only, one organization, not a provider key, not a console login, revocation semantics |
| curl example | Yes |
| Python example | Yes (httpx) |
| TypeScript example | Yes (`fetch`) |
| SDK example | Yes, with error handling |
| Authentication explanation | Yes — `x-api-key`, and why the browser gets a signalling token instead |
| Base URL | Stated, `http://localhost:8000`, hard-coded rather than derived from the serving origin |
| Error examples | Yes — a status-code table plus the envelope shape |
| WebRTC explanation | Yes — four endpoints and the four-step sequence |
| Session lifecycle | Yes — create, get, transcript, end |
| "Not available yet" | Yes — an explicit list (no analytics API, no scopes/expiry/rotation/rate limits, no session listing, no barge-in, no cost, no telephony) |
| Live reference | Yes — generated from this server's `/openapi.json`, with the credential per operation |

Key UX: created once, shown once, `Cache-Control: no-store`, copy with a manual-copy fallback,
cleared from the DOM on "Done", **not recoverable after reload** (harness-verified), revocation
behind a confirm and effective on the next request (`401`, harness-verified).

**Gaps:** no copy button on the code samples (**FRONTEND ONLY**, trivial); the examples do not use
the origin the console is actually served from; the SDK install instruction is a directory path
because publication is deliberately deferred.

---

## 12. Navigation

Seven sidebar items matching the product vision's information architecture. `NavLink` gives
`aria-current="page"`. A capability the server does not declare is rendered as "Soon" and routes
to an honest `UnavailablePage` — never to faked data. Breadcrumbs exist on the two detail pages
(agent editor, conversation detail) and on the docs page.

| Check | Result |
|---|---|
| Active state | Correct, including `/developers/docs` keeping "API keys & docs" active |
| Breadcrumbs | Present where needed |
| Back navigation | Works — but the breadcrumb **drops the list's filters** (`/conversations?agent=…` → `/conversations`) |
| Dead routes | None. `*` renders "Page not found" inside the working shell |
| Wrong redirects | None. Signed-out → `/login` for every protected path (5 checked) |
| Organization context | Always in the top bar; switching remounts every view via the `org:path` boundary key |
| Stale filters | Dropped on switch (`TopBar.switchTo`), verified in the browser |
| Org switch from every page | Verified on Overview, Agents, Conversations, Analytics, Developers, Settings |

---

## 13. Role-Based UX

Audited against the LLD §3 matrix with **real accounts of each role**, created for this audit and
driven through a real browser.

| Surface | owner | admin | developer | viewer | Server truth |
|---|---|---|---|---|---|
| Read agents / conversations / analytics | shown | shown | shown | shown | ✓ |
| New agent button | shown | shown | shown | **hidden** | ✓ |
| Agent form | editable | editable | editable | **disabled + "read-only access"** | ✓ |
| Playground "Start call" | enabled | enabled | enabled | **enabled — defect** | `403` |
| Create / revoke keys | shown | shown | **"Only an owner or admin…" + docs link** | same | ✓ |
| Rename organization | form | **"Only an owner can rename"** | same | same | ✓ |

Measured: `POST /v1/console/playground/rendezvous` returns **403 for viewer, 201 for developer and
admin**. Authorization is correct everywhere; only its *presentation* is inconsistent.

**Defect:** the Playground is the one restricted action the UI does not gate. A viewer is asked
for microphone permission and only then sees "This action needs the developer role or higher".
The message is accurate and nothing leaks — but the microphone prompt should never have happened.
**FRONTEND ONLY.**

Nothing in the console returns a 403 that the user cannot understand, and no surface is hidden
that the server would have allowed.

---

## 14. Empty / Loading / Error States

| Page | Loading | Empty | Success | Error | Verdict |
|---|---|---|---|---|---|
| Overview | Per card | Attention + per-panel empties | Figures + range note | Per card, retry + request id | **CURRENT** |
| Agents | `Loading` | Role-aware copy | List | Retry | **CURRENT** |
| Agent editor | `Loading` | n/a | "Saved" (`role="status"`) | `FormError` + banner for unsupported language | **CURRENT** |
| Playground | `Loading` | Two distinct empties (no agent / voice off) | State pill, timer, log | Inline `role="alert"` | **CURRENT** |
| Conversations | `Loading` | Distinguishes "none" from "no match" | Table + Load more | Retry; a separate inline error for "Load more" | **SHOULD FIX (minor)** — "Load more" has **no pending state**; a slow page looks like a dead button |
| Conversation detail | `Loading` | "Nothing was said"; "No turn was completed"; "No runtime events were recorded" | Full detail | Retry | **CURRENT** |
| Analytics | Per panel | Per panel + "Choose both days" | Four panels | Per panel, retry | **CURRENT** |
| Developers | `Loading` | "No API keys yet" | Table + one-time key | `FormError` on create and revoke | **CURRENT** |
| Settings | `Loading` (members) | n/a | Rename disabled until changed | Retry / `FormError` | **CURRENT** |

Global: an `ErrorBoundary` per `org:path` catches a render crash and recovers on navigation; a
network failure becomes "Cannot reach the VoxVoice API. Is the server running?"; `Loading` appears
only after 150 ms so fast responses do not flash.

**This is the most complete part of the console.** One real gap (Load more), nothing missing.

---

## 15. Form UX

| Form | Labels | Validation | Required | Error placement | Disabled | Double-submit | Success | Cancel | Unsaved changes |
|---|---|---|---|---|---|---|---|---|---|
| Sign in | Wrapping `<label>` | `type=email` | Yes | Above button | While pending | Guarded | Redirect | n/a | n/a |
| Sign up | Wrapping | `minLength=12` + hint | Yes | Above button | While pending | Guarded | Redirect | n/a | n/a |
| Agent create/edit | `htmlFor` + `aria-describedby` hints | `maxLength`, `required`, language banner | Yes | `FormError` + banner | `fieldset` disabled while saving | Save disabled unless dirty | "Saved" `role=status` | — | **None — edits are lost silently on navigate** |
| Archive | — | — | — | `FormError` | While pending | `window.confirm` | Redirects to list | Confirm | n/a |
| API key create | `htmlFor` + hint | `required`, `maxLength` | Yes | `FormError` | Disabled while pending or empty | Guarded | One-time key panel | "Done" | n/a |
| Revoke | — | — | — | `FormError` | While pending | `window.confirm` naming the key | Row disappears | Confirm | n/a |
| Filters | `htmlFor` | — | — | — | — | — | URL updated with `replace` | Reset by choosing "All" | n/a |
| Custom date range | `htmlFor`, "(UTC)" in the label | Needs both days | — | "Choose both days" empty state | — | — | Panels load | — | n/a |
| Org rename / new org | **Wrapping `<label>Name`** | `required`, `maxLength` | Yes | `FormError` | Until changed | Guarded | Auth refresh | — | n/a |

**Findings:**
1. **No unsaved-changes guard on the agent editor.** `dirty` is computed (it drives the Save
   button) but nothing warns on navigation. A long system prompt can be lost with one sidebar
   click. **SHOULD FIX**, frontend-only.
2. **Two fields labelled "Name" on Settings** (organization rename, new organization), both using
   implicit wrapping labels with no `id`. Accessible names are valid but ambiguous — this is what
   forced the M12.10 harness to scope `fill()` to a card. **CAN DEFER.**
3. Filter changes use `replace`, so the browser Back button does not step through filter states —
   a deliberate, defensible choice.

---

## 16. Security UX

| Check | Result |
|---|---|
| Secrets in the frontend | **None.** No provider key, no long-lived key, no password is ever held by the console |
| API key handling | Shown exactly once from the create response, held in component state, cleared on "Done", never re-fetchable. **Harness-verified** not recoverable after reload |
| Browser storage | **Nothing.** No `localStorage`, no `sessionStorage`, no IndexedDB anywhere in `console/src` |
| Keys in URLs | Never. Only ids and filters are in query strings |
| Clipboard | Explicit user action; failure falls back to a selectable field with an explanation |
| Error messages | Server messages verbatim with a request id; no stack traces; a cross-org id returns 404 exactly like a missing one |
| Logout | Server-side invalidation; the replayed cookie fails (harness-verified) |
| Organization switch | Every view remounts; **verified that org B shows nothing of org A** across six paths |
| Signalling token vs API key | **Never confused.** The docs table labels each operation's credential; the Playground holds only the signalling token; a console cookie does not open the developer API and an API key does not open the console (both harness-verified) |
| Session cookie | HttpOnly, never readable by the console; `/v1/console/me` *is* the auth state |
| CSRF | `x-voxvoice-console: 1` on every mutation |

**One defect, and it is an honesty defect rather than an exposure:**

> `console/src/components/TopBar.tsx` maps `transport === "webrtc"` to the label
> **"WebRTC · real providers"**, shown in the top bar of every page and as the Overview's
> Transport fact. `/v1/console/meta` carries **no provider information at all** — by design, so
> that `api/**` never names a vendor (`E11-3`). The transport says nothing about whether the STT,
> LLM and TTS plugins are the real ones. During this audit the console displayed "real providers"
> while running the deterministic scripted TTS of the E2E harness.

This is the only place in the console that asserts something it cannot know, and it is precisely
the assertion an operator would rely on to believe a call was real. **MUST FIX** — see §21.

---

## 17. Responsive UX

Measured live over the DevTools protocol on this tree, at 1440 / 1024 / 768 / 390 px across
Playground, Conversations, conversation detail, Settings, Developers, Agents and the agent editor
— the pages the M12.10 harness does **not** cover at narrow widths (it covers Overview, Analytics,
agent editor and docs).

| Width | Result |
|---|---|
| 1440 px | Clean on every page |
| 1024 px | Clean on every page |
| 768 px | **Conversation detail overflows by 7 px** (`TABLE.table` right edge 775 px) |
| 761 px | **Overflows by 14 px** — worst just above the 760 px breakpoint |
| 500 px | **Overflows by 35 px** (table 535 px) |
| 390 / 360 px | Clean on every page |

**Root cause:** the Turns table on conversation detail has eight columns (turn, voice-to-voice,
five stages) and is the **only** wide table in the console not wrapped in `.table-scroll` —
Analytics wraps both of its wide tables. At 761–768 px the desktop sidebar is still present and
the content column is at its narrowest; the table sets the page's minimum width and the whole page
scrolls sideways.

This contradicts the frontend architecture doc §5: *"Below 760 px the sidebar collapses into a top
navigation row, and nothing scrolls horizontally."* M12.10's claim was scoped to the four pages it
tested and remains accurate for those; conversation detail was never measured at these widths.

Also observed, not defects: the sidebar collapses to a wrapping top row below 760 px; the top bar
**hides the user's name** below 760 px (`\.topbar__user { display: none }`), so on a phone there
is no indication of *which account* is signed in — the organization is still shown. Minor.

Tap targets: the smallest interactive element is the "Sign out" button at 30 px high — above the
WCAG 2.2 AA 24×24 minimum, below the 44 px comfort convention. Not a defect.

---

## 18. Accessibility

**Tested here** (and what it means):

| Property | Result |
|---|---|
| Semantic landmarks | `nav[aria-label="Console"]`, `main`, `header` present |
| Headings | One `h1` per page; cards are `article` + `aria-labelledby` pointing at their `h2` |
| Accessible names | **Every button and link on every page at every width** — 0 problems across 28 page/width combinations in this audit, plus the harness's own sweep |
| Form association | **Every input, select and textarea has a label** — 0 problems, same sweep. Hints use `aria-describedby`, so they are descriptions, not part of the name |
| Status announcements | `role="status"` on Loading, "Saved", banners; `role="alert"` on every error |
| Active nav | `aria-current="page"` |
| Tables | `scope="col"` / `scope="row"` throughout; `<caption class="visually-hidden">` where a table needs one |
| Charts | `DailyBars` is a table-like list, not a canvas — readable, not an image |
| Dialogs | **None exist.** Destructive actions use `window.confirm`, which is natively accessible |
| Colour independence | Status is text inside the pill, not colour alone |
| **Colour contrast** | **Computed here for the first time.** Every text token pair passes **WCAG AA** in both themes: light — text 16.9, muted 5.6, accent 7.4, ok 5.1, warn 5.6, error 6.2 on `--bg`; status pills on their soft backgrounds — ok 4.79, warn 5.47, error 5.75, accent 6.98. Dark — all ≥ 7.0. Borders are 1.25:1, which is below the 3:1 non-text minimum but they are decorative, never the sole indicator |

**Not tested, and not claimed:** screen-reader output (VoiceOver/NVDA), keyboard-only traversal of
a full journey, focus order, browsers other than Chrome, and contrast **as rendered** (the
measurement above is of the declared tokens, not a scan of every painted element).

**Known structural gaps** (standard SPA issues, neither verified nor fixed here):

| Gap | Class |
|---|---|
| No skip-to-content link | **GOOD TO HAVE** |
| No focus management on route change — focus stays on the clicked nav link, and a screen-reader user is not told the page changed | **GOOD TO HAVE** (the strongest candidate of the three) |
| No `prefers-reduced-motion` beyond the spinner | FUTURE |
| Screen-reader and keyboard audit | **MUST FIX BEFORE A PUBLIC DEPLOYMENT** — not before an internal one |

**Do not claim WCAG AA conformance.** Contrast passing is one criterion of many.

**Documentation defect:** `m12-frontend-architecture.md` §5 states "Contrast meets WCAG AA in both
themes" while `m12-certification.md` records contrast as **NOT EXECUTED**. The claim turns out to
be true, but the two documents contradicted each other for a fortnight.

---

## 19. Feature Gap Matrix

| Feature | Current status | User value | Dependencies | Must have before deployment? | Why | Area | Complexity |
|---|---|---|---|---|---|---|---|
| Authentication | Complete | — | — | — | Sign-in, sign-up, sign-out, server-side sessions all verified | — | — |
| Password change | **Absent** | High | New console endpoint | **Yes, before a public deployment** | A password product with no way to change a password | FRONTEND + BACKEND | M |
| Password reset | **Absent** | High | Endpoint + email delivery | **Yes, before a public deployment** | A forgotten password is unrecoverable without DB access | FRONTEND + BACKEND | L |
| Organization management | Rename + create | Medium | — | No | Adequate for the audited scope | — | — |
| Team invitations | Absent | High | Endpoint + tokens + email | **No** | Explicitly out of scope: vision §5, LLD §3 | FRONTEND + BACKEND | L |
| Member role change / removal | Absent | Medium | Endpoint | No | Deferred with invitations | FRONTEND + BACKEND | M |
| Agent management | Complete | — | — | — | Create, edit, archive, validate, persist | — | — |
| Agent duplication | Absent | Medium | — | No | Convenience over an existing `POST` | FRONTEND ONLY | S |
| Archived-agent view | Absent | Medium | Existing filter | No | Archived agents are invisible and unrestorable | FRONTEND + API | S |
| Agent versioning | Absent | Medium | New table | No | No history exists to show | FRONTEND + DATA MODEL | L |
| Agent publishing / drafts | Absent | Low | — | **No — do not add** | There is no deploy step; the state would be fictional | — | — |
| Prompt variables | Absent | Medium | Runtime substitution | No | Runtime does not substitute | FRONTEND + RUNTIME | L |
| Voice configuration (beyond id) | Voice id only | Medium | Runtime plumbing | No | Only the id reaches the runtime | FRONTEND + RUNTIME | M |
| Model parameters | Model id only | Medium | Runtime plumbing | No | Same | FRONTEND + RUNTIME | M |
| Knowledge / RAG | Absent | High | A retrieval subsystem | No | A new epic, not a frontend gap | FUTURE | XL |
| Tools / actions | Stored, ignored | High | Runtime execution | **No — do not show** | Showing an inert control breaks AR-25 | FRONTEND + RUNTIME | XL |
| Playground | Complete for its scope | — | — | — | Real call, states, timer, mute, cleanup | — | — |
| Mic level meter | Absent | High | — | No | The one unanswered question a caller has | FRONTEND ONLY | S |
| Live transcript / speaking state | Absent | High | Event push (SSE/WS) | No | No live event surface exists | FRONTEND + BACKEND | L |
| Role gating in the Playground | **Defect** | Medium | — | **Yes** | Inconsistent with every other restricted surface; prompts for a microphone before failing | FRONTEND ONLY | S |
| Conversation list | Complete | — | — | — | Filters + cursor pagination | — | — |
| Conversation search | Absent | High | Search endpoint + index | No | Value grows with volume; none exists locally | FRONTEND + BACKEND | L |
| Date filter on conversations | Absent | Medium | Query params | No | Analytics has ranges; the list does not | FRONTEND + API | S |
| Transcript copy / export | Absent | High | — | No | Data already on the page | FRONTEND ONLY | S |
| Conversation replay (audio) | Absent | High | Audio storage | **No — decision first** | Storing call audio is a privacy decision, not a feature | FUTURE | XL |
| Conversation detail overflow | **Defect** | Medium | — | **Yes** | Page scrolls sideways at 761–768 px and ~500 px | FRONTEND ONLY | S |
| Auto-refresh of an active call | Absent | Low | — | No | A reload shows it | FRONTEND ONLY | S |
| Analytics | Complete for measured data | — | — | — | Volume, duration, turns, latency, per-agent, outcomes | — | — |
| Analytics export | Absent | Medium | — | No | — | FRONTEND ONLY | S |
| Cost / billing | Absent | High (commercially) | Metering + pricing | **No — do not add** | Excluded by vision §5; no usage is metered | FUTURE | XL |
| Token usage | Absent | Medium | LLM plugin emission | No | Not emitted | FRONTEND + RUNTIME | L |
| Error rate / end reasons | Absent | High | Runtime taxonomy | No | The runtime records state, not cause | FRONTEND + RUNTIME | L |
| API keys | Complete | — | — | — | One-time reveal, immediate revocation | — | — |
| Key audit trail | Log-only | Medium | New table | No | Already recorded as non-blocking debt | FRONTEND + DATA MODEL | M |
| API documentation | Complete | — | — | — | Guide + live reference from `/openapi.json` | — | — |
| Copy button on samples | Absent | Low | — | No | — | FRONTEND ONLY | S |
| SDK | Built, unpublished | High | Publication decision | No | An open decision, not a gap | — | — |
| Webhooks | Absent | High | Delivery subsystem | No | Nothing to deliver from | FUTURE | XL |
| Rate limiting | Absent | High | Middleware | **No, but before exposure** | Already recorded as non-blocking debt; no limit is claimed | BACKEND | M |
| Audit logs | Absent | Medium | New table | No | — | FRONTEND + DATA MODEL | L |
| Profile / settings | Read-only | Medium | Endpoint | No | Cannot edit one's own name | FRONTEND + BACKEND | S |
| Notifications | Absent | Low | — | **No — do not add** | Nothing generates events worth notifying about | — | — |
| Onboarding | Partial | Medium | — | No | The attention path works; the get-started card is inert | FRONTEND ONLY | S |
| Runtime honesty label | **Defect** | High | — | **Yes** | Claims "real providers" on transport evidence alone | FRONTEND ONLY | S |

---

## 20. Dependency Classification

| Class | Items |
|---|---|
| **FRONTEND ONLY** | Runtime label honesty · conversation-detail overflow · Playground role gating · unsaved-changes guard · transcript copy/export · mic level meter · agent duplication · analytics CSV · copy buttons · get-started links · "Load more" pending state · skip link · focus-on-route-change · auto-refresh |
| **FRONTEND + API** | Date filter on conversations · archived-agent view · period comparison |
| **FRONTEND + BACKEND** | Password change · password reset · team invitations · member management · profile edit · conversation search · live event push |
| **FRONTEND + RUNTIME** | Speaking indicators · end reasons · error taxonomy · token usage · prompt variables · voice/model parameters · tools · barge-in (F-1) |
| **FRONTEND + DATA MODEL** | Agent versioning · annotations/tags · key audit trail · audit logs |
| **FUTURE / NOT CURRENTLY SUPPORTED** | Knowledge/RAG · audio replay · webhooks · cost/billing · telephony · notifications |

**Everything in §21 is FRONTEND ONLY.** Acting on this audit's blockers starts no backend or
runtime work — which was the point of auditing the frontend separately.

---

## 21. MUST FIX BEFORE DEPLOYMENT

Four items. Each is a defect against something the repository already claims, not a preference.

| # | Defect | Why it qualifies | Evidence | Area |
|---|---|---|---|---|
| **1** | The top bar and Overview label `transport === "webrtc"` as **"WebRTC · real providers"** | **Misleading UI.** `/v1/console/meta` carries no provider information; the console cannot know this. Observed displaying "real providers" while running scripted TTS. Contradicts product principle 2 | `components/TopBar.tsx:8`, `routers/meta.py` (`RuntimeDescription` = transport, signalling, ICE only) | FRONTEND ONLY |
| **2** | Conversation detail scrolls horizontally at 761–768 px and ~500 px | Contradicts the frontend architecture doc §5; the Turns table is the only wide table not wrapped in `.table-scroll` | Measured: 775 px vs 768 px, 535 px vs 500 px | FRONTEND ONLY |
| **3** | The Playground offers a `viewer` an enabled "Start call" and prompts for their microphone before failing | Inconsistent with every other restricted surface; requests a device permission for an action that cannot succeed | Live: `403` from `POST /playground/rendezvous`; UI showed "Microphone ready" then the refusal | FRONTEND ONLY |
| **4** | `m12-frontend-architecture.md` claims WCAG AA contrast; `m12-certification.md` records contrast as NOT EXECUTED | Two active documents contradict each other on an accessibility claim, which M12.11 forbade making beyond what was tested | Both documents; §18 now supplies the measurement | DOCUMENTATION |

**Before a *public* deployment**, and outside this audit's frontend scope, the certification
record's own gates still apply: a real-provider spoken call, a Postgres run, password
change/reset, rate limiting, and a screen-reader/keyboard audit.

**None of the four is an Epic 12 certification blocker.** None contradicts a control in
`E12-1…E12-10`; #1 and #4 are honesty defects in presentation and documentation, #2 and #3 are
bounded UX defects on paths the existing harness did not measure. The M12.11 verdict stands.

---

## 22. CAN DEFER

| Item | Why deferring is safe |
|---|---|
| "Load more" pending state | The button works; only slow feedback is missing |
| Unsaved-changes guard on the agent editor | Loss is recoverable by retyping; Save is clearly enabled when dirty |
| Get-started card is not clickable | The attention path already routes the user |
| Two "Name" labels on Settings | Valid accessible names; ambiguity affects tooling, not users |
| Filters lost by the breadcrumb | One click to reapply |
| Hard-coded `localhost:8000` in docs examples | Correct for the documented local product |
| No copy button on code samples | Text is selectable |
| Top bar hides the user's name below 760 px | The organization is still shown |
| No skip link / no focus-on-route-change | Real, but part of the deferred accessibility audit |
| Transcript export, conversation search, date filters, duplication, archived-agent view, CSV export, mic meter | Genuine product value, none blocking a first deployment |
| Border contrast 1.25:1 | Decorative; never the sole indicator |
| Long conversations render every turn | Already recorded as non-blocking debt |

---

## 23. Future Product Ideas

Derived from this repository — its product vision, roadmap, ADRs and runtime — **not** from
generic SaaS conventions. None is proposed for now.

1. **Live call inspection** — a push surface (SSE over the durable log) turning the Playground
   into a live view: partial transcript, speaking state, per-turn latency as it happens. The
   platform already measures everything needed; only delivery is missing. This is the most
   product-differentiating idea available, because per-boundary latency is the runtime's edge.
2. **Latency regression tracking** — the platform measures five stages per turn; comparing an
   agent's p95 across releases is a projection away.
3. **An end-reason taxonomy in the runtime**, which would unlock error rate, abandonment and
   "why did it end" — three separate gaps with one root cause.
4. **Conversation search and export** once volume justifies it.
5. **Agent versioning with a diff**, valuable exactly because a system prompt is the product.
6. **Team invitations and member management**, the acknowledged next step for organizations.
7. **Barge-in (F-1)**, an owner decision that is a product feature, not a bug fix.

**EXTERNAL RESEARCH REQUIRED** — deliberately not done here: how this surface compares to hosted
voice-agent platforms in the same category. The vision states VoxVoice "is not a clone of any of
them", so a competitive comparison needs an explicit instruction and its own phase. Nothing in
this document was derived from a competitor.

---

## 24. Questions Requiring Product Decision

1. **The runtime label.** Should the console say "WebRTC transport" (what it knows), or should
   `/v1/console/meta` gain a **vendor-free** provider-realness flag (e.g. `speech: "real" |
   "deterministic"`)? The second is more useful and must not name a vendor (`E11-3`).
2. **Is a `viewer` meant to use the Playground at all?** The server says no. Should the UI hide
   it, disable it with a reason, or keep today's behaviour?
3. **Password change and reset** — required before any deployment where users are not the person
   who runs `bootstrap_owner`. Who owns that milestone?
4. **Team invitations** — vision §5 defers them. Does "toward deployment" change that?
5. **Transcript export** — cheap and useful; is it in scope before deployment?
6. **Audio replay** — a privacy and storage decision before it is a feature.
7. **Accessibility target** — is WCAG 2.2 AA an actual commitment? If so it needs a real audit; if
   not, the architecture doc's contrast sentence should be narrowed to what was measured.
8. The five decisions already open from M12.11 (F-1 barge-in, key audit trail, rate limiting, the
   `PATCH`-null 500, SDK publication) are unchanged by this audit.

---

## 25. Final Recommendation

**The frontend is complete enough to move toward deployment.** Every workflow in the product
vision is implemented, reachable and verified in a real browser; the states are thorough; the
security posture in the browser is clean; and the discipline of showing only measured data is
intact and should be protected from every "richer dashboard" instinct.

Recommended order, smallest and most honest first:

1. Decide question 1, then correct the runtime label (§21.1) and reconcile the two documents
   (§21.4). These are about truthfulness and cost almost nothing.
2. Wrap the Turns table (§21.2) and gate the Playground by role (§21.3).
3. Re-run `e2e/browser.mjs`, extending its responsive sweep to conversation detail and the
   Playground so the gap that hid §21.2 cannot reopen.
4. Only then consider the deferred list, starting with transcript export and a microphone level
   meter — the two cheapest items with real user value.
5. Keep the backend and runtime untouched by this work. Everything in §21 is frontend-only; the
   remaining gates (real-provider call, Postgres, password lifecycle, accessibility audit) belong
   to their own milestones and to the owner decisions in §24.

No deployment, tag, merge or publication is authorized by this document.

---

## 26. M12.12A — Corrective Pass

| | |
|---|---|
| **Date** | 2026-09-25 |
| **Scope** | The four §21 defects. Nothing else. |
| **Result** | All four closed, each with a regression test proven to fail without its fix |

No product feature was added, no surface redesigned, no backend authorization or runtime
behaviour changed, no dependency added, no new network request introduced, and no file frozen by
`E10-7` touched. The deferred list in §22 and the future ideas in §23 were left alone.

### 26.1 Defect 1 — the "real providers" claim

**Before:** `transportLabel()` mapped `transport === "webrtc"` to `"WebRTC · real providers"`,
shown in the top bar of every page and as the Overview's Transport fact.

**After:** `"WebRTC transport"` and `"Loopback transport · no audio path"` — what the metadata
proves. The Overview's Runtime card adds: *"The transport is all this server reports; it says
nothing about which speech providers are configured."* No heuristic, no detection, no change to
`/v1/console/meta`.

**Pinned by three independent tests.** A component test asserts the rendered page shows the
transport, shows the disclaimer, and contains no "real providers" text anywhere. A source guard
(`test_m12_12a_regressions.py`) fails if any console file — comments excluded — ships the phrase,
and a second test asserts `RuntimeDescription` still carries only transport, signalling and ICE
policy, which is the premise the fix rests on. A browser check asserts it on the live page in
**both themes**.

### 26.2 Defect 2 — conversation-detail horizontal overflow

**Before:** the eight-column Turns table set a 775 px minimum width; the page scrolled sideways at
761–768 px (7–14 px) and at ~500 px (35 px).

**After:** the table is wrapped in the existing `.table-scroll` box, exactly as the two Analytics
tables already were. No data, API response or transcript semantics changed.

**Pinned** by a component test asserting the containment (width is a layout property jsdom does
not compute) and, in the browser, by extending the responsive sweep from three widths and four
pages to **1024 / 768 / 500 / 390 px across seven pages**, now including conversation detail, the
conversations list and the Playground. Measured after the fix: **no page-level overflow at any of
1440 / 1024 / 768 / 500 / 390 px**.

### 26.3 Defect 3 — viewer Playground authorization UX

**Before:** a `viewer` saw an enabled "Start call", the browser asked for their microphone, and
the server then refused the rendezvous with `403`.

**After:** `canStartCalls()` — the same shape as the existing `canEditAgents` and `canManageKeys`
— gates the page before anything is attempted. A viewer sees *"Only a developer, admin or owner
can start calls"* with a pointer to Conversations, and no agent picker, no call controls. An
unknown role (auth still loading) is not a refused one: nothing is offered and nothing is refused
until the organization is known.

**Server-side authorization is untouched.** The frontend gate is UX only; `POST
/v1/console/playground/rendezvous` still answers `403` to a viewer, and that was re-verified after
the change.

**Pinned** by a component test that asserts, for a viewer: no "Start call" button, no agent
control, `getUserMedia` never called, and no request to `/playground/rendezvous`,
`/playground/sessions` or `/webrtc/` — plus a companion test that a `developer` is still offered
the call.

### 26.4 Defect 4 — the WCAG AA documentation claim

**Before:** `m12-frontend-architecture.md` §5 stated "Contrast meets WCAG AA in both themes" while
`m12-certification.md` recorded colour contrast as **NOT EXECUTED**.

**After:** §5 states what was measured — contrast checked against WCAG AA for the text colour
tokens in both themes, every pair passing, lowest 4.79:1, borders decorative — and that this is a
check of declared tokens rather than of every painted element. It then says plainly that
**no WCAG conformance is claimed**, and names what stays untested: screen-reader output, a
keyboard-only journey, and browsers other than Chrome. The sentence about horizontal scrolling was
also corrected to describe the `.table-scroll` behaviour rather than claim that nothing scrolls.

**Pinned** by three documentation tests: no conformance verb may appear near "WCAG" in the
architecture doc; the doc must state what contrast testing covered and what it did not; and the
architecture doc and the certification record must not contradict each other on accessibility
again.

### 26.5 Accessibility scope after this pass

| | |
|---|---|
| **Tested** | Accessible names · form labels · page-level horizontal overflow (5 widths, 7 pages) · colour contrast of the text tokens, both themes |
| **Not tested** | Screen-reader output · a keyboard-only full journey · browsers other than Chrome · contrast of every painted element |

No accessibility conformance is claimed, and this pass did not begin an accessibility
certification.

### 26.6 What this pass did **not** change

Authentication, organizations and switching, agents, conversations data, analytics, the Overview
composition, API keys, the developer API and its contract, the SDK, tenant isolation, and the
WebRTC path for every role the server permits — all unchanged, and all re-verified by the full
suite and the browser and SDK journeys after the change.

The §22 deferred items and §23 future ideas remain open and untouched, as do the five decisions
carried from M12.11 and the questions in §24. **M12.11's certification stands**: none of the four
defects contradicted a control in `E12-1…E12-10`.
