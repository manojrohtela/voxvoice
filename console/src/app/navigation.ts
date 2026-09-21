// The console's information architecture, defined once (product vision §4).
//
// `capability` is the id the server declares in `/v1/console/meta` when the surface exists.
// Until then the item routes to the "not yet available" page. Nothing is faked in the meantime.

export interface NavItem {
  path: string;
  label: string;
  capability: string;
  milestone: string;
  summary: string;
}

export const NAVIGATION: readonly NavItem[] = [
  {
    path: "/",
    label: "Overview",
    capability: "runtime.status",
    milestone: "M12.1",
    summary: "Runtime status and the path from sign-in to a live conversation.",
  },
  {
    path: "/agents",
    label: "Agents",
    capability: "agents",
    milestone: "M12.3",
    summary: "Create and configure voice agents: instructions, greeting, language, model and voice.",
  },
  {
    path: "/playground",
    label: "Playground",
    capability: "playground",
    milestone: "M12.4",
    summary: "Pick an agent and talk to it through the real runtime, straight from your browser.",
  },
  {
    path: "/conversations",
    label: "Conversations",
    capability: "conversations",
    milestone: "M12.5",
    summary: "Every session with its transcript, per-turn latency and lifecycle timeline.",
  },
  {
    path: "/analytics",
    label: "Analytics",
    capability: "analytics",
    milestone: "M12.6",
    summary: "Sessions, turns, durations and voice-to-voice latency from measured signals only.",
  },
  {
    path: "/developers",
    label: "API keys & docs",
    capability: "api_keys",
    milestone: "M12.7",
    summary: "Developer API keys and the documentation for calling VoxVoice from your server.",
  },
  {
    path: "/settings",
    label: "Settings",
    capability: "identity",
    milestone: "M12.2",
    summary: "Your account, your organization and its members.",
  },
];
