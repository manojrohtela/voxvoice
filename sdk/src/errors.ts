/**
 * The one error the SDK throws for anything the API or the network did (a cancelled request
 * keeps the platform's own `AbortError`, so cancellation reads the way it does everywhere else).
 *
 * It carries only what the server actually returns. The developer API answers errors with
 * `{"detail": <string | validation list>}` and no request id, so there is no `code` and no
 * `requestId` here. The API key never appears in an error: it travels only as a request header.
 */
export class VoxVoiceError extends Error {
  override readonly name = "VoxVoiceError";

  /** The HTTP status, or `null` when no response arrived (network failure or timeout). */
  readonly status: number | null;

  /**
   * The server's `detail`, exactly as returned: a string (`"Agent not found"`), or for `422` the
   * validation list `[{type, loc, msg, input, ...}]`. `undefined` when the body was not JSON.
   * Note that FastAPI echoes the offending *request body* value in `input`.
   */
  readonly detail: unknown;

  constructor(message: string, status: number | null, detail?: unknown, options?: { cause?: unknown }) {
    super(message, options);
    this.status = status;
    this.detail = detail;
  }
}

interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

/** A readable one-line message from a response body, never from the request. */
export function messageFor(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const parts = (detail as ValidationIssue[]).map((issue) => {
      const where = Array.isArray(issue.loc) ? issue.loc.join(".") : "request";
      return `${where}: ${typeof issue.msg === "string" ? issue.msg : "invalid"}`;
    });
    return `Invalid request (${parts.join("; ")})`;
  }
  return `VoxVoice API request failed with status ${status}`;
}
