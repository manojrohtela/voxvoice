import { describe, expect, it, vi } from "vitest";

import { OFFER_DEADLINE_MS, VoiceCall, type CallBackend, type CallEnvironment, type CallState } from "./call";

// Fakes for the three browser capabilities. The real RTCPeerConnection is exercised end to end
// by the Python suite (`test_console_playground.py`) with a genuine aiortc peer.

class FakeTrack {
  enabled = true;
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

class FakeStream {
  readonly track = new FakeTrack();
  getTracks() {
    return [this.track];
  }
  getAudioTracks() {
    return [this.track];
  }
}

class FakePeer {
  ontrack: ((e: { streams: unknown[] }) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  connectionState = "new";
  iceGatheringState = "complete";
  localDescription: { sdp: string } | null = null;
  remote: string | null = null;
  added = 0;
  closed = false;
  addTrack() {
    this.added += 1;
  }
  async setRemoteDescription(d: { sdp: string }) {
    this.remote = d.sdp;
  }
  async createAnswer() {
    return { type: "answer", sdp: "answer-sdp" };
  }
  async setLocalDescription(d: { sdp: string }) {
    this.localDescription = { sdp: d.sdp };
  }
  addEventListener() {}
  removeEventListener() {}
  close() {
    this.closed = true;
  }
  fireState(state: string) {
    this.connectionState = state;
    this.onconnectionstatechange?.();
  }
}

interface Harness {
  call: VoiceCall;
  states: [CallState, string | undefined][];
  peer: FakePeer;
  stream: FakeStream;
  backend: { [K in keyof CallBackend]: ReturnType<typeof vi.fn> };
  fetchMock: ReturnType<typeof vi.fn>;
  resolveStart: (sessionId: string) => void;
}

function harness(options: { mic?: "ok" | "denied"; offerAfterPolls?: number; start?: "wait" | "reject" } = {}): Harness {
  const peer = new FakePeer();
  const stream = new FakeStream();
  let clock = 0;
  let polls = 0;
  let resolveStart: (sessionId: string) => void = () => undefined;

  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith("/offer")) {
      polls += 1;
      clock += 1_000;
      if (polls < (options.offerAfterPolls ?? 1)) return new Response(null, { status: 204 });
      return new Response(JSON.stringify({ sdp: "offer-sdp" }), { status: 200 });
    }
    if (url.endsWith("/answer")) return new Response(null, { status: 204 });
    return new Response(null, { status: 404 });
  });

  const backend = {
    reserve: vi.fn(async () => ({ rendezvous_id: "rdv_1", signalling_token: "tok_1" })),
    start: vi.fn(
      () =>
        new Promise<{ session_id: string }>((resolve, reject) => {
          if (options.start === "reject") reject(new Error("Agent not found"));
          resolveStart = (id) => resolve({ session_id: id });
        }),
    ),
    end: vi.fn(async () => undefined),
  };

  const env: CallEnvironment = {
    getUserMedia: async () => {
      if (options.mic === "denied") throw new DOMException("denied", "NotAllowedError");
      return stream as unknown as MediaStream;
    },
    createPeerConnection: () => peer as unknown as RTCPeerConnection,
    fetch: fetchMock as unknown as typeof fetch,
    now: () => clock,
  };

  const states: [CallState, string | undefined][] = [];
  const call = new VoiceCall("agt_1", backend, env, {
    onState: (state, detail) => states.push([state, detail]),
    onRemoteStream: () => undefined,
  });
  return { call, states, peer, stream, backend, fetchMock, resolveStart: (id) => resolveStart(id) };
}

async function until(condition: () => boolean) {
  for (let i = 0; i < 100 && !condition(); i++) await Promise.resolve();
  expect(condition()).toBe(true);
}

describe("VoiceCall", () => {
  it("connects through reserve, a concurrent start and the offer/answer exchange", async () => {
    const h = harness();
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/answer")));
    h.resolveStart("ses_1");
    await connecting;

    expect(h.states.map(([s]) => s)).toEqual(["requesting_microphone", "reserving", "connecting", "connected"]);
    expect(h.backend.start).toHaveBeenCalledWith("agt_1", "rdv_1");
    expect(h.peer.remote).toBe("offer-sdp");
    expect(h.peer.added).toBe(1);
    const answer = h.fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/answer"))!;
    const init = answer[1] as RequestInit;
    expect((init.headers as Record<string, string>)["x-signalling-token"]).toBe("tok_1");
    expect(JSON.parse(String(init.body))).toEqual({ sdp: "answer-sdp" });
    expect(h.call.session).toBe("ses_1");
  });

  it("uses only the signalling token on the signalling endpoints", async () => {
    const h = harness({ offerAfterPolls: 2 });
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.length >= 3);
    h.resolveStart("ses_1");
    await connecting;

    for (const [url, init] of h.fetchMock.mock.calls as [string, RequestInit][]) {
      expect(url.startsWith("/v1/webrtc/rendezvous/rdv_1/")).toBe(true);
      expect(Object.keys(init.headers as Record<string, string>)).not.toContain("x-api-key");
    }
  });

  it("hangs up by closing the peer, stopping the microphone and ending the session", async () => {
    const h = harness();
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/answer")));
    h.resolveStart("ses_1");
    await connecting;

    await h.call.hangUp();

    expect(h.peer.closed).toBe(true);
    expect(h.stream.track.stopped).toBe(true);
    expect(h.backend.end).toHaveBeenCalledWith("ses_1");
    expect(h.states.at(-1)?.[0]).toBe("ended");
  });

  it("fails with a useful message when the microphone is denied, and reserves nothing", async () => {
    const h = harness({ mic: "denied" });
    await h.call.connect();

    expect(h.states.at(-1)).toEqual(["failed", "Microphone access was denied. Allow it in the browser and try again."]);
    expect(h.backend.reserve).not.toHaveBeenCalled();
  });

  it("fails at once when the server refuses the session, and releases everything", async () => {
    const h = harness({ start: "reject", offerAfterPolls: Number.MAX_SAFE_INTEGER });
    await h.call.connect();

    expect(h.states.at(-1)).toEqual(["failed", "Agent not found"]);
    expect(h.peer.closed).toBe(true);
    expect(h.stream.track.stopped).toBe(true);
    expect(h.backend.end).not.toHaveBeenCalled(); // no session exists to end
  });

  it("gives up when the runtime never publishes an offer", async () => {
    const h = harness({ offerAfterPolls: Number.MAX_SAFE_INTEGER });
    await h.call.connect();

    expect(h.states.at(-1)).toEqual(["failed", "The runtime did not start the call in time"]);
    const polls = h.fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/offer")).length;
    expect(polls).toBe(OFFER_DEADLINE_MS / 1_000);
  });

  it("ends a session that starts after the user already hung up", async () => {
    const h = harness();
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/answer")));
    await h.call.hangUp();
    h.resolveStart("ses_late");
    await connecting;

    expect(h.backend.end).toHaveBeenCalledWith("ses_late");
    expect(h.states.at(-1)?.[0]).toBe("ended");
  });

  it("reports a lost connection and ends the session", async () => {
    const h = harness();
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/answer")));
    h.resolveStart("ses_1");
    await connecting;

    h.peer.fireState("failed");
    await until(() => h.states.at(-1)?.[0] === "failed");

    expect(h.states.at(-1)?.[1]).toBe("The connection to the runtime was lost");
    expect(h.backend.end).toHaveBeenCalledWith("ses_1");
  });

  it("mutes by disabling the microphone track", async () => {
    const h = harness();
    const connecting = h.call.connect();
    await until(() => h.fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/answer")));
    h.resolveStart("ses_1");
    await connecting;

    h.call.setMuted(true);
    expect(h.stream.track.enabled).toBe(false);
    h.call.setMuted(false);
    expect(h.stream.track.enabled).toBe(true);
  });
});
