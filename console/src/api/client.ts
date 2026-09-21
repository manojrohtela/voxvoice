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

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      headers: { accept: "application/json" },
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network", "Cannot reach the VoxVoice API. Is the server running?");
  }

  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw toApiError(response.status, body, response.headers.get("x-request-id") ?? undefined);
  }
  if (body === undefined) {
    throw new ApiError(response.status, "invalid_response", "The server returned an unreadable response");
  }
  return body as T;
}
