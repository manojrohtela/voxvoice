import { vi } from "vitest";

import type { ConsoleMeta, Health, Me } from "../api/types";

export const META: ConsoleMeta = {
  api_version: "v1",
  console_api_revision: "m12.8",
  runtime: {
    transport: "local",
    webrtc_signalling: false,
    ice_policy: "no ICE servers (explicit; host candidates only)",
  },
  capabilities: ["runtime.status", "identity", "agents", "playground", "conversations", "analytics", "api_keys", "overview"],
  console_bundle: false,
  signup: { mode: "first_run", available: false },
};

export const ME: Me = {
  user: { id: "usr_1", email: "ada@example.com", display_name: "Ada" },
  organization: { id: "org_a", name: "Analytical Engines", role: "owner" },
  memberships: [
    { id: "org_a", name: "Analytical Engines", role: "owner" },
    { id: "org_b", name: "Babbage Labs", role: "viewer" },
  ],
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

type Route = (init: RequestInit | undefined) => Response | Promise<Response>;

/**
 * Stub `fetch` with fixed responses. Keys are `"/path"` (any method) or `"POST /path"`; a
 * method-specific key wins. Anything else is a 404.
 */
export function stubApi(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const route = routes[`${method} ${path}`] ?? routes[path];
    return route ? route(init) : json({ error: { type: "not_found", message: "nope" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const UNAUTHENTICATED = () =>
  json({ error: { type: "unauthenticated", message: "Sign in to continue" } }, 401);

/** A healthy server with a signed-in user (override `me` with `null` for signed-out). */
export function healthyApi(
  meta: Partial<ConsoleMeta> = {},
  me: Me | null = ME,
  extra: Record<string, Route> = {},
) {
  return stubApi({
    "/v1/console/meta": () => json({ ...META, ...meta }),
    "/health": () => json(HEALTH),
    "/v1/console/me": () => (me ? json(me) : UNAUTHENTICATED()),
    ...extra,
  });
}

export { json };
