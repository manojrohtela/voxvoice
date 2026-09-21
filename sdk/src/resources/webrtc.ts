import type { RequestOptions, Transport } from "../http.js";
import type { Rendezvous, SessionCreated, WebRTCSessionCreate } from "../types.js";

/**
 * The server-side half of connecting a caller's browser (`/v1/webrtc/*` with the API key).
 * Available only when the server has voice enabled; otherwise these calls fail with 404.
 *
 * The browser-side half — fetching the offer and sending the answer with the signalling token —
 * is `SignallingClient` from `@voxvoice/sdk/signalling`, which never sees the API key. Creating the
 * `RTCPeerConnection` itself is the browser's job; this SDK does not implement WebRTC.
 */
export class WebRTC {
  readonly #http: Transport;

  constructor(http: Transport) {
    this.#http = http;
  }

  /** `POST /v1/webrtc/rendezvous`: pass `rendezvous_id` and `signalling_token` to the browser. */
  createRendezvous(options?: RequestOptions): Promise<Rendezvous> {
    return this.#http.request("POST", "/v1/webrtc/rendezvous", undefined, options);
  }

  /**
   * `POST /v1/webrtc/sessions`. **Resolves only once the browser is connected**, which requires the
   * browser to fetch the offer and answer it meanwhile — so do not await it before handing the
   * browser its token, and give it a generous `timeoutMs` if you set one.
   */
  startSession(body: WebRTCSessionCreate, options?: RequestOptions): Promise<SessionCreated> {
    return this.#http.request("POST", "/v1/webrtc/sessions", body, options);
  }
}
