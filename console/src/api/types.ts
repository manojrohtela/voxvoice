// Wire types for the endpoints the console reads. They mirror the server's response models
// (api/console/routers/meta.py and the M11 /health) and nothing more.

/** The console API revision this build was written against (`/v1/console/meta`). */
export const EXPECTED_CONSOLE_API_REVISION = "m12.7";

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

/** A conversation is one M11 session (M12.5). */
export interface ConversationSummary {
  id: string;
  agent: { id: string; name: string | null };
  status: string;
  channel: string;
  source: "console_playground" | "api";
  started_at: string | null;
  ended_at: string | null;
  duration_s: number | null;
  message_count: number;
}

export interface ConversationPage {
  data: ConversationSummary[];
  next_cursor: string | null;
}

export interface TurnView {
  turn: number | null;
  status: string | null;
  /** Null when the runtime could not measure it — never shown as 0. */
  voice_to_voice_ms: number | null;
  stages_ms: Partial<Record<"endpoint" | "stt" | "llm" | "tts" | "egress", number>>;
  user_speech_ms: number | null;
  unmeasured: string[];
}

export interface ConversationDetail extends ConversationSummary {
  transcript: { role: string; text: string; offset_s: number }[];
  transcript_ordered: boolean;
  turns: TurnView[];
  timeline: { seq: number; type: string; label: string; turn: number | null; offset_s: number }[];
  events_recorded: boolean;
}

// ── analytics (M12.6) — contract: docs/architecture/epic12/m12-analytics-definitions.md ──

export type AnalyticsPreset = "today" | "7d" | "30d";

/** What the server resolved, echoed on every analytics response. Days are UTC. */
export interface AnalyticsQuery {
  range: {
    preset: AnalyticsPreset | null;
    first_day: string;
    last_day: string;
    start: string;
    end: string;
    timezone: "UTC";
    bucket: "day";
  };
  filters: { agent_id: string | null; source: "playground" | "api" | null };
}

export interface AnalyticsSummary {
  query: AnalyticsQuery;
  conversations: {
    total: number;
    by_status: Record<string, number>;
    by_source: Record<string, number>;
    recorded: number;
    not_recorded: number;
  };
  /** Averages are null when nothing was measured — never 0. */
  duration: { measured: number; unmeasured: number; total_s: number | null; avg_s: number | null };
  turns: { total: number; by_status: Record<string, number>; avg_per_conversation: number | null };
}

export interface LatencyFigure {
  metric: string;
  turns: number;
  samples: number;
  missing: number;
  avg_ms: number | null;
  min_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
}

export interface LatencyReport {
  query: AnalyticsQuery;
  data: LatencyFigure[];
}

export interface SeriesBucket {
  date: string;
  conversations: number;
  turns: number;
  avg_duration_s: number | null;
  voice_to_voice_samples: number;
  voice_to_voice_p50_ms: number | null;
}

export interface SeriesReport {
  query: AnalyticsQuery;
  data: SeriesBucket[];
}

export interface AgentUsage {
  id: string;
  name: string | null;
  archived: boolean;
  conversations: number;
  recorded: number;
  turns: number;
  avg_duration_s: number | null;
  avg_turns_per_conversation: number | null;
  voice_to_voice: LatencyFigure;
}

export interface AgentReport {
  query: AnalyticsQuery;
  data: AgentUsage[];
}

// ── developer API keys (M12.7) ──

/** A key as the console may show it after creation: no key material at all. */
export interface ApiKeyView {
  /** Non-secret identifier (`key_…`); it cannot authenticate. */
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
}

/** The create response: the only object that ever carries the key itself. Never persisted. */
export interface ApiKeyCreated extends ApiKeyView {
  key: string;
}

/** `GET /health` — owned by the M11 WebRTC application, read as-is. */
export interface Health {
  ok: boolean;
  db: boolean;
  transport?: string;
  webrtc_signalling?: boolean;
  ice_policy?: string;
}
