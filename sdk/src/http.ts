// Internal: the one HTTP transport every resource uses. Not exported from the package.

import { messageFor, VoxVoiceError } from "./errors.js";

/** Per-call options every method accepts. */
export interface RequestOptions {
  /** Cancel the request. The rejection is the platform's `AbortError`. */
  signal?: AbortSignal;
  /** Fail with a `VoxVoiceError` (status `null`) if no response arrives in time. Overrides the client default. */
  timeoutMs?: number;
}

export type Fetch = typeof globalThis.fetch;

export interface TransportConfig {
  baseUrl: string;
  /** Extra headers on every request (the credential header, for example). */
  headers: Readonly<Record<string, string>>;
  fetch: Fetch;
  timeoutMs: number | undefined;
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";

/**
 * Normalizes a base URL to its origin-plus-prefix without a trailing slash, and refuses one that
 * already ends in `/v1` (every path this SDK sends starts with `/v1`, so it would be doubled).
 */
export function normalizeBaseUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new TypeError("baseUrl must be an absolute http(s) URL, e.g. http://localhost:8000");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("baseUrl must use http or https");
  }
  if (url.search || url.hash || url.username || url.password) {
    throw new TypeError("baseUrl must not contain credentials, a query or a fragment");
  }
  const base = `${url.origin}${url.pathname}`.replace(/\/+$/, "");
  if (/\/v1$/.test(base)) {
    throw new TypeError("baseUrl must not include /v1 (the SDK adds it), e.g. http://localhost:8000");
  }
  return base;
}

/** Encodes one path segment (ids are opaque; they must never change the path). */
export function segment(value: string): string {
  if (typeof value !== "string" || value === "") throw new TypeError("an id must be a non-empty string");
  return encodeURIComponent(value);
}

export class Transport {
  readonly #config: TransportConfig;

  constructor(config: TransportConfig) {
    this.#config = config;
  }

  async request<T>(method: Method, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { accept: "application/json", ...this.#config.headers };
    if (body !== undefined) headers["content-type"] = "application/json";

    const timeoutMs = options.timeoutMs ?? this.#config.timeoutMs;
    const timeout = timeoutMs !== undefined ? AbortSignal.timeout(timeoutMs) : undefined;
    const signal =
      options.signal && timeout ? AbortSignal.any([options.signal, timeout]) : (options.signal ?? timeout);

    let response: Response;
    try {
      response = await this.#config.fetch(`${this.#config.baseUrl}${path}`, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
      });
    } catch (cause) {
      if (options.signal?.aborted) throw cause; // the caller cancelled: keep the platform's AbortError
      if (timeout?.aborted) {
        throw new VoxVoiceError(`VoxVoice API request timed out after ${timeoutMs} ms`, null, undefined, { cause });
      }
      throw new VoxVoiceError("Could not reach the VoxVoice API", null, undefined, { cause });
    }

    if (response.status === 204) return null as T;
    const text = await response.text();
    let parsed: unknown;
    try {
      parsed = text ? JSON.parse(text) : undefined;
    } catch {
      parsed = undefined;
    }
    if (!response.ok) {
      const detail =
        parsed && typeof parsed === "object" && "detail" in parsed ? (parsed as { detail: unknown }).detail : undefined;
      throw new VoxVoiceError(messageFor(response.status, detail), response.status, detail);
    }
    if (parsed === undefined || parsed === null || typeof parsed !== "object") {
      throw new VoxVoiceError(
        `The VoxVoice API returned an unreadable response (status ${response.status})`,
        response.status,
      );
    }
    return parsed as T;
  }
}
