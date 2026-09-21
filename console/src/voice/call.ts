// A browser voice call over the existing M11 WebRTC path (M12.4, LLD §5).
//
// A port of the flow `platform/api/webrtc/client.html` proved against the real runtime:
//   microphone → reserve a signalling slot → start the session AND answer the runtime's offer
//   concurrently (the start call only returns once the peer connection is usable) → play the
//   assistant's audio → hang up.
// The one difference is who authorizes it: the `CallBackend` (the console's session-cookie routes)
// instead of an org API key in the page. The only credential this module ever holds is the
// single-purpose signalling token (security model S1).
//
// Framework-free and dependency-injected, so it is testable without a browser and can move to
// `sdk/` as the TypeScript client core (M12.9) unchanged.

export type CallState =
  | "idle"
  | "requesting_microphone"
  | "reserving"
  | "connecting"
  | "connected"
  | "ending"
  | "ended"
  | "failed";

export interface Rendezvous {
  rendezvous_id: string;
  signalling_token: string;
}

/** Who authorizes the call. The console implements it with its cookie-authenticated routes. */
export interface CallBackend {
  reserve(): Promise<Rendezvous>;
  start(agentId: string, rendezvousId: string): Promise<{ session_id: string }>;
  end(sessionId: string): Promise<void>;
}

/** The browser capabilities the call needs; injected so tests can supply fakes. */
export interface CallEnvironment {
  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
  createPeerConnection(): RTCPeerConnection;
  fetch: typeof fetch;
  now(): number;
}

export interface CallEvents {
  onState(state: CallState, detail?: string): void;
  onRemoteStream(stream: MediaStream): void;
  onLog?(message: string): void;
}

/** How long the runtime has to publish its offer. The server bounds each poll itself. */
export const OFFER_DEADLINE_MS = 30_000;

export class CallError extends Error {}

export function browserEnvironment(): CallEnvironment {
  return {
    getUserMedia: (constraints) => {
      if (!navigator.mediaDevices?.getUserMedia) {
        return Promise.reject(
          new CallError("This browser cannot access a microphone here. Use http://localhost or HTTPS."),
        );
      }
      return navigator.mediaDevices.getUserMedia(constraints);
    },
    // No ICE servers: local peers connect over host candidates (ADR-0054, local-run §4).
    createPeerConnection: () => new RTCPeerConnection(),
    fetch: (...args) => fetch(...args),
    now: () => Date.now(),
  };
}

export class VoiceCall {
  private state: CallState = "idle";
  private mic: MediaStream | null = null;
  private peer: RTCPeerConnection | null = null;
  private sessionId: string | null = null;
  private muted = false;
  private released = false;

  constructor(
    private readonly agentId: string,
    private readonly backend: CallBackend,
    private readonly env: CallEnvironment,
    private readonly events: CallEvents,
  ) {}

  get currentState(): CallState {
    return this.state;
  }

  get session(): string | null {
    return this.sessionId;
  }

  async connect(): Promise<void> {
    if (this.state !== "idle") throw new CallError("A call can only be connected once");
    try {
      this.set("requesting_microphone");
      this.mic = await this.env.getUserMedia({ audio: true, video: false }).catch((error: unknown) => {
        if (error instanceof CallError) throw error;
        throw new CallError("Microphone access was denied. Allow it in the browser and try again.");
      });
      this.log("Microphone ready");

      this.set("reserving");
      const rendezvous = await this.backend.reserve();
      this.log("Signalling slot reserved");

      this.set("connecting");
      const peer = this.env.createPeerConnection();
      this.peer = peer;
      for (const track of this.mic.getTracks()) peer.addTrack(track, this.mic);
      peer.ontrack = (event) => {
        const stream = event.streams[0];
        if (stream) this.events.onRemoteStream(stream);
        this.log("Receiving the agent's audio");
      };
      peer.onconnectionstatechange = () => this.onPeerState(peer.connectionState);

      // Concurrent by contract: `start` resolves only once this page has answered the offer that
      // the very same call makes the runtime publish.
      const started = this.backend.start(this.agentId, rendezvous.rendezvous_id);
      // A refused start (unknown agent, runtime down) publishes no offer: fail at once rather
      // than waiting out the offer deadline.
      const startRefused = new Promise<never>((_, reject) => {
        started.catch(reject);
      });
      await Promise.race([this.answerOffer(peer, rendezvous), startRefused]);
      const { session_id } = await started;
      if (this.released) {
        // Hung up while connecting: the session started anyway, so end it rather than leak it.
        await this.backend.end(session_id).catch(() => undefined);
        return;
      }
      this.sessionId = session_id;
      this.log(`Connected · session ${session_id}`);
      // Read through the getter: the state may have changed while awaiting (hang-up, peer loss).
      if (this.currentState === "connecting") this.set("connected");
    } catch (error) {
      await this.release();
      if (this.currentState === "ending" || this.currentState === "ended") return; // hung up
      this.set("failed", error instanceof Error ? error.message : "The call could not be connected");
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    for (const track of this.mic?.getAudioTracks() ?? []) track.enabled = !muted;
  }

  get isMuted(): boolean {
    return this.muted;
  }

  async hangUp(): Promise<void> {
    if (this.state === "ended" || this.state === "failed" || this.state === "idle") return;
    this.set("ending");
    await this.release();
    this.set("ended");
  }

  private async answerOffer(peer: RTCPeerConnection, rendezvous: Rendezvous): Promise<void> {
    const offer = await this.pollOffer(rendezvous);
    this.log("Offer received");
    await peer.setRemoteDescription({ type: "offer", sdp: offer });
    await peer.setLocalDescription(await peer.createAnswer());
    await waitForIceGathering(peer);
    const response = await this.env.fetch(`/v1/webrtc/rendezvous/${rendezvous.rendezvous_id}/answer`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-signalling-token": rendezvous.signalling_token },
      body: JSON.stringify({ sdp: peer.localDescription?.sdp ?? "" }),
    });
    if (!response.ok) throw new CallError(`The runtime refused the answer (HTTP ${response.status})`);
    this.log("Answer sent");
  }

  private async pollOffer(rendezvous: Rendezvous): Promise<string> {
    // 204 means "not yet"; each request waits server-side, so this is a few polls, not a spin.
    const deadline = this.env.now() + OFFER_DEADLINE_MS;
    while (this.env.now() < deadline && !this.released) {
      const response = await this.env.fetch(`/v1/webrtc/rendezvous/${rendezvous.rendezvous_id}/offer`, {
        headers: { "x-signalling-token": rendezvous.signalling_token },
      });
      if (response.status === 200) return ((await response.json()) as { sdp: string }).sdp;
      if (response.status !== 204) throw new CallError(`The runtime's offer could not be fetched (HTTP ${response.status})`);
    }
    throw new CallError("The runtime did not start the call in time");
  }

  private onPeerState(state: RTCPeerConnectionState): void {
    this.log(`Peer ${state}`);
    if ((state === "failed" || state === "closed") && (this.state === "connected" || this.state === "connecting")) {
      void this.release().then(() => this.set("failed", "The connection to the runtime was lost"));
    }
  }

  /** Stop the microphone, close the peer and end the session (if it started). Never throws. */
  private async release(): Promise<void> {
    this.released = true;
    const sessionId = this.sessionId;
    this.sessionId = null;
    if (this.peer) {
      this.peer.onconnectionstatechange = null;
      this.peer.close();
      this.peer = null;
    }
    for (const track of this.mic?.getTracks() ?? []) track.stop();
    this.mic = null;
    if (sessionId) {
      try {
        await this.backend.end(sessionId);
        this.log("Session ended");
      } catch {
        this.log("The session could not be ended cleanly; the runtime will time it out");
      }
    }
  }

  private set(state: CallState, detail?: string): void {
    this.state = state;
    this.events.onState(state, detail);
  }

  private log(message: string): void {
    this.events.onLog?.(message);
  }
}

function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  // aiortc does not trickle and neither do we: one SDP carries every candidate.
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (peer.iceGatheringState === "complete") {
        peer.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    peer.addEventListener("icegatheringstatechange", check);
  });
}
