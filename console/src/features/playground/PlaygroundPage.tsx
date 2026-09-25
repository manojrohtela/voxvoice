import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { getJson, postOnUnload } from "../../api/client";
import type { AgentView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useAuth } from "../../app/auth";
import { useConsoleMeta } from "../../app/meta";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill, type Tone } from "../../components/StatusPill";
import { browserEnvironment, VoiceCall, type CallEnvironment, type CallState } from "../../voice/call";
import { consoleCallBackend } from "./backend";
import { canStartCalls } from "./roles";

function loadAgents(signal: AbortSignal): Promise<{ data: AgentView[] }> {
  return getJson<{ data: AgentView[] }>("/v1/console/agents", signal);
}

const STATE_LABEL: Record<CallState, [string, Tone]> = {
  idle: ["Ready", "neutral"],
  requesting_microphone: ["Waiting for microphone", "warn"],
  reserving: ["Reserving", "warn"],
  connecting: ["Connecting", "warn"],
  connected: ["Connected", "ok"],
  ending: ["Hanging up", "warn"],
  ended: ["Call ended", "neutral"],
  failed: ["Failed", "error"],
};

const ACTIVE: ReadonlySet<CallState> = new Set(["requesting_microphone", "reserving", "connecting", "connected"]);

function useElapsed(since: number | null): string {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [since]);
  if (since === null) return "0:00";
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The call itself. `environment` is injectable so tests can run without a real browser. */
export function Playground({ agents, environment }: { agents: AgentView[]; environment: CallEnvironment }) {
  const [params, setParams] = useSearchParams();
  const usable = agents.filter((agent) => agent.language_supported);
  const requested = params.get("agent");
  const [agentId, setAgentId] = useState(
    usable.find((agent) => agent.id === requested)?.id ?? usable[0]?.id ?? "",
  );
  const [state, setState] = useState<CallState>("idle");
  const [detail, setDetail] = useState<string | null>(null);
  const [log, setLog] = useState<{ at: string; message: string }[]>([]);
  const [muted, setMuted] = useState(false);
  const [connectedAt, setConnectedAt] = useState<number | null>(null);
  const [lastSession, setLastSession] = useState<string | null>(null);
  const callRef = useRef<VoiceCall | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const elapsed = useElapsed(state === "connected" ? connectedAt : null);

  // Leaving the page must not leave a live session behind: end it even as the page unloads.
  useEffect(() => {
    const onPageHide = () => {
      const session = callRef.current?.session;
      if (session) postOnUnload(`/v1/console/playground/sessions/${session}/end`);
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      void callRef.current?.hangUp();
    };
  }, []);

  const start = () => {
    setLog([]);
    setDetail(null);
    setLastSession(null);
    setMuted(false);
    const call = new VoiceCall(agentId, consoleCallBackend, environment, {
      onState: (next, why) => {
        setState(next);
        setDetail(why ?? null);
        if (next === "connected") {
          setConnectedAt(Date.now());
          setLastSession(call.session);
        }
      },
      onRemoteStream: (stream) => {
        if (audioRef.current) audioRef.current.srcObject = stream;
      },
      onLog: (message) =>
        setLog((entries) => [...entries, { at: new Date().toLocaleTimeString(), message }]),
    });
    callRef.current = call;
    void call.connect();
  };

  const active = ACTIVE.has(state);
  const [label, tone] = STATE_LABEL[state];

  if (usable.length === 0) {
    return (
      <EmptyState title="No agent to call yet">
        <p>
          {agents.length === 0
            ? "Create an agent first, then come back to talk to it."
            : "Every agent uses a language the speech recognizer does not support. Change one agent's language first."}{" "}
          <Link to="/agents">Go to Agents</Link>
        </p>
      </EmptyState>
    );
  }

  return (
    <div className="grid playground">
      <article className="card" aria-labelledby="call-heading">
        <h2 id="call-heading">Call</h2>
        <div className="form">
          <div className="form__field">
            <label htmlFor="playground-agent">Agent</label>
            <select
              id="playground-agent"
              value={agentId}
              disabled={active}
              onChange={(e) => {
                setAgentId(e.target.value);
                setParams({ agent: e.target.value }, { replace: true });
              }}
            >
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id} disabled={!agent.language_supported}>
                  {agent.language_supported ? agent.name : `${agent.name} (unsupported language)`}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="call">
          <StatusPill tone={tone}>{label}</StatusPill>
          {state === "connected" ? <span className="call__timer" aria-label="Call duration">{elapsed}</span> : null}
        </div>
        {detail ? (
          <p className="form__error" role="alert">
            {detail}
          </p>
        ) : null}
        <div className="form__actions">
          {active ? (
            <>
              <button type="button" className="button button--danger button--left" onClick={() => void callRef.current?.hangUp()}>
                Hang up
              </button>
              <button
                type="button"
                className="button button--secondary"
                aria-pressed={muted}
                disabled={state !== "connected"}
                onClick={() => {
                  callRef.current?.setMuted(!muted);
                  setMuted(!muted);
                }}
              >
                {muted ? "Unmute" : "Mute"}
              </button>
            </>
          ) : (
            <button type="button" className="button button--primary" onClick={start} disabled={!agentId}>
              {state === "idle" ? "Start call" : "Call again"}
            </button>
          )}
        </div>
        {lastSession && !active ? (
          <p className="note">
            <Link to={`/conversations/${lastSession}`}>View this conversation</Link>
          </p>
        ) : null}
        <p className="note">
          Use headphones: on speakers the agent can hear itself. Speak, then pause, and the agent
          answers when you stop.
        </p>
        {/* The agent's voice. Autoplay is allowed because the call starts from a click. */}
        <audio ref={audioRef} autoPlay aria-hidden="true" />
      </article>
      <article className="card" aria-labelledby="events-heading">
        <h2 id="events-heading">Connection</h2>
        {log.length === 0 ? (
          <p className="note">Connection steps appear here as the call is set up.</p>
        ) : (
          <ol className="call-log">
            {log.map((entry, index) => (
              <li key={index}>
                <time>{entry.at}</time> {entry.message}
              </li>
            ))}
          </ol>
        )}
        <p className="note">After the call, its transcript, per-turn latency and timeline are in Conversations.</p>
      </article>
    </div>
  );
}

export function PlaygroundPage({ environment = browserEnvironment() }: { environment?: CallEnvironment }) {
  const meta = useConsoleMeta();
  // Read through the auth state rather than `useMe`: an unknown role is not a refused one, so
  // nothing is offered and nothing is refused until the organization is known.
  const { state } = useAuth();
  const organization = state.status === "signed_in" ? state.me.organization : null;
  const agents = useResource(loadAgents);
  const voiceReady =
    meta.status === "ready" && meta.data.runtime.transport === "webrtc" && meta.data.runtime.webrtc_signalling;
  const roleKnown = organization !== null;
  const canCall = roleKnown && canStartCalls(organization.role);

  return (
    <section className="page">
      <header className="page__header">
        <h1>Playground</h1>
        <p className="page__lede">Talk to an agent through the real runtime, from this browser.</p>
      </header>
      {/* Before anything else: a role that cannot start a call is never offered one, so the
          browser is not asked for a microphone for a request the server will refuse. */}
      {roleKnown && !canCall ? (
        <EmptyState title="Only a developer, admin or owner can start calls">
          <p>
            Ask someone with one of those roles in {organization.name} to try an agent. You can
            still read every <Link to="/conversations">conversation</Link> they have.
          </p>
        </EmptyState>
      ) : null}
      {!roleKnown || (canCall && (meta.status === "loading" || agents.status === "loading")) ? <Loading /> : null}
      {canCall && agents.status === "error" ? (
        <ErrorState title="Could not load agents" error={agents.error} onRetry={agents.reload} />
      ) : null}
      {canCall && meta.status === "ready" && !voiceReady ? (
        <EmptyState title="Voice is not enabled on this server">
          <p>
            This server runs the <code>{meta.data.runtime.transport}</code> transport, which carries no real audio.
            Restart the API with <code>RUNTIME_TRANSPORT=webrtc</code> and the provider credentials, as described in{" "}
            <code>docs/runtime/local-run.md</code>.
          </p>
        </EmptyState>
      ) : null}
      {canCall && voiceReady && agents.status === "ready" ? (
        <Playground agents={agents.data.data} environment={environment} />
      ) : null}
    </section>
  );
}
