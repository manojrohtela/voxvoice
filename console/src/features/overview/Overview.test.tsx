import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { AnalyticsQuery, LatencyFigure, OverviewView } from "../../api/types";
import { App } from "../../app/App";
import { ME, META, healthyApi, json } from "../../test/fakeApi";

const QUERY: AnalyticsQuery = {
  range: {
    preset: "7d",
    first_day: "2026-09-15",
    last_day: "2026-09-21",
    start: "2026-09-15T00:00:00Z",
    end: "2026-09-22T00:00:00Z",
    timezone: "UTC",
    bucket: "day",
  },
  filters: { agent_id: null, source: null },
};

function figure(metric: string, samples: number, p50: number | null): LatencyFigure {
  return { metric, turns: 5, samples, missing: 5 - samples, avg_ms: p50, min_ms: p50, p50_ms: p50, p95_ms: p50, max_ms: p50 };
}

const DAYS = ["15", "16", "17", "18", "19", "20", "21"];

const OVERVIEW: OverviewView = {
  query: QUERY,
  agents: { active: 2, archived: 1, unsupported_language: 0 },
  conversations: {
    total: 3,
    by_status: { created: 0, active: 1, ended: 2, failed: 0 },
    by_source: { playground: 2, api: 1 },
    recorded: 3,
    not_recorded: 0,
  },
  duration: { measured: 2, unmeasured: 1, total_s: 130, avg_s: 65 },
  turns: { total: 5, by_status: { completed: 5, cancelled: 0, interrupted: 0, other: 0 }, avg_per_conversation: 1.67 },
  usage: DAYS.map((d) => ({
    date: `2026-09-${d}`,
    conversations: d === "21" ? 3 : 0,
    turns: d === "21" ? 5 : 0,
    avg_duration_s: d === "21" ? 65 : null,
    voice_to_voice_samples: d === "21" ? 4 : 0,
    voice_to_voice_p50_ms: d === "21" ? 820 : null,
  })),
  latency: [
    figure("voice_to_voice", 4, 820),
    figure("endpoint", 5, 0),
    figure("stt", 0, null),
    figure("llm", 5, 0),
    figure("tts", 5, 95),
    figure("egress", 5, 12),
  ],
  agent_activity: [
    {
      id: "agt_1",
      name: "Support",
      archived: false,
      conversations: 2,
      recorded: 2,
      turns: 4,
      avg_duration_s: 65,
      avg_turns_per_conversation: 2,
      voice_to_voice: figure("voice_to_voice", 3, 800),
    },
    {
      id: "agt_old",
      name: "Old",
      archived: true,
      conversations: 1,
      recorded: 1,
      turns: 1,
      avg_duration_s: null,
      avg_turns_per_conversation: 1,
      voice_to_voice: figure("voice_to_voice", 1, 900),
    },
  ],
  agent_activity_more: 2,
  recent_conversations: [
    {
      id: "ses_1",
      agent: { id: "agt_1", name: "Support" },
      status: "ended",
      channel: "web",
      source: "console_playground",
      started_at: "2026-09-21T10:00:00",
      ended_at: "2026-09-21T10:01:05",
      duration_s: 65,
      message_count: 4,
    },
  ],
  attention: [],
};

const EMPTY: OverviewView = {
  ...OVERVIEW,
  agents: { active: 0, archived: 0, unsupported_language: 0 },
  conversations: { total: 0, by_status: {}, by_source: {}, recorded: 0, not_recorded: 0 },
  duration: { measured: 0, unmeasured: 0, total_s: null, avg_s: null },
  turns: { total: 0, by_status: {}, avg_per_conversation: null },
  usage: OVERVIEW.usage.map((b) => ({ ...b, conversations: 0, turns: 0 })),
  latency: OVERVIEW.latency.map((f) => ({ ...figure(f.metric, 0, null), turns: 0, missing: 0 })),
  agent_activity: [],
  agent_activity_more: 0,
  recent_conversations: [],
  attention: [{ code: "no_agents", message: "No agents yet. Create one to start." }],
};

const agents = () => json({ data: [{ id: "agt_1", name: "Support" }] });

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function stat(label: string): string {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling!.querySelector(".stat__value")!.textContent!;
}

describe("Overview", () => {
  it("shows the organization's summary, usage, latency, recent conversations and agent activity", async () => {
    healthyApi({}, ME, { "/v1/console/agents": agents, "/v1/console/overview?range=7d": () => json(OVERVIEW) });
    renderAt("/");

    await screen.findByRole("article", { name: "Summary" });
    expect(stat("Agents")).toBe("2");
    expect(screen.getByText("1 archived")).toBeTruthy();
    expect(stat("Conversations")).toBe("3");
    expect(stat("Turns")).toBe("5");
    expect(stat("Avg duration")).toBe("1:05");
    expect(screen.getByText("1 not ended: no duration yet")).toBeTruthy();
    expect(screen.queryByRole("article", { name: "Needs attention" })).toBeNull();

    const usage = screen.getByRole("article", { name: "Usage" });
    expect(within(usage).getAllByRole("listitem")).toHaveLength(7);

    const recent = screen.getByRole("article", { name: "Recent conversations" });
    expect(within(recent).getByRole("link", { name: /Sep/ }).getAttribute("href")).toBe("/conversations/ses_1");
    expect(within(recent).getByText(/of any date/)).toBeTruthy();

    const activity = screen.getByRole("article", { name: "Agent activity" });
    expect(within(activity).getByRole("link", { name: "Support" }).getAttribute("href")).toBe("/agents/agt_1");
    // An archived agent has no editor; its history is in Conversations.
    expect(within(activity).getByRole("link", { name: "Old" }).getAttribute("href")).toBe("/conversations?agent=agt_old");
    expect(within(activity).getByText("archived")).toBeTruthy();
    expect(within(activity).getByRole("link", { name: "2 more in Analytics" }).getAttribute("href")).toBe(
      "/analytics?range=7d",
    );
    expect(screen.getByText(/from recorded conversations/)).toBeTruthy();
  });

  it("shows an unmeasured latency as not measured and a measured 0 as 0 ms", async () => {
    healthyApi({}, ME, { "/v1/console/agents": agents, "/v1/console/overview?range=7d": () => json(OVERVIEW) });
    renderAt("/");

    const latency = await screen.findByRole("article", { name: "Latency" });
    const value = (label: string) => within(latency).getByText(label).nextElementSibling!.textContent!;
    expect(value("Voice-to-voice")).toContain("820 ms");
    expect(value("Speech recognition")).toContain("not measured");
    expect(value("Speech recognition")).toContain("0 of 5 turns measured");
    expect(value("Language model")).toContain("0 ms");
    expect(value("Language model")).not.toContain("not measured");
  });

  it("sends the range and agent filters to the one overview request", async () => {
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/overview?range=7d": () => json(OVERVIEW),
      "/v1/console/overview?range=today": () => json(OVERVIEW),
      "/v1/console/overview?range=today&agent_id=agt_1": () => json(OVERVIEW),
    });
    renderAt("/");

    await screen.findByRole("article", { name: "Summary" });
    fireEvent.change(screen.getByLabelText("Range"), { target: { value: "today" } });
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/overview?range=today")).toBe(true));
    fireEvent.change(await screen.findByLabelText("Agent"), { target: { value: "agt_1" } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/overview?range=today&agent_id=agt_1")).toBe(true),
    );
    // One request per query: no per-panel, per-agent or per-conversation fan-out.
    const overviewCalls = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/v1/console/overview"));
    expect(overviewCalls).toHaveLength(3);
    expect(fetchMock.mock.calls.some((c) => String(c[0]).startsWith("/v1/console/analytics"))).toBe(false);
  });

  it("guides an empty organization instead of drawing empty charts", async () => {
    healthyApi({}, ME, { "/v1/console/agents": () => json({ data: [] }), "/v1/console/overview?range=7d": () => json(EMPTY) });
    renderAt("/");

    const attention = await screen.findByRole("article", { name: "Needs attention" });
    expect(within(attention).getByText("No agents yet. Create one to start.")).toBeTruthy();
    expect(within(attention).getByRole("link", { name: "Create an agent" }).getAttribute("href")).toBe("/agents/new");
    expect(stat("Conversations")).toBe("0");
    expect(stat("Avg duration")).toBe("not measured");
    const usage = screen.getByRole("article", { name: "Usage" });
    expect(within(usage).getByText("No conversations in this range")).toBeTruthy();
    expect(within(usage).queryByRole("list")).toBeNull();
    expect(screen.getByText("No turns in this range")).toBeTruthy();
    expect(screen.getByText("No conversations yet")).toBeTruthy();
    expect(screen.getByText("No agent had a conversation in this range")).toBeTruthy();
  });

  it("shows loading, then a page-level error with a retry that recovers", async () => {
    let fail = true;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/overview?range=7d": async () => {
        await gate;
        return fail
          ? json({ error: { type: "internal", message: "Something went wrong", request_id: "req_1" } }, 500)
          : json(OVERVIEW);
      },
    });
    renderAt("/");

    expect(await screen.findByText("Loading the overview…")).toBeTruthy();
    release();
    expect(await screen.findByText("Unable to load the overview")).toBeTruthy();
    // The runtime card does not depend on it and still renders.
    expect(screen.getByRole("article", { name: "Runtime" })).toBeTruthy();

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("article", { name: "Summary" })).toBeTruthy();
  });

  it("reloads for the new organization when switching, leaving nothing of the old one", async () => {
    let org = "org_a";
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents": agents,
      "/v1/console/overview?range=7d": () =>
        json(org === "org_a" ? OVERVIEW : { ...EMPTY, attention: [] }),
      "/v1/console/overview?range=7d&agent_id=agt_1": () => json(OVERVIEW),
      "POST /v1/console/session/organization": () => {
        org = "org_b";
        return json({ ...ME, organization: ME.memberships[1] });
      },
    });
    renderAt("/?agent=agt_1");

    await screen.findByRole("article", { name: "Summary" });
    expect(stat("Conversations")).toBe("3");

    fireEvent.change(screen.getByLabelText("Organization"), { target: { value: "org_b" } });

    await waitFor(() => expect(stat("Conversations")).toBe("0"));
    expect(screen.queryByText("Support", { selector: "a" })).toBeNull();
    // The previous organization's agent filter is dropped, not sent to the new one.
    const last = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/v1/console/overview")).at(-1)!;
    expect(last[0]).toBe("/v1/console/overview?range=7d");
  });

  it("keeps the runtime card and skips the dashboard on a server without the overview", async () => {
    const fetchMock = healthyApi({ capabilities: META.capabilities.filter((c) => c !== "overview") }, ME, {
      "/v1/console/agents": agents,
    });
    renderAt("/");

    expect(await screen.findByRole("article", { name: "Runtime" })).toBeTruthy();
    expect(screen.queryByLabelText("Range")).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).startsWith("/v1/console/overview"))).toBe(false);
  });
});
