// The developer API's shapes, transcribed from the server's schemas (`platform/api/schemas.py`,
// `platform/api/routers/webrtc.py`). `contract/developer-openapi.json` is that server's OpenAPI for
// the developer surface; `test/contract.test.ts` fails if a type here and a schema there differ in
// their fields. Values are passed through unchanged: `null` stays `null`, and timestamps stay the
// strings the server sends (ISO 8601 in UTC **without** an offset, e.g. "2026-09-21T12:27:16.405839").

/** Any JSON value the server stores and returns without interpreting (`metadata`, `tools`). */
export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

// ── agents ────────────────────────────────────────────────────────────────────

/** An agent, as every agent endpoint returns it. */
export interface Agent {
  id: string;
  org_id: string;
  name: string;
  system_prompt: string;
  first_message: string | null;
  language: string;
  stt_provider: string;
  tts_provider: string;
  tts_voice: string | null;
  llm_model: string | null;
  /** Stored and returned, but not applied to calls today (barge-in is not available). */
  allow_interruptions: boolean;
  /** Stored and returned, but not applied to calls today. */
  min_endpointing_delay: number;
  /** Stored and returned, but not applied to calls today. */
  max_endpointing_delay: number;
  /** Stored and returned, but not applied to calls today. */
  tools: Json[];
  metadata: { [key: string]: Json };
  created_at: string;
  updated_at: string;
}

/** `POST /v1/agents`. Only `name` and `system_prompt` are required; the server fills the rest. */
export interface AgentCreate {
  name: string;
  system_prompt: string;
  first_message?: string | null;
  /** Server default `"en-IN"`, which the speech recognizer does not support today: set it (e.g. `"en"`). */
  language?: string;
  /** Leave as `"default"`; providers are chosen by the server operator. */
  stt_provider?: string;
  /** Leave as `"default"`; providers are chosen by the server operator. */
  tts_provider?: string;
  tts_voice?: string | null;
  llm_model?: string | null;
  allow_interruptions?: boolean;
  min_endpointing_delay?: number;
  max_endpointing_delay?: number;
  tools?: Json[];
  metadata?: { [key: string]: Json };
}

/**
 * `PATCH /v1/agents/{id}`: only the fields you send change. `null` clears the three nullable
 * fields. The server schema also accepts `null` for the others, but storing it fails (`500`), so
 * this type does not offer it.
 */
export interface AgentUpdate {
  name?: string;
  system_prompt?: string;
  first_message?: string | null;
  language?: string;
  stt_provider?: string;
  tts_provider?: string;
  tts_voice?: string | null;
  llm_model?: string | null;
  allow_interruptions?: boolean;
  min_endpointing_delay?: number;
  max_endpointing_delay?: number;
  tools?: Json[];
  metadata?: { [key: string]: Json };
}

/** `DELETE /v1/agents/{id}`: a permanent delete (not an archive). */
export interface AgentDeleted {
  deleted: string;
}

// ── sessions ──────────────────────────────────────────────────────────────────

/** `POST /v1/sessions` and `POST /v1/webrtc/sessions`. */
export interface SessionCreate {
  agent_id: string;
  /** Server default `"web"`. */
  channel?: string;
  metadata?: { [key: string]: Json };
}

export interface WebConnection {
  token: string;
  url: string;
  room: string;
}

export interface PhoneConnection {
  call_id: string;
}

/** What starting a session returns. `web` and `phone` are always `null` today. */
export interface SessionCreated {
  session_id: string;
  engine: string;
  channel: string;
  web?: WebConnection | null;
  phone?: PhoneConnection | null;
}

/** A session. `status` is a string; the server writes `"active"`, `"ended"` and `"failed"`. */
export interface Session {
  id: string;
  org_id: string;
  agent_id: string;
  engine: string;
  engine_ref: string | null;
  channel: string;
  status: string;
  started_at: string | null;
  /** `null` until the session ends. */
  ended_at: string | null;
  metadata: { [key: string]: Json };
}

/** One final transcript line. `role` is `"user"` or `"agent"`; `ts` is seconds since the session started. */
export interface TranscriptItem {
  session_id: string;
  role: string;
  text: string;
  ts: number;
}

/** `POST /v1/sessions/{id}/end`. Ending an ended session returns the same body. */
export interface SessionEnded {
  session_id: string;
  status: string;
}

// ── WebRTC ────────────────────────────────────────────────────────────────────

/** `POST /v1/webrtc/rendezvous`: hand `rendezvous_id` and `signalling_token` to the caller's browser. */
export interface Rendezvous {
  rendezvous_id: string;
  /** Single-purpose and short-lived: it can exchange one offer/answer. It is not an API key. */
  signalling_token: string;
  expires_in_s: number;
}

/** `POST /v1/webrtc/sessions`: `SessionCreate` plus the rendezvous the caller reserved. */
export interface WebRTCSessionCreate extends SessionCreate {
  rendezvous_id: string;
}

/** The runtime's SDP offer, for the browser's `RTCPeerConnection`. */
export interface Offer {
  sdp: string;
}
