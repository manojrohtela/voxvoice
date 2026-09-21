import { createContext, useContext, type ReactNode } from "react";

import { ApiError, getJson } from "../api/client";
import type { ConsoleMeta } from "../api/types";
import { useResource, type Resource } from "../api/useResource";

type MetaResource = Resource<ConsoleMeta> & { reload: () => void };

const MetaContext = createContext<MetaResource | null>(null);

function isConsoleMeta(value: unknown): value is ConsoleMeta {
  if (typeof value !== "object" || value === null) return false;
  const meta = value as Partial<ConsoleMeta>;
  return (
    typeof meta.console_api_revision === "string" &&
    Array.isArray(meta.capabilities) &&
    meta.capabilities.every((capability) => typeof capability === "string") &&
    typeof meta.runtime === "object" &&
    meta.runtime !== null &&
    typeof meta.runtime.transport === "string"
  );
}

/** Everything else in the console trusts this shape, so it is checked once, here. */
export async function loadMeta(signal: AbortSignal): Promise<ConsoleMeta> {
  const meta = await getJson<unknown>("/v1/console/meta", signal);
  if (!isConsoleMeta(meta)) {
    throw new ApiError(200, "invalid_response", "The server returned unrecognised console metadata");
  }
  return meta;
}

/** Loads `/v1/console/meta` once for the whole console: version, runtime, capabilities. */
export function MetaProvider({ children }: { children: ReactNode }) {
  const meta = useResource(loadMeta);
  return <MetaContext.Provider value={meta}>{children}</MetaContext.Provider>;
}

export function useConsoleMeta(): MetaResource {
  const meta = useContext(MetaContext);
  if (meta === null) throw new Error("useConsoleMeta must be used inside <MetaProvider>");
  return meta;
}

/** True only when the server has declared the capability. Unknown means unavailable. */
export function hasCapability(meta: MetaResource, capability: string): boolean {
  return meta.status === "ready" && meta.data.capabilities.includes(capability);
}
