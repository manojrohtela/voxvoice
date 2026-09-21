import { segment, type RequestOptions, type Transport } from "../http.js";
import type { Session, SessionCreate, SessionCreated, SessionEnded, TranscriptItem } from "../types.js";

/** `/v1/sessions`. Every call is scoped to the API key's organization. */
export class Sessions {
  readonly #http: Transport;

  constructor(http: Transport) {
    this.#http = http;
  }

  /**
   * `POST /v1/sessions`. This starts a session, but gives no caller a way to join it: to connect a
   * browser with audio, use `client.webrtc.startSession()` instead.
   */
  create(body: SessionCreate, options?: RequestOptions): Promise<SessionCreated> {
    return this.#http.request("POST", "/v1/sessions", body, options);
  }

  /** `GET /v1/sessions/{id}`. There is no endpoint that lists sessions. */
  get(sessionId: string, options?: RequestOptions): Promise<Session> {
    return this.#http.request("GET", `/v1/sessions/${segment(sessionId)}`, undefined, options);
  }

  /** `GET /v1/sessions/{id}/transcript`: final lines, ordered by `ts`. */
  transcript(sessionId: string, options?: RequestOptions): Promise<TranscriptItem[]> {
    return this.#http.request("GET", `/v1/sessions/${segment(sessionId)}/transcript`, undefined, options);
  }

  /** `POST /v1/sessions/{id}/end`. Safe to repeat. */
  end(sessionId: string, options?: RequestOptions): Promise<SessionEnded> {
    return this.#http.request("POST", `/v1/sessions/${segment(sessionId)}/end`, undefined, options);
  }
}
