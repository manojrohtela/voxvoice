import { postJson } from "../../api/client";
import type { CallBackend } from "../../voice/call";

/** The console's cookie-authenticated Playground routes, as the call's authorizer. */
export const consoleCallBackend: CallBackend = {
  reserve: () =>
    postJson<{ rendezvous_id: string; signalling_token: string }>("/v1/console/playground/rendezvous"),
  start: (agentId, rendezvousId) =>
    postJson<{ session_id: string }>("/v1/console/playground/sessions", {
      agent_id: agentId,
      rendezvous_id: rendezvousId,
    }),
  end: async (sessionId) => {
    await postJson(`/v1/console/playground/sessions/${sessionId}/end`);
  },
};
