import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ApiKeyView } from "../../api/types";
import { App } from "../../app/App";
import { ME, healthyApi, json } from "../../test/fakeApi";

// A fixture value shaped like a key. It is not a real credential.
const SECRET = `vf_${"T".repeat(43)}`;

const KEY: ApiKeyView = {
  id: "key_1",
  label: "billing-backend",
  created_at: "2026-09-21T10:00:00",
  last_used_at: null,
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

const DEVELOPER = { ...ME, organization: { ...ME.organization!, role: "developer" as const } };

describe("API keys", () => {
  it("lists keys by label and id, with no key material", async () => {
    healthyApi({}, ME, { "/v1/console/api-keys": () => json({ data: [KEY] }) });
    renderAt("/developers");

    const row = (await screen.findByText("billing-backend")).closest("tr")!;
    expect(within(row).getByText("key_1")).toBeTruthy();
    expect(within(row).getByText("never")).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Revoke billing-backend" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read the developer docs" }).getAttribute("href")).toBe("/developers/docs");
  });

  it("shows the key once, copies it on request, and never stores it", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const writeText = vi.fn(async () => undefined);
    stubClipboard(writeText);
    let keys: ApiKeyView[] = [];
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/api-keys": () => json({ data: keys }),
      "POST /v1/console/api-keys": () => {
        keys = [KEY];
        return json({ ...KEY, key: SECRET }, 201);
      },
    });
    const { unmount } = renderAt("/developers");

    expect(await screen.findByText("No API keys yet")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "billing-backend" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    const panel = await screen.findByRole("article", { name: "Copy your new key now" });
    expect(within(panel).getByText(/shown only once/)).toBeTruthy();
    expect((within(panel).getByLabelText("billing-backend") as HTMLInputElement).value).toBe(SECRET);
    // Sent with the console CSRF header, as a body — never in a URL.
    const create = fetchMock.mock.calls.find((c) => c[0] === "/v1/console/api-keys" && c[1]?.method === "POST")!;
    expect((create[1]!.headers as Record<string, string>)["x-voxvoice-console"]).toBe("1");
    expect(JSON.parse(String(create[1]!.body))).toEqual({ label: "billing-backend" });
    // The list reloads and shows the key's id only.
    expect(await screen.findByText("key_1")).toBeTruthy();

    fireEvent.click(within(panel).getByRole("button", { name: "Copy key" }));
    expect(await within(panel).findByText("Copied")).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(SECRET);

    fireEvent.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("article", { name: "Copy your new key now" })).toBeNull();
    expect(document.body.textContent).not.toContain(SECRET);

    // A fresh load has nowhere to get it from: no storage, no URL, and the list never returns it.
    unmount();
    renderAt("/developers");
    expect(await screen.findByText("key_1")).toBeTruthy();
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(setItem).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every((c) => !String(c[0]).includes(SECRET))).toBe(true);
  });

  it("leaves the key selectable when the clipboard is unavailable", async () => {
    stubClipboard(async () => {
      throw new Error("denied");
    });
    healthyApi({}, ME, {
      "/v1/console/api-keys": () => json({ data: [] }),
      "POST /v1/console/api-keys": () => json({ ...KEY, key: SECRET }, 201),
    });
    renderAt("/developers");

    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    const panel = await screen.findByRole("article", { name: "Copy your new key now" });
    fireEvent.click(within(panel).getByRole("button", { name: "Copy key" }));

    expect(await within(panel).findByText(/copy it manually/)).toBeTruthy();
    expect((within(panel).getByRole("textbox") as HTMLInputElement).value).toBe(SECRET);
  });

  it("revokes a key only after confirmation", async () => {
    let keys = [KEY];
    const fetchMock = healthyApi({}, ME, {
      "/v1/console/api-keys": () => json({ data: keys }),
      "POST /v1/console/api-keys/key_1/revoke": () => {
        keys = [];
        return new Response(null, { status: 204 });
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderAt("/developers");

    const revoke = await screen.findByRole("button", { name: "Revoke billing-backend" });
    fireEvent.click(revoke);
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/revoke"))).toBe(false);

    fireEvent.click(revoke);
    expect(await screen.findByText("No API keys yet")).toBeTruthy();
    expect(confirm).toHaveBeenCalledTimes(2);
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/revoke"))!;
    expect((call[1]!.headers as Record<string, string>)["x-voxvoice-console"]).toBe("1");
  });

  it("shows the server's refusal to revoke", async () => {
    healthyApi({}, ME, {
      "/v1/console/api-keys": () => json({ data: [KEY] }),
      "POST /v1/console/api-keys/key_1/revoke": () =>
        json({ error: { type: "not_found", message: "API key not found" } }, 404),
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAt("/developers");

    fireEvent.click(await screen.findByRole("button", { name: "Revoke billing-backend" }));
    expect((await screen.findByRole("alert")).textContent).toBe("API key not found");
  });

  it("has loading and error states for the list", async () => {
    let fail = true;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    healthyApi({}, ME, {
      "/v1/console/api-keys": async () => {
        await gate;
        return fail
          ? json({ error: { type: "internal", message: "Database unavailable", request_id: "req_7" } }, 500)
          : json({ data: [KEY] });
      },
    });
    renderAt("/developers");

    const card = await screen.findByRole("article", { name: "Active keys" });
    expect(await within(card).findByRole("status")).toBeTruthy();
    await act(async () => release());
    expect((await within(card).findByRole("alert")).textContent).toContain("Database unavailable");

    fail = false;
    fireEvent.click(within(card).getByRole("button", { name: "Try again" }));
    expect(await within(card).findByText("billing-backend")).toBeTruthy();
  });

  it("does not offer key management to a developer, and does not ask the server for keys", async () => {
    const fetchMock = healthyApi({}, DEVELOPER, {});
    renderAt("/developers");

    expect(await screen.findByText("Only an owner or admin can manage API keys")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create key" })).toBeNull();
    expect(screen.getByRole("link", { name: "developer docs" }).getAttribute("href")).toBe("/developers/docs");
    expect(fetchMock.mock.calls.some((c) => c[0] === "/v1/console/api-keys")).toBe(false);
  });
});

const SPEC = {
  paths: {
    "/v1/agents": {
      get: { summary: "List Agents", "x-voxvoice-surface": "developer", responses: { "200": {} } },
    },
    "/v1/webrtc/rendezvous/{rendezvous_id}/offer": {
      get: { summary: "Get Offer", "x-voxvoice-surface": "signalling", responses: { "200": {}, "204": {} } },
    },
    "/v1/console/api-keys": {
      get: { summary: "List Api Keys", "x-voxvoice-surface": "console", responses: { "200": {} } },
    },
    "/health": { get: { summary: "Health", "x-voxvoice-surface": "public", responses: { "200": {} } } },
  },
};

describe("Developer docs", () => {
  it("explains authentication, resources, errors and what is not available, for every role", async () => {
    healthyApi({}, DEVELOPER, { "/openapi.json": () => json(SPEC) });
    renderAt("/developers/docs");

    expect(await screen.findByRole("heading", { name: "Developer docs", level: 1 })).toBeTruthy();
    const auth = screen.getByRole("article", { name: "Authentication" });
    expect(within(auth).getByText("x-api-key: <YOUR_API_KEY>")).toBeTruthy();
    expect(within(auth).getByText(/Not a provider key/)).toBeTruthy();
    for (const section of ["Agents", "Sessions", "Connecting a caller (WebRTC)", "Errors", "Examples", "Not available yet"]) {
      expect(screen.getByRole("article", { name: section })).toBeTruthy();
    }
    const unavailable = screen.getByRole("article", { name: "Not available yet" });
    expect(within(unavailable).getByText(/barge-in/)).toBeTruthy();
    const agents = screen.getByRole("article", { name: "Agents" });
    expect(within(agents).getAllByText("/v1/agents/{agent_id}")).toHaveLength(3); // GET, PATCH, DELETE
  });

  it("builds the reference from the server's OpenAPI, developer operations only", async () => {
    healthyApi({}, ME, { "/openapi.json": () => json(SPEC) });
    renderAt("/developers/docs");

    const reference = await screen.findByRole("article", { name: "Reference" });
    const table = await within(reference).findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").querySelector("code")!.textContent)).toEqual([
      "/v1/agents",
      "/v1/webrtc/rendezvous/{rendezvous_id}/offer",
    ]);
    expect(within(rows[1]!).getByText("signalling token")).toBeTruthy();
    expect(within(table).queryByText("/v1/console/api-keys")).toBeNull();
  });

  it("says when the reference cannot be loaded", async () => {
    healthyApi({}, ME, {});
    renderAt("/developers/docs");

    const reference = await screen.findByRole("article", { name: "Reference" });
    expect(await within(reference).findByText("Could not load the API reference")).toBeTruthy();
    await waitFor(() => expect(within(reference).getByRole("button", { name: "Try again" })).toBeTruthy());
  });
});
