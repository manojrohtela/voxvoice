// The SDK against the developer API contract: `contract/developer-openapi.json` is generated from
// the server (a platform test keeps it current). Every SDK method must send exactly a documented
// operation, with the documented credential, and every SDK type must have exactly the schema's fields.

import { describe, expect, it } from "vitest";

import contract from "../contract/developer-openapi.json" with { type: "json" };
import { VoxVoiceClient } from "../src/index.js";
import { SignallingClient } from "../src/signalling.js";
import type {
  Agent,
  AgentCreate,
  AgentUpdate,
  Rendezvous,
  Session,
  SessionCreate,
  SessionCreated,
  TranscriptItem,
  WebRTCSessionCreate,
} from "../src/types.js";
import { API_KEY, BASE_URL, json, recordingFetch, type Recorded } from "./helpers.js";

type Schema = { properties?: Record<string, unknown>; required?: string[] };
const SCHEMAS = contract.components.schemas as Record<string, Schema>;
const PATHS = contract.paths as Record<string, Record<string, { security?: Record<string, unknown>[]; requestBody?: unknown }>>;

function operationOf(call: Recorded): { template: string; method: string } {
  const method = call.method.toLowerCase();
  const path = new URL(call.url).pathname;
  for (const template of Object.keys(PATHS)) {
    const pattern = new RegExp(`^${template.replace(/\{[^}]+\}/g, "[^/]+")}$`);
    if (pattern.test(path) && PATHS[template]![method]) return { template, method };
  }
  throw new Error(`${call.method} ${path} is not a developer API operation`);
}

function requestSchema(template: string, method: string): Schema | undefined {
  const body = PATHS[template]![method]!.requestBody as
    | { content: { "application/json": { schema: { $ref: string } } } }
    | undefined;
  const ref = body?.content["application/json"].schema.$ref;
  return ref ? SCHEMAS[ref.split("/").pop()!] : undefined;
}

const keys = (value: object) => Object.keys(value).sort();
const fields = (schema: string) => Object.keys(SCHEMAS[schema]!.properties ?? {}).sort();

// ── every SDK method is one documented operation ──────────────────────────────

async function exercise() {
  const { fetch, calls } = recordingFetch((r) => (r.path.endsWith("/offer") || r.path.endsWith("/answer") ? new Response(null, { status: 204 }) : json({})));
  const client = new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch });
  const browser = new SignallingClient({ baseUrl: BASE_URL, rendezvousId: "rdv_1", signallingToken: "tok_1", fetch });
  await client.agents.create({ name: "A", system_prompt: "s" });
  await client.agents.list();
  await client.agents.get("agt_1");
  await client.agents.update("agt_1", { name: "B" });
  await client.agents.delete("agt_1");
  await client.sessions.create({ agent_id: "agt_1" });
  await client.sessions.get("ses_1");
  await client.sessions.transcript("ses_1");
  await client.sessions.end("ses_1");
  await client.webrtc.createRendezvous();
  await client.webrtc.startSession({ agent_id: "agt_1", rendezvous_id: "rdv_1" });
  await browser.getOffer();
  await browser.sendAnswer("v=0");
  return calls;
}

describe("contract", () => {
  it("covers every developer operation, and nothing else", async () => {
    const calls = await exercise();
    const sent = calls.map((c) => { const op = operationOf(c); return `${op.method.toUpperCase()} ${op.template}`; });
    const documented = Object.entries(PATHS).flatMap(([template, ops]) => Object.keys(ops).map((m) => `${m.toUpperCase()} ${template}`));
    expect(new Set(sent)).toEqual(new Set(documented));
    expect(sent).toHaveLength(documented.length);
  });

  it("sends each operation's own credential and never the other", async () => {
    for (const call of await exercise()) {
      const { template, method } = operationOf(call);
      const scheme = Object.keys(PATHS[template]![method]!.security![0]!)[0];
      if (scheme === "ApiKeyAuth") {
        expect(call.headers["x-api-key"]).toBe(API_KEY);
        expect(call.headers["x-signalling-token"]).toBeUndefined();
      } else {
        expect(scheme).toBe("SignallingToken");
        expect(call.headers["x-signalling-token"]).toBe("tok_1");
        expect(call.headers["x-api-key"]).toBeUndefined();
      }
    }
  });

  it("sends bodies that fit the operation's request schema", async () => {
    for (const call of await exercise()) {
      const { template, method } = operationOf(call);
      const schema = requestSchema(template, method);
      if (schema === undefined) {
        expect(call.body, `${call.method} ${template} takes no body`).toBeUndefined();
        continue;
      }
      const sent = keys(call.body as object);
      for (const key of sent) expect(Object.keys(schema.properties ?? {})).toContain(key);
      for (const key of schema.required ?? []) expect(sent).toContain(key);
    }
  });
});

// ── every SDK type has exactly the schema's fields ────────────────────────────
// Each sample is type-checked against the SDK type (no missing required field, no extra field),
// then its keys are compared with the server schema, so the two cannot drift apart silently.

describe("types match the server schemas", () => {
  const agent: Agent = {
    id: "agt_1", org_id: "org_1", name: "A", system_prompt: "s", first_message: null, language: "en",
    stt_provider: "default", tts_provider: "default", tts_voice: null, llm_model: null, allow_interruptions: true,
    min_endpointing_delay: 0.5, max_endpointing_delay: 6, tools: [], metadata: {},
    created_at: "2026-09-21T12:27:16.405839", updated_at: "2026-09-21T12:27:16.405847",
  };
  const create: Required<AgentCreate> = {
    name: "A", system_prompt: "s", first_message: null, language: "en", stt_provider: "default", tts_provider: "default",
    tts_voice: null, llm_model: null, allow_interruptions: true, min_endpointing_delay: 0.5, max_endpointing_delay: 6,
    tools: [], metadata: {},
  };
  const update: Required<AgentUpdate> = { ...create };
  const session: Session = {
    id: "ses_1", org_id: "org_1", agent_id: "agt_1", engine: "voxvoice", engine_ref: null, channel: "web",
    status: "active", started_at: "2026-09-21T12:27:16.428926", ended_at: null, metadata: {},
  };
  const sessionCreate: Required<SessionCreate> = { agent_id: "agt_1", channel: "web", metadata: {} };
  const created: Required<SessionCreated> = { session_id: "ses_1", engine: "voxvoice", channel: "web", web: null, phone: null };
  const line: TranscriptItem = { session_id: "ses_1", role: "user", text: "hi", ts: 1.5 };
  const rendezvous: Rendezvous = { rendezvous_id: "rdv_1", signalling_token: "tok", expires_in_s: 120 };
  const webrtcCreate: Required<WebRTCSessionCreate> = { ...sessionCreate, rendezvous_id: "rdv_1" };

  it.each([
    ["AgentRead", agent],
    ["api__schemas__AgentCreate", create],
    ["api__schemas__AgentUpdate", update],
    ["SessionRead", session],
    ["SessionCreate", sessionCreate],
    ["SessionCreateResponse", created],
    ["TranscriptItem", line],
    ["RendezvousResponse", rendezvous],
    ["WebRTCSessionCreate", webrtcCreate],
  ] as const)("%s", (schema, sample) => {
    expect(keys(sample)).toEqual(fields(schema));
    // A field the server always returns is required in the SDK type too.
    const required = SCHEMAS[schema]!.required ?? [];
    for (const key of required) expect(keys(sample)).toContain(key);
  });
});
