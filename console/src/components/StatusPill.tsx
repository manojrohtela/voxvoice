export type Tone = "ok" | "warn" | "error" | "neutral";

/** Status is always spoken in text; the colour only reinforces it. */
export function StatusPill({ tone, children }: { tone: Tone; children: string }) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}
