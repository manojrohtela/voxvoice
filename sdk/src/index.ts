// @voxvoice/sdk — a typed client for the VoxVoice developer API (v1). See README.md.
//
// Public surface: the client, its error, and the API's types. The HTTP transport and helpers are
// internal. The browser-side signalling client is the separate entry `@voxvoice/sdk/signalling`.

export { VoxVoiceClient, type VoxVoiceClientOptions } from "./client.js";
export { VoxVoiceError } from "./errors.js";
export type { RequestOptions } from "./http.js";
export type { Agents } from "./resources/agents.js";
export type { Sessions } from "./resources/sessions.js";
export type { WebRTC } from "./resources/webrtc.js";
export type * from "./types.js";
