import { useEffect, useRef, useState } from "react";

import type { DiagnosticEvent, Severity } from "./diagnostics";

/**
 * The most recent events kept in the DOM. The durable log is unbounded; a browser's layout is not.
 * Older rows are not lost — they are in the session's history, and the panel says so.
 */
export const RENDER_LIMIT = 300;

const SEVERITY_LABEL: Record<Severity, string> = { info: "INFO", warn: "WARN", error: "ERROR" };

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false });
}

/** Measured stages, in the pipeline's own order. An unmeasured stage is simply not here. */
function Stages({ event }: { event: DiagnosticEvent }) {
  const stages = Object.entries(event.stages_ms ?? {});
  if (stages.length === 0 && event.voice_to_voice_ms == null) return null;
  return (
    <span className="diag__stages">
      {event.voice_to_voice_ms != null ? <strong>{event.voice_to_voice_ms} ms</strong> : null}
      {stages.map(([stage, ms]) => (
        <span key={stage} className="diag__stage">
          {stage} {ms}
        </span>
      ))}
    </span>
  );
}

function Row({ event }: { event: DiagnosticEvent }) {
  const [open, setOpen] = useState(false);
  const details = Object.entries(event.detail);
  const expandable = details.length > 0 || event.turn !== null;

  return (
    <li className={`diag__row diag__row--${event.severity}`}>
      <div className="diag__line">
        <time className="diag__time">{clock(event.ts)}</time>
        <span className={`diag__severity diag__severity--${event.severity}`}>
          {SEVERITY_LABEL[event.severity]}
        </span>
        <span className="diag__category">{event.category}</span>
        <span className="diag__message">{event.message}</span>
        <Stages event={event} />
        {expandable ? (
          <button
            type="button"
            className="diag__expand"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "Hide details" : "Details"}
          </button>
        ) : null}
      </div>
      {open ? (
        <dl className="diag__detail">
          <div>
            <dt>event</dt>
            <dd>
              <code>{event.type}</code>
            </dd>
          </div>
          <div>
            <dt>seq</dt>
            <dd>{event.seq}</dd>
          </div>
          <div>
            <dt>at</dt>
            <dd>+{event.offset_s.toFixed(3)}s</dd>
          </div>
          {event.turn !== null ? (
            <div>
              <dt>turn</dt>
              <dd>{event.turn}</dd>
            </div>
          ) : null}
          {details.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

export function DiagnosticsPanel({
  events,
  streamError,
  complete,
}: {
  events: DiagnosticEvent[];
  streamError: string | null;
  complete: boolean;
}) {
  const [hidden, setHidden] = useState(0);
  const listRef = useRef<HTMLOListElement | null>(null);
  const pinned = useRef(true);

  const visible = events.slice(Math.max(hidden, events.length - RENDER_LIMIT));
  const omitted = events.length - visible.length;

  // Follow the log only while the reader is already at the bottom, so reading history is never
  // interrupted by an arriving event.
  useEffect(() => {
    const list = listRef.current;
    if (list && pinned.current) list.scrollTop = list.scrollHeight;
  }, [events]);

  const onScroll = () => {
    const list = listRef.current;
    if (!list) return;
    pinned.current = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  };

  return (
    <article className="card" aria-labelledby="diagnostics-heading">
      <div className="diag__header">
        <h2 id="diagnostics-heading">Diagnostics</h2>
        <div className="diag__actions">
          <span className="muted">{events.length} events</span>
          {events.length > 0 ? (
            <button
              type="button"
              className="button button--secondary button--small"
              onClick={() => setHidden(events.length)}
              title="Hides them in this view only. The session's history is kept on the server."
            >
              Clear view
            </button>
          ) : null}
        </div>
      </div>

      {streamError ? (
        <p className="banner banner--warn" role="status">
          {streamError} Nothing is lost: the runtime's events are recorded, and the view resumes
          where it stopped.
        </p>
      ) : null}

      {events.length === 0 ? (
        <p className="note">
          Runtime events appear here as the call proceeds, and stay after it ends.
        </p>
      ) : (
        <>
          <ol className="diag" ref={listRef} onScroll={onScroll} aria-label="Runtime diagnostics">
            {visible.map((event) => (
              <Row key={event.seq} event={event} />
            ))}
          </ol>
          {omitted > 0 ? (
            <p className="note">
              Showing the most recent {visible.length} of {events.length}.
              {hidden > 0 ? " Cleared rows are hidden here only; the server keeps them." : null}
            </p>
          ) : null}
        </>
      )}
      <p className="note">
        Every row is one event the runtime recorded, in its own order. Stage durations arrive once
        a turn completes, which is when the runtime measures them.
        {complete ? " This session is finished, so this is its complete history." : null}
      </p>
    </article>
  );
}
