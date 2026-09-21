import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { ConversationDetail, ConversationSummary } from "../../api/types";
import { App } from "../../app/App";
import { ME, healthyApi, json } from "../../test/fakeApi";

const SUMMARY: ConversationSummary = {
  id: "ses_1",
  agent: { id: "agt_1", name: "Support" },
  status: "ended",
  channel: "web",
  source: "console_playground",
  started_at: "2026-09-21T10:00:00",
  ended_at: "2026-09-21T10:01:05",
  duration_s: 65,
  message_count: 2,
};

const DETAIL: ConversationDetail = {
  ...SUMMARY,
  transcript: [
    { role: "user", text: "Hello there", offset_s: 2.1 },
    { role: "agent", text: "Hi! How can I help?", offset_s: 3.4 },
  ],
  transcript_ordered: true,
  turns: [
    {
      turn: 1,
      status: "completed",
      voice_to_voice_ms: 780,
      stages_ms: { endpoint: 120, llm: 310, tts: 90, egress: 40 },
      user_speech_ms: 800,
      unmeasured: ["stt"],
    },
  ],
  timeline: [
    { seq: 1, type: "session.started", label: "Session started", turn: null, offset_s: 0 },
    { seq: 9, type: "session.ended", label: "Session ended", turn: null, offset_s: 65 },
  ],
  events_recorded: true,
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const agents = () => json({ data: [] });

describe("Conversations list", () => {
  it("lists conversations with their agent, status, duration and source", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/conversations": () => json({ data: [SUMMARY], next_cursor: null }),
    });
    renderAt("/conversations");

    const row = (await screen.findByText("Support")).closest("tr")!;
    expect(within(row).getByText("ended")).toBeTruthy();
    expect(within(row).getByText("1:05")).toBeTruthy();
    expect(within(row).getByText("Playground")).toBeTruthy();
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/conversations/ses_1");
  });

  it("passes the agent filter from the URL to the server", async () => {
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/conversations?agent_id=agt_1": () => json({ data: [SUMMARY], next_cursor: null }),
    });
    renderAt("/conversations?agent=agt_1");

    expect(await screen.findByText("Support")).toBeTruthy();
    expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/conversations?agent_id=agt_1")).toBe(true);
  });

  it("loads the next page with the server's cursor", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/conversations": () => json({ data: [SUMMARY], next_cursor: "c2" }),
      "/v1/console/conversations?cursor=c2": () =>
        json({ data: [{ ...SUMMARY, id: "ses_2", agent: { id: "agt_2", name: "Sales" } }], next_cursor: null }),
    });
    renderAt("/conversations");

    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Sales")).toBeTruthy();
    expect(screen.getByText("Support")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more" })).toBeNull());
  });

  it("points to the Playground when there is nothing yet", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/conversations": () => json({ data: [], next_cursor: null }),
    });
    renderAt("/conversations");

    expect(await screen.findByText("No conversations yet")).toBeTruthy();
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Playground" })).toBeTruthy();
  });
});

describe("Conversation detail", () => {
  it("shows the transcript in order, per-turn latency and the timeline", async () => {
    healthyApi({}, ME, { "/v1/console/conversations/ses_1": () => json(DETAIL) });
    renderAt("/conversations/ses_1");

    const transcript = await screen.findByRole("article", { name: "Transcript" });
    expect(within(transcript).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Caller0:02Hello there",
      "Agent0:03Hi! How can I help?",
    ]);

    const turns = screen.getByRole("article", { name: "Turns" });
    const row = within(turns).getAllByRole("row")[1]!;
    expect(within(row).getByText("780 ms")).toBeTruthy();
    // An unmeasured stage says so; it is never a zero.
    expect(within(row).getByText("not measured")).toBeTruthy();
    expect(within(row).queryByText("0 ms")).toBeNull();

    const timeline = screen.getByRole("article", { name: "Timeline" });
    expect(within(timeline).getByText("Session ended")).toBeTruthy();
  });

  it("warns when the transcript order is unknown", async () => {
    healthyApi({}, ME, {
      "/v1/console/conversations/ses_1": () => json({ ...DETAIL, transcript_ordered: false }),
    });
    renderAt("/conversations/ses_1");

    expect(await screen.findByText(/order of its lines is unknown/)).toBeTruthy();
  });

  it("says when no runtime events were recorded rather than showing empty turns", async () => {
    healthyApi({}, ME, {
      "/v1/console/conversations/ses_1": () => json({ ...DETAIL, events_recorded: false, turns: [], timeline: [] }),
    });
    renderAt("/conversations/ses_1");

    expect(await screen.findByText(/No runtime events were recorded/)).toBeTruthy();
    expect(screen.queryByRole("article", { name: "Turns" })).toBeNull();
  });

  it("explains a conversation this organization cannot see", async () => {
    healthyApi({}, ME, {
      "/v1/console/conversations/ses_x": () =>
        json({ error: { type: "not_found", message: "Conversation not found" } }, 404),
    });
    renderAt("/conversations/ses_x");

    expect(await screen.findByText("Conversation not found")).toBeTruthy();
  });
});
