import { useState } from "react";

import { ApiError } from "../api/client";

/** Pending + error state for one form submission. The error is always an ApiError. */
export function useSubmit() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function run(action: () => Promise<void>): Promise<boolean> {
    setPending(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught : new ApiError(0, "internal", "Something went wrong"),
      );
      return false;
    } finally {
      setPending(false);
    }
  }

  return { pending, error, run };
}
