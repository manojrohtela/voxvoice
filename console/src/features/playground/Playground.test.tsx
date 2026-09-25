import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import type { AgentView, Me } from "../../api/types";
import { AuthProvider } from "../../app/auth";
import { MetaProvider } from "../../app/meta";
import { ME, META, healthyApi, json } from "../../test/fakeApi";
import type { CallEnvironment } from "../../voice/call";
import { PlaygroundPage } from "./PlaygroundPage";

const AGENT: AgentView = {
  id: "agt_1",
  name: "Support",
  system_prompt: "Be brief.",
  first_message: null,
  language: "en",
  language_supported: true,
  llm_model: null,
  tts_voice: null,
  created_at: "2026-09-21T00:00:00",
  updated_at: "2026-09-21T00:00:00",
};
const LEGACY: AgentView = { ...AGENT, id: "agt_2", name: "Legacy", language: "en-IN", language_supported: false };
const VOICE = { runtime: { ...META.runtime, transport: "webrtc", webrtc_signalling: true } };
/** The same user in a role the server's matrix (LLD §3) does and does not allow to call. */
const VIEWER: Me = { ...ME, organization: { ...ME.organization!, role: "viewer" } };
const DEVELOPER: Me = { ...ME, organization: { ...ME.organization!, role: "developer" } };

/** A browser whose microphone, peer and signalling fetch are all fakes that succeed. */
function fakeBrowser(options: { mic?: "denied" } = {}): CallEnvironment & { closed: () => boolean } {
  let closed = false;
  const track = { enabled: true, stop: () => undefined };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
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
    close: () => {
      closed = true;
    },
  };
  return {
    getUserMedia: async () => {
      if (options.mic === "denied") throw new DOMException("denied", "NotAllowedError");
      return stream as unknown as MediaStream;
    },
    createPeerConnection: () => peer as unknown as RTCPeerConnection,
    fetch: (async (url: string) =>
      String(url).endsWith("/offer")
        ? new Response(JSON.stringify({ sdp: "offer" }), { status: 200 })
        : new Response(null, { status: 204 })) as typeof fetch,
    now: () => 0,
    closed: () => closed,
  };
}

function renderPlayground(path: string, environment: CallEnvironment) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MetaProvider>
        <AuthProvider>
          <Routes>
            <Route path="/playground" element={<PlaygroundPage environment={environment} />} />
          </Routes>
        </AuthProvider>
      </MetaProvider>
    </MemoryRouter>,
  );
}

function consoleRoutes(agents: AgentView[] = [AGENT, LEGACY]) {
  return {
    "/v1/console/agents": () => json({ data: agents }),
    "POST /v1/console/playground/rendezvous": () =>
      json({ rendezvous_id: "rdv_1", signalling_token: "tok_1", expires_in_s: 30 }, 201),
    "POST /v1/console/playground/sessions": () => json({ session_id: "ses_1", agent_id: "agt_1" }, 201),
    "POST /v1/console/playground/sessions/ses_1/end": () => json({ session_id: "ses_1", status: "ended" }),
  };
}

describe("Playground", () => {
  it("explains that voice is not enabled instead of offering a call", async () => {
    healthyApi({}, ME, consoleRoutes()); // loopback transport
    renderPlayground("/playground", fakeBrowser());

    expect(await screen.findByText("Voice is not enabled on this server")).toBeTruthy();
    expect(screen.getByText("RUNTIME_TRANSPORT=webrtc")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start call" })).toBeNull();
  });

  it("preselects the requested agent and disables agents the recognizer cannot hear", async () => {
    healthyApi(VOICE, ME, consoleRoutes([AGENT, { ...AGENT, id: "agt_3", name: "Sales" }, LEGACY]));
    renderPlayground("/playground?agent=agt_3", fakeBrowser());

    const select = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    expect(select.value).toBe("agt_3");
    const legacy = screen.getByRole("option", { name: "Legacy (unsupported language)" }) as HTMLOptionElement;
    expect(legacy.disabled).toBe(true);
  });

  it("points to Agents when there is nothing to call", async () => {
    healthyApi(VOICE, ME, consoleRoutes([]));
    renderPlayground("/playground", fakeBrowser());

    expect(await screen.findByText("No agent to call yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to Agents" })).toBeTruthy();
  });

  it("connects a real call through the console routes and hangs up by ending the session", async () => {
    const fetchMock = healthyApi(VOICE, ME, consoleRoutes());
    const browser = fakeBrowser();
    renderPlayground("/playground", browser);

    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));

    expect(await screen.findByText("Connected")).toBeTruthy();
    expect(screen.getByText(/Connected · session ses_1/)).toBeTruthy();
    const start = fetchMock.mock.calls.find((c) => c[0] === "/v1/console/playground/sessions");
    expect(JSON.parse(String((start?.[1] as RequestInit).body))).toEqual({
      agent_id: "agt_1",
      rendezvous_id: "rdv_1",
    });

    fireEvent.click(screen.getByRole("button", { name: "Hang up" }));

    expect(await screen.findByText("Call ended")).toBeTruthy();
    expect(browser.closed()).toBe(true);
    expect(screen.getByRole("link", { name: "View this conversation" }).getAttribute("href")).toBe(
      "/conversations/ses_1",
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/playground/sessions/ses_1/end")).toBe(true),
    );
  });

  // M12.12 defect 3: a viewer was offered an enabled "Start call", the browser asked for their
  // microphone, and only then did the server refuse with 403. The gate is UX only — the server
  // stays the authorization boundary — but nothing should be attempted on a viewer's behalf.
  it("offers a viewer no call, and attempts nothing on their behalf", async () => {
    const fetchMock = healthyApi(VOICE, VIEWER, consoleRoutes());
    const browser = fakeBrowser();
    let microphoneAsked = false;
    renderPlayground("/playground", {
      ...browser,
      getUserMedia: async (constraints) => {
        microphoneAsked = true;
        return browser.getUserMedia(constraints);
      },
    });

    expect(await screen.findByText("Only a developer, admin or owner can start calls")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start call" })).toBeNull();
    expect(screen.queryByLabelText("Agent")).toBeNull();
    expect(microphoneAsked).toBe(false);
    const attempted = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(attempted.some((path) => path.includes("/playground/rendezvous"))).toBe(false);
    expect(attempted.some((path) => path.includes("/playground/sessions"))).toBe(false);
    expect(attempted.some((path) => path.includes("/webrtc/"))).toBe(false);
  });

  it("still offers the call to a developer, whom the server allows", async () => {
    healthyApi(VOICE, DEVELOPER, consoleRoutes());
    renderPlayground("/playground", fakeBrowser());

    expect(await screen.findByRole("button", { name: "Start call" })).toBeTruthy();
    expect(screen.queryByText("Only a developer, admin or owner can start calls")).toBeNull();
  });

  it("says why a call could not start", async () => {
    healthyApi(VOICE, ME, consoleRoutes());
    renderPlayground("/playground", fakeBrowser({ mic: "denied" }));

    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Microphone access was denied. Allow it in the browser and try again.",
    );
    expect(screen.getByRole("button", { name: "Call again" })).toBeTruthy();
  });

  it("shows the server's refusal", async () => {
    healthyApi(VOICE, ME, {
      ...consoleRoutes(),
      "POST /v1/console/playground/sessions": () =>
        json({ error: { type: "runtime_failed", message: "Runtime failed to start the session" } }, 502),
    });
    const browser = fakeBrowser();
    renderPlayground("/playground", browser);

    fireEvent.click(await screen.findByRole("button", { name: "Start call" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Runtime failed to start the session");
    expect(browser.closed()).toBe(true);
  });
});
