# E2E harness (M12.10)

Drives the **whole product** — console UI, console API, developer API and the SDK — against one
running application, with a **real WebRTC media path** and **deterministic speech providers**.

Nothing here is a product profile, and nothing here is real-provider evidence: the speech
providers are the deterministic test ones, so no provider credential is needed or read. A real
spoken call is still the manual step in [`../docs/runtime/local-run.md`](../docs/runtime/local-run.md) §7a.

| File | What it is |
|---|---|
| `serve.py` | the console application + the M12.4 test media stack (real aiortc transport, scripted TTS) + Epic 10's durable log. It reports `transport=webrtc`, because the media path really is WebRTC. |
| `browser.mjs` | a real Chrome, driven over the DevTools protocol, through the console UI: sign in → agent → **a real WebRTC call** → conversation → analytics → overview → API keys → docs → organization switching → responsive widths → sign out. Writes screenshots and `report.json`. |
| `sdk.mjs` | the **built** `@voxvoice/sdk` against the same server, plus a browser WebRTC exchange driven by the SDK's own `SignallingClient` (signalling token only). |
| `cdp.mjs` | the shared Chrome/DevTools plumbing (Node's built-in WebSocket; no dependency). |

Requires Chrome (macOS path by default; override with `--chrome`). These are run locally, not in
CI. The deterministic cross-product tests that **do** run in CI are
`platform/tests/test_m12_e2e.py`.

## Run

```bash
# 1. build what the harness serves
(cd console && npm ci && npm run build)
(cd sdk && npm ci && npm run build)

# 2. start the harness on a scratch database
SCRATCH=$(mktemp -d)
PYTHONPATH=platform DATABASE_URL="sqlite:///$SCRATCH/e2e.db" \
  platform/.venv/bin/python e2e/serve.py --port 8790 --dist "$PWD/console/dist"

# 3. in another shell
node e2e/browser.mjs --base http://127.0.0.1:8790 --out "$SCRATCH/out"
node e2e/sdk.mjs     --base http://127.0.0.1:8790 --out "$SCRATCH/out-sdk"
```

Each driver exits non-zero if any check fails, and prints one line per check.

**Output is never committed.** Screenshots go to `--out` (a scratch directory): the API-key
screenshot shows the throwaway key that run created, and every key it creates is revoked or
discarded with the database.

## What it proves, and what it does not

* **Proves:** the surfaces work together — one signed-in session across every page, a browser
  `RTCPeerConnection` reaching the runtime through the frozen signalling endpoints, the same
  numbers in Conversations, Analytics and the Overview, the API-key lifecycle including immediate
  revocation, tenant isolation on organization switching, and that the three credentials never
  cross.
* **Does not prove:** anything about real speech providers, real audio quality, latency of a real
  call, barge-in (F-1 is unresolved), or behaviour on Postgres.
