import { type ReactNode, useCallback } from "react";
import { Link } from "react-router";

import { getJson } from "../../api/client";
import { useResource } from "../../api/useResource";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";

// The guide mirrors docs/apis/developer-api.md (the contract). Every "METHOD /path" named here is
// checked against the server's OpenAPI document by tests/test_console_api_keys.py, and the
// reference below is generated from that document at runtime, so neither can drift from the API.

interface Endpoint {
  op: string;
  what: string;
}

const AGENTS: Endpoint[] = [
  { op: "POST /v1/agents", what: "Create an agent (name and system_prompt required)." },
  { op: "GET /v1/agents", what: "All of your organization's agents, as an array (not paginated)." },
  { op: "GET /v1/agents/{agent_id}", what: "One agent." },
  { op: "PATCH /v1/agents/{agent_id}", what: "Change only the fields you send." },
  { op: "DELETE /v1/agents/{agent_id}", what: "Delete permanently. Prefer keeping agents that have sessions." },
];

const SESSIONS: Endpoint[] = [
  { op: "POST /v1/sessions", what: "Start a session with no audio path. On a voice-enabled server this fails with 502 — use WebRTC below." },
  { op: "GET /v1/sessions/{session_id}", what: "Status (active, ended, failed), times and your metadata." },
  { op: "GET /v1/sessions/{session_id}/transcript", what: "Final transcript lines with seconds since start." },
  { op: "POST /v1/sessions/{session_id}/end", what: "End the session. Safe to repeat." },
];

const WEBRTC: Endpoint[] = [
  { op: "POST /v1/webrtc/rendezvous", what: "Your server: get a rendezvous id and a single-purpose signalling token." },
  { op: "POST /v1/webrtc/sessions", what: "Your server: start the session; returns once the caller is connected." },
  { op: "GET /v1/webrtc/rendezvous/{rendezvous_id}/offer", what: "The browser, with the token: poll until 200 (204 = ask again)." },
  { op: "POST /v1/webrtc/rendezvous/{rendezvous_id}/answer", what: "The browser, with the token: send its answer." },
];

const ERRORS: [string, string][] = [
  ["401", "Missing, invalid or revoked API key (or a wrong signalling token)."],
  ["404", "Not found, or belongs to another organization — the two look the same."],
  ["409 · 413 · 504", "WebRTC signalling: wrong step, SDP too large, or the browser did not answer in time."],
  ["422", "The request body or parameters are invalid; detail lists each problem."],
  ["429", "Too many WebRTC rendezvous open on the server (a capacity limit, not a rate limit)."],
  ["502 · 503", "The runtime could not start the session, or none is configured."],
];

const SDK = `import { VoxVoiceClient, VoxVoiceError } from "@voxvoice/sdk";

const client = new VoxVoiceClient({
  apiKey: process.env.VOXVOICE_API_KEY!, // <YOUR_API_KEY>, from your server's environment
  baseUrl: "http://localhost:8000",      // without /v1
});

const agent = await client.agents.create({ name: "Support", system_prompt: "Be brief.", language: "en" });
const { session_id } = await client.sessions.create({ agent_id: agent.id });
try {
  await client.sessions.get("ses_missing");
} catch (error) {
  if (error instanceof VoxVoiceError) console.error(error.status, error.message); // 404
}`;

const CURL = `export VOXVOICE_API_KEY="<YOUR_API_KEY>"

curl -sS -X POST http://localhost:8000/v1/agents \\
  -H "x-api-key: $VOXVOICE_API_KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Support", "system_prompt": "You are a helpful support agent.", "language": "en"}'`;

const PYTHON = `import os, httpx

client = httpx.Client(base_url="http://localhost:8000",
                      headers={"x-api-key": os.environ["VOXVOICE_API_KEY"]})
agent = client.post("/v1/agents", json={"name": "Support",
                    "system_prompt": "You are a helpful support agent.", "language": "en"}).json()
session = client.post("/v1/sessions", json={"agent_id": agent["id"]}).json()
client.post(f"/v1/sessions/{session['session_id']}/end")`;

const TYPESCRIPT = `// Server-side only (Node 18+). Never ship the key to a browser.
const headers = { "x-api-key": process.env.VOXVOICE_API_KEY!, "Content-Type": "application/json" };
const res = await fetch("http://localhost:8000/v1/sessions", {
  method: "POST", headers, body: JSON.stringify({ agent_id: "agt_…" }),
});
if (!res.ok) throw new Error(\`VoxVoice \${res.status}\`);
const { session_id } = await res.json();`;

function EndpointList({ items }: { items: Endpoint[] }) {
  return (
    <dl className="endpoints">
      {items.map((e) => {
        const [method, path] = e.op.split(" ");
        return (
          <div key={e.op}>
            <dt>
              <span className={`method method--${method!.toLowerCase()}`}>{method}</span> <code>{path}</code>
            </dt>
            <dd>{e.what}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <article className="card doc" aria-labelledby={`${id}-heading`} id={id}>
      <h2 id={`${id}-heading`}>{title}</h2>
      {children}
    </article>
  );
}

// ── live reference from /openapi.json ─────────────────────────────────────────

interface Operation {
  summary?: string;
  "x-voxvoice-surface"?: string;
  responses?: Record<string, unknown>;
}

interface OpenApi {
  paths: Record<string, Record<string, Operation>>;
}

const DEVELOPER_SURFACES = new Set(["developer", "signalling"]);

function loadSpec(signal: AbortSignal): Promise<OpenApi> {
  return getJson<OpenApi>("/openapi.json", signal);
}

function Reference() {
  const spec = useResource(useCallback(loadSpec, []));
  if (spec.status === "loading") return <Loading />;
  if (spec.status === "error") {
    return <ErrorState title="Could not load the API reference" error={spec.error} onRetry={spec.reload} />;
  }
  const rows = Object.entries(spec.data.paths).flatMap(([path, ops]) =>
    Object.entries(ops)
      .filter(([, op]) => DEVELOPER_SURFACES.has(op["x-voxvoice-surface"] ?? ""))
      .map(([method, op]) => ({ method: method.toUpperCase(), path, op })),
  );
  if (rows.length === 0) return <EmptyState title="This server exposes no developer operations" />;
  return (
    <table className="table">
      <caption className="visually-hidden">Developer API operations on this server</caption>
      <thead>
        <tr>
          <th scope="col">Operation</th>
          <th scope="col">Credential</th>
          <th scope="col">Responses</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ method, path, op }) => (
          <tr key={`${method} ${path}`}>
            <th scope="row">
              <span className={`method method--${method.toLowerCase()}`}>{method}</span> <code>{path}</code>
              {op.summary ? <div className="muted">{op.summary}</div> : null}
            </th>
            <td>{op["x-voxvoice-surface"] === "signalling" ? "signalling token" : "API key"}</td>
            <td>{Object.keys(op.responses ?? {}).join(" · ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DeveloperDocsPage() {
  return (
    <section className="page docs">
      <header className="page__header">
        <p className="breadcrumb">
          <Link to="/developers">API keys</Link> / Developer docs
        </p>
        <h1>Developer docs</h1>
        <p className="page__lede">
          Call VoxVoice from your server: create agents, start sessions and connect callers. This describes the API
          as it is today.
        </p>
      </header>

      <nav aria-label="On this page" className="docs__toc">
        <a href="#auth">Authentication</a> · <a href="#agents">Agents</a> · <a href="#sessions">Sessions</a> ·{" "}
        <a href="#webrtc">Connecting a caller</a> · <a href="#errors">Errors</a> · <a href="#examples">Examples</a> · <a href="#sdk">TypeScript SDK</a> ·{" "}
        <a href="#unavailable">Not available yet</a> · <a href="#reference">Reference</a>
      </nav>

      <Section id="auth" title="Authentication">
        <p>
          Send your organization's API key in the <code>x-api-key</code> header on every request:{" "}
          <code>x-api-key: &lt;YOUR_API_KEY&gt;</code>. An owner or admin creates keys on the{" "}
          <Link to="/developers">API keys</Link> page. A key is shown once, so store it in your secret manager or
          server environment.
        </p>
        <ul>
          <li>
            <strong>Server-side only.</strong> Never put a key in browser code, a mobile app, a URL or a log. To
            connect a caller's browser, give it a single-purpose signalling token instead (below).
          </li>
          <li>
            <strong>One organization.</strong> A key reaches only its organization's agents and sessions. Anything
            else answers <code>404</code>, exactly like something that does not exist. There are no scopes.
          </li>
          <li>
            <strong>Not a provider key.</strong> VoxVoice's speech, language-model and voice providers are configured
            on the server. You never need or send their credentials.
          </li>
          <li>
            <strong>Not a console login.</strong> Your console sign-in does not work on the API, and a key does not
            work in the console.
          </li>
          <li>
            <strong>Revoking</strong> a key takes effect on the next request (<code>401</code>). Keys do not expire
            and cannot be rotated in place: create a new one, switch, then revoke the old one.
          </li>
        </ul>
      </Section>

      <Section id="agents" title="Agents">
        <EndpointList items={AGENTS} />
        <p className="note">
          Set <code>language</code> explicitly (the server currently recognizes <code>en</code>; the default{" "}
          <code>en-IN</code> is accepted but calls with it drop after the greeting). Leave <code>stt_provider</code>{" "}
          and <code>tts_provider</code> as <code>"default"</code>. <code>allow_interruptions</code>, the endpointing
          delays and <code>tools</code> are stored but have no effect yet.
        </p>
      </Section>

      <Section id="sessions" title="Sessions">
        <EndpointList items={SESSIONS} />
        <p className="note">
          IDs are opaque strings (<code>agt_…</code>, <code>ses_…</code>). Times are ISO 8601 in UTC without an
          offset. Unset values are <code>null</code>. Sessions do not record why a call ended, and cannot be listed
          through the API.
        </p>
      </Section>

      <Section id="webrtc" title="Connecting a caller (WebRTC)">
        <p>Available when the server has voice enabled. Your API key stays on your server throughout.</p>
        <EndpointList items={WEBRTC} />
        <ol>
          <li>Your server reserves a rendezvous and passes its id and signalling token to the caller's browser.</li>
          <li>
            Your server starts the session with the rendezvous id. That request waits until the caller is connected.
          </li>
          <li>At the same time the browser fetches the offer, creates an answer and sends it back.</li>
          <li>
            The session call returns its <code>session_id</code>. End the call with the sessions endpoint above.
          </li>
        </ol>
      </Section>

      <Section id="errors" title="Errors">
        <p>
          Errors are <code>{'{"detail": "<message>"}'}</code>; validation errors list each problem under{" "}
          <code>detail</code>. Rely on the status code; messages are for people.
        </p>
        <dl className="endpoints">
          {ERRORS.map(([code, meaning]) => (
            <div key={code}>
              <dt>
                <code>{code}</code>
              </dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section id="examples" title="Examples">
        <h3>curl</h3>
        <pre className="code">{CURL}</pre>
        <h3>Python</h3>
        <pre className="code">{PYTHON}</pre>
        <h3>TypeScript</h3>
        <pre className="code">{TYPESCRIPT}</pre>
      </Section>

      <Section id="sdk" title="TypeScript SDK">
        <p>
          <code>@voxvoice/sdk</code> is a thin, typed client for exactly this API, for Node.js 20.3+ on your server.
          It returns the same fields and <code>null</code>s, and every failure is a <code>VoxVoiceError</code> with
          the status and the server&apos;s detail. It is not on a registry yet: install it from the repository&apos;s{" "}
          <code>sdk/</code> directory.
        </p>
        <pre className="code">{SDK}</pre>
        <ul>
          <li>
            <code>client.agents</code>, <code>client.sessions</code> and <code>client.webrtc</code> use your API key.{" "}
            <code>SignallingClient</code> from <code>@voxvoice/sdk/signalling</code> is for the browser and holds only
            the signalling token.
          </li>
          <li>It does not implement WebRTC: the browser&apos;s RTCPeerConnection does. It never retries a request.</li>
          <li>Everything under &ldquo;Not available yet&rdquo; applies to the SDK as well.</li>
        </ul>
      </Section>

      <Section id="unavailable" title="Not available yet">
        <ul>
          <li>Conversation history and analytics through the API; they are in this console only.</li>
          <li>Key scopes, expiry, in-place rotation and rate limits.</li>
          <li>Pagination and session listing.</li>
          <li>Interrupting the agent while it speaks (barge-in).</li>
          <li>Cost, token or usage figures, and end reasons.</li>
          <li>Phone channels, a high-level voice-call helper in the SDK, and a hosted service.</li>
        </ul>
      </Section>

      <Section id="reference" title="Reference">
        <p>
          Generated from this server's <code>/openapi.json</code>: the operations of the developer API, with the
          credential each one takes.
        </p>
        <Reference />
      </Section>
    </section>
  );
}
