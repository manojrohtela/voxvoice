import { useEffect, useState } from "react";

/** A loading indicator that appears only after a short delay, so fast responses don't flash. */
export function Loading({ label = "Loading…", delayMs = 150 }: { label?: string; delayMs?: number }) {
  const [visible, setVisible] = useState(delayMs === 0);

  useEffect(() => {
    if (delayMs === 0) return;
    const timer = setTimeout(() => setVisible(true), delayMs);
    return () => clearTimeout(timer);
  }, [delayMs]);

  return (
    <div className="loading" role="status" aria-live="polite">
      {visible ? (
        <>
          <span className="loading__spinner" aria-hidden="true" />
          {label}
        </>
      ) : null}
    </div>
  );
}
