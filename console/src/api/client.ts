// The console's only door to the API. Same-origin requests, typed results, and one error type.
//
// Two error shapes arrive here and both become an ApiError:
//   console API   {"error": {"type", "message", "request_id"}}   (ADR-0055 DP7)
//   developer API {"detail": "..."} or {"detail": [validation issues]}   (M11, unchanged)

export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly requestId: string | undefined;

  constructor(status: number, type: string, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.type = type;
    this.requestId = requestId;
  }
}

const TYPE_BY_STATUS: Record<number, string> = {
  400: "invalid_request",
  401: "unauthenticated",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  422: "invalid_request",
  429: "rate_limited",
  503: "runtime_unavailable",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function toApiError(status: number, body: unknown, headerRequestId?: string): ApiError {
  const fallbackType = TYPE_BY_STATUS[status] ?? (status >= 500 ? "internal" : "http_error");

  if (isRecord(body) && isRecord(body.error)) {
    const { type, message, request_id: requestId } = body.error;
    return new ApiError(
      status,
      typeof type === "string" ? type : fallbackType,
      typeof message === "string" ? message : `Request failed (${status})`,
      typeof requestId === "string" ? requestId : headerRequestId,
    );
  }
  if (isRecord(body) && typeof body.detail === "string") {
    return new ApiError(status, fallbackType, body.detail, headerRequestId);
  }
  if (isRecord(body) && Array.isArray(body.detail)) {
    return new ApiError(status, "invalid_request", "The request was not valid", headerRequestId);
  }
  return new ApiError(status, fallbackType, `Request failed (${status})`, headerRequestId);
}

/** Sent on every console mutation; the server refuses mutations without it (CSRF guard). */
export const CONSOLE_HEADER = "x-voxvoice-console";

type Method = "GET" | "POST" | "PATCH";

async function request<T>(
  method: Method,
  path: string,
  options: { body?: unknown; signal?: AbortSignal; expectBody?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (method !== "GET") headers[CONSOLE_HEADER] = "1";
  if (options.body !== undefined) headers["content-type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
      ...(options.signal ? { signal: options.signal } : {}),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network", "Cannot reach the VoxVoice API. Is the server running?");
  }

  if (response.status === 204 && options.expectBody === false) return undefined as T;
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw toApiError(response.status, body, response.headers.get("x-request-id") ?? undefined);
  }
  if (body === undefined) {
    throw new ApiError(response.status, "invalid_response", "The server returned an unreadable response");
  }
  return body as T;
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>("GET", path, signal ? { signal } : {});
}

export function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("POST", path, body === undefined ? {} : { body });
}

export function patchJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>("PATCH", path, { body });
}

/** A mutation whose success is a `204 No Content`. */
export function postNoContent(path: string): Promise<void> {
  return request<void>("POST", path, { expectBody: false });
}
