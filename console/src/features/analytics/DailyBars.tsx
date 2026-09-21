import type { SeriesBucket } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { formatDay } from "./format";

/**
 * Conversations (and turns) per UTC day, as the analytics time series returns them. Shared by
 * Analytics and the Overview so both draw the same buckets the same way. With no conversation in
 * the range it says so rather than drawing an empty chart.
 */
export function DailyBars({ buckets }: { buckets: SeriesBucket[] }) {
  const peak = Math.max(0, ...buckets.map((b) => b.conversations));
  if (peak === 0) return <EmptyState title="No conversations in this range" />;
  return (
    <ol className="bars" aria-label="Conversations per day (UTC)">
      {buckets.map((b) => (
        <li key={b.date} className="bars__row">
          <span className="bars__label">{formatDay(b.date)}</span>
          <span className="bars__track" aria-hidden="true">
            <span className="bars__fill" style={{ width: `${(b.conversations / peak) * 100}%` }} />
          </span>
          <span className="bars__value">
            {b.conversations} <span className="muted">· {b.turns} turns</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
