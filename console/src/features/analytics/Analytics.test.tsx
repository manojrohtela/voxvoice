import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { AgentReport, AnalyticsQuery, AnalyticsSummary, LatencyReport, SeriesReport } from "../../api/types";
import { App } from "../../app/App";
import { ME, healthyApi, json } from "../../test/fakeApi";

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

const SUMMARY: AnalyticsSummary = {
  query: QUERY,
  conversations: {
    total: 3,
    by_status: { created: 0, active: 1, ended: 2, failed: 0 },
    by_source: { playground: 2, api: 1 },
    recorded: 2,
    not_recorded: 1,
  },
  duration: { measured: 2, unmeasured: 1, total_s: 130, avg_s: 65 },
  turns: { total: 5, by_status: { completed: 4, cancelled: 0, interrupted: 1, other: 0 }, avg_per_conversation: 2.5 },
};

const EMPTY_SUMMARY: AnalyticsSummary = {
  query: QUERY,
  conversations: { total: 0, by_status: {}, by_source: {}, recorded: 0, not_recorded: 0 },
  duration: { measured: 0, unmeasured: 0, total_s: null, avg_s: null },
  turns: { total: 0, by_status: {}, avg_per_conversation: null },
};

function figure(metric: string, samples: number, value: number | null) {
  return {
    metric,
    turns: 5,
    samples,
    missing: 5 - samples,
    avg_ms: value,
    min_ms: value,
    p50_ms: value,
    p95_ms: value,
    max_ms: value,
  };
}

const LATENCY: LatencyReport = {
  query: QUERY,
  data: [
    figure("voice_to_voice", 4, 820),
    figure("endpoint", 5, 0), // a genuine 0 ms measurement
    figure("stt", 0, null), // never measured
    figure("llm", 5, 310),
    figure("tts", 5, 95),
    figure("egress", 5, 12),
  ],
};

const SERIES: SeriesReport = {
  query: QUERY,
  data: ["15", "16", "17", "18", "19", "20", "21"].map((d) => ({
    date: `2026-09-${d}`,
    conversations: d === "20" ? 3 : 0,
    turns: d === "20" ? 5 : 0,
    avg_duration_s: d === "20" ? 65 : null,
    voice_to_voice_samples: d === "20" ? 4 : 0,
    voice_to_voice_p50_ms: d === "20" ? 820 : null,
  })),
};

const AGENTS: AgentReport = {
  query: QUERY,
  data: [
    {
      id: "agt_1",
      name: "Support",
      archived: false,
      conversations: 2,
      recorded: 2,
      turns: 5,
      avg_duration_s: 65,
      avg_turns_per_conversation: 2.5,
      voice_to_voice: figure("voice_to_voice", 4, 820),
    },
    {
      id: "agt_2",
      name: "Old",
      archived: true,
      conversations: 1,
      recorded: 0,
      turns: 0,
      avg_duration_s: null,
      avg_turns_per_conversation: null,
      voice_to_voice: { ...figure("voice_to_voice", 0, null), turns: 0, missing: 0 },
    },
  ],
};

const agents = () => json({ data: [{ id: "agt_1", name: "Support" }] });

function routes(query = "?range=7d", overrides: Record<string, () => Response | Promise<Response>> = {}) {
  return {
    "/v1/console/agents": agents,
    [`/v1/console/analytics/summary${query}`]: () => json(SUMMARY),
    [`/v1/console/analytics/latency${query}`]: () => json(LATENCY),
    [`/v1/console/analytics/timeseries${query}`]: () => json(SERIES),
    [`/v1/console/analytics/agents${query}`]: () => json(AGENTS),
    ...overrides,
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function stat(label: string): string {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling!.querySelector(".stat__value")!.textContent!;
}

describe("Analytics", () => {
  it("shows the summary, usage, latency, agents and outcomes from the API", async () => {
    healthyApi({}, ME, routes());
    renderAt("/analytics");

    const summary = await screen.findByRole("article", { name: "Summary" });
    await within(summary).findByText("Conversations");
    expect(stat("Conversations")).toBe("3");
    expect(stat("Turns")).toBe("5");
    expect(stat("Avg duration")).toBe("1:05");
    expect(stat("Avg turns / conversation")).toBe("2.50");
    // Incomplete data is explained, not hidden.
    expect(within(summary).getByText(/1 conversation\(s\) not ended/)).toBeTruthy();
    expect(within(summary).getByText(/1 conversation\(s\) without recorded events/)).toBeTruthy();

    const usage = screen.getByRole("article", { name: "Usage over time" });
    const days = await within(usage).findAllByRole("listitem");
    expect(days).toHaveLength(7);
    expect(days[5]!.textContent).toContain("3");

    const agentsCard = screen.getByRole("article", { name: "Agents" });
    const old = (await within(agentsCard).findByRole("link", { name: "Old" })).closest("tr")!;
    expect(within(old).getByText("archived")).toBeTruthy();
    expect(within(agentsCard).getByRole("link", { name: "Support" }).getAttribute("href")).toBe(
      "/conversations?agent=agt_1",
    );

    const outcomes = screen.getByRole("article", { name: "Outcomes" });
    const statuses = within(outcomes).getByRole("list", { name: "Conversations by status" });
    expect(within(statuses).getByText("ended").closest("li")!.textContent).toBe("ended2");
    expect(within(outcomes).getByText(/not why it ended/)).toBeTruthy();
    expect(screen.getByText(/whole days in UTC/)).toBeTruthy();
  });

  it("shows an unmeasured latency as not measured and a measured 0 as 0", async () => {
    healthyApi({}, ME, routes());
    renderAt("/analytics");

    const latency = await screen.findByRole("article", { name: "Latency" });
    const row = (name: string) => within(latency).getByRole("rowheader", { name }).closest("tr")!;
    await within(latency).findByRole("rowheader", { name: "Voice-to-voice" });

    expect(within(row("Voice-to-voice")).getAllByText("820 ms")).toHaveLength(5);
    expect(within(row("End of speech → turn detected")).getAllByText("0 ms")).toHaveLength(5);
    const stt = row("Speech recognition");
    expect(within(stt).getAllByText("not measured")).toHaveLength(5);
    expect(within(stt).queryByText("0 ms")).toBeNull();
    // Samples 0, not-measured 5: counts are numbers, even when zero.
    expect([...stt.querySelectorAll("td")].slice(0, 2).map((td) => td.textContent)).toEqual(["0", "5"]);

    const agentsCard = screen.getByRole("article", { name: "Agents" });
    const old = (await within(agentsCard).findByRole("link", { name: "Old" })).closest("tr")!;
    expect(within(old).getAllByText("not measured").length).toBeGreaterThanOrEqual(2);
  });

  it("sends the filters from the URL and the range selector to the server", async () => {
    const fetchMock = healthyApi({}, ME, {
      ...routes("?range=30d&agent_id=agt_1&source=playground"),
      ...routes("?range=today&agent_id=agt_1&source=playground"),
    });
    renderAt("/analytics?range=30d&agent=agt_1&source=playground");

    await screen.findByRole("article", { name: "Summary" });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) => c[0] === "/v1/console/analytics/summary?range=30d&agent_id=agt_1&source=playground"),
      ).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("Range"), { target: { value: "today" } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) => c[0] === "/v1/console/analytics/timeseries?range=today&agent_id=agt_1&source=playground",
        ),
      ).toBe(true),
    );
  });

  it("asks for a custom range only once both days are chosen", async () => {
    const fetchMock = healthyApi({}, ME, routes("?from=2026-09-01&to=2026-09-03"));
    renderAt("/analytics?range=custom");

    expect(await screen.findByText("Choose both days")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("From (UTC)"), { target: { value: "2026-09-01" } });
    expect(screen.getByText("Choose both days")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("To (UTC)"), { target: { value: "2026-09-03" } });

    expect(await screen.findByRole("article", { name: "Summary" })).toBeTruthy();
    const analyticsCalls = fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/analytics/"));
    expect(analyticsCalls.every((u) => u.endsWith("?from=2026-09-01&to=2026-09-03"))).toBe(true);
  });

  it("says there is nothing in the range instead of drawing empty charts", async () => {
    const empty = { data: [] };
    healthyApi({}, ME, {
      ...routes("?range=7d", {
        "/v1/console/analytics/summary?range=7d": () => json(EMPTY_SUMMARY),
        "/v1/console/analytics/latency?range=7d": () =>
          json({ query: QUERY, data: LATENCY.data.map((f) => ({ ...figure(f.metric, 0, null), turns: 0, missing: 0 })) }),
        "/v1/console/analytics/timeseries?range=7d": () =>
          json({ query: QUERY, data: SERIES.data.map((b) => ({ ...b, conversations: 0, turns: 0 })) }),
        "/v1/console/analytics/agents?range=7d": () => json({ query: QUERY, ...empty }),
      }),
    });
    renderAt("/analytics");

    const summary = await screen.findByRole("article", { name: "Summary" });
    expect(await within(summary).findByText("No conversations in this range")).toBeTruthy();
    const usage = screen.getByRole("article", { name: "Usage over time" });
    expect(await within(usage).findByText("No conversations in this range")).toBeTruthy();
    expect(within(usage).queryByRole("list")).toBeNull();
    expect(await screen.findByText("No turns in this range")).toBeTruthy();
    expect(await screen.findByText("No agent had a conversation in this range")).toBeTruthy();
  });

  it("shows loading, then an error per panel with a retry, while the other panels still load", async () => {
    let failLatency = true;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    healthyApi({}, ME, {
      ...routes("?range=7d", {
        "/v1/console/analytics/summary?range=7d": async () => {
          await gate;
          return json(SUMMARY);
        },
        "/v1/console/analytics/latency?range=7d": () =>
          failLatency
            ? json({ error: { type: "internal", message: "Database unavailable", request_id: "req_9" } }, 500)
            : json(LATENCY),
      }),
    });
    renderAt("/analytics");

    const summary = await screen.findByRole("article", { name: "Summary" });
    expect(await within(summary).findByRole("status")).toBeTruthy();

    const latency = screen.getByRole("article", { name: "Latency" });
    expect((await within(latency).findByRole("alert")).textContent).toContain("Database unavailable");
    expect(within(latency).getByText("req_9")).toBeTruthy();
    const usage = screen.getByRole("article", { name: "Usage over time" });
    expect(await within(usage).findAllByRole("listitem")).toHaveLength(7);

    release();
    await within(summary).findByText("Conversations");

    failLatency = false;
    fireEvent.click(within(latency).getByRole("button", { name: "Try again" }));
    expect(await within(latency).findByRole("rowheader", { name: "Voice-to-voice" })).toBeTruthy();
  });
});
