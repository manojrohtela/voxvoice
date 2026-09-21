import { vi } from "vitest";

import type { ConsoleMeta, Health } from "../api/types";

export const META: ConsoleMeta = {
  api_version: "v1",
  console_api_revision: "m12.1",
  runtime: {
    transport: "local",
    webrtc_signalling: false,
    ice_policy: "no ICE servers (explicit; host candidates only)",
  },
  capabilities: ["runtime.status"],
  console_bundle: false,
};

export const HEALTH: Health = {
  ok: true,
  db: true,
  transport: "local",
  webrtc_signalling: false,
  ice_policy: META.runtime.ice_policy,
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/** Stub `fetch` with fixed responses per path; any other path is a 404. */
export function stubApi(routes: Record<string, () => Response | Promise<Response>>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = typeof input === "string" ? input : input.toString();
    const route = routes[path];
    return route ? route() : json({ error: { type: "not_found", message: "nope" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function healthyApi(meta: Partial<ConsoleMeta> = {}) {
  return stubApi({
    "/v1/console/meta": () => json({ ...META, ...meta }),
    "/health": () => json(HEALTH),
  });
}

export { json };
