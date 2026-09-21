import type { Role } from "../../api/types";

/** Mirrors the server's role matrix (LLD §3) for display only; the server enforces it. */
export function canManageKeys(role: Role): boolean {
  return role === "owner" || role === "admin";
}
