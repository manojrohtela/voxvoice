// Imports the BUILT package by its name, the way a consumer does (Node resolves the package's own
// "exports"), and checks its public surface. Run with `npm run test:package`.
import assert from "node:assert/strict";

const sdk = await import("@voxvoice/sdk");
const signalling = await import("@voxvoice/sdk/signalling");

assert.deepEqual(Object.keys(sdk).sort(), ["VoxVoiceClient", "VoxVoiceError"]);
assert.deepEqual(Object.keys(signalling).sort(), ["SignallingClient", "VoxVoiceError"]);

// Internals are not importable.
for (const internal of ["@voxvoice/sdk/http", "@voxvoice/sdk/dist/http.js", "@voxvoice/sdk/client"]) {
  await assert.rejects(import(internal), (e) => e.code === "ERR_PACKAGE_PATH_NOT_EXPORTED", internal);
}

const seen = [];
const client = new sdk.VoxVoiceClient({
  apiKey: "vf_package-smoke-not-a-real-key",
  baseUrl: "http://voxvoice.test/",
  fetch: async (url, init) => {
    seen.push([init.method, url, init.headers["x-api-key"] !== undefined]);
    return new Response("[]", { status: 200 });
  },
});
assert.deepEqual(await client.agents.list(), []);
assert.deepEqual(seen, [["GET", "http://voxvoice.test/v1/agents", true]]);
console.log("package smoke: ok (exports, types entry, request)");
