import type { Role } from "../../api/types";

/** Mirrors the server's role matrix (LLD §3) for display only; the server enforces it. */
export function canEditAgents(role: Role): boolean {
  return role === "owner" || role === "admin" || role === "developer";
}
