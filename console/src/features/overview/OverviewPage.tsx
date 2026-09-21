import { getJson } from "../../api/client";
import { EXPECTED_CONSOLE_API_REVISION, type Health } from "../../api/types";
import { useResource } from "../../api/useResource";
import { hasCapability, useConsoleMeta } from "../../app/meta";
import { NAVIGATION } from "../../app/navigation";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { transportLabel } from "../../components/TopBar";

function loadHealth(signal: AbortSignal): Promise<Health> {
  return getJson<Health>("/health", signal);
}

// The journey from the product vision, in order. Each step names the surface that delivers it.
const JOURNEY: ReadonlyArray<{ step: string; path: string }> = [
  { step: "Create your account and organization", path: "/settings" },
  { step: "Create an agent", path: "/agents" },
  { step: "Talk to it in the Playground", path: "/playground" },
  { step: "Review the conversation and its latency", path: "/conversations" },
  { step: "Create an API key and integrate", path: "/developers" },
];

export function OverviewPage() {
  const meta = useConsoleMeta();
  const health = useResource(loadHealth);

  return (
    <section className="page">
      <header className="page__header">
        <h1>Overview</h1>
        <p className="page__lede">
          The state of this VoxVoice server and the path from sign-in to a live voice conversation.
        </p>
      </header>

      {meta.status === "ready" && meta.data.console_api_revision !== EXPECTED_CONSOLE_API_REVISION ? (
        <div className="banner banner--warn" role="status">
          This console was built for API revision <code>{EXPECTED_CONSOLE_API_REVISION}</code> but the
          server reports <code>{meta.data.console_api_revision}</code>. Rebuild the console.
        </div>
      ) : null}

      <div className="grid">
        <article className="card" aria-labelledby="runtime-heading">
          <h2 id="runtime-heading">Runtime</h2>
          {meta.status === "loading" || health.status === "loading" ? <Loading /> : null}
          {meta.status === "error" ? (
            <ErrorState title="Cannot load server details" error={meta.error} onRetry={meta.reload} />
          ) : null}
          {health.status === "error" ? (
            <ErrorState title="Health check failed" error={health.error} onRetry={health.reload} />
          ) : null}
          {meta.status === "ready" && health.status === "ready" ? (
            <dl className="facts">
              <div>
                <dt>Status</dt>
                <dd>
                  {health.data.ok ? (
                    <StatusPill tone="ok">Healthy</StatusPill>
                  ) : (
                    <StatusPill tone="error">Unhealthy</StatusPill>
                  )}
                </dd>
              </div>
              <div>
                <dt>Database</dt>
                <dd>{health.data.db ? "Reachable" : "Unreachable"}</dd>
              </div>
              <div>
                <dt>Transport</dt>
                <dd>{transportLabel(meta.data.runtime.transport)}</dd>
              </div>
              <div>
                <dt>WebRTC signalling</dt>
                <dd>{meta.data.runtime.webrtc_signalling ? "Enabled" : "Disabled"}</dd>
              </div>
              <div>
                <dt>ICE policy</dt>
                <dd>{meta.data.runtime.ice_policy}</dd>
              </div>
              <div>
                <dt>API</dt>
                <dd>
                  <code>{meta.data.api_version}</code> · console <code>{meta.data.console_api_revision}</code>
                </dd>
              </div>
            </dl>
          ) : null}
          {meta.status === "ready" && meta.data.runtime.transport !== "webrtc" ? (
            <p className="note">
              Real voice needs <code>RUNTIME_TRANSPORT=webrtc</code> and provider credentials in the
              server&apos;s environment. See <code>docs/runtime/local-run.md</code>.
            </p>
          ) : null}
        </article>

        <article className="card" aria-labelledby="journey-heading">
          <h2 id="journey-heading">Get started</h2>
          <ol className="journey">
            {JOURNEY.map(({ step, path }) => {
              const item = NAVIGATION.find((candidate) => candidate.path === path);
              const available = item !== undefined && hasCapability(meta, item.capability);
              return (
                <li key={path} className={available ? "journey__step" : "journey__step is-pending"}>
                  <span>{step}</span>
                  {available ? (
                    <StatusPill tone="ok">Available</StatusPill>
                  ) : (
                    <StatusPill tone="neutral">{`Arrives in ${item?.milestone ?? "a later milestone"}`}</StatusPill>
                  )}
                </li>
              );
            })}
          </ol>
        </article>
      </div>
    </section>
  );
}
