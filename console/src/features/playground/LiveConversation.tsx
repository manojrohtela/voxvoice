import { StatusPill, type Tone } from "../../components/StatusPill";
import type { RuntimeStateName, TranscriptLine } from "./diagnostics";

/**
 * What the runtime heard and said, while it is happening.
 *
 * These are the **persisted** transcript rows, served by the same read the Conversations page
 * performs — so what is on screen during the call is what will be on screen after it, by
 * construction rather than by reconciliation. Interim (non-final) recognition is not shown
 * because this runtime does not record it; the note says so rather than leaving a gap to guess at.
 */

const STATE_LABEL: Record<RuntimeStateName, [string, Tone]> = {
  CONNECTING: ["Connecting", "warn"],
  CONNECTED: ["Connected", "ok"],
  LISTENING: ["Listening", "ok"],
  USER_SPEAKING: ["Caller speaking", "ok"],
  PROCESSING: ["Processing", "warn"],
  ASSISTANT_SPEAKING: ["Agent speaking", "ok"],
  ENDING: ["Ending", "warn"],
  ENDED: ["Ended", "neutral"],
  ERROR: ["Error", "error"],
};

/** The order a turn moves through, so the current state reads as progress rather than a word. */
const PROGRESS: { state: RuntimeStateName; label: string }[] = [
  { state: "LISTENING", label: "Listening" },
  { state: "USER_SPEAKING", label: "Caller speaking" },
  { state: "PROCESSING", label: "Processing" },
  { state: "ASSISTANT_SPEAKING", label: "Agent speaking" },
];

export function RuntimeStatePanel({ state, turn }: { state: RuntimeStateName; turn: number | null }) {
  const [label, tone] = STATE_LABEL[state];
  return (
    <article className="card" aria-labelledby="runtime-state-heading">
      <h2 id="runtime-state-heading">Runtime</h2>
      <div className="call">
        <StatusPill tone={tone}>{label}</StatusPill>
        {turn !== null ? <span className="muted">Turn {turn}</span> : null}
      </div>
      <ul className="runtime-steps" aria-label="Turn progress">
        {PROGRESS.map((step) => (
          <li
            key={step.state}
            className={step.state === state ? "runtime-steps__step is-current" : "runtime-steps__step"}
            aria-current={step.state === state ? "step" : undefined}
          >
            <span className="runtime-steps__dot" aria-hidden="true" />
            {step.label}
          </li>
        ))}
      </ul>
      <p className="note">The runtime's own state, derived from the events it recorded.</p>
    </article>
  );
}

export function LiveConversation({
  transcript,
  ordered,
  state,
}: {
  transcript: TranscriptLine[];
  ordered: boolean;
  state: RuntimeStateName;
}) {
  return (
    <article className="card" aria-labelledby="live-conversation-heading">
      <h2 id="live-conversation-heading">Conversation</h2>
      {transcript.length === 0 ? (
        <p className="note">
          {state === "ENDED"
            ? "Nothing was said in this session."
            : "What the runtime hears and says appears here, line by line."}
        </p>
      ) : (
        <ol className="transcript">
          {transcript.map((line, index) => (
            <li
              key={`${index}-${line.offset_s}`}
              className={`transcript__line transcript__line--${line.role === "user" ? "caller" : "agent"}`}
            >
              <span className="transcript__who">{line.role === "user" ? "Caller" : "Agent"}</span>
              {ordered ? <time className="transcript__at">+{line.offset_s.toFixed(1)}s</time> : null}
              <p>{line.text}</p>
            </li>
          ))}
        </ol>
      )}
      <p className="note">
        Finalized transcript, as recorded. This runtime does not record interim recognition, so
        partial text is not shown rather than guessed at.
      </p>
    </article>
  );
}
