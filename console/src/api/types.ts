// Wire types for the endpoints the console reads. They mirror the server's response models
// (api/console/routers/meta.py and the M11 /health) and nothing more.

/** The console API revision this build was written against (`/v1/console/meta`). */
export const EXPECTED_CONSOLE_API_REVISION = "m12.4";

export interface RuntimeDescription {
  transport: string;
  webrtc_signalling: boolean;
  ice_policy: string;
}

export interface SignupDescription {
  mode: "first_run" | "open" | "closed";
  available: boolean;
}

export interface ConsoleMeta {
  api_version: "v1";
  console_api_revision: string;
  runtime: RuntimeDescription;
  capabilities: string[];
  console_bundle: boolean;
  signup: SignupDescription;
}

export type Role = "owner" | "admin" | "developer" | "viewer";

export interface UserView {
  id: string;
  email: string;
  display_name: string;
}

export interface OrganizationView {
  id: string;
  name: string;
  role: Role;
}

/** `GET /v1/console/me` — the signed-in user, the active organization and every membership. */
export interface Me {
  user: UserView;
  organization: OrganizationView | null;
  memberships: OrganizationView[];
}

export interface MemberView {
  user_id: string;
  email: string;
  display_name: string;
  role: Role;
  joined_at: string;
}

/** An agent as the console sees it (M12.3): only the fields that reach the runtime. */
export interface AgentView {
  id: string;
  name: string;
  system_prompt: string;
  first_message: string | null;
  language: string;
  /** False when the speech recognizer would refuse this language (the call would drop). */
  language_supported: boolean;
  llm_model: string | null;
  tts_voice: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentOptions {
  languages: string[];
  default_language: string;
}

export interface AgentInput {
  name: string;
  system_prompt: string;
  first_message: string | null;
  language: string;
  llm_model: string | null;
  tts_voice: string | null;
}

/** `GET /health` — owned by the M11 WebRTC application, read as-is. */
export interface Health {
  ok: boolean;
  db: boolean;
  transport?: string;
  webrtc_signalling?: boolean;
  ice_policy?: string;
}
