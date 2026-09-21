import { describe, expect, it, vi } from "vitest";

import { json, stubApi } from "../test/fakeApi";
import { ApiError, getJson, toApiError } from "./client";

describe("toApiError", () => {
  it("reads the console envelope", () => {
    const error = toApiError(404, {
      error: { type: "not_found", message: "Agent not found", request_id: "req-1" },
    });
    expect(error).toMatchObject({ status: 404, type: "not_found", message: "Agent not found", requestId: "req-1" });
  });

  it("reads the developer API's string detail and derives the type from the status", () => {
    const error = toApiError(401, { detail: "Missing x-api-key" }, "req-2");
    expect(error).toMatchObject({ status: 401, type: "unauthenticated", message: "Missing x-api-key", requestId: "req-2" });
  });

  it("summarises validation details instead of dumping them", () => {
    const error = toApiError(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] });
    expect(error.type).toBe("invalid_request");
    expect(error.message).toBe("The request was not valid");
  });

  it("falls back for unknown bodies", () => {
    expect(toApiError(502, "<html>")).toMatchObject({ type: "internal", message: "Request failed (502)" });
    expect(toApiError(418, undefined).type).toBe("http_error");
  });
});

describe("getJson", () => {
  it("returns the parsed body on success, same-origin", async () => {
    const fetchMock = stubApi({ "/health": () => json({ ok: true, db: true }) });

    await expect(getJson("/health")).resolves.toEqual({ ok: true, db: true });
    expect(fetchMock).toHaveBeenCalledWith("/health", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("raises an ApiError carrying the response's request id", async () => {
    stubApi({
      "/v1/console/x": () => json({ detail: "Nope" }, 403, { "x-request-id": "hdr-9" }),
    });

    const error = await getJson("/v1/console/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, type: "forbidden", requestId: "hdr-9" });
  });

  it("refuses a successful response whose body is not JSON", async () => {
    stubApi({ "/health": () => new Response("<html>proxy page</html>", { status: 200 }) });

    await expect(getJson("/health")).rejects.toMatchObject({ status: 200, type: "invalid_response" });
  });

  it("turns a network failure into a readable error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    await expect(getJson("/health")).rejects.toMatchObject({ status: 0, type: "network" });
  });
});
