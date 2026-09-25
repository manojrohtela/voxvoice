import type { Role } from "../../api/types";

/**
 * Mirrors the server's role matrix (LLD §3) for display only; the server enforces it, and a
 * viewer who reaches `POST /v1/console/playground/rendezvous` anyway still gets a 403.
 *
 * The gate exists so a role that cannot start a call is never offered one — before the browser
 * asks for a microphone (M12.12 defect 3). It is the same rule `canEditAgents` states for agents.
 */
export function canStartCalls(role: Role): boolean {
  return role === "owner" || role === "admin" || role === "developer";
}
