import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { CONSOLE_HEADER } from "../../api/client";
import type { AgentView } from "../../api/types";
import { App } from "../../app/App";
import { ME, healthyApi, json } from "../../test/fakeApi";

const AGENT: AgentView = {
  id: "agt_1",
  name: "Support",
  system_prompt: "You are a concise support agent.",
  first_message: "Hi!",
  language: "en",
  language_supported: true,
  llm_model: null,
  tts_voice: null,
  created_at: "2026-09-21T00:00:00",
  updated_at: "2026-09-21T00:00:00",
};
const OPTIONS = { languages: ["en"], default_language: "en" };
const VIEWER = { ...ME, organization: { ...ME.organization!, role: "viewer" as const } };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function body(call: unknown[] | undefined): unknown {
  return JSON.parse(String((call?.[1] as RequestInit).body));
}

function callTo(mock: ReturnType<typeof healthyApi>, path: string, method: string) {
  return mock.mock.calls.find((c) => c[0] === path && (c[1] as RequestInit | undefined)?.method === method);
}

describe("Agents list", () => {
  it("guides a developer to the first agent when there are none", async () => {
    healthyApi({}, ME, { "/v1/console/agents": () => json({ data: [] }) });
    renderAt("/agents");

    expect(await screen.findByText("No agents yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "New agent" })).toBeTruthy();
  });

  it("lists agents and flags an unsupported language", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents": () =>
        json({ data: [AGENT, { ...AGENT, id: "agt_2", name: "Legacy", language: "en-IN", language_supported: false }] }),
    });
    renderAt("/agents");

    expect(await screen.findByRole("link", { name: "Support" })).toBeTruthy();
    expect(screen.getByText("en-IN · unsupported")).toBeTruthy();
  });

  it("offers no creation to a viewer", async () => {
    healthyApi({}, VIEWER, { "/v1/console/agents": () => json({ data: [] }) });
    renderAt("/agents");

    expect(await screen.findByText("No agents yet")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "New agent" })).toBeNull();
  });
});

describe("Agent editor", () => {
  it("creates an agent with the server's default language and opens it", async () => {
    const created = { ...AGENT, id: "agt_new", name: "Concierge" };
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "POST /v1/console/agents": () => json(created, 201),
      "/v1/console/agents/agt_new": () => json(created),
    });
    renderAt("/agents/new");

    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Concierge" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Be helpful." } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(await screen.findByRole("heading", { name: "Concierge", level: 1 })).toBeTruthy();
    const post = callTo(fetchMock, "/v1/console/agents", "POST");
    expect(body(post)).toEqual({
      name: "Concierge",
      system_prompt: "Be helpful.",
      first_message: null,
      language: "en",
      llm_model: null,
      tts_voice: null,
    });
    expect(((post?.[1] as RequestInit).headers as Record<string, string>)[CONSOLE_HEADER]).toBe("1");
  });

  it("saves only the fields that changed", async () => {
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_1": () => json(AGENT),
      "PATCH /v1/console/agents/agt_1": () => json({ ...AGENT, name: "Renamed" }),
    });
    renderAt("/agents/agt_1");

    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Saved")).toBeTruthy();
    expect(body(callTo(fetchMock, "/v1/console/agents/agt_1", "PATCH"))).toEqual({ name: "Renamed" });
  });

  it("warns about an unsupported language and does not resend it on unrelated edits", async () => {
    const legacy = { ...AGENT, language: "en-IN", language_supported: false };
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_1": () => json(legacy),
      "PATCH /v1/console/agents/agt_1": () => json({ ...legacy, first_message: "Hello" }),
    });
    renderAt("/agents/agt_1");

    expect(await screen.findByText(/calls to this agent would end after the greeting/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Greeting"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText("Saved");
    expect(body(callTo(fetchMock, "/v1/console/agents/agt_1", "PATCH"))).toEqual({ first_message: "Hello" });
  });

  it("shows the server's validation message", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_1": () => json(AGENT),
      "PATCH /v1/console/agents/agt_1": () =>
        json({ error: { type: "invalid_request", message: "Model may contain only letters" } }, 400),
    });
    renderAt("/agents/agt_1");

    fireEvent.change(await screen.findByLabelText("Model"), { target: { value: "two words" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Model may contain only letters");
  });

  it("archives after confirmation and returns to the list", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_1": () => json(AGENT),
      "POST /v1/console/agents/agt_1/archive": () => new Response(null, { status: 204 }),
      "/v1/console/agents": () => json({ data: [] }),
    });
    renderAt("/agents/agt_1");

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

    expect(await screen.findByText("No agents yet")).toBeTruthy();
    expect(callTo(fetchMock, "/v1/console/agents/agt_1/archive", "POST")).toBeTruthy();
  });

  it("is read-only for a viewer", async () => {
    healthyApi({}, VIEWER, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_1": () => json(AGENT),
    });
    renderAt("/agents/agt_1");

    expect(await screen.findByText(/read-only access/)).toBeTruthy();
    // Disabled through the fieldset, which disables every control inside it.
    expect(screen.getByLabelText("Name").closest("fieldset")?.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
  });

  it("explains an agent that does not exist in this organization", async () => {
    healthyApi({}, ME, {
      "/v1/console/agents/options": () => json(OPTIONS),
      "/v1/console/agents/agt_other": () => json({ error: { type: "not_found", message: "Agent not found" } }, 404),
    });
    renderAt("/agents/agt_other");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Agent not found")).toBeTruthy();
    await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
  });
});
