import type { ReactNode } from "react";

export function AuthLayout({ title, lede, children }: { title: string; lede: string; children: ReactNode }) {
  return (
    <main className="auth">
      <div className="auth__card">
        <div className="sidebar__brand">
          <span className="sidebar__mark" aria-hidden="true" />
          VoxVoice
        </div>
        <h1>{title}</h1>
        <p className="page__lede">{lede}</p>
        {children}
      </div>
    </main>
  );
}
