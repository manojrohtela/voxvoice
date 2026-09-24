import { type Fetch, normalizeBaseUrl, Transport } from "./http.js";
import { Agents } from "./resources/agents.js";
import { Sessions } from "./resources/sessions.js";
import { WebRTC } from "./resources/webrtc.js";

export interface VoxVoiceClientOptions {
  /**
   * Your organization's developer API key. Read it from your server's environment or secret
   * manager; never ship it to a browser. The client keeps it in memory only and sends it only as
   * the `x-api-key` header to `baseUrl`.
   */
  apiKey: string;
  /** Your VoxVoice server, without `/v1`, e.g. `http://localhost:8000`. */
  baseUrl: string;
  /** Default per-request timeout. None by default (the WebRTC session start waits for a caller). */
  timeoutMs?: number;
  /** Advanced: a `fetch` implementation (defaults to the global one). */
  fetch?: Fetch;
}

/**
 * The VoxVoice developer API client. Server-side: it holds an API key.
 *
 * Configuration is fixed at construction and resources hold no per-request state, so one client
 * can serve concurrent calls. Nothing is retried: a failed request fails once, so a create is never
 * sent twice.
 */
export class VoxVoiceClient {
  readonly agents: Agents;
  readonly sessions: Sessions;
  readonly webrtc: WebRTC;

  constructor(options: VoxVoiceClientOptions) {
    if (!options || typeof options.apiKey !== "string" || options.apiKey.trim() === "") {
      throw new TypeError("apiKey is required (read it from your server's environment, e.g. VOXVOICE_API_KEY)");
    }
    if (options.timeoutMs !== undefined && !(options.timeoutMs > 0)) {
      throw new TypeError("timeoutMs must be a positive number");
    }
    // Bound to `globalThis`: a browser's `fetch` throws "Illegal invocation" when it is called as
    // anything but a method of `window` (found by the M12.10 browser run; Node does not mind).
    const fetchImpl = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (typeof fetchImpl !== "function") {
      throw new TypeError("no fetch implementation is available (Node.js 20.3 or later provides one)");
    }
    const http = new Transport({
      baseUrl: normalizeBaseUrl(options.baseUrl),
      headers: { "x-api-key": options.apiKey },
      fetch: fetchImpl,
      timeoutMs: options.timeoutMs,
    });
    this.agents = new Agents(http);
    this.sessions = new Sessions(http);
    this.webrtc = new WebRTC(http);
  }
}
