// SDK end to end (M12.10): the BUILT @voxvoice/sdk against the running product (`e2e/serve.py`),
// including a real browser WebRTC exchange driven by the SDK's own SignallingClient.
//
//   (cd sdk && npm run build) && node e2e/sdk.mjs --base http://127.0.0.1:8790 --out <dir>
//
// Node side (server role): VoxVoiceClient with an API key created through the console.
// Browser side (caller role): Chrome imports the built `signalling.js` and holds ONLY the
// signalling token; its RTCPeerConnection (fake microphone) really connects to the runtime.
// Speech providers are the harness's deterministic ones: this is not real-provider evidence.

import { createServer } from "node:http";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

import { launch } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = join(HERE, "..", "sdk", "dist");
const { VoxVoiceClient, VoxVoiceError } = await import(join(DIST, "index.js"));

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? [...acc, [v.slice(2), a[i + 1]]] : acc), []));
const BASE = (args.base ?? "http://127.0.0.1:8790").replace(/\/$/, "");
const OUT = args.out ?? "e2e-out-sdk";
const CHROME = args.chrome ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
mkdirSync(OUT, { recursive: true });

const checks = [];
const printed = [];
function check(name, ok, detail = "") {
  checks.push({ name, ok: Boolean(ok) });
  const line = `${ok ? "PASS" : "FAIL"}  ${name}${detail && !ok ? `  — ${String(detail).slice(0, 200)}` : ""}`;
  printed.push(line);
  console.log(line);
}
const catching = (p) => p.then(() => null, (e) => e);

// ── console setup: two organizations, a key for A (through the console API) ───

async function signup(email, org) {
  const r = await fetch(`${BASE}/v1/console/auth/signup`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-voxvoice-console": "1" },
    body: JSON.stringify({ email, password: "correct horse battery", organization_name: org }),
  });
  if (r.status !== 201) throw new Error(`signup ${r.status}`);
  const cookie = r.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
  return (path, init = {}) => fetch(`${BASE}${path}`, { ...init, headers: { cookie, "x-voxvoice-console": "1", "content-type": "application/json", ...(init.headers ?? {}) } });
}
const stamp = Date.now().toString(36);
const consoleA = await signup(`sdk-a-${stamp}@example.com`, `SDK A ${stamp}`);
const consoleB = await signup(`sdk-b-${stamp}@example.com`, `SDK B ${stamp}`);
const created = await (await consoleA("/v1/console/api-keys", { method: "POST", body: JSON.stringify({ label: "sdk-e2e" }) })).json();
const KEY = created.key;
const foreignAgent = (await (await consoleB("/v1/console/agents", { method: "POST", body: JSON.stringify({ name: "Theirs", system_prompt: "x" }) })).json()).id;

// A fetch that counts requests, so "no retry" is observable.
let sent = 0;
const counting = (...a) => { sent += 1; return fetch(...a); };
const client = new VoxVoiceClient({ apiKey: KEY, baseUrl: `${BASE}/`, fetch: counting });

// ── agents and sessions ───────────────────────────────────────────────────────

const agent = await client.agents.create({ name: `SDK agent ${stamp}`, system_prompt: "Be brief.", language: "en" });
check("agents.create", agent.id.startsWith("agt_") && agent.first_message === null);
check("agents.list (organization A only)", (await client.agents.list()).map((a) => a.id).join() === agent.id);
check("agents.get", (await client.agents.get(agent.id)).name === agent.name);
check("agents.update (null clears a nullable field)", (await client.agents.update(agent.id, { first_message: "Hi", tts_voice: null })).first_message === "Hi");
// On a voice-enabled server a session with no rendezvous has nothing to connect to: it fails,
// exactly as the developer API documents. (Without voice it starts a session with no audio path.)
const plain = await catching(client.sessions.create({ agent_id: agent.id, metadata: { via: "sdk" } }));
check("sessions.create without a rendezvous → 502 on a voice-enabled server", plain?.status === 502 && plain.message === "Runtime failed to start the session", `${plain?.status} ${plain?.message}`);

// ── errors, isolation, no retry ───────────────────────────────────────────────

const foreign = await catching(client.agents.get(foreignAgent));
check("foreign agent → 404 VoxVoiceError", foreign instanceof VoxVoiceError && foreign.status === 404 && foreign.message === "Agent not found");
const invalid = await catching(client.agents.create({ name: "x" }));
check("invalid request → readable 422", invalid?.status === 422 && invalid.message === "Invalid request (body.system_prompt: Field required)", invalid?.message);
sent = 0;
const bad = await catching(new VoxVoiceClient({ apiKey: "vf_not-a-key", baseUrl: BASE, fetch: counting }).agents.list());
check("invalid key → 401, sent once (no retry)", bad?.status === 401 && sent === 1, `${bad?.status} ${sent}`);
sent = 0;
const down = await catching(new VoxVoiceClient({ apiKey: KEY, baseUrl: "http://127.0.0.1:9", fetch: counting }).agents.create({ name: "x", system_prompt: "y" }));
check("unreachable server → status null, create sent once", down?.status === null && sent === 1, `${down?.status} ${sent}`);

// ── the credentials stay separate ─────────────────────────────────────────────

const rendezvous = await client.webrtc.createRendezvous();
check("webrtc.createRendezvous", rendezvous.rendezvous_id.startsWith("rdv_") && rendezvous.expires_in_s > 0);
const tokenAsKey = await fetch(`${BASE}/v1/agents`, { headers: { "x-signalling-token": rendezvous.signalling_token } });
check("a signalling token does not authenticate the developer API", tokenAsKey.status === 401);
const tokenAsApiKey = await fetch(`${BASE}/v1/agents`, { headers: { "x-api-key": rendezvous.signalling_token } });
check("a signalling token is not an API key", tokenAsApiKey.status === 401);
check("an API key does not open the console", (await fetch(`${BASE}/v1/console/me`, { headers: { "x-api-key": KEY } })).status === 401);

// ── a real browser WebRTC exchange through the SDK's SignallingClient ─────────

// Serve the built SDK to the page (CORS, read-only, dist/ only).
const assets = createServer((req, res) => {
  const file = normalize(join(DIST, decodeURIComponent(new URL(req.url, "http://x").pathname)));
  if (!file.startsWith(DIST)) { res.writeHead(403).end(); return; }
  try {
    res.writeHead(200, { "content-type": "text/javascript", "access-control-allow-origin": "*" }).end(readFileSync(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => assets.listen(0, "127.0.0.1", r));
const assetsUrl = `http://127.0.0.1:${assets.address().port}`;

const pageErrors = [];
const browser = await launch({
  chrome: CHROME, port: 9335, profileDir: OUT,
  onEvent(method, params) {
    if (method === "Runtime.exceptionThrown") pageErrors.push(params.exceptionDetails.exception?.description ?? params.exceptionDetails.text);
  },
});
try {
  await browser.navigate(`${BASE}/console/login`); // any page of the API's origin (the signalling endpoints are same-origin)
  const call = { baseUrl: BASE, rendezvousId: rendezvous.rendezvous_id, signallingToken: rendezvous.signalling_token };
  const browserSide = browser.evaluate(`(async () => {
    const { SignallingClient } = await import(${JSON.stringify(`${assetsUrl}/signalling.js`)});
    const signalling = new SignallingClient(${JSON.stringify(call)});
    const log = [];
    let offer = null;
    for (let i = 0; i < 60 && !offer; i++) offer = await signalling.getOffer();
    if (!offer) return { ok: false, log: ["no offer"] };
    log.push("offer");
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    const peer = new RTCPeerConnection();
    const heard = new Promise((r) => peer.addEventListener("track", () => r(true)));
    for (const track of mic.getAudioTracks()) peer.addTrack(track, mic);
    await peer.setRemoteDescription({ type: "offer", sdp: offer.sdp });
    await peer.setLocalDescription(await peer.createAnswer());
    if (peer.iceGatheringState !== "complete") {
      await new Promise((r) => peer.addEventListener("icegatheringstatechange", () => peer.iceGatheringState === "complete" && r()));
    }
    await signalling.sendAnswer(peer.localDescription.sdp);
    log.push("answer");
    const connected = await Promise.race([
      new Promise((r) => { const on = () => peer.connectionState === "connected" && r(true); peer.addEventListener("connectionstatechange", on); on(); }),
      new Promise((r) => setTimeout(() => r(false), 20000)),
    ]);
    const gotTrack = await Promise.race([heard, new Promise((r) => setTimeout(() => r(false), 5000))]);
    window.__peer = peer;
    return { ok: connected, state: peer.connectionState, gotTrack, log, keyInPage: document.documentElement.outerHTML.includes("vf_") };
  })()`);
  // The server side of the same call: start the session concurrently (it resolves once connected).
  const [result, started] = await Promise.all([browserSide, client.webrtc.startSession({ agent_id: agent.id, rendezvous_id: rendezvous.rendezvous_id })]);
  check("browser SignallingClient: offer → answer → RTCPeerConnection connected", result.ok, JSON.stringify(result));
  check("the browser received the agent's audio track", result.gotTrack === true);
  check("webrtc.startSession resolves with the session once connected", started.session_id?.startsWith("ses_"));
  check("the browser page never held an API key", result.keyInPage === false);
  const live = await client.sessions.get(started.session_id);
  check("sessions.get: the WebRTC session is active, with the agent and metadata sent", live.status === "active" && live.agent_id === agent.id);
  check("timestamps are returned as the server sends them (UTC, no offset)", /^\d{4}-\d\d-\d\dT[\d:.]+$/.test(live.started_at) && live.ended_at === null, live.started_at);
  check("sessions.transcript", Array.isArray(await client.sessions.transcript(started.session_id)));
  check("sessions.end, and ending twice is the same answer", (await client.sessions.end(started.session_id)).status === "ended" && (await client.sessions.end(started.session_id)).status === "ended");
  check("ending it through the SDK ends it", (await client.sessions.get(started.session_id)).status === "ended");
  check("the browser page raised no errors", pageErrors.length === 0, pageErrors.join(" | "));
} finally {
  browser.close();
  assets.close();
}

// ── revocation: the SAME client instance fails at once ────────────────────────

check("revoke through the console", (await consoleA(`/v1/console/api-keys/${created.id}/revoke`, { method: "POST" })).status === 204);
const revoked = await catching(client.agents.list());
check("the same SDK client → 401 after revocation", revoked?.status === 401 && revoked.message === "Invalid API key");
const errorText = [foreign, invalid, bad, down, revoked]
  .map((e) => `${String(e)}${JSON.stringify(e)}${e?.stack ?? ""}`)
  .join("\n");
check("the key appears in no SDK error, and in nothing this run printed", !errorText.includes(KEY) && !printed.join("\n").includes(KEY));

const failed = checks.filter((c) => !c.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} SDK checks passed`);
process.exit(failed.length ? 1 : 0);
