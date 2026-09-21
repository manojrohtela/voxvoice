import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { healthyApi, json, stubApi } from "../test/fakeApi";
import { App } from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("Overview", () => {
  it("shows the real runtime status from /health and /v1/console/meta", async () => {
    healthyApi();
    renderAt("/");

    const runtime = await screen.findByRole("article", { name: "Runtime" });
    expect(await within(runtime).findByText("Healthy")).toBeTruthy();
    expect(within(runtime).getByText("Loopback · no real voice")).toBeTruthy();
    expect(within(runtime).getByText("Disabled")).toBeTruthy();
    expect(within(runtime).getByText(/RUNTIME_TRANSPORT=webrtc/)).toBeTruthy();
  });

  it("marks every journey step the server has not declared as pending, with its milestone", async () => {
    healthyApi();
    renderAt("/");

    const journey = await screen.findByRole("article", { name: "Get started" });
    expect(await within(journey).findByText("Arrives in M12.2")).toBeTruthy();
    expect(within(journey).getByText("Arrives in M12.4")).toBeTruthy();
    expect(within(journey).queryByText("Available")).toBeNull();
  });

  it("shows a step as available once the server declares its capability", async () => {
    healthyApi({ capabilities: ["runtime.status", "agents"] });
    renderAt("/");

    const journey = await screen.findByRole("article", { name: "Get started" });
    expect(await within(journey).findByText("Available")).toBeTruthy();
  });

  it("explains an unreachable API instead of rendering empty data", async () => {
    stubApi({});
    renderAt("/");

    expect(await screen.findByText("Cannot load server details")).toBeTruthy();
    expect(screen.getByText("API unreachable")).toBeTruthy();
  });

  it("warns when the server's console API revision differs from this build", async () => {
    healthyApi({ console_api_revision: "m12.9" });
    renderAt("/");

    expect(await screen.findByText(/This console was built for API revision/)).toBeTruthy();
  });
});

describe("Navigation", () => {
  it("routes an undeclared surface to the not-yet-available page, never to sample data", async () => {
    healthyApi();
    renderAt("/agents");

    expect(await screen.findByRole("heading", { name: "Agents", level: 1 })).toBeTruthy();
    expect(screen.getByText("Not yet available — arrives in M12.3")).toBeTruthy();
  });

  it("badges undeclared surfaces as Soon and marks the current page", async () => {
    healthyApi({ capabilities: ["runtime.status", "agents"] });
    renderAt("/");

    const nav = screen.getByRole("navigation", { name: "Console" });
    expect(await within(nav).findByRole("link", { name: /Playground/ })).toBeTruthy();
    await within(nav).findAllByText("Soon");

    const overview = within(nav).getByRole("link", { name: "Overview" });
    expect(overview.getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("link", { name: "Agents" }).textContent).toBe("Agents");
    expect(within(nav).getByRole("link", { name: /Playground/ }).textContent).toContain("Soon");
  });

  it("shows a not-found page for unknown paths", async () => {
    healthyApi();
    renderAt("/nowhere");

    expect(await screen.findByText("Page not found")).toBeTruthy();
  });

  it("treats malformed server metadata as an error, not as data", async () => {
    stubApi({ "/v1/console/meta": () => json({}), "/health": () => json({ ok: true, db: true }) });
    renderAt("/");

    expect(await screen.findByText("The server returned unrecognised console metadata")).toBeTruthy();
    // Nothing is declared, so nothing is offered as available.
    expect(screen.getByRole("link", { name: /Agents/ }).textContent).toContain("Soon");
  });
});
