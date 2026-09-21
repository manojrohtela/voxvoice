import type { ApiError } from "../api/client";

export function FormError({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <p className="form__error" role="alert">
      {error.message}
    </p>
  );
}
