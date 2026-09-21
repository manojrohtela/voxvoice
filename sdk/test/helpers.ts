// A recording fetch: the HTTP boundary is the only thing mocked in these tests.

export interface Recorded {
  method: string;
  url: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

export type Responder = (request: Recorded) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export function recordingFetch(responder: Responder = () => json({})) {
  const calls: Recorded[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = Object.fromEntries(new Headers(init?.headers).entries());
    const request: Recorded = {
      method: init?.method ?? "GET",
      url,
      path: new URL(url).pathname + new URL(url).search,
      headers,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    calls.push(request);
    return responder(request);
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

// A fixture shaped like a key. It is not a real credential.
export const API_KEY = `vf_${"K".repeat(43)}`;
export const BASE_URL = "http://voxvoice.test";
