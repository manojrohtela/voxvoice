# Epic 12 — Local Development Architecture

| | |
|---|---|
| **Status** | Living (M12.1). The commands below were executed against this repository on 2026-09-21. |
| **Date** | 2026-09-21 |
| **Related** | [Frontend architecture](m12-frontend-architecture.md) · [Runtime local-run guide](../../runtime/local-run.md) (real providers, M11) · [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP3 |

> **Local development only.** Nothing here covers Stage-2 deployment. No cloud dependency is
> needed.

## 1. Topology

```
browser ── http://localhost:5173/console/ ──► Vite dev server (console/)
                                                │ proxy /v1, /health
                                                ▼
                               http://127.0.0.1:8000  api.console.app:build_console_app
                                 = build_webrtc_app (M11) + /v1/console/*
                                                │
                            SQLite file (DATABASE_URL) · runtime in-process
```

In local product mode the Vite server is not used. The API serves the built console at
`http://localhost:8000/console/`.

## 2. Two runtime profiles

| Profile | When | Needs |
|---|---|---|
| **Loopback** (`RUNTIME_TRANSPORT=local`, the default) | UI work, no voice | nothing. No provider keys. |
| **Real voice** (`RUNTIME_TRANSPORT=webrtc`) | Playground with real providers | the three provider credentials + `VOXVOICE_TTS_VOICE_ID`, exported exactly as in [local-run §3](../../runtime/local-run.md#3-credentials) |

The real profile refuses to start with a missing credential. That is M11 behaviour and is
unchanged.

## 3. Start the API

```bash
cd platform
uv sync --locked --extra dev
export DATABASE_URL=sqlite:///./voxvoice-local.db
uv run uvicorn "api.console.app:build_console_app" --factory --host 127.0.0.1 --port 8000
curl -sS http://127.0.0.1:8000/v1/console/meta
```

The response names the API version, the runtime transport, and the capabilities that exist now.

## 4. Start the console (dev)

```bash
cd console
npm ci
npm run dev          # http://localhost:5173/console/
```

The dev server proxies to `http://127.0.0.1:8000`. Point it elsewhere with
`VOXVOICE_API_ORIGIN=http://127.0.0.1:9000 npm run dev`.

## 5. Local product mode (one process)

```bash
cd console && npm ci && npm run build
cd ../platform
export VOXVOICE_CONSOLE_DIST="$(cd ../console/dist && pwd)"
uv run uvicorn "api.console.app:build_console_app" --factory --host 127.0.0.1 --port 8000
# open http://localhost:8000/console/
```

`VOXVOICE_CONSOLE_DIST` must be an absolute path to a directory containing `index.html`, or the
API refuses to start. Unset means the API serves no console (the default).

## 6. Checks (the same commands CI runs)

```bash
# backend
cd platform && uv run pytest -q && uv run ruff check core api runtime providers events observability billing tests
# console
cd console && npm run typecheck && npm test && npm run build
```

## 7. Notes

* Use `localhost`, not a LAN IP. The Playground (M12.4) needs a secure context for the microphone.
* Keep one `DATABASE_URL` across terminals, or the CLI and the server see different databases.
* The M11 entrypoints (`api.main:app`, `api.webrtc.app:build_webrtc_app`) still work exactly as
  before. The console app is an additional entrypoint.
