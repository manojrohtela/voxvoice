import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./client";

export type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ApiError };

/**
 * Load one resource with loading / error / data states and a `reload`.
 *
 * `load` must be stable (define it at module level or memoize it): a new function identity
 * triggers a new request. In-flight requests are aborted on unmount and on reload.
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
): Resource<T> & { reload: () => void } {
  const [resource, setResource] = useState<Resource<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setResource({ status: "loading" });
    load(controller.signal).then(
      (data) => setResource({ status: "ready", data }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setResource({
          status: "error",
          error:
            error instanceof ApiError
              ? error
              : new ApiError(0, "internal", "Something went wrong while loading this view"),
        });
      },
    );
    return () => controller.abort();
  }, [load, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...resource, reload };
}
