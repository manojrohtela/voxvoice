export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  // Stored as naive UTC by the M11 model; read it as UTC.
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatMs(value: number | null | undefined): string {
  return value === null || value === undefined ? "not measured" : `${value} ms`;
}

export const STATUS_TONE: Record<string, "ok" | "warn" | "error" | "neutral"> = {
  active: "warn",
  ended: "ok",
  failed: "error",
  created: "neutral",
};
