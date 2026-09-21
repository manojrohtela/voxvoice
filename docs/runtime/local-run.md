# Running VoxVoice locally — the real voice product

> ## LOCAL DEVELOPMENT ONLY
> This is **not** production or Stage-2 deployment documentation. It describes running the real
> provider stack on a developer Mac so you can actually talk to VoxVoice. Local latency, local NAT
> behaviour and local reliability say **nothing** about Stage-2.

**Verified 2026-09-20 (M11.16.9)** — every command below was executed against this repository.
What could not be executed here is the conversation itself: it needs real provider keys and a
person at a microphone.

---

## 1. What you need

| | |
|---|---|
| Python | 3.12+ (verified on **3.13.3**) |
| `uv` | for dependency sync |
| Browser | **Chrome** or **Safari** |
| Audio | a microphone and headphones (**use headphones** — speakers cause the assistant to hear itself) |
| Credentials | Deepgram, Groq, Cartesia + a Cartesia voice id |

**No public IP, DNS, HTTPS certificate or TURN server is needed locally.** `http://localhost` is a
browser **secure context**, so the microphone works, and both peers are on one machine, so host
ICE candidates connect directly. That is why local works while Stage-2 is still blocked.

## 2. Install

```bash
cd platform
uv sync --locked --extra dev
uv lock --check            # expect: 53 packages
```

Verify the media and provider libraries import:

```bash
uv run python -c "import aiortc, av, deepgram, groq, cartesia, pysilero_vad; print('ok')"
```

## 3. Credentials

Export these in the shell you start the server from. **Never commit them, never paste them into a
file in this repository.** They are resolved only through the ADR-0049 `CredentialProvider`;
provider plugins never read the environment themselves.

```bash
export VOXVOICE_SECRET_DEEPGRAM_API_KEY=...
export VOXVOICE_SECRET_GROQ_API_KEY=...
export VOXVOICE_SECRET_CARTESIA_API_KEY=...
export VOXVOICE_TTS_VOICE_ID=...          # configuration, not a credential
```

**There is no fallback.** A missing credential is a **startup failure**, not a quiet downgrade:

```
ValueError: the deepgram stt profile requires the 'deepgram_api_key' credential,
which is not configured; refusing to fall back to the local transcriber
```

If the server starts, all four were present. That is the check — there is no separate one.

## 4. Start the server

```bash
export RUNTIME_TRANSPORT=webrtc
export DATABASE_URL=sqlite:///./voxvoice-local.db

uv run uvicorn "api.webrtc.app:build_webrtc_app" --factory --host 127.0.0.1 --port 8000
```

Note the entrypoint: **`api.webrtc.app`**, not `api.main:app`. The default application has no
signalling surface and uses the loopback transport.

Leave `VOXVOICE_ICE_STUN_URLS` **unset** locally — host candidates are all you need on one
machine, and the ICE policy is explicit by default (ADR-0054).

## 5. Verify it came up

```bash
curl -sS http://127.0.0.1:8000/health
```

```json
{"ok":true,"db":true,"transport":"webrtc","webrtc_signalling":true,
 "ice_policy":"no ICE servers (explicit; host candidates only)"}
```

`transport: "webrtc"` confirms the real stack. **Green health does not mean a call will work** —
it says nothing about the providers accepting your keys or about media flowing.

## 6. Create an org and an agent (once)

```bash
# In a second terminal, with the SAME DATABASE_URL:
export DATABASE_URL=sqlite:///./voxvoice-local.db
uv run python -m api.bootstrap "Local Dev"
# prints org_id and api_key — the api_key is shown ONCE. Keep it.

curl -sS -X POST http://127.0.0.1:8000/v1/agents \
  -H "x-api-key: <api_key>" -H 'content-type: application/json' \
  -d '{"name":"Walkthrough",
       "system_prompt":"You are VoxVoice. Answer in one or two short sentences.",
       "first_message":"Hi, VoxVoice here. What would you like to know?"}'
# returns the agent id
```

`first_message` is optional. If set, the agent speaks it through the Playback Engine as soon as
the media path is ready — which is also the quickest confirmation that TTS and egress work.

### Two agent fields that will stop the call dead if wrong

Both were found the hard way during the first live run (M11.16.9).

**`language` must be one the STT provider declares.** The schema default is **`en-IN`**, but
`DeepgramSTT` declares only `("en",)` and refuses an undeclared language rather than guessing at
the vendor. An `en-IN` agent fails at stream open, which looks like *"the welcome message plays
then it disconnects"*. Use `"language": "en"` until the declared set is widened.

**`llm_model` must be one your Groq account actually offers.** The configured default is
`llama-3.3-70b-versatile`, which is **not available on all accounts** — when it is missing the turn
fails with `ProviderProtocolError` and the assistant simply never answers. List what you have:

```bash
curl -sS https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $VOXVOICE_SECRET_GROQ_API_KEY" | jq -r '.data[].id' | sort
```

Then set it per agent — no code change needed:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/agents \
  -H "x-api-key: <api_key>" -H 'content-type: application/json' \
  -d '{"name":"Walkthrough","language":"en","llm_model":"openai/gpt-oss-20b",
       "system_prompt":"You are VoxVoice. Answer in one or two short sentences.",
       "first_message":"Hi, VoxVoice here. What would you like to know?"}'
```

## 7. Talk to it

1. Open **`http://localhost:8000/v1/webrtc/client`** in Chrome or Safari.
   Use `localhost` or `127.0.0.1` — a LAN address like `192.168.x.x` is **not** a secure context
   and the browser will refuse the microphone.
2. Paste the **org API key** and the **agent id**.
3. Click **Connect** and **allow the microphone**.
4. Wait for the status line to read **`connected`**.
5. Speak, then **stop and stay quiet** — endpointing fires on trailing silence, so trailing
   "umm" keeps the turn open.

**First smoke utterance:**

> *"Hello VoxVoice. Tell me briefly what you can do."*

6. Click **Hang up** when finished. That ends the session and releases the rendezvous.

## 7a. Talk to it from the console (M12.4)

The Developer Platform console does the same thing without handing an API key to the browser.
Start the **console** app instead of the WebRTC app, in the shell with the credentials from §3:

```bash
cd console && npm ci && npm run build && cd ../platform
export RUNTIME_TRANSPORT=webrtc DATABASE_URL=sqlite:///./voxvoice-local.db
export VOXVOICE_CONSOLE_DIST="$(cd ../console/dist && pwd)"
uv run uvicorn "api.console.app:build_console_app" --factory --host 127.0.0.1 --port 8000
```

1. Open **`http://localhost:8000/console/`**. On a fresh database, create the first account.
2. **Agents → New agent.** The language list offers only what the recognizer supports (`en`).
   Set **Model** to one your Groq account offers (see §6).
3. **Talk to this agent** (or **Playground**), then **Start call**, and allow the microphone.
4. Wait for **Connected** and the timer. Speak, then pause. **Hang up** when done.

The browser receives only a single-purpose signalling token. The session is recorded with
`source: console_playground`. With headphones on, the checks in §8 apply unchanged.

## 8. What to watch for

| | |
|---|---|
| Connection | status goes `connected` within a couple of seconds |
| First audio | if `first_message` is set, you hear it almost immediately |
| Progressive speech | the reply should **start before it is fully generated** — clause pipelining |
| Endpointing | a clear pause ends your turn; it should not hang open |
| Barge-in | speaking over the assistant should cut it off, and stale queued audio must **not** play afterwards |
| Ordering | clauses arrive in order, no overlap |

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| Server exits at startup with `ValueError: … requires the '…' credential` | a credential is unset — this is correct behaviour, not a bug |
| Browser will not offer the microphone | not a secure context — use `localhost`, not a LAN IP |
| `204` forever on the offer poll | the session create never happened, or the agent id / API key is wrong |
| Connects, then silence | check the browser console for the peer state; locally this is usually a device/permission issue rather than ICE |
| Every turn fails with `ProviderUnavailable` | a provider rejected the key, or `tts=local` was composed against a real media path (`OPAQUE` vs PCM16 — see `docs/providers/webrtc-external-client.md` §10) |
| The assistant answers itself | you are on speakers, not headphones |
| Welcome plays, then disconnects | agent `language` is not declared by the STT provider (see above) |
| Welcome plays, you speak, nothing comes back | agent `llm_model` is unavailable on your account (see above) |
| `degraded: tts/not_streaming` in the log | **benign and expected** — Cartesia declares no streaming *text input*, so clauses are synthesized whole |

## 10. Local-only limitations

* Local media never crosses a NAT, so this proves **nothing** about Stage-2 traversal.
* Local latency is not Stage-2 latency and is **not** a performance measurement.
* SQLite is fine locally; Stage-2 wants a durable database.
* The browser holds the **org API key**, which is acceptable for a first-party operator page and
  **not** acceptable for end users (see `webrtc-external-client.md` §4).
* One provider per capability is configured, so nothing here exercises failover.
