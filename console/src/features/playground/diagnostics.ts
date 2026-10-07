// The diagnostics stream, as the browser sees it (M12.14).
//
// One `EventSource` over the console's own cookie — no token, no second credential, no library.
// Every frame is a batch of events the server has *already recorded*, so this holds no truth of
// its own: it accumulates what the durable log says, in `seq` order, and nothing else. A dropped
// connection is not a lost event; reconnecting with the last `seq` seen resumes exactly where it
// left off, which is why there is no live-versus-replayed distinction to draw.

export type Severity = "info" | "warn" | "error";

/** Mirrors `api/console/diagnostics/service.py`. Every field is already browser-safe. */
export interface DiagnosticEvent {
  seq: number;
  offset_s: number;
  ts: number;
  type: string;
  category: string;
  severity: Severity;
  message: string;
  turn: number | null;
  detail: Record<string, string | number>;
  /** `metric.turn` only. A stage the runtime could not measure is absent, never 0. */
  stages_ms?: Record<string, number>;
  voice_to_voice_ms?: number | null;
}

export interface TranscriptLine {
  role: string;
  text: string;
  offset_s: number;
}

/** The states the server derives from the runtime's own events. */
export type RuntimeStateName =
  | "CONNECTING"
  | "CONNECTED"
  | "LISTENING"
  | "USER_SPEAKING"
  | "PROCESSING"
  | "ASSISTANT_SPEAKING"
  | "ENDING"
  | "ENDED"
  | "ERROR";

export interface DiagnosticsSnapshot {
  events: DiagnosticEvent[];
  transcript: TranscriptLine[];
  transcriptOrdered: boolean;
  state: RuntimeStateName;
  /** True once the server has said the session produced everything it will produce. */
  complete: boolean;
  /** Set when the stream itself could not be read — not a runtime failure. */
  streamError: string | null;
}

export const EMPTY: DiagnosticsSnapshot = {
  events: [],
  transcript: [],
  transcriptOrdered: true,
  state: "CONNECTING",
  complete: false,
  streamError: null,
};

interface Batch {
  events: DiagnosticEvent[];
  transcript: TranscriptLine[];
  transcript_ordered: boolean;
  state: RuntimeStateName;
  last_seq: number;
}

/** Injected so tests can drive the stream without a browser (as `CallEnvironment` is). */
export type StreamFactory = (url: string) => EventSource;

export const browserStream: StreamFactory = (url) => new EventSource(url, { withCredentials: true });

/** How long to wait before resuming a stream that dropped mid-call. */
export const RETRY_MS = 1500;

export interface DiagnosticsStream {
  close(): void;
}

/**
 * Follow one session's diagnostics, calling `onChange` with the accumulated snapshot.
 *
 * Events are merged by `seq`, so a resumed or overlapping stream can never duplicate a row or
 * reorder one. The transcript is replaced wholesale, because the server sends the whole of it:
 * it is the same read the Conversations page performs, so the two cannot drift.
 */
export function followDiagnostics(
  sessionId: string,
  onChange: (snapshot: DiagnosticsSnapshot) => void,
  open: StreamFactory = browserStream,
  retryMs: number = RETRY_MS,
): DiagnosticsStream {
  let snapshot: DiagnosticsSnapshot = { ...EMPTY };
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;
  let lastSeq = 0;

  const emit = () => onChange(snapshot);

  const merge = (batch: Batch) => {
    const bySeq = new Map(snapshot.events.map((event) => [event.seq, event]));
    for (const event of batch.events) bySeq.set(event.seq, event);
    const events = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
    lastSeq = Math.max(lastSeq, batch.last_seq ?? 0);
    snapshot = {
      ...snapshot,
      events,
      transcript: batch.transcript ?? snapshot.transcript,
      transcriptOrdered: batch.transcript_ordered ?? snapshot.transcriptOrdered,
      state: batch.state ?? snapshot.state,
      streamError: null,
    };
    emit();
  };

  const connect = () => {
    if (closed) return;
    // Diagnostics are an auxiliary observation: if the stream cannot even be opened — no
    // `EventSource` in this environment, say — the call itself must be unaffected (DL-0013).
    let stream: EventSource;
    try {
      stream = open(`/v1/console/playground/sessions/${encodeURIComponent(sessionId)}/diagnostics?after_seq=${lastSeq}`);
    } catch {
      closed = true;
      snapshot = { ...snapshot, streamError: "Diagnostics are not available in this browser." };
      emit();
      return;
    }
    source = stream;

    stream.addEventListener("batch", (event) => {
      try {
        merge(JSON.parse((event as MessageEvent<string>).data) as Batch);
      } catch {
        // A frame we cannot parse is a frame we ignore; the next one carries the same state.
      }
    });

    stream.addEventListener("end", () => {
      snapshot = { ...snapshot, complete: true };
      emit();
      closed = true;
      stream.close();
    });

    stream.onerror = () => {
      stream.close();
      if (closed) return;
      // The log is durable, so a dropped stream loses nothing: say so, and resume from `seq`.
      snapshot = { ...snapshot, streamError: "Reconnecting to the runtime…" };
      emit();
      timer = setTimeout(connect, retryMs);
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (timer !== null) clearTimeout(timer);
      source?.close();
    },
  };
}

/** What the user is told when the runtime reports a failure. No implementation detail. */
export function userFacingError(events: DiagnosticEvent[]): string | null {
  const failure = [...events].reverse().find((event) => event.severity === "error");
  if (!failure) return null;
  if (failure.type === "transport.failed") {
    return "The audio connection to the runtime failed. The call cannot continue.";
  }
  if (failure.type === "runtime.error") {
    return "The runtime could not continue this call. The diagnostics below show what it reported.";
  }
  return "Something in the runtime failed. The diagnostics below show what it reported.";
}
