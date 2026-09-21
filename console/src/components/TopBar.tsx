import { useConsoleMeta } from "../app/meta";
import { StatusPill } from "./StatusPill";

export function transportLabel(transport: string): string {
  if (transport === "webrtc") return "WebRTC · real providers";
  if (transport === "local") return "Loopback · no real voice";
  return transport;
}

export function TopBar() {
  const meta = useConsoleMeta();

  return (
    <header className="topbar">
      <span className="topbar__env">Local</span>
      <div className="topbar__spacer" />
      {meta.status === "ready" ? (
        <StatusPill tone={meta.data.runtime.transport === "webrtc" ? "ok" : "neutral"}>
          {transportLabel(meta.data.runtime.transport)}
        </StatusPill>
      ) : meta.status === "error" ? (
        <StatusPill tone="error">API unreachable</StatusPill>
      ) : null}
    </header>
  );
}
