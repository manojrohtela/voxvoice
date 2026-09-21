import type { ApiError } from "../api/client";

export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: ApiError;
  onRetry?: () => void;
}) {
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">{title}</p>
      <p className="state__body">{error.message}</p>
      {error.requestId ? (
        <p className="state__meta">
          Request ID <code>{error.requestId}</code>
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" className="button button--secondary" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}
