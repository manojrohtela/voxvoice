import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { CONSOLE_HEADER } from "../api/client";
import { HEALTH, ME, META, healthyApi, json, stubApi } from "../test/fakeApi";
import { App } from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function sentHeaders(call: unknown[] | undefined): Record<string, string> {
  const init = call?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
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

  it("marks declared steps available and the rest pending with their milestone", async () => {
    healthyApi();
    renderAt("/");

    const journey = await screen.findByRole("article", { name: "Get started" });
    await within(journey).findAllByText("Available");
    expect(within(journey).getAllByText("Available")).toHaveLength(3); // identity, agents, playground
    expect(within(journey).getByText("Arrives in M12.5")).toBeTruthy();
    expect(within(journey).queryByText("Arrives in M12.4")).toBeNull();
  });

  it("warns when the server's console API revision differs from this build", async () => {
    healthyApi({ console_api_revision: "m12.9" });
    renderAt("/");

    expect(await screen.findByText(/This console was built for API revision/)).toBeTruthy();
  });
});

describe("Signing in", () => {
  it("sends a signed-out visitor to the sign-in page", async () => {
    healthyApi({}, null);
    renderAt("/settings");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Console" })).toBeNull();
  });

  it("signs in with the CSRF header and lands in the console", async () => {
    const fetchMock = healthyApi({}, null, {
      "POST /v1/console/auth/login": () => json(ME),
    });
    renderAt("/login");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeTruthy();
    const login = fetchMock.mock.calls.find((call) => call[0] === "/v1/console/auth/login");
    expect(sentHeaders(login)[CONSOLE_HEADER]).toBe("1");
    expect(JSON.parse(String((login?.[1] as RequestInit).body))).toEqual({
      email: "ada@example.com",
      password: "correct horse battery",
    });
  });

  it("shows the server's message when credentials are wrong", async () => {
    healthyApi({}, null, {
      "POST /v1/console/auth/login": () =>
        json({ error: { type: "unauthenticated", message: "Email or password is incorrect" } }, 401),
    });
    renderAt("/login");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@b.io" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong password!!" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Email or password is incorrect");
  });

  it("offers sign-up only when the server accepts one", async () => {
    healthyApi({ signup: { mode: "first_run", available: true } }, null);
    renderAt("/login");
    expect(await screen.findByRole("link", { name: "Create the first account" })).toBeTruthy();
  });

  it("explains a closed sign-up and how to create an account from the server", async () => {
    healthyApi({ signup: { mode: "closed", available: false } }, null);
    renderAt("/signup");

    expect(await screen.findByRole("heading", { name: "Sign-up is closed" })).toBeTruthy();
    expect(screen.getByText(/api\.console\.bootstrap_owner/)).toBeTruthy();
  });

  it("creates the first account and its organization", async () => {
    const fetchMock = healthyApi({ signup: { mode: "first_run", available: true } }, null, {
      "POST /v1/console/auth/signup": () => json(ME, 201),
    });
    renderAt("/signup");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "correct horse battery" } });
    fireEvent.change(screen.getByLabelText("Organization name"), { target: { value: "Analytical Engines" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeTruthy();
    const signup = fetchMock.mock.calls.find((call) => call[0] === "/v1/console/auth/signup");
    expect(sentHeaders(signup)[CONSOLE_HEADER]).toBe("1");
  });

  it("never writes to browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    healthyApi({}, null, { "POST /v1/console/auth/login": () => json(ME) });
    renderAt("/login");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Overview" });

    expect(setItem).not.toHaveBeenCalled();
  });
});

describe("Organizations", () => {
  it("asks a user with no organization to create or choose one", async () => {
    healthyApi({}, { ...ME, organization: null, memberships: [] });
    renderAt("/");

    expect(await screen.findByRole("heading", { name: "Choose an organization" })).toBeTruthy();
    expect(screen.getByLabelText("New organization name")).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Console" })).toBeNull();
  });

  it("switches organization through the server-side session", async () => {
    const switched = { ...ME, organization: ME.memberships[1]! };
    const fetchMock = healthyApi({}, ME, {
      "POST /v1/console/session/organization": () => json(switched),
    });
    renderAt("/");

    fireEvent.change(await screen.findByLabelText("Organization"), { target: { value: "org_b" } });

    await waitFor(() => expect((screen.getByLabelText("Organization") as HTMLSelectElement).value).toBe("org_b"));
    const call = fetchMock.mock.calls.find((c) => c[0] === "/v1/console/session/organization");
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ organization_id: "org_b" });
  });

  it("signs out through the server and returns to sign-in", async () => {
    const fetchMock = healthyApi({}, ME, {
      "POST /v1/console/auth/logout": () => new Response(null, { status: 204 }),
    });
    renderAt("/");

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/auth/logout")).toBe(true);
  });
});

describe("Settings", () => {
  const members = () =>
    json([{ user_id: "usr_1", email: "ada@example.com", display_name: "Ada", role: "owner", joined_at: "2026-09-21T00:00:00" }]);

  it("lets an owner rename the organization and lists members", async () => {
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/organization/members": members,
      "PATCH /v1/console/organization": () => json({ ...ME.organization, name: "Renamed" }),
    });
    renderAt("/settings");

    const org = await screen.findByRole("article", { name: "Organization" });
    fireEvent.change(within(org).getByLabelText("Name"), { target: { value: "Renamed" } });
    fireEvent.click(within(org).getByRole("button", { name: "Rename" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === "PATCH")).toBe(true),
    );
    const membersCard = screen.getByRole("article", { name: "Members" });
    expect(await within(membersCard).findByText("Ada · ada@example.com")).toBeTruthy();
  });

  it("does not offer renaming to a non-owner", async () => {
    healthyApi({}, { ...ME, organization: ME.memberships[1]! }, { "/v1/console/organization/members": members });
    renderAt("/settings");

    const org = await screen.findByRole("article", { name: "Organization" });
    expect(within(org).queryByRole("button", { name: "Rename" })).toBeNull();
    expect(within(org).getByText(/Only an owner can rename/)).toBeTruthy();
  });
});

describe("Navigation", () => {
  it("routes an undeclared surface to the not-yet-available page, never to sample data", async () => {
    healthyApi();
    renderAt("/conversations");

    expect(await screen.findByRole("heading", { name: "Conversations", level: 1 })).toBeTruthy();
    expect(screen.getByText("Not yet available — arrives in M12.5")).toBeTruthy();
  });

  it("badges undeclared surfaces as Soon and marks the current page", async () => {
    healthyApi();
    renderAt("/");

    const nav = await screen.findByRole("navigation", { name: "Console" });
    await within(nav).findAllByText("Soon");
    expect(within(nav).getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("link", { name: "Settings" }).textContent).toBe("Settings");
    expect(within(nav).getByRole("link", { name: "Playground" }).textContent).toBe("Playground");
    expect(within(nav).getByRole("link", { name: /Conversations/ }).textContent).toContain("Soon");
  });

  it("shows a not-found page for unknown paths", async () => {
    healthyApi();
    renderAt("/nowhere");
    expect(await screen.findByText("Page not found")).toBeTruthy();
  });

  it("explains an unreachable API instead of rendering an empty console", async () => {
    stubApi({});
    renderAt("/");
    expect(await screen.findByText("Could not load your account")).toBeTruthy();
  });

  it("treats malformed server metadata as an error, not as data", async () => {
    stubApi({
      "/v1/console/meta": () => json({}),
      "/health": () => json(HEALTH),
      "/v1/console/me": () => json(ME),
    });
    renderAt("/");

    expect(await screen.findByText("The server returned unrecognised console metadata")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Settings/ }).textContent).toContain("Soon");
  });
});

// Keep the fixture honest: the fake metadata is the server's shape at this revision.
it("the fixture metadata matches this build's revision", () => {
  expect(META.console_api_revision).toBe("m12.4");
});
