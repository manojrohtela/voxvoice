import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { AgentView } from "../../api/types";
import { AuthProvider } from "../../app/auth";
import { MetaProvider } from "../../app/meta";
import { ME, healthyApi, json } from "../../test/fakeApi";
import type { CallEnvironment } from "../../voice/call";
import { DiagnosticsPanel, RENDER_LIMIT } from "./DiagnosticsPanel";
import {
  EMPTY,
  followDiagnostics,
  userFacingError,
  type DiagnosticEvent,
  type DiagnosticsSnapshot,
} from "./diagnostics";
import { LiveConversation, RuntimeStatePanel } from "./LiveConversation";
import { PlaygroundPage } from "./PlaygroundPage";

// ── a fake EventSource, so the stream can be driven without a browser ─────────

class FakeStream implements EventSource {
  static opened: FakeStream[] = [];
  readonly url: string;
  readonly listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
  onerror: ((this: EventSource, ev: Event) => unknown) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeStream.opened.push(this);
  }
  addEventListener(name: string, handler: unknown) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), handler as never]);
  }
  removeEventListener() {}
  dispatchEvent() {
    return true;
  }
  close() {
    this.closed = true;
  }
  /** Deliver one server frame. */
  send(name: string, body: unknown) {
    for (const handler of this.listeners.get(name) ?? []) {
      handler(new MessageEvent(name, { data: JSON.stringify(body) }));
    }
  }
  fail() {
    this.onerror?.call(this as unknown as EventSource, new Event("error"));
  }
  // Unused EventSource surface.
  readonly CONNECTING = 0 as const;
  readonly OPEN = 1 as const;
  readonly CLOSED = 2 as const;
  readyState = 1;
  withCredentials = true;
  onmessage = null;
  onopen = null;
}

function event(over: Partial<DiagnosticEvent> & { seq: number }): DiagnosticEvent {
  return {
    offset_s: over.seq * 0.1,
    ts: 1_700_000_000 + over.seq,
    type: "turn.started",
    category: "TURN",
    severity: "info",
    message: "Turn started",
    turn: null,
    detail: {},
    ...over,
  };
}

const batch = (events: DiagnosticEvent[], over: Record<string, unknown> = {}) => ({
  events,
  transcript: [],
  transcript_ordered: true,
  state: "LISTENING",
  last_seq: events.at(-1)?.seq ?? 0,
  ...over,
});

// ── the stream client ─────────────────────────────────────────────────────────

describe("Following a session's diagnostics", () => {
  it("accumulates batches in sequence order", () => {
    FakeStream.opened = [];
    const seen: DiagnosticsSnapshot[] = [];
    followDiagnostics("ses_1", (s) => seen.push(s), (url) => new FakeStream(url));
    const stream = FakeStream.opened[0]!;

    stream.send("batch", batch([event({ seq: 1 }), event({ seq: 2 })]));
    stream.send("batch", batch([event({ seq: 3 })]));

    expect(seen.at(-1)!.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(seen.at(-1)!.state).toBe("LISTENING");
  });

  it("never duplicates or reorders an event that arrives twice", () => {
    FakeStream.opened = [];
    const seen: DiagnosticsSnapshot[] = [];
    followDiagnostics("ses_1", (s) => seen.push(s), (url) => new FakeStream(url));
    const stream = FakeStream.opened[0]!;

    stream.send("batch", batch([event({ seq: 2 }), event({ seq: 1 })]));
    stream.send("batch", batch([event({ seq: 1 }), event({ seq: 3 })]));

    expect(seen.at(-1)!.events.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("resumes from the last sequence it saw, so nothing is lost or repeated", async () => {
    vi.useFakeTimers();
    try {
      FakeStream.opened = [];
      const seen: DiagnosticsSnapshot[] = [];
      followDiagnostics("ses_1", (s) => seen.push(s), (url) => new FakeStream(url), 10);
      FakeStream.opened[0]!.send("batch", batch([event({ seq: 1 }), event({ seq: 7 })]));

      FakeStream.opened[0]!.fail();
      expect(seen.at(-1)!.streamError).toMatch(/Reconnecting/);
      vi.advanceTimersByTime(20);

      expect(FakeStream.opened).toHaveLength(2);
      expect(FakeStream.opened[1]!.url).toContain("after_seq=7");
      FakeStream.opened[1]!.send("batch", batch([event({ seq: 8 })]));
      expect(seen.at(-1)!.events.map((e) => e.seq)).toEqual([1, 7, 8]);
      expect(seen.at(-1)!.streamError).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops following once the server says the session is complete", () => {
    FakeStream.opened = [];
    const seen: DiagnosticsSnapshot[] = [];
    followDiagnostics("ses_1", (s) => seen.push(s), (url) => new FakeStream(url));
    const stream = FakeStream.opened[0]!;

    stream.send("batch", batch([event({ seq: 1 })]));
    stream.send("end", { state: "ENDED", last_seq: 1 });

    expect(seen.at(-1)!.complete).toBe(true);
    expect(stream.closed).toBe(true);
    stream.fail(); // an error after "end" must not reopen anything
    expect(FakeStream.opened).toHaveLength(1);
  });

  it("ignores a frame it cannot parse rather than losing the session", () => {
    FakeStream.opened = [];
    const seen: DiagnosticsSnapshot[] = [];
    followDiagnostics("ses_1", (s) => seen.push(s), (url) => new FakeStream(url));
    const stream = FakeStream.opened[0]!;
    stream.send("batch", batch([event({ seq: 1 })]));

    for (const handler of stream.listeners.get("batch") ?? []) {
      handler(new MessageEvent("batch", { data: "{not json" }));
    }

    expect(seen.at(-1)!.events.map((e) => e.seq)).toEqual([1]);
  });

  it("does not break the call when the stream cannot be opened at all", () => {
    const seen: DiagnosticsSnapshot[] = [];
    expect(() =>
      followDiagnostics("ses_1", (s) => seen.push(s), () => {
        throw new ReferenceError("EventSource is not defined");
      }),
    ).not.toThrow();
    expect(seen.at(-1)!.streamError).toMatch(/not available/);
  });

  it("closes the stream when the caller stops following", () => {
    FakeStream.opened = [];
    const stream = followDiagnostics("ses_1", () => {}, (url) => new FakeStream(url));
    stream.close();
    expect(FakeStream.opened[0]!.closed).toBe(true);
  });
});

// ── user-facing error wording ─────────────────────────────────────────────────

describe("User-facing errors", () => {
  it("says nothing when nothing failed", () => {
    expect(userFacingError([event({ seq: 1 })])).toBeNull();
  });

  it("explains a failure without leaking how it is implemented", () => {
    const message = userFacingError([
      event({ seq: 1 }),
      event({
        seq: 2,
        type: "runtime.error",
        severity: "error",
        message: "Runtime error",
        detail: { reason: "stt_stream_failed" },
      }),
    ]);
    expect(message).toMatch(/could not continue this call/i);
    expect(message).not.toMatch(/stt_stream_failed/);
  });
});

// ── the panel ─────────────────────────────────────────────────────────────────

describe("Diagnostics panel", () => {
  it("says where events will appear before any arrive", () => {
    render(<DiagnosticsPanel events={[]} streamError={null} complete={false} />);
    expect(screen.getByText(/Runtime events appear here/)).toBeTruthy();
  });

  it("renders time, severity, category and message for each event", () => {
    render(
      <DiagnosticsPanel
        events={[
          event({ seq: 1, category: "SESSION", message: "Session started" }),
          event({ seq: 2, category: "TTS", severity: "error", message: "Media failed" }),
        ]}
        streamError={null}
        complete={false}
      />,
    );
    const log = screen.getByRole("list", { name: "Runtime diagnostics" });
    expect(within(log).getByText("Session started")).toBeTruthy();
    expect(within(log).getByText("ERROR")).toBeTruthy();
    expect(within(log).getByText("Media failed")).toBeTruthy();
  });

  it("shows measured stage durations and omits the ones that were not measured", () => {
    render(
      <DiagnosticsPanel
        events={[
          event({
            seq: 1,
            type: "metric.turn",
            category: "LATENCY",
            message: "Turn latency measured",
            stages_ms: { stt: 310, llm: 710 },
            voice_to_voice_ms: 1420,
          }),
        ]}
        streamError={null}
        complete={false}
      />,
    );
    expect(screen.getByText("1420 ms")).toBeTruthy();
    expect(screen.getByText("stt 310")).toBeTruthy();
    expect(screen.queryByText(/tts/)).toBeNull();
    expect(screen.queryByText(/ 0$/)).toBeNull();
  });

  it("expands one event to its structured detail", () => {
    render(
      <DiagnosticsPanel
        events={[event({ seq: 9, turn: 3, detail: { reason: "stt_stream_failed" } })]}
        streamError={null}
        complete={false}
      />,
    );
    const expand = screen.getByRole("button", { name: "Details" });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(expand);
    expect(screen.getByText("stt_stream_failed")).toBeTruthy();
    expect(screen.getByText("turn.started")).toBeTruthy();
    expect(screen.getByText("9")).toBeTruthy();
  });

  it("keeps the DOM bounded for a very large log and says what it is showing", () => {
    const many = Array.from({ length: 5000 }, (_, n) => event({ seq: n + 1 }));
    render(<DiagnosticsPanel events={many} streamError={null} complete={false} />);

    const log = screen.getByRole("list", { name: "Runtime diagnostics" });
    expect(within(log).getAllByRole("listitem")).toHaveLength(RENDER_LIMIT);
    expect(screen.getByText(/Showing the most recent 300 of 5000/)).toBeTruthy();
    expect(screen.getByText("5000 events")).toBeTruthy();
  });

  it("clears the view without implying the server forgot anything", () => {
    render(
      <DiagnosticsPanel events={[event({ seq: 1 }), event({ seq: 2 })]} streamError={null} complete={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear view" }));
    expect(screen.queryByRole("list", { name: "Runtime diagnostics" })?.children.length ?? 0).toBe(0);
    expect(screen.getByText(/the server keeps them/)).toBeTruthy();
  });

  it("explains a dropped stream as a view problem, not a lost session", () => {
    render(<DiagnosticsPanel events={[]} streamError="Reconnecting to the runtime…" complete={false} />);
    expect(screen.getByRole("status").textContent).toMatch(/Nothing is lost/);
  });
});

// ── conversation and runtime state ────────────────────────────────────────────

describe("Live conversation", () => {
  it("shows what was heard and said, and is explicit about interim text", () => {
    render(
      <LiveConversation
        transcript={[
          { role: "user", text: "I want to book an appointment.", offset_s: 2.0 },
          { role: "assistant", text: "What date suits you?", offset_s: 3.1 },
        ]}
        ordered
        state="LISTENING"
      />,
    );
    expect(screen.getByText("I want to book an appointment.")).toBeTruthy();
    expect(screen.getByText("Caller")).toBeTruthy();
    expect(screen.getByText("Agent")).toBeTruthy();
    expect(screen.getByText(/does not record interim recognition/)).toBeTruthy();
  });

  it("does not pretend a silent session said something", () => {
    render(<LiveConversation transcript={[]} ordered state="ENDED" />);
    expect(screen.getByText("Nothing was said in this session.")).toBeTruthy();
  });

  it.each([
    ["LISTENING", "Listening"],
    ["USER_SPEAKING", "Caller speaking"],
    ["PROCESSING", "Processing"],
    ["ASSISTANT_SPEAKING", "Agent speaking"],
    ["ERROR", "Error"],
    ["ENDED", "Ended"],
  ] as const)("shows the runtime state %s", (state, label) => {
    render(<RuntimeStatePanel state={state} turn={2} />);
    const panel = screen.getByRole("article", { name: "Runtime" });
    // The label appears on the status pill; for the four turn states it also names the current
    // step, so both are legitimate and only presence is asserted.
    expect(within(panel).getAllByText(label).length).toBeGreaterThan(0);
    expect(within(panel).getByText("Turn 2")).toBeTruthy();
  });
});

// ── in the Playground ─────────────────────────────────────────────────────────

const AGENT: AgentView = {
  id: "agt_1",
  name: "Support",
  system_prompt: "Be brief.",
  first_message: null,
  language: "en",
  language_supported: true,
  llm_model: null,
  tts_voice: null,
  created_at: "2026-10-07T00:00:00",
  updated_at: "2026-10-07T00:00:00",
};

function fakeBrowser(): CallEnvironment {
  const track = { enabled: true, stop: () => undefined };
  const media = { getTracks: () => [track], getAudioTracks: () => [track] };
  const peer = {
    ontrack: null,
    onconnectionstatechange: null,
    connectionState: "new",
    iceGatheringState: "complete",
    localDescription: { sdp: "answer" },
    addTrack: () => undefined,
    setRemoteDescription: async () => undefined,
    createAnswer: async () => ({ type: "answer", sdp: "answer" }),
    setLocalDescription: async () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    close: () => undefined,
  };
  return {
    getUserMedia: async () => media as unknown as MediaStream,
    createPeerConnection: () => peer as unknown as RTCPeerConnection,
    fetch: (async (url: string) =>
      String(url).endsWith("/offer")
        ? new Response(JSON.stringify({ sdp: "offer" }), { status: 200 })
        : new Response(null, { status: 204 })) as typeof fetch,
    now: () => 0,
  };
}

describe("Diagnostics in the Playground", () => {
  it("follows the session once the call connects, and shows what the runtime reports", async () => {
    healthyApi(
      { runtime: { transport: "webrtc", webrtc_signalling: true, ice_policy: "none" } },
      ME,
      {
        "/v1/console/agents": () => json({ data: [AGENT] }),
        "POST /v1/console/playground/rendezvous": () =>
          json({ rendezvous_id: "rdv_1", signalling_token: "tok_1", expires_in_s: 30 }, 201),
        "POST /v1/console/playground/sessions": () => json({ session_id: "ses_1", agent_id: "agt_1" }, 201),
      },
    );
    FakeStream.opened = [];
    render(
      <MemoryRouter initialEntries={["/playground"]}>
        <MetaProvider>
          <AuthProvider>
            <Routes>
              <Route
                path="/playground"
                element={
                  <PlaygroundPage environment={fakeBrowser()} stream={(url) => new FakeStream(url)} />
                }
              />
            </Routes>
          </AuthProvider>
        </MetaProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));
    await screen.findByText("Connected");
    await waitFor(() => expect(FakeStream.opened).toHaveLength(1));
    // The stream is opened for the session the runtime actually created.
    expect(FakeStream.opened[0]!.url).toContain("/v1/console/playground/sessions/ses_1/diagnostics");

    FakeStream.opened[0]!.send(
      "batch",
      batch([event({ seq: 1, category: "SESSION", message: "Session started" })], {
        transcript: [{ role: "user", text: "Hello there", offset_s: 1.5 }],
        state: "USER_SPEAKING",
      }),
    );

    expect(await screen.findByText("Session started")).toBeTruthy();
    expect(screen.getByText("Hello there")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Runtime" })).getAllByText("Caller speaking").length).toBeGreaterThan(0);
  });

  it("shows an understandable message when the runtime reports a failure", async () => {
    render(
      <>
        <DiagnosticsPanel
          events={[event({ seq: 1, type: "transport.failed", severity: "error", message: "Media failed" })]}
          streamError={null}
          complete
        />
      </>,
    );
    expect(screen.getByText("Media failed")).toBeTruthy();
    expect(
      userFacingError([event({ seq: 1, type: "transport.failed", severity: "error", message: "Media failed" })]),
    ).toMatch(/audio connection to the runtime failed/i);
  });

  it("starts from an empty snapshot", () => {
    expect(EMPTY.events).toEqual([]);
    expect(EMPTY.state).toBe("CONNECTING");
    expect(EMPTY.complete).toBe(false);
  });
});
