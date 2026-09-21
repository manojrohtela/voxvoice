// Analytics formatting. The one rule: a missing value reads "not measured" (the Conversations
// wording), and only a real number, 0 included, is shown as a number.

export const NOT_MEASURED = "not measured";

/** Product names for the latency measures `/analytics/latency` returns (generic stages, no vendors). */
export const LATENCY_LABELS: Record<string, string> = {
  voice_to_voice: "Voice-to-voice",
  endpoint: "End of speech → turn detected",
  stt: "Speech recognition",
  llm: "Language model",
  tts: "Speech synthesis",
  egress: "Audio out",
};

export function formatValue(value: number | null | undefined, unit = ""): string {
  if (value === null || value === undefined) return NOT_MEASURED;
  const shown = Number.isInteger(value) ? String(value) : value.toFixed(value < 10 ? 2 : 1);
  return `${shown}${unit}`;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return NOT_MEASURED;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** A UTC day (`YYYY-MM-DD`) as a short label. Formatted in UTC so the day never shifts. */
export function formatDay(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export interface AnalyticsFilters {
  preset: string;
  from: string;
  to: string;
  agentId: string;
  source: string;
}

/** The query string every analytics endpoint takes. `custom` is a UI state, sent as from/to. */
export function analyticsQuery({ preset, from, to, agentId, source }: AnalyticsFilters): string {
  const params = new URLSearchParams();
  if (preset === "custom") {
    params.set("from", from);
    params.set("to", to);
  } else {
    params.set("range", preset);
  }
  if (agentId) params.set("agent_id", agentId);
  if (source) params.set("source", source);
  return `?${params.toString()}`;
}
