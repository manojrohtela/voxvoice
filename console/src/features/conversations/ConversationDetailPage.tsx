import { useCallback } from "react";
import { Link, useParams } from "react-router";

import { getJson } from "../../api/client";
import type { ConversationDetail } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { formatDuration, formatMs, formatWhen, STATUS_TONE } from "./format";

const STAGES: { key: "endpoint" | "stt" | "llm" | "tts" | "egress"; label: string }[] = [
  { key: "endpoint", label: "Endpoint" },
  { key: "stt", label: "Speech-to-text" },
  { key: "llm", label: "Model" },
  { key: "tts", label: "Voice" },
  { key: "egress", label: "Playback" },
];

function Detail({ c }: { c: ConversationDetail }) {
  return (
    <>
      <div className="grid">
        <article className="card" aria-labelledby="facts-heading">
          <h2 id="facts-heading">Session</h2>
          <dl className="facts">
            <div>
              <dt>Status</dt>
              <dd>
                <StatusPill tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</StatusPill>
              </dd>
            </div>
            <div>
              <dt>Agent</dt>
              <dd>{c.agent.name ? <Link to={`/agents/${c.agent.id}`}>{c.agent.name}</Link> : "deleted agent"}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{formatWhen(c.started_at)}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(c.duration_s)}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{c.source === "console_playground" ? "Playground" : "API"}</dd>
            </div>
            <div>
              <dt>Session ID</dt>
              <dd>
                <code>{c.id}</code>
              </dd>
            </div>
          </dl>
        </article>

        <article className="card" aria-labelledby="transcript-heading">
          <h2 id="transcript-heading">Transcript</h2>
          {!c.transcript_ordered ? (
            <p className="banner banner--warn" role="status">
              This session was recorded without timing, so the order of its lines is unknown.
            </p>
          ) : null}
          {c.transcript.length === 0 ? (
            <p className="note">Nothing was said in this session.</p>
          ) : (
            <ol className="transcript">
              {c.transcript.map((line, index) => (
                <li key={index} className={`transcript__line transcript__line--${line.role === "user" ? "caller" : "agent"}`}>
                  <span className="transcript__who">{line.role === "user" ? "Caller" : "Agent"}</span>
                  {c.transcript_ordered ? <time className="transcript__at">{formatDuration(line.offset_s)}</time> : null}
                  <p>{line.text}</p>
                </li>
              ))}
            </ol>
          )}
        </article>
      </div>

      {!c.events_recorded ? (
        <p className="note">
          No runtime events were recorded for this session (it predates event recording), so its turns and
          timeline are unknown.
        </p>
      ) : (
        <div className="grid">
          <article className="card" aria-labelledby="turns-heading">
            <h2 id="turns-heading">Turns</h2>
            {c.turns.length === 0 ? (
              <p className="note">No turn was completed in this session.</p>
            ) : (
              // Eight columns: wider than the content column between ~500 and ~770 px, so it
              // scrolls inside its own box rather than pushing the page sideways (M12.12 defect 2).
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Turn</th>
                      <th scope="col">Voice-to-voice</th>
                      {STAGES.map((s) => (
                        <th key={s.key} scope="col">
                          {s.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {c.turns.map((turn, index) => (
                      <tr key={index}>
                        <td>
                          {turn.turn ?? "—"}
                          {turn.status && turn.status !== "completed" ? <span className="muted"> · {turn.status}</span> : null}
                        </td>
                        <td>
                          <strong>{formatMs(turn.voice_to_voice_ms)}</strong>
                        </td>
                        {STAGES.map((s) => (
                          <td key={s.key} className={turn.stages_ms[s.key] === undefined ? "muted" : undefined}>
                            {formatMs(turn.stages_ms[s.key])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="note">Measured by the runtime on every turn. A stage it could not measure says so.</p>
          </article>

          <article className="card" aria-labelledby="timeline-heading">
            <h2 id="timeline-heading">Timeline</h2>
            <ol className="call-log">
              {c.timeline.map((entry) => (
                <li key={entry.seq}>
                  <time>+{entry.offset_s.toFixed(1)}s</time> {entry.label}
                  {entry.turn !== null ? <span className="muted"> · turn {entry.turn}</span> : null}
                </li>
              ))}
            </ol>
          </article>
        </div>
      )}
    </>
  );
}

export function ConversationDetailPage() {
  const { conversationId } = useParams();
  const load = useCallback(
    (signal: AbortSignal) => getJson<ConversationDetail>(`/v1/console/conversations/${conversationId}`, signal),
    [conversationId],
  );
  const conversation = useResource(load);

  return (
    <section className="page">
      <header className="page__header">
        <p className="breadcrumb">
          <Link to="/conversations">Conversations</Link>
        </p>
        <h1>
          {conversation.status === "ready"
            ? `${conversation.data.agent.name ?? "Conversation"} · ${formatWhen(conversation.data.started_at)}`
            : "Conversation"}
        </h1>
      </header>
      {conversation.status === "loading" ? <Loading /> : null}
      {conversation.status === "error" ? (
        <ErrorState title="Could not load this conversation" error={conversation.error} onRetry={conversation.reload} />
      ) : null}
      {conversation.status === "ready" ? <Detail c={conversation.data} /> : null}
    </section>
  );
}
