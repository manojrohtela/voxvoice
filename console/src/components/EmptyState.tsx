import type { ReactNode } from "react";

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {children ? <div className="state__body">{children}</div> : null}
    </div>
  );
}
