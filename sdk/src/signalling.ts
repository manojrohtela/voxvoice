/**
 * `@voxvoice/sdk/signalling`: the browser-side half of connecting a caller over WebRTC.
 *
 * It holds a **signalling token**, never an API key: your server gets `{rendezvous_id,
 * signalling_token}` from `client.webrtc.createRendezvous()` and hands them to the browser. The token
 * can exchange one offer/answer for one session and nothing else, and it expires. This module
 * imports nothing that knows about API keys.
 *
 * It only carries SDP. Creating the `RTCPeerConnection`, applying the offer, creating the answer and
 * playing audio are the browser's own WebRTC API; the SDK does not implement WebRTC.
 */

import { type Fetch, normalizeBaseUrl, type RequestOptions, segment, Transport } from "./http.js";
import type { Offer } from "./types.js";

export { VoxVoiceError } from "./errors.js";
export type { RequestOptions } from "./http.js";
export type { Offer } from "./types.js";

export interface SignallingClientOptions {
  /** Your VoxVoice server, without `/v1`. */
  baseUrl: string;
  rendezvousId: string;
  signallingToken: string;
  /** Advanced: a `fetch` implementation (defaults to the global one). */
  fetch?: Fetch;
}

export class SignallingClient {
  readonly #http: Transport;
  readonly #path: string;

  constructor(options: SignallingClientOptions) {
    if (!options?.signallingToken) throw new TypeError("signallingToken is required");
    const fetchImpl = options.fetch ?? globalThis.fetch;
    if (typeof fetchImpl !== "function") throw new TypeError("no fetch implementation is available");
    this.#http = new Transport({
      baseUrl: normalizeBaseUrl(options.baseUrl),
      headers: { "x-signalling-token": options.signallingToken },
      fetch: fetchImpl,
      timeoutMs: undefined,
    });
    this.#path = `/v1/webrtc/rendezvous/${segment(options.rendezvousId)}`;
  }

  /**
   * `GET …/offer`: the runtime's offer, or `null` when it is not ready yet (`204`). The server
   * holds each call open for a bounded time, so call again on `null` until the offer arrives.
   */
  getOffer(options?: RequestOptions): Promise<Offer | null> {
    return this.#http.request("GET", `${this.#path}/offer`, undefined, options);
  }

  /** `POST …/answer` with the browser's answer SDP. Accepted once. */
  async sendAnswer(sdp: string, options?: RequestOptions): Promise<void> {
    await this.#http.request("POST", `${this.#path}/answer`, { sdp }, options);
  }
}
