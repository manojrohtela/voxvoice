# Epic 12 — Console Frontend Architecture

| | |
|---|---|
| **Status** | Living (M12.1). The shell, API client, status surface and test harness are **implemented**. Feature areas land per milestone. |
| **Date** | 2026-09-21 |
| **Related** | [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP8 · [Product vision](m12-product-vision.md) · [Local dev](m12-local-development.md) · DL-0236 (dependencies) |

## 1. Stack (owner ruling, M12.1 gate)

| Concern | Choice | Why |
|---|---|---|
| Language | TypeScript, `strict` | The console, SDK and API types share one typed language (Part B: TS SDK core) |
| UI | React | Part B names React for the SDK binding. One framework across console and SDK. |
| Build/dev | Vite | Fast dev server with a same-origin proxy. Static output the API can serve. |
| Routing | React Router | Standard, no data-layer lock-in |
| Server state | a small `useResource` hook over the typed client | Enough for M12.1–M12.3. A query library is added only if caching/invalidation needs outgrow it (it would get a DL entry). |
| Styling | plain CSS with design tokens (CSS custom properties), light and dark | No CSS framework or component library to learn. The UI is small enough. |
| Tests | Vitest + Testing Library + jsdom | Same toolchain as Vite, and tests exercise behaviour, not implementation. |

Not used: a component library, a global state store, CSS-in-JS, or a second framework. Each can be
added later if a milestone justifies it.

## 2. Layout

```
console/
├── index.html
├── package.json · package-lock.json (committed) · tsconfig.json · vite.config.ts
└── src/
    ├── main.tsx                  mount + router
    ├── app/
    │   ├── App.tsx               routes inside the AppShell
    │   └── navigation.ts         the single IA definition: path, label, capability, milestone
    ├── api/
    │   ├── client.ts             typed fetch wrapper → ApiError (parses both error shapes)
    │   ├── types.ts              wire types (ConsoleMeta, Health, …)
    │   └── useResource.ts        loading / error / data / reload
    ├── components/               AppShell · Sidebar · TopBar · StatusPill · EmptyState ·
    │                             ErrorState · Loading · ErrorBoundary
    ├── features/
    │   ├── overview/             Overview (real runtime status + getting started)
    │   └── unavailable/          the honest "not yet available" surface
    ├── styles/                   tokens.css · app.css
    └── test/setup.ts
```

Feature areas (`features/agents`, `features/playground`, …) are added by their milestones. Shared
UI moves into `components/` only once a second feature needs it.

## 3. Data flow

```
component → useResource(fetcher) → api/client.ts → fetch('/v1/console/…', same origin)
                                          │
                        ApiError{status, type, message, requestId}
```

* The client parses **both** error shapes: the console envelope `{error:{type,message,request_id}}`
  and the developer API's `{detail}`. A network failure becomes `type: "network"`.
* Requests send `credentials: "same-origin"`. From M12.2, mutating requests add
  `x-voxvoice-console: 1`.
* Capabilities come from `GET /v1/console/meta`, loaded once by the shell. Navigation items whose
  capability is absent render the *not yet available* page. That page names the milestone and
  never shows sample data. Until M12.7 the router checked only whether this build implemented a
  surface. Since M12.7 it also honours the server's declaration, so an older server that does not
  declare a surface gets that page, not requests it cannot answer. While meta is loading, or if it
  failed, the page renders as before.
* Each view is keyed by **organization and path** (M12.8). Switching organization remounts the
  view, so every resource reloads for the new organization and nothing of the previous one stays on
  screen. The switcher also drops URL filters (for example an agent id) that belong to the previous
  organization.

## 4. UX states (every data view)

| State | Component | Rule |
|---|---|---|
| loading | `Loading` | shown only after 150 ms, so fast responses do not flash |
| error | `ErrorState` | says what failed, shows the request id, offers retry |
| empty | `EmptyState` | explains the next action (e.g. "Create your first agent") |
| unexpected render error | `ErrorBoundary` | contains the failure to one route. The shell stays usable. |

## 5. Accessibility & responsiveness

Semantic landmarks (`nav`, `main`, `header`). `aria-current="page"` on the active nav item.
Visible focus rings. Status is conveyed with text as well as colour. Desktop-first. Below 760 px
the sidebar collapses into a top navigation row, and a table wider than its column scrolls inside
its own `.table-scroll` box rather than scrolling the page.

**What has been tested, and what that means.** Accessible names, form labels and page-level
horizontal overflow are checked on every page at 1440 / 1024 / 768 / 500 / 390 px by
`e2e/browser.mjs`. **Colour contrast** was measured in M12.12 against WCAG AA for the text colour
tokens in `tokens.css`, in both themes: every foreground/background pair used for text passes AA
(the lowest is the `ok` status pill at 4.79:1); borders are decorative and never the sole
indicator. That is a check of the declared tokens, not of every painted element.

**No WCAG conformance is claimed.** Screen-reader output, a keyboard-only journey and browsers
other than Chrome remain untested, as [the certification record](../../testing/m12-certification.md)
states. Contrast passing is one criterion among many.

## 6. Serving

* **Dev:** `npm run dev` serves `http://localhost:5173/console/` and proxies `/v1` and `/health`
  to `127.0.0.1:8000`, so everything is same-origin.
* **Local product:** `npm run build` writes `console/dist/`. Starting the API with
  `VOXVOICE_CONSOLE_DIST=<abs path to console/dist>` serves it at `http://localhost:8000/console/`.
* Vite `base` is `/console/`, so asset URLs are identical in both modes.
