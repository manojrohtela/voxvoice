import { describe, expect, it, vi } from "vitest";

import * as sdk from "../src/index.js";
import { VoxVoiceClient, VoxVoiceError } from "../src/index.js";
import { SignallingClient } from "../src/signalling.js";
import { API_KEY, BASE_URL, json, recordingFetch } from "./helpers.js";

function client(responder?: Parameters<typeof recordingFetch>[0], extra: Partial<sdk.VoxVoiceClientOptions> = {}) {
  const { fetch, calls } = recordingFetch(responder);
  return { client: new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch, ...extra }), calls };
}

const AGENT: sdk.Agent = {
  id: "agt_1", org_id: "org_1", name: "Support", system_prompt: "Be brief.", first_message: null, language: "en",
  stt_provider: "default", tts_provider: "default", tts_voice: null, llm_model: null, allow_interruptions: true,
  min_endpointing_delay: 0.5, max_endpointing_delay: 6, tools: [], metadata: { team: "a" },
  created_at: "2026-09-21T12:27:16.405839", updated_at: "2026-09-21T12:27:16.405847",
};

// ── configuration ─────────────────────────────────────────────────────────────

describe("configuration", () => {
  it("requires an API key and an absolute base URL", () => {
    const fetch = recordingFetch().fetch;
    for (const apiKey of ["", "   ", undefined as unknown as string]) {
      expect(() => new VoxVoiceClient({ apiKey, baseUrl: BASE_URL, fetch })).toThrow(/apiKey is required/);
    }
    for (const baseUrl of ["localhost:8000", "/api", "ftp://x.test", "http://u:p@x.test", "http://x.test?a=1"]) {
      expect(() => new VoxVoiceClient({ apiKey: API_KEY, baseUrl, fetch })).toThrow(TypeError);
    }
    expect(() => new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch, timeoutMs: 0 })).toThrow(/timeoutMs/);
  });

  it.each([
    ["http://voxvoice.test", "http://voxvoice.test/v1/agents"],
    ["http://voxvoice.test/", "http://voxvoice.test/v1/agents"],
    ["http://voxvoice.test//", "http://voxvoice.test/v1/agents"],
    ["https://api.example.com/voxvoice/", "https://api.example.com/voxvoice/v1/agents"],
  ])("builds paths from %s without doubling slashes", async (baseUrl, expected) => {
    const { client: c, calls } = client(() => json([]), { baseUrl });
    await c.agents.list();
    expect(calls[0]!.url).toBe(expected);
  });

  it("refuses a base URL that already ends in /v1, which would give /v1/v1", () => {
    const fetch = recordingFetch().fetch;
    for (const baseUrl of ["http://x.test/v1", "http://x.test/v1/"]) {
      expect(() => new VoxVoiceClient({ apiKey: API_KEY, baseUrl, fetch })).toThrow(/must not include \/v1/);
    }
  });

  it("exposes only the intended public surface", () => {
    expect(Object.keys(sdk).sort()).toEqual(["VoxVoiceClient", "VoxVoiceError"]);
  });
});

describe("the global fetch", () => {
  // A browser's `fetch` refuses to run unless it is called on `window`; Node's does not care. The
  // SDK must bind it, or every request from a browser fails with "Illegal invocation".
  const browserLike = function (this: unknown, ...rest: Parameters<typeof fetch>) {
    if (this !== globalThis) throw new TypeError("Illegal invocation");
    void rest;
    return Promise.resolve(json([]));
  };

  it("is used the way a browser requires", async () => {
    vi.stubGlobal("fetch", browserLike);
    await expect(new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL }).agents.list()).resolves.toEqual([]);
    await expect(new SignallingClient({ baseUrl: BASE_URL, rendezvousId: "rdv_1", signallingToken: "tok" }).getOffer()).resolves.toEqual([]);
  });
});

// ── HTTP ──────────────────────────────────────────────────────────────────────

describe("requests", () => {
  it("sends GET, POST, PATCH and DELETE with the key header and JSON only where there is a body", async () => {
    const { client: c, calls } = client((r) => json(r.method === "DELETE" ? { deleted: "agt_1" } : AGENT, r.method === "POST" ? 201 : 200));
    await c.agents.get("agt_1");
    await c.agents.create({ name: "Support", system_prompt: "Be brief.", language: "en" });
    await c.agents.update("agt_1", { first_message: null, metadata: { team: "a" } });
    await c.agents.delete("agt_1");

    expect(calls.map((r) => `${r.method} ${r.path}`)).toEqual([
      "GET /v1/agents/agt_1",
      "POST /v1/agents",
      "PATCH /v1/agents/agt_1",
      "DELETE /v1/agents/agt_1",
    ]);
    for (const r of calls) {
      expect(r.headers["x-api-key"]).toBe(API_KEY);
      expect(r.headers.accept).toBe("application/json");
      expect("content-type" in r.headers).toBe(r.method === "POST" || r.method === "PATCH");
    }
    expect(calls[1]!.body).toEqual({ name: "Support", system_prompt: "Be brief.", language: "en" });
    // null is sent as null (it clears the field); an omitted field is not sent at all.
    expect(calls[2]!.body).toEqual({ first_message: null, metadata: { team: "a" } });
    expect(calls[0]!.path).not.toContain("?");
  });

  it("returns the server's values unchanged: null stays null, timestamps stay strings", async () => {
    const { client: c } = client(() => json(AGENT));
    const agent = await c.agents.get("agt_1");
    expect(agent).toEqual(AGENT);
    expect(agent.first_message).toBeNull();
    expect(agent.created_at).toBe("2026-09-21T12:27:16.405839");
  });

  it("covers the session and WebRTC operations with their exact paths and bodies", async () => {
    const { client: c, calls } = client(() => json({}));
    await c.sessions.create({ agent_id: "agt_1", metadata: { ref: "c-42" } });
    await c.sessions.get("ses_1");
    await c.sessions.transcript("ses_1");
    await c.sessions.end("ses_1");
    await c.webrtc.createRendezvous();
    await c.webrtc.startSession({ agent_id: "agt_1", rendezvous_id: "rdv_1" });
    expect(calls.map((r) => [r.method, r.path, r.body])).toEqual([
      ["POST", "/v1/sessions", { agent_id: "agt_1", metadata: { ref: "c-42" } }],
      ["GET", "/v1/sessions/ses_1", undefined],
      ["GET", "/v1/sessions/ses_1/transcript", undefined],
      ["POST", "/v1/sessions/ses_1/end", undefined],
      ["POST", "/v1/webrtc/rendezvous", undefined],
      ["POST", "/v1/webrtc/sessions", { agent_id: "agt_1", rendezvous_id: "rdv_1" }],
    ]);
  });

  it("encodes ids so they can never change the path, and refuses an empty one", async () => {
    const { client: c, calls } = client(() => json(AGENT));
    await c.agents.get("a/../b?x=1#y");
    expect(calls[0]!.url).toBe(`${BASE_URL}/v1/agents/a%2F..%2Fb%3Fx%3D1%23y`);
    expect(() => c.agents.get("")).toThrow(TypeError);
  });

  it("lists agents as the plain array the API returns (no pagination)", async () => {
    const { client: c, calls } = client(() => json([AGENT, { ...AGENT, id: "agt_2" }]));
    expect((await c.agents.list()).map((a) => a.id)).toEqual(["agt_1", "agt_2"]);
    expect(calls[0]!.path).toBe("/v1/agents");
  });

  it("is safe to use concurrently", async () => {
    const { client: c, calls } = client((r) => json({ ...AGENT, id: r.path.split("/").pop() }));
    const ids = Array.from({ length: 20 }, (_, n) => `agt_${n}`);
    const got = await Promise.all(ids.map((id) => c.agents.get(id)));
    expect(got.map((a) => a.id)).toEqual(ids);
    expect(new Set(calls.map((r) => r.headers["x-api-key"]))).toEqual(new Set([API_KEY]));
  });
});

// ── errors ────────────────────────────────────────────────────────────────────

describe("errors", () => {
  it.each([
    [400, { detail: "Bad request" }, "Bad request"],
    [401, { detail: "Invalid API key" }, "Invalid API key"],
    [403, { detail: "Forbidden" }, "Forbidden"],
    [404, { detail: "Agent not found" }, "Agent not found"],
    [409, { detail: "rendezvous is bound, not reserved" }, "rendezvous is bound, not reserved"],
    [500, { detail: "Internal Server Error" }, "Internal Server Error"],
    [502, { detail: "Runtime failed to start the session" }, "Runtime failed to start the session"],
  ])("maps %i to a VoxVoiceError with the server's detail", async (status, body, message) => {
    const { client: c, calls } = client(() => json(body, status));
    const error = await c.agents.get("agt_1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(VoxVoiceError);
    expect(error).toMatchObject({ status, detail: body.detail, message, name: "VoxVoiceError" });
    expect(calls).toHaveLength(1); // never retried
  });

  it("summarizes a 422 from its locations and messages, without echoing the input", async () => {
    const detail = [{ type: "missing", loc: ["body", "system_prompt"], msg: "Field required", input: { name: "secret-ish" } }];
    const { client: c } = client(() => json({ detail }, 422));
    const error = (await c.agents.create({ name: "x", system_prompt: "" }).catch((e: unknown) => e)) as VoxVoiceError;
    expect(error.status).toBe(422);
    expect(error.message).toBe("Invalid request (body.system_prompt: Field required)");
    expect(error.detail).toEqual(detail);
  });

  it("handles a non-JSON error body and an unreadable success body", async () => {
    const text = client(() => new Response("upstream exploded", { status: 500 })).client;
    await expect(text.agents.list()).rejects.toMatchObject({ status: 500, detail: undefined, message: "VoxVoice API request failed with status 500" });
    const garbled = client(() => new Response("<html>", { status: 200 })).client;
    await expect(garbled.agents.list()).rejects.toMatchObject({ status: 200, message: expect.stringMatching(/unreadable/) });
    const empty = client(() => new Response("", { status: 200 })).client;
    await expect(empty.agents.list()).rejects.toBeInstanceOf(VoxVoiceError);
  });

  it("reports a network failure once, with no status and no retry", async () => {
    const fetch = vi.fn(async () => { throw new TypeError("fetch failed"); });
    const c = new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch });
    const error = (await c.agents.create({ name: "A", system_prompt: "s" }).catch((e: unknown) => e)) as VoxVoiceError;
    expect(error).toMatchObject({ status: null, message: "Could not reach the VoxVoice API" });
    expect(fetch).toHaveBeenCalledTimes(1); // a create is never sent twice
  });

  it("times out deterministically, per client or per call", async () => {
    const hang = (_: unknown, init?: RequestInit) =>
      new Promise<Response>((_, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal!.reason)));
    const slow = new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch: hang as typeof fetch, timeoutMs: 20 });
    await expect(slow.agents.list()).rejects.toMatchObject({ status: null, message: "VoxVoice API request timed out after 20 ms" });
    const noDefault = new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch: hang as typeof fetch });
    await expect(noDefault.agents.list({ timeoutMs: 15 })).rejects.toMatchObject({ message: "VoxVoice API request timed out after 15 ms" });
  });

  it("lets the caller cancel, with the platform's AbortError", async () => {
    const hang = (_: unknown, init?: RequestInit) =>
      new Promise<Response>((_, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal!.reason)));
    const c = new VoxVoiceClient({ apiKey: API_KEY, baseUrl: BASE_URL, fetch: hang as typeof fetch, timeoutMs: 5_000 });
    const controller = new AbortController();
    const pending = c.agents.list({ signal: controller.signal });
    controller.abort();
    const error = await pending.catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(VoxVoiceError);
    expect((error as Error).name).toBe("AbortError");
  });
});

// ── secrets ───────────────────────────────────────────────────────────────────

describe("the API key", () => {
  it("never reaches a URL, an error, a log, or a serialized client", async () => {
    const log = vi.spyOn(console, "log");
    const warn = vi.spyOn(console, "warn");
    const errorLog = vi.spyOn(console, "error");
    const { client: c, calls } = client((r) => (r.path.endsWith("agt_x") ? json({ detail: "Agent not found" }, 404) : json(AGENT)));

    await c.agents.get("agt_1");
    const error = await c.agents.get("agt_x").catch((e: unknown) => e);

    expect(calls.every((r) => !r.url.includes(API_KEY))).toBe(true);
    const everything = (value: object) =>
      JSON.stringify(Reflect.ownKeys(value).map((k) => String((value as Record<PropertyKey, unknown>)[k])));
    for (const text of [String(error), JSON.stringify(error), everything(error as object), (error as Error).stack ?? ""]) {
      expect(text).not.toContain(API_KEY);
    }
    // The key lives in a private (#) field of the internal transport: no property exposes it.
    for (const text of [JSON.stringify(c), everything(c), everything(c.agents), everything(c.sessions), everything(c.webrtc)]) {
      expect(text).not.toContain(API_KEY);
    }
    expect(log).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    expect(errorLog).not.toHaveBeenCalled();
  });
});

// ── the browser-side signalling client ────────────────────────────────────────

describe("SignallingClient", () => {
  it("fetches the offer (null while not ready) and sends the answer with only the signalling token", async () => {
    let ready = false;
    const { fetch, calls } = recordingFetch((r) =>
      r.path.endsWith("/offer") ? (ready ? json({ sdp: "v=0 offer" }) : new Response(null, { status: 204 })) : new Response(null, { status: 204 }),
    );
    const browser = new SignallingClient({ baseUrl: BASE_URL, rendezvousId: "rdv_1", signallingToken: "tok_1", fetch });
    expect(await browser.getOffer()).toBeNull();
    ready = true;
    expect(await browser.getOffer()).toEqual({ sdp: "v=0 offer" });
    expect(await browser.sendAnswer("v=0 answer")).toBeUndefined();
    expect(calls.map((r) => `${r.method} ${r.path}`)).toEqual([
      "GET /v1/webrtc/rendezvous/rdv_1/offer",
      "GET /v1/webrtc/rendezvous/rdv_1/offer",
      "POST /v1/webrtc/rendezvous/rdv_1/answer",
    ]);
    expect(calls[2]!.body).toEqual({ sdp: "v=0 answer" });
    for (const r of calls) {
      expect(r.headers["x-signalling-token"]).toBe("tok_1");
      expect(r.headers["x-api-key"]).toBeUndefined();
    }
  });

  it("reports an expired rendezvous as a VoxVoiceError", async () => {
    const { fetch } = recordingFetch(() => json({ detail: "no such signalling rendezvous" }, 404));
    const browser = new SignallingClient({ baseUrl: BASE_URL, rendezvousId: "rdv_1", signallingToken: "tok_1", fetch });
    await expect(browser.getOffer()).rejects.toMatchObject({ status: 404, message: "no such signalling rendezvous" });
    expect(() => new SignallingClient({ baseUrl: BASE_URL, rendezvousId: "rdv_1", signallingToken: "", fetch })).toThrow();
  });
});
