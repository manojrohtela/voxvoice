import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { AgentView, Me } from "../../api/types";
import { App } from "../../app/App";
import { ME, healthyApi, json } from "../../test/fakeApi";
import { TEMPLATES, templateAgentInput } from "./templates";

const OPTIONS = { languages: ["en"], default_language: "en" };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function created(name: string, id = "agt_new"): AgentView {
  return {
    id,
    name,
    system_prompt: "…",
    first_message: null,
    language: "en",
    language_supported: true,
    llm_model: null,
    tts_voice: null,
    created_at: "2026-10-07T00:00:00",
    updated_at: "2026-10-07T00:00:00",
  };
}

function consoleRoutes(agents: AgentView[] = [], onCreate?: () => AgentView) {
  return {
    "/v1/console/agents": () => json({ data: agents }),
    "/v1/console/agents/options": () => json(OPTIONS),
    "POST /v1/console/agents": () => json(onCreate ? onCreate() : created("Sales representative"), 201),
    "/v1/console/agents/agt_new": () => json(created("Sales representative")),
  };
}

// ── Layer 1: the definitions themselves ───────────────────────────────────────

describe("Template definitions", () => {
  it("ships the eight starter templates", () => {
    expect(TEMPLATES).toHaveLength(8);
    expect(TEMPLATES.map((t) => t.id)).toEqual([
      "sales_representative",
      "interviewer",
      "customer_support",
      "receptionist",
      "lead_qualifier",
      "appointment_assistant",
      "faq_assistant",
      "voxvoice_demo",
    ]);
  });

  it("gives every template a unique id and a unique name", () => {
    expect(new Set(TEMPLATES.map((t) => t.id)).size).toBe(TEMPLATES.length);
    expect(new Set(TEMPLATES.map((t) => t.name)).size).toBe(TEMPLATES.length);
  });

  it.each(TEMPLATES.map((t) => [t.id, t] as const))("%s is complete and within the agent limits", (_id, t) => {
    for (const text of [t.name, t.category, t.short_description, t.purpose, t.recommended_use]) {
      expect(text.trim()).not.toBe("");
    }
    expect(t.behaviour.length).toBeGreaterThanOrEqual(3);
    expect(t.version).toBeGreaterThanOrEqual(1);

    // The agent fields are validated by the server exactly like any other agent's (M12.3).
    expect(t.agent.name.length).toBeGreaterThan(0);
    expect(t.agent.name.length).toBeLessThanOrEqual(100);
    expect(t.agent.system_prompt.trim().length).toBeGreaterThan(200);
    expect(t.agent.system_prompt.length).toBeLessThanOrEqual(20_000);
    expect(t.agent.first_message.trim().length).toBeGreaterThan(0);
    expect(t.agent.first_message.length).toBeLessThanOrEqual(2_000);
    expect(t.agent.language).toBe("en");
  });

  // A template that promised a capability the runtime does not have would be a lie the user only
  // discovers mid-call. Each prompt must therefore state its own limits (§4: no tools, no
  // retrieval, no integrations exist).
  it.each(TEMPLATES.map((t) => [t.id, t] as const))("%s tells the agent what it cannot do", (_id, t) => {
    expect(t.agent.system_prompt).toMatch(/cannot|do not know|does not|never invent|not have/i);
  });

  it.each(TEMPLATES.map((t) => [t.id, t] as const))("%s claims no integration the platform lacks", (_id, t) => {
    const prompt = t.agent.system_prompt.toLowerCase();
    // Claims of acting on the world. The platform has no tools, no booking, no CRM, no mail.
    for (const claim of [
      "i have booked",
      "i've booked",
      "i have scheduled",
      "i've scheduled",
      "i will email",
      "i'll email",
      "according to our records",
      "let me look that up",
      "searching our database",
      "transferring you now",
    ]) {
      expect(prompt).not.toContain(claim);
    }
  });

  it("contains nothing that looks like a credential", () => {
    const text = JSON.stringify(TEMPLATES);
    expect(text).not.toMatch(/api[_-]?key\s*[:=]\s*["'][^"']{8,}/i);
    expect(text).not.toMatch(/\b(sk|vf|pk)_[A-Za-z0-9]{12,}/);
    expect(text).not.toMatch(/bearer\s+[A-Za-z0-9._-]{16,}/i);
  });
});

// ── Layer 2: what "use this template" actually sends ──────────────────────────

describe("Template values", () => {
  it("sends the template's own language when the server declares it", () => {
    const input = templateAgentInput(TEMPLATES[0]!, ["en", "de"], "en");
    expect(input.language).toBe("en");
    expect(input.system_prompt).toBe(TEMPLATES[0]!.agent.system_prompt);
    // What a provider account offers is environment-specific, so neither is asserted here.
    expect(input.llm_model).toBeNull();
    expect(input.tts_voice).toBeNull();
  });

  it("falls back to the server default rather than sending a language it would refuse", () => {
    expect(templateAgentInput(TEMPLATES[0]!, ["de"], "de").language).toBe("de");
  });

  it("copies the values, so an edited agent cannot reach the template", () => {
    const before = JSON.stringify(TEMPLATES[0]);
    const input = templateAgentInput(TEMPLATES[0]!, ["en"], "en");
    input.name = "Edited";
    input.system_prompt = "Replaced";
    expect(JSON.stringify(TEMPLATES[0])).toBe(before);
  });
});

// ── Layers 3–5: the surface ───────────────────────────────────────────────────

describe("Templates in the console", () => {
  it("offers the templates on the Agents page, distinct from the organization's agents", async () => {
    healthyApi({}, ME, consoleRoutes());
    renderAt("/agents");

    const section = await screen.findByRole("region", { name: "Starter templates" });
    expect(within(section).getAllByRole("listitem")).toHaveLength(8);
    expect(within(section).getByRole("link", { name: "Sales representative" }).getAttribute("href")).toBe(
      "/agents/templates/sales_representative",
    );
  });

  it("previews what the template would create before anything is created", async () => {
    const fetchMock = healthyApi({}, ME, consoleRoutes());
    renderAt("/agents/templates/interviewer");

    expect(await screen.findByRole("heading", { level: 1, name: "Interviewer" })).toBeTruthy();
    // The whole prompt, verbatim: it is what the user is about to own. Compared as raw text
    // because the testing-library matcher collapses the newlines the <pre> preserves.
    await waitFor(() =>
      expect(document.querySelector(".template-prompt")?.textContent).toBe(TEMPLATES[1]!.agent.system_prompt),
    );
    expect(screen.getByText(TEMPLATES[1]!.agent.first_message)).toBeTruthy();
    // Shown, not sent: nothing is created by looking.
    expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("creates an ordinary agent and opens it in the normal editor", async () => {
    const fetchMock = healthyApi({}, ME, consoleRoutes());
    renderAt("/agents/templates/sales_representative");

    fireEvent.click(await screen.findByRole("button", { name: "Use this template" }));

    await waitFor(() => {
      const create = fetchMock.mock.calls.find(
        (c) => c[0] === "/v1/console/agents" && (c[1] as RequestInit).method === "POST",
      );
      expect(create).toBeTruthy();
      expect(JSON.parse(String((create![1] as RequestInit).body))).toEqual(
        templateAgentInput(TEMPLATES[0]!, OPTIONS.languages, OPTIONS.default_language),
      );
    });
    // The normal agent editor, on the normal agent route.
    expect(await screen.findByLabelText("Instructions")).toBeTruthy();
  });

  it("sends the same starting values the second time, so two agents are siblings not copies", async () => {
    let n = 0;
    const fetchMock = healthyApi({}, ME, consoleRoutes([], () => created("Sales representative", `agt_${++n}`)));
    const { unmount } = renderAt("/agents/templates/sales_representative");
    fireEvent.click(await screen.findByRole("button", { name: "Use this template" }));
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === "POST")).toBe(true));
    unmount();

    renderAt("/agents/templates/sales_representative");
    fireEvent.click(await screen.findByRole("button", { name: "Use this template" }));

    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter((c) => c[0] === "/v1/console/agents" && (c[1] as RequestInit).method === "POST")
        .map((c) => String((c[1] as RequestInit).body));
      expect(bodies).toHaveLength(2);
      expect(bodies[0]).toBe(bodies[1]);
    });
  });

  it("lets a viewer read a template but not create from it", async () => {
    const viewer: Me = { ...ME, organization: { ...ME.organization!, role: "viewer" } };
    const fetchMock = healthyApi({}, viewer, consoleRoutes());
    renderAt("/agents/templates/customer_support");

    expect(await screen.findByText("Only a developer, admin or owner can create agents")).toBeTruthy();
    await waitFor(() =>
      expect(document.querySelector(".template-prompt")?.textContent).toBe(TEMPLATES[2]!.agent.system_prompt),
    );
    expect(screen.queryByRole("button", { name: "Use this template" })).toBeNull();
    expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("says so when a template id does not exist", async () => {
    healthyApi({}, ME, consoleRoutes());
    renderAt("/agents/templates/not_a_template");

    expect(await screen.findByText("No such template")).toBeTruthy();
  });
});
